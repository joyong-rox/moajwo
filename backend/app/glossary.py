"""용어사전: 기관·자치단체 이름 → 7자리 코드 해석.

용어사전 항목과 별칭은 사용자가 업로드·등록해 DB에 둔다 (처음에는 예시 몇 개만 있음).
- 항목은 7자리 기관코드 하나로 식별한다 (현행 기관끼리 겹치지 않음).
- 자치단체는 시도코드(2자리)·시군구코드(3자리)를 속성으로 가진다. 지역 약칭(서울, 서울 강서구 …)과
  데이터의 지역코드 열 연결에 쓴다. 법정동 코드는 쓰지 않는다.

해석 순서: 별칭(유효기간 반영) → 정식명칭 → 기본 별칭(행정구역 개편 승계) → 지역 약칭 → 유사 이름 추천.
사용자가 올리거나 등록한 별칭·항목이 기본 데이터(마이그레이션 시드)보다 항상 먼저다.
날짜 형식 값('2026-08', '2026년')은 이름으로 해석하지 않는다 (유사 이름 후보도 만들지 않음).
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

Prefer = Literal["org", "region"]
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


def _valid(valid_from: date | None, valid_to: date | None, at: date | None) -> bool:
    """기준일(at)에 유효한지. 기준일을 정하지 않으면 오늘로 보고 시작일·종료일을 모두 검사한다."""
    at = at or date.today()
    return (valid_from is None or valid_from <= at) and (valid_to is None or at <= valid_to)


@dataclass(frozen=True)
class Entry:
    code: str  # 7자리
    name: str
    type_label: str = ""
    sido_code: str | None = None  # 2자리
    sigungu_code: str | None = None  # 3자리
    valid_from: date | None = None
    valid_to: date | None = None  # 그날까지 유효
    previous_codes: tuple[str, ...] = ()

    @property
    def is_region(self) -> bool:
        return self.sido_code is not None

    @property
    def region_code(self) -> str | None:
        """시도 2자리 또는 시도+시군구 5자리."""
        if self.sido_code is None:
            return None
        return self.sido_code + (self.sigungu_code or "")

    def valid_at(self, at: date | None) -> bool:
        return _valid(self.valid_from, self.valid_to, at)


@dataclass(frozen=True)
class EntityRef:
    code: str
    name: str
    type_label: str
    valid_to: str | None = None  # YYYY-MM-DD, 종료된 항목이면 마지막 유효일

    @property
    def key(self) -> str:
        return f"entity:{self.code}"


def entity_ref(entry: Entry) -> EntityRef:
    return EntityRef(entry.code, entry.name, entry.type_label, entry.valid_to.isoformat() if entry.valid_to else None)


@dataclass(frozen=True)
class AliasEntry:
    target_code: str
    valid_from: date | None = None
    valid_to: date | None = None
    base: bool = False  # 기본 데이터(개편 전 이름 → 승계 항목). 사용자 별칭·정식명칭보다 뒤에 쓴다

    def valid_at(self, at: date | None) -> bool:
        return _valid(self.valid_from, self.valid_to, at)


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
    """'서울특별시' → {'서울', '서울시'}, '경기도' → {'경기'}, '충청북도' → {'충청북', '충북'}"""
    for suffix in _SIDO_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[: -len(suffix)]
            if not suffix.endswith("도"):  # '…시' 계열만 '서울시'처럼 쓴다 ('제주시'는 실제 기초자치단체 이름)
                return {base, base + "시"}
            # 충청북도·경상남도·전라북도처럼 세 글자 + '도'는 첫 글자와 끝 글자로 줄여 쓴다 (충북·경남·전북)
            return {base, base[0] + base[2]} if len(base) == 3 else {base}
    return set()


# 날짜·기간 값: 2026-08, 2026.08.31, 2026/8, 2026년, 2026년 8월, 20260831
DATE_VALUE = re.compile(
    r"^(?:19|20)\d{2}(?:\s*[-./]\s*\d{1,2}(?:\s*[-./]\s*\d{1,2})?\.?"
    r"|\s*년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?)?|(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"
)


def looks_like_date(text: str) -> bool:
    return bool(DATE_VALUE.match(unicodedata.normalize("NFKC", text).strip()))


DateKind = Literal["year", "month", "date"]
_TIME = r"(?:[\sT]+\d{1,2}:\d{2}(?::\d{2})?)?"
_DATE_FORMS: list[tuple[re.Pattern[str], DateKind]] = [
    (re.compile(r"^((?:19|20)\d{2})\s*년$"), "year"),
    (re.compile(r"^((?:19|20)\d{2})\s*[-./]\s*(\d{1,2})\.?$"), "month"),
    (re.compile(r"^((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월$"), "month"),
    (re.compile(r"^((?:19|20)\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\.?" + _TIME + "$"), "date"),
    (re.compile(r"^((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일" + _TIME + "$"), "date"),
    (re.compile(r"^((?:19|20)\d{2})(\d{2})(\d{2})$"), "date"),
]


def parse_date_value(text: str) -> tuple[DateKind, str] | None:
    """날짜·기간 글자 → (종류, 기간). '2026년' → ('year', '2026'), '2026-08'·'2026년 8월' → ('month', '2026-08'),
    '2026-08-31'·'2026년 8월 31일'·'20260831' → ('date', '2026-08'). 실제로 없는 월·일(2026-13, 2026-02-30)은 None.

    열 매핑의 날짜 필터와 통합 전 검증의 기간 검사가 같은 규칙을 쓴다.
    8자리 숫자 글자를 날짜로 볼지는 호출하는 쪽이 정한다 (매핑은 코드와 겹쳐 날짜로 보지 않음).
    """
    value = unicodedata.normalize("NFKC", text).strip()
    for pattern, kind in _DATE_FORMS:
        m = pattern.match(value)
        if m is None:
            continue
        year = int(m.group(1))
        if kind == "year":
            return kind, str(year)
        month = int(m.group(2))
        if not 1 <= month <= 12:
            return None
        if kind == "date":
            try:
                date(year, month, int(m.group(3)))
            except ValueError:
                return None
        return kind, f"{year}-{month:02d}"
    return None


def prefer_for_field(field_key: str | None) -> Prefer:
    return "region" if field_key in REGION_FIELDS else "org"


class Glossary:
    def __init__(self, entries: list[Entry] | None = None, aliases: dict[str, list[AliasEntry]] | None = None) -> None:
        self.entries = {e.code: e for e in entries or []}
        self.aliases = aliases or {}
        # 정규화 이름 → [(항목, 약칭 여부)]
        self._index: dict[str, list[tuple[Entry, bool]]] = {}
        # 시도 2자리 / 시도+시군구 5자리 → 항목들 (기간이 다른 항목이 있을 수 있음)
        self._by_region: dict[str, list[Entry]] = {}
        for entry in self.entries.values():
            self._add(entry.name, entry, False)
            if entry.region_code:
                self._by_region.setdefault(entry.region_code, []).append(entry)
        for entry in self.entries.values():
            if entry.is_region:
                self._add_region_shorts(entry)

    def _add(self, name: str, entry: Entry, short: bool) -> None:
        bucket = self._index.setdefault(normalize_name(name), [])
        if all(e.code != entry.code for e, _ in bucket):
            bucket.append((entry, short))

    def _sido_of(self, entry: Entry) -> Entry | None:
        candidates = self._by_region.get(entry.sido_code or "", [])
        return next((e for e in candidates if e.valid_at(None)), candidates[0] if candidates else None)

    def _add_region_shorts(self, entry: Entry) -> None:
        if entry.sigungu_code is None:  # 시도
            for short in sido_short_names(entry.name):
                self._add(short, entry, True)
            return
        sido = self._sido_of(entry)
        if sido is None:
            return
        leaf = entry.name[len(sido.name) :].strip() if entry.name.startswith(sido.name) else entry.name
        if not leaf:
            return
        self._add(leaf, entry, True)  # '강서구' 단독 (여러 시도에서 겹칠 수 있음)
        for short in sido_short_names(sido.name):
            self._add(f"{short} {leaf}", entry, True)  # '서울 강서구', '서울시 강서구'

    # ------------------------------------------------------------ 해석

    def by_region_code(self, code: str, at: date | None = None) -> Entry | None:
        """데이터의 지역코드 값 → 항목. 2자리 시도, 5자리 시도+시군구 (10자리 행정동 코드는 앞 5자리)."""
        key = code[:5] if len(code) == 10 else code
        candidates = self._by_region.get(key, [])
        valid = [e for e in candidates if e.valid_at(at)]
        return (valid or candidates)[0] if len(valid or candidates) == 1 else None

    def resolve(self, name: str, at: date | None = None, prefer: Prefer = "org") -> Resolution:
        norm = normalize_name(name)
        if not norm:
            return Resolution(name, "unknown")
        if looks_like_date(name):
            return Resolution(name, "unknown", note="날짜 형식 값 — 기관·지역 이름으로 해석하지 않음")

        aliases = [a for a in self.aliases.get(norm, []) if a.valid_at(at)]
        user_aliases = [a for a in aliases if not a.base]
        targets = list({e.code: e for a in user_aliases if (e := self.entries.get(a.target_code))}.values())
        if len(targets) == 1:
            return Resolution(name, "resolved", "alias", entity_ref(targets[0]), note=self._ended_note(targets[0], at))
        if targets:
            refs = [entity_ref(e) for e in targets]
            return Resolution(name, "ambiguous", "alias", candidates=refs, note="별칭이 여러 항목에 연결됨")

        matches = self._index.get(norm, [])
        valid = [(e, short) for e, short in matches if e.valid_at(at)]
        exact = [e for e, short in valid if not short]
        if not exact:  # 개편 전 이름 → 승계 항목 (기본 별칭). 기준일에 유효한 정식명칭이 있으면 그쪽이 먼저다
            successors = list(
                {e.code: e for a in aliases if a.base and (e := self.entries.get(a.target_code))}.values()
            )
            if len(successors) == 1:
                return Resolution(name, "resolved", "alias", entity_ref(successors[0]))
            if successors:
                refs = [entity_ref(e) for e in successors]
                return Resolution(name, "ambiguous", "alias", candidates=refs, note="행정구역 개편으로 나뉜 지역")
        if not matches:
            return Resolution(name, "unknown", candidates=self.suggest(norm, at), note="용어사전에 없는 이름")
        picked = exact or [e for e, _ in valid]
        if len(picked) > 1:  # 이름이 겹치면 기준 열 성격(지역/기관)에 맞는 항목을 우선
            preferred = [e for e in picked if e.is_region == (prefer == "region")]
            picked = preferred or picked
        if len(picked) == 1:
            via: Via = "name" if exact else "short"
            return Resolution(name, "resolved", via, entity_ref(picked[0]))
        if len(picked) > 1:
            refs = [entity_ref(e) for e in picked]
            return Resolution(name, "ambiguous", "name", candidates=refs, note="같은 이름의 항목이 여러 개")
        ended = list({e.code: e for e, _ in matches}.values())
        if len(ended) == 1:
            return Resolution(name, "resolved", "name", entity_ref(ended[0]), note=self._ended_note(ended[0], at))
        refs = [entity_ref(e) for e in ended]
        return Resolution(name, "ambiguous", "name", candidates=refs, note="기준일에 유효한 항목이 없음")

    def suggest(self, norm: str, at: date | None) -> list[EntityRef]:
        """사전에 없는 이름에 대한 유사 이름 후보 (기준일에 유효한 항목 우선)."""
        hits = process.extract(
            norm, list(self._index), scorer=fuzz.ratio, limit=SUGGEST_LIMIT * 3, score_cutoff=SUGGEST_CUTOFF
        )
        refs: dict[str, EntityRef] = {}
        for matched, _, _ in hits:
            for entry, _ in sorted(self._index[matched], key=lambda t: not t[0].valid_at(at)):
                refs.setdefault(entry.code, entity_ref(entry))
        return list(refs.values())[:SUGGEST_LIMIT]

    def replacements(self, ref: EntityRef, at: date | None) -> list[EntityRef]:
        """종료된 항목의 후보: 이 항목을 이전코드로 가진 항목 → 유사 이름 순."""
        out = [entity_ref(e) for e in self.entries.values() if ref.code in e.previous_codes and e.valid_at(at)]
        out.extend(self.suggest(normalize_name(ref.name), at))
        unique = {r.code: r for r in out if r.code != ref.code}
        return list(unique.values())[:SUGGEST_LIMIT]

    @staticmethod
    def _ended_note(entry: Entry, at: date | None) -> str | None:
        """기준일(없으면 오늘) 밖의 항목 안내. 이런 해석은 후보 안내로만 쓰고 통합에서 자동으로 바꾸지 않는다."""
        at = at or date.today()
        if entry.valid_to and at > entry.valid_to:
            return f"{entry.valid_to.isoformat()}까지만 유효한 {entry.type_label or '항목'} — 폐지 또는 개편된 이름"
        if entry.valid_from and at < entry.valid_from:
            return f"{entry.valid_from.isoformat()}부터 유효한 {entry.type_label or '항목'} — 기준일 이전"
        return None
