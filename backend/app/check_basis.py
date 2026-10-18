"""검토의 검사 기준 — 검사에 실제 적용한 열 매핑을 남기고, 완료·통합 전에 지금 매핑과 비교한다.

검토 결과(차단·경고·확인 항목)는 그 검사에 적용한 매핑과 값 해석으로 계산한 것이다. 다른 매핑으로 결과를 만들면
그 검사 결과를 쓸 수 없으므로, 다시 검사하기 전에는 완료·통합하지 않는다.

기록 (검토 params의 'check_basis' — 화면의 조건 표시에는 내보내지 않고, 다시 검사할 때 새로 만든다):
- files: 파일(검토 대상 + 전월 비교 자료) → 시트 → 열 → [상태, 항목, 구분어, 판정 방법] — 검사 때의 매핑 결과.
  상태가 confirmed면 사용자 확정, auto면 자동 적용(값은 원문 그대로, 수량 해석은 확정만), 그 밖은 검사에 쓰지 않음.
- lookups: 사용자 확정이 없는 열의 자동 적용 근거 — 학습 별칭·전사 표준 사전이 그 열 이름에 주는 항목.
  표준 사전과 정확히 일치한 열은 사전이 먼저라 넣지 않는다 (mapping.MappingEngine._exact_match 순서).
- baseline: 전월 비교에 쓴 파일, dictionary: 표준 사전 파일 지문.

비교:
- 조회·완료 (모델 호출·파일 읽기 없음): 사용자 확정, 자동 적용 근거, 표준 사전 지문. 개인정보 때문에 제외한 파일은
  통합하지 않으므로 보지 않고, 전월 비교 자료는 검사에 썼으므로 본다.
- 통합: 위 비교에 더해, 결과를 만들려고 다시 계산한 매핑(기존 동작 — LLM 없이)의 적용 상태가 기록과 같은지 본다.
  임베딩 유사도처럼 조회 때는 알 수 없는 자동 적용 변화도 결과 파일을 만들기 전에 여기서 막는다.
- 기록이 없는 이전 버전 검토는 같은지 확인할 수 없으므로 같다고 보지 않는다 (다시 검사 필요).

검토 안의 매핑 승인은 검사 입력이 그대로임을 확인할 수 있을 때만 기록에 더해 기존 검사 결과를 이어 쓴다 (effect_free).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from app.mapping import ColumnMapping, dictionary_digest, load_dictionary, split_column_name
from app.models import SourceFile
from app.parsing import SheetResult
from app.pii import mask_text
from app.quantity import QUANTITY_FIELDS, text_value_columns
from app.validation import CHECKED_FIELDS

CHECK_BASIS_KEY = "check_basis"
BASIS_VERSION = 2
# 검토 params 안의 내부 기록 ('verified_decisions'는 이전 보완의 확정만 담은 기록 — 읽지 않고 버린다)
INTERNAL_PARAMS = (CHECK_BASIS_KEY, "verified_decisions")
LEGACY_REASON = (
    "이 검토는 검사 기준(검사에 적용한 열 매핑)을 기록하기 전 버전에서 만들어 지금 매핑과 같은지 확인할 수 없습니다"
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
