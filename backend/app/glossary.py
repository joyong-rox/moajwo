"""용어사전: 기관·지역 이름 → 용어사전 항목(코드) 해석.

용어사전 항목과 별칭은 사용자가 업로드·등록해 DB에 둔다 (처음에는 예시 몇 개만 있음).
- 기관(org): 코드는 사용자 지정 (예: 행정기관코드 7자리)
- 지역(region): 행정동코드 시도 2자리 / 시군구 5자리. 법정동 코드는 쓰지 않는다.

해석 순서: 별칭(유효기간 반영) → 항목 정식명칭 → 지역 약칭(서울, 서울시, 서울 강서구 …) → 유사 이름 추천.
같은 이름이 기준일에 유효한 항목 여러 개로 갈리면 '모호'로 돌려준다 (임의로 고르지 않는다).
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from rapidfuzz import fuzz, process

Kind = Literal["org", "region"]
Status = Literal["resolved", "ambiguous", "unknown"]
Via = Literal["alias", "name", "short", "none"]

REGION_FIELDS = frozenset({"sido", "sigungu", "region", "region_code"})
# 시도 약칭을 만들 때 떼어내는 접미사 (긴 것부터)
_SIDO_SUFFIXES = ("통합특별시", "특별자치시", "특별자치도", "특별시", "광역시", "직할시", "도")
SUGGEST_LIMIT = 5
SUGGEST_CUTOFF = 55


def normalize_name(text: str) -> str:
    """이름 비교용 키: NFKC, 공백·가운뎃점 제거, 소문자."""
    return re.sub(r"[\s·ㆍ]+", "", unicodedata.normalize("NFKC", text)).lower()


def period_end(period: str | None) -> date | None:
    """'2026' → 2026-12-31, '2026-09' → 2026-09-30. 통계 기준 시점은 기간 말일로 본다."""
    if not period:
        return None
    year = int(period[:4])
    if len(period) == 4:
        return date(year, 12, 31)
    month = int(period[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


@dataclass(frozen=True)
class Entry:
    kind: Kind
    code: str
    name: str
    type_label: str = ""
    parent_code: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None  # 그날까지 유효
    previous_codes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.code}"

    def valid_at(self, at: date | None) -> bool:
        if at is None:
            return self.valid_to is None or self.valid_to >= date.today()
        return (self.valid_from is None or self.valid_from <= at) and (self.valid_to is None or at <= self.valid_to)


@dataclass(frozen=True)
class EntityRef:
    kind: Kind
    code: str
    name: str
    type_label: str
    valid_to: str | None = None  # YYYY-MM-DD, 폐지·종료된 항목이면 마지막 유효일

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.code}"


def entity_ref(entry: Entry) -> EntityRef:
    return EntityRef(
        entry.kind,
        entry.code,
        entry.name,
        entry.type_label,
        entry.valid_to.isoformat() if entry.valid_to else None,
    )


@dataclass(frozen=True)
class AliasEntry:
    kind: Kind
    target_code: str
    valid_from: date | None
    valid_to: date | None

    def valid_at(self, at: date | None) -> bool:
        if at is None:
            return self.valid_to is None or self.valid_to >= date.today()
        return (self.valid_from is None or self.valid_from <= at) and (self.valid_to is None or at <= self.valid_to)


@dataclass
class Resolution:
    name: str
    status: Status
    via: Via = "none"
    ref: EntityRef | None = None
    candidates: list[EntityRef] = field(default_factory=list)
    note: str | None = None

    @property
    def key(self) -> str | None:
        return self.ref.key if self.ref and self.status == "resolved" else None


def sido_short_names(name: str) -> set[str]:
    """'서울특별시' → {'서울', '서울시'}, '경기도' → {'경기'}"""
    for suffix in _SIDO_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[: -len(suffix)]
            return {base} if suffix == "도" else {base, base + "시"}
    return set()


def prefer_for_field(field_key: str | None) -> Kind:
    return "region" if field_key in REGION_FIELDS else "org"


class Glossary:
    def __init__(self, entries: list[Entry] | None = None, aliases: dict[str, list[AliasEntry]] | None = None) -> None:
        self.entries = {e.key: e for e in entries or []}
        self.aliases = aliases or {}
        # 정규화 이름 → [(항목, 약칭 여부)]
        self._index: dict[str, list[tuple[Entry, bool]]] = {}
        for entry in self.entries.values():
            self._add(entry.name, entry, False)
            if entry.kind == "region":
                self._add_region_shorts(entry)

    def _add(self, name: str, entry: Entry, short: bool) -> None:
        bucket = self._index.setdefault(normalize_name(name), [])
        if all(e.key != entry.key for e, _ in bucket):
            bucket.append((entry, short))

    def _add_region_shorts(self, entry: Entry) -> None:
        parent = self.entries.get(f"region:{entry.parent_code}") if entry.parent_code else None
        if parent is None:  # 시도
            for short in sido_short_names(entry.name):
                self._add(short, entry, True)
            return
        leaf = entry.name[len(parent.name) :].strip() if entry.name.startswith(parent.name) else entry.name
        if not leaf:
            return
        self._add(leaf, entry, True)  # '강서구' 단독 (여러 시도에서 겹칠 수 있음)
        for short in sido_short_names(parent.name):
            self._add(f"{short} {leaf}", entry, True)  # '서울 강서구', '서울시 강서구'

    # ------------------------------------------------------------ 해석

    def resolve(self, name: str, at: date | None = None, prefer: Kind = "org") -> Resolution:
        norm = normalize_name(name)
        if not norm:
            return Resolution(name, "unknown")

        aliases = [a for a in self.aliases.get(norm, []) if a.valid_at(at)]
        targets = list({e.key: e for a in aliases if (e := self.entries.get(f"{a.kind}:{a.target_code}"))}.values())
        if len(targets) == 1:
            return Resolution(name, "resolved", "alias", entity_ref(targets[0]), note=self._ended_note(targets[0], at))
        if targets:
            refs = [entity_ref(e) for e in targets]
            return Resolution(name, "ambiguous", "alias", candidates=refs, note="별칭이 여러 항목에 연결됨")

        matches = self._index.get(norm, [])
        order: tuple[Kind, Kind] = ("region", "org") if prefer == "region" else ("org", "region")
        for kind in order:
            of_kind = [(e, short) for e, short in matches if e.kind == kind]
            if not of_kind:
                continue
            valid = [(e, short) for e, short in of_kind if e.valid_at(at)]
            exact = [e for e, short in valid if not short]
            picked = exact or [e for e, _ in valid]
            if len(picked) == 1:
                via: Via = "name" if exact else "short"
                return Resolution(name, "resolved", via, entity_ref(picked[0]))
            if len(picked) > 1:
                refs = [entity_ref(e) for e in picked]
                return Resolution(name, "ambiguous", "name", candidates=refs, note="같은 이름의 항목이 여러 개")
            ended = list({e.key: e for e, _ in of_kind}.values())
            if len(ended) == 1:
                return Resolution(name, "resolved", "name", entity_ref(ended[0]), note=self._ended_note(ended[0], at))
            refs = [entity_ref(e) for e in ended]
            return Resolution(name, "ambiguous", "name", candidates=refs, note="기준일에 유효한 항목이 없음")

        return Resolution(name, "unknown", candidates=self.suggest(norm, at), note="용어사전에 없는 이름")

    def suggest(self, norm: str, at: date | None) -> list[EntityRef]:
        """사전에 없는 이름에 대한 유사 이름 후보 (기준일에 유효한 항목 우선)."""
        names = list(self._index)
        hits = process.extract(norm, names, scorer=fuzz.ratio, limit=SUGGEST_LIMIT * 3, score_cutoff=SUGGEST_CUTOFF)
        refs: dict[str, EntityRef] = {}
        for matched, _, _ in hits:
            for entry, _ in sorted(self._index[matched], key=lambda t: not t[0].valid_at(at)):
                refs.setdefault(entry.key, entity_ref(entry))
        return list(refs.values())[:SUGGEST_LIMIT]

    def replacements(self, ref: EntityRef, at: date | None) -> list[EntityRef]:
        """종료된 항목의 후보: 이 항목을 개편 전 코드로 가진 항목 → 유사 이름 순."""
        out = [
            entity_ref(e)
            for e in self.entries.values()
            if e.kind == ref.kind and ref.code in e.previous_codes and e.valid_at(at)
        ]
        out.extend(self.suggest(normalize_name(ref.name), at))
        unique = {r.key: r for r in out if r.key != ref.key}
        return list(unique.values())[:SUGGEST_LIMIT]

    @staticmethod
    def _ended_note(entry: Entry, at: date | None) -> str | None:
        if entry.valid_to and (at is None or at > entry.valid_to):
            return f"{entry.valid_to.isoformat()}까지만 유효한 {entry.type_label or '항목'} — 폐지 또는 개편된 이름"
        if entry.valid_from and at is not None and at < entry.valid_from:
            return f"{entry.valid_from.isoformat()}부터 유효한 {entry.type_label or '항목'} — 기준일 이전"
        return None
