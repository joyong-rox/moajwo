"""검토의 검사 기준 — 검사에 실제 적용한 열 매핑을 남기고, 완료·통합 전에 지금 매핑과 비교한다.

검토 결과(차단·경고·확인 항목)는 그 검사에 적용한 매핑과 값 해석으로 계산한 것이다. 다른 매핑으로 결과를 만들면
그 검사 결과를 쓸 수 없으므로, 다시 검사하기 전에는 완료·통합하지 않는다.

기록 (검토 params의 'check_basis' — 화면의 조건 표시에는 내보내지 않고, 다시 검사할 때 새로 만든다):
- files: 파일(검토 대상 + 전월 비교 자료) → 시트 → 열 → [상태, 항목, 구분어, 판정 방법] — 검사 때의 매핑 결과.
  상태가 confirmed면 사용자 확정, auto면 자동 적용(값은 원문 그대로, 수량 해석은 확정만), 그 밖은 검사에 쓰지 않음.
- lookups: 사용자 확정이 없는 열의 자동 적용 근거 — 학습 별칭·전사 표준 사전이 그 열 이름에 주는 항목.
  표준 사전과 정확히 일치한 열은 사전이 먼저라 넣지 않는다 (mapping.MappingEngine._exact_match 순서).
- baseline: 전월 비교에 쓴 파일, dictionary: 표준 사전 파일 지문.
- entities: 기관·지역 용어사전 — 검사가 해석한 이름(데이터·전월 자료·필수 기관 목록)마다 상태·방법·항목 코드·정식명칭·
  안내, 지역코드마다 연결한 항목, 해석 기준일, 용어사전 지문. 통합은 이 이름들을 용어사전으로 다시 해석해 결과에 쓴다.

비교:
- 조회·완료 (모델 호출·파일 읽기 없음): 사용자 확정, 자동 적용 근거, 표준 사전 지문. 개인정보 때문에 제외한 파일은
  통합하지 않으므로 보지 않고, 전월 비교 자료는 검사에 썼으므로 본다. 용어사전은 지문이 다를 때만 기록한 이름과
  검토에서 승인한 연결을 지금 용어사전으로 다시 해석해 비교한다 (관련 없는 이름의 변경은 막지 않음).
- 통합: 위 비교에 더해, 결과를 만들려고 다시 계산한 매핑(기존 동작 — LLM 없이)의 적용 상태가 기록과 같은지 본다.
  임베딩 유사도처럼 조회 때는 알 수 없는 자동 적용 변화도 결과 파일을 만들기 전에 여기서 막는다. 용어사전은 한 번
  읽어 비교하고 그 용어사전으로 결과를 만든다.
- 기록이 없는 이전 버전 검토는 같은지 확인할 수 없으므로 같다고 보지 않는다 (다시 검사 필요).

검토 안의 매핑 승인은 검사 입력이 그대로임을 확인할 수 있을 때만 기록에 더해 기존 검사 결과를 이어 쓴다 (effect_free).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import date
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from app.glossary import Glossary, Resolution
from app.mapping import ColumnMapping, dictionary_digest, load_dictionary, split_column_name
from app.models import ReviewItem, SourceFile
from app.parsing import SheetResult
from app.pii import mask_text
from app.quantity import QUANTITY_FIELDS, text_value_columns
from app.validation import CHECKED_FIELDS, EntityResolver

CHECK_BASIS_KEY = "check_basis"
BASIS_VERSION = 2
# 검토 params 안의 내부 기록 ('verified_decisions'는 이전 보완의 확정만 담은 기록 — 읽지 않고 버린다)
INTERNAL_PARAMS = (CHECK_BASIS_KEY, "verified_decisions")
LEGACY_REASON = (
    "이 검토는 검사 기준(검사에 적용한 열 매핑)을 기록하기 전 버전에서 만들어 지금 매핑과 같은지 확인할 수 없습니다"
)
ENTITY_LEGACY_REASON = (
    "이 검토는 기관·지역 용어사전 해석을 기록하기 전 버전에서 만들어 지금 용어사전과 같은지 확인할 수 없습니다"
)

Decisions = dict[str, dict[str, str | None]]  # 한 파일의 열 매핑 확정: 시트 → 열 → 항목 (None = 해당 없음)


@lru_cache(maxsize=1)
def _labels() -> dict[str, str]:
    return {f.key: f.label for f in load_dictionary()}


def _lookup(method: str, primary: str, learned: dict[str, str], standard: dict[str, str]) -> list[str | None] | None:
    """이 열의 자동 적용을 바꿀 수 있는 근거 (판정 순서: 사전 → 전사 표준 사전 → 학습 별칭 → 날짜 형식·유사도)."""
    if method == "exact":
        return None  # 사전과 정확히 일치 — 전사 표준 사전·학습 별칭보다 먼저다
    if method == "standard":
        return [None, standard.get(primary)]  # 전사 표준 사전이 학습 별칭보다 먼저다
    return [learned.get(primary), standard.get(primary)]


def build_basis(
    files: list[tuple[int, dict[str, list[ColumnMapping]]]],
    baseline_ids: list[int],
    learned: dict[str, str],
    standard: dict[str, str],
) -> dict[str, Any]:
    """files: (파일 id, 시트 → 검사에 쓴 매핑) — 검토 대상과 전월 비교 자료. learned·standard: 그 매핑에 쓴 값."""
    out: dict[str, Any] = {}
    lookups: dict[str, Any] = {}
    for fid, sheets in files:
        out[str(fid)] = {
            name: {m.column: [m.status, m.field_key, m.qualifier, m.method] for m in ms} for name, ms in sheets.items()
        }
        found = {}
        for name, ms in sheets.items():
            cols = {
                m.column: v
                for m in ms
                if m.status != "confirmed"
                and (v := _lookup(m.method, split_column_name(m.column).primary, learned, standard)) is not None
            }
            if cols:
                found[name] = cols
        if found:
            lookups[str(fid)] = found
    return {"v": BASIS_VERSION, "files": out, "lookups": lookups, "baseline": list(baseline_ids),
            "dictionary": dictionary_digest()}  # fmt: skip


def _shown(key: str | None) -> str:
    return _labels().get(key, key) if key else "해당 없음"


def _applied(entry: list[Any]) -> str:
    status, key = entry[0], entry[1]
    if status == "confirmed":
        return f"확정 {_shown(key)}"
    if status == "auto" and key:
        return f"자동 적용 {_shown(key)}"
    return "검사에 쓰지 않음"


def basis_changes(
    db: Session,
    basis: dict[str, Any],
    decisions: dict[int, Decisions],
    learned: dict[str, str],
    standard: dict[str, str],
) -> list[str]:
    """검사 기준과 지금이 다른 점 (조회·완료·통합 공통, 모델 호출·파일 읽기 없음).

    decisions: 비교할 파일 → 지금 확정. learned·standard: 지금의 학습 별칭·전사 표준 사전.
    """
    out = []
    if basis.get("dictionary") != dictionary_digest():
        out.append("표준 사전이 검사한 뒤 바뀌었습니다 (사전과 일치해 자동 적용한 열이 달라질 수 있음)")
    baseline = set(basis.get("baseline", []))
    for fid, now in decisions.items():
        record = db.get(SourceFile, fid)
        where = ("전월 자료 " if fid in baseline else "") + (record.original_name if record is not None else f"#{fid}")
        sheets = basis["files"].get(str(fid))
        if sheets is None:
            out.append(mask_text(f"{where}: 검사 기준에 없는 파일"))
            continue
        for sheet, cols in sheets.items():
            decided = now.get(sheet, {})
            stored = basis.get("lookups", {}).get(str(fid), {}).get(sheet, {})
            for column, entry in cols.items():
                was = (entry[0] == "confirmed", entry[1] if entry[0] == "confirmed" else None)
                cur = (column in decided, decided.get(column))
                if was != cur:
                    after = f"확정 {_shown(cur[1])}" if cur[0] else "확정 없음"
                    out.append(mask_text(f"{where} '{column}' 열: {_applied(entry)} → {after}"))
                elif entry[0] != "confirmed" and column in stored:
                    new = _lookup(entry[3], split_column_name(column).primary, learned, standard)
                    if new != stored[column]:
                        pairs = zip(("학습 별칭", "전사 표준 사전"), stored[column], new or [None, None], strict=True)
                        source = ", ".join(
                            f"{name} {_shown(a) if a else '없음'} → {_shown(b) if b else '없음'}"
                            for name, a, b in pairs
                            if a != b
                        )
                        out.append(
                            mask_text(f"{where} '{column}' 열: {_applied(entry)} — 자동 적용 근거가 바뀜 ({source})")
                        )
    return out


def _klass(status: str, key: str | None, qualifier: str | None) -> tuple[Any, ...]:
    if status in ("auto", "confirmed") and key:
        return ("applied", key, qualifier, status == "confirmed")  # 확정 여부: 수량 해석이 달라짐
    if status == "confirmed":
        return ("none",)  # '해당 없음' 확정
    return ("unapplied",)


def applied_drift(basis: dict[str, Any], fid: int, sheet: str, mappings: list[ColumnMapping]) -> list[str]:
    """통합하려고 다시 계산한 매핑의 적용 상태가 검사 기준과 다른 열 ('검사 때 → 지금')."""
    cols = basis["files"].get(str(fid), {}).get(sheet)
    if cols is None:
        return [f"'{sheet}' 시트: 검사 기준에 없는 시트"]
    out = []
    for m in mappings:
        entry = cols.get(m.column)
        if entry is None:
            out.append(f"'{m.column}' 열: 검사 기준에 없는 열")
        elif _klass(entry[0], entry[1], entry[2]) != _klass(m.status, m.field_key, m.qualifier):
            out.append(f"'{m.column}' 열: 검사 때 {_applied(entry)} → 지금 {_applied([m.status, m.field_key])}")
    return [mask_text(t) for t in out]


# ---------------------------------------------------------------- 기관·지역 용어사전


def _sig(res: Resolution | None) -> list[Any]:
    """해석 결과 중 검사·결과에 쓰는 것 (상태·방법·항목 코드·정식명칭·안내). 후보 목록은 결과에 쓰지 않아 뺀다."""
    if res is None:
        return [None, None, None, None, None]
    ref = res.ref
    return [res.status, res.via, ref.code if ref else None, ref.name if ref else None, res.note]


def entity_basis(resolver: EntityResolver, digest: str) -> dict[str, Any]:
    """검사가 해석한 기관·지역 이름과 지역코드 (검사 기준의 'entities'). digest: 그 용어사전의 지문."""
    names, codes = resolver.used()
    return {
        "digest": digest,
        "at": resolver.at.isoformat() if resolver.at else None,
        "names": [[name, prefer, *_sig(res)] for (name, prefer), res in sorted(names.items())],
        "codes": [[code, entry] for code, entry in sorted(codes.items())],
    }


def _described(sig: list[Any]) -> str:
    status, _via, code, name, note = sig
    if status == "resolved" and code:
        return f"{name}({code})" + (f" — {note}" if note else "")
    if status == "ambiguous":
        return "후보가 여러 개" + (f" ({note})" if note else "")
    return "용어사전에 없음" if status is not None else "용어사전 없이 검사"


def approved_links(items: Iterable[ReviewItem]) -> list[tuple[str, str, str | None]]:
    """검토에서 승인한 기관·지역 연결 (이름, 항목 코드, 승인 때 정식명칭) — 통합은 이 연결을 용어사전보다 먼저 쓴다."""
    out = []
    for i in items:
        if i.kind == "entity" and i.decision == "approved" and i.choice and i.before:
            candidates = json.loads(i.payload or "{}").get("candidates", [])
            out.append((i.before, i.choice, next((c["name"] for c in candidates if c["code"] == i.choice), None)))
    return out


def entity_changes(
    record: dict[str, Any] | None,
    digest: str,
    glossary: Callable[[], Glossary],
    approved: Callable[[], list[tuple[str, str, str | None]]],
) -> list[str]:
    """검사가 해석한 기관·지역 이름과 검토에서 승인한 연결이 지금 용어사전에서 달라진 점 (파일 읽기·모델 호출 없음).

    record: 검사 기준의 'entities' (없으면 기록하기 전 버전의 검토). digest·glossary: 지금 용어사전의 지문과 용어사전
    (glossary_api.glossary_state — 지문이 같으면 만들지 않는다). 기준일 없이 오늘 기준으로 해석한 검토는 날짜가 지나면
    해석이 달라질 수 있어 지문이 같아도 다시 해석한다.
    승인한 연결이 있는 이름은 통합이 그 연결을 쓰므로 연결한 항목만 본다 ('이 형식 기억하기'로 완료하면 그 연결이
    용어사전 별칭으로 학습되어 이름의 해석이 바뀌는데, 그것은 이 검토의 결정 그대로다).
    """
    if record is None:
        return [ENTITY_LEGACY_REASON]
    if record["digest"] == digest and record["at"] is not None:
        return []
    now_glossary = glossary()
    at = date.fromisoformat(record["at"]) if record["at"] else None
    links = approved()
    decided = {name for name, _, _ in links}
    out = []
    for name, prefer, *was in record["names"]:
        if name in decided:
            continue
        now = _sig(now_glossary.resolve(name, at, prefer))
        if now != was:
            out.append(f"기관·지역 이름 '{name}': 검사 때 {_described(was)} → 지금 {_described(now)}")
    for code, was in record["codes"]:
        entry = now_glossary.by_region_code(code, at)
        if (entry.code if entry else None) != was:
            now_code = entry.code if entry else "용어사전에 없음"
            out.append(f"지역코드 '{code}': 검사 때 {was or '용어사전에 없음'} → 지금 {now_code}")
    for name, code, shown in links:
        entry = now_glossary.entries.get(code)
        if entry is None or (shown is not None and entry.name != shown):
            now_text = f"{entry.name}({code})" if entry else "용어사전에 없음"
            out.append(f"기관·지역 이름 '{name}': 검토에서 승인한 연결 {shown or ''}({code}) → 지금 {now_text}")
    return [mask_text(t) for t in out]


# ---------------------------------------------------------------- 검토 안의 승인


def column_facts(sheet: SheetResult) -> dict[str, dict[str, bool]]:
    """열마다 검사에 보이는 값: 글자(빈 칸·결측 표기 제외), 빈 칸(데이터 행), 숫자(데이터·합계 행)."""
    texts = set(text_value_columns(sheet))
    out = {}
    for j, name in enumerate(sheet.columns):
        cells = [(r["kind"], r["cells"][j] if j < len(r["cells"]) else None) for r in sheet.body]
        out[name] = {
            "text_values": name in texts,
            "blank_values": any(kind == "data" and c is None for kind, c in cells),
            "numeric_values": any(
                kind in ("data", "subtotal") and isinstance(c, int | float) and not isinstance(c, bool)
                for kind, c in cells
            ),
        }
    return out


def effect_free(sheet_basis: dict[str, list[Any]], column: str, facts: dict[str, Any], key: str | None) -> bool:
    """검토에서 이 열을 key로 확정해도 검사 입력이 그대로인지 — 그렇다고 확인될 때만 기존 검사 결과를 이어 쓴다.

    facts: 이 열의 column_facts (검토 항목에 저장). 검사는 확정·자동 적용된 열만 (열 번호·항목) 읽고, 수량 항목
    확정이면 글자 값을 계산용 값으로 바꾼다 (validation.SheetInput) — 그 입력이 그대로인 경우만 True.
    """
    entry = sheet_basis.get(column)
    if entry is None:
        return False
    applied = entry[1] if entry[0] in ("auto", "confirmed") and entry[1] else None
    if applied is None:  # 검사에 쓰지 않은 열
        if key is None:
            return True  # '해당 없음' — 검사 입력이 그대로
        if key in CHECKED_FIELDS:
            return False  # 기간·기관·지역·합산 제외·시점 값·합계·수량·금액 항목은 검사 조건에 쓰인다
        if facts.get("blank_values") or facts.get("numeric_values"):
            return False  # 빈 칸(공백 경고)·숫자(합계·전월·단위 비교)가 있으면 연결만으로 검사에 쓰인다
        # 같은 항목 열이 둘이 되면 전월·단위 비교에서 그 항목이 빠진다
        return not any(c != column and e[0] in ("auto", "confirmed") and e[1] == key for c, e in sheet_basis.items())
    if key == applied:  # 검사에 쓴 자동 매핑을 그대로 확정 — 달라질 수 있는 것은 수량 해석뿐
        return not (key in QUANTITY_FIELDS and facts.get("text_values"))
    return False  # 검사에 쓴 매핑과 다른 항목·'해당 없음'


def confirmed_entry(column: str, key: str | None) -> list[Any]:
    """검토에서 확정한 열의 기록 — 통합 때 다시 계산하는 확정 매핑과 같은 모양 (구분어는 열 이름에서)."""
    return ["confirmed", key, split_column_name(column).qualifier, "user"]
