"""통합 전 데이터 검증 엔진.

결과는 등급별로 분리된 목록으로만 돌려준다 (섞지 않는다).
- 차단(blocks): 합계 불일치, 필수 기관 누락, 기간 불일치 → 통합 불가, 검토 필요
- 경고(warnings): 전월 대비 이상치, 공백 값, 단위 추정 → 통합 진행하되 표시
- 개인정보(pii): 업로드 시 탐지 결과. 사용자가 확인하기 전에는 통합하지 않는다.
- 참고(notes): 검사를 할 수 없었던 이유 등. 상태에 영향 없음.

원본 파일과 파싱 결과는 읽기만 하고 바꾸지 않는다.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.glossary import Glossary, Resolution, period_end, prefer_for_field
from app.parsing import Cell, SheetResult, is_missing, is_subtotal_label

BlockCode = Literal["sum_mismatch", "required_entity_missing", "period_mismatch"]
WarningCode = Literal["mom_outlier", "blank_value", "unit_estimate"]
Status = Literal["blocked", "pii_confirmation_required", "passed_with_warnings", "passed"]

BLOCK_LABEL: dict[str, str] = {
    "sum_mismatch": "합계 불일치",
    "required_entity_missing": "필수 기관 누락",
    "period_mismatch": "기간 불일치",
}
WARNING_LABEL: dict[str, str] = {
    "mom_outlier": "전월 대비 이상치",
    "blank_value": "공백 값",
    "unit_estimate": "단위 추정",
}

# 합산하면 안 되는 항목 (비율·연월·코드·연락처)
NON_ADDITIVE_FIELDS = frozenset(
    {"execution_rate", "ratio", "change_rate", "year", "month", "base_date", "region_code", "org_code", "phone"}
)
_RATE_NAME = re.compile(r"(률|율|%|비율|비중|구성비|평균|지수)")
ENTITY_FIELDS = ("org_name", "sido", "sigungu", "region", "region_code")
ZERO_MARKERS = frozenset({"-", "－", "–", "—"})  # 통계표에서 '-'는 0(해당 없음)
DEFAULT_CHANGE_THRESHOLD = 0.5
UNIT_RATIO_THRESHOLD = 300  # 같은 항목 값 규모가 이 배수 이상 다르면 단위가 다를 가능성
MONEY_FIELDS = frozenset({"budget", "expenditure", "amount"})


# ---------------------------------------------------------------- 결과 타입


@dataclass
class BlockIssue:
    code: BlockCode
    message: str
    file_id: int | None = None
    file_name: str | None = None
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    expected: Any = None
    actual: Any = None

    @property
    def label(self) -> str:
        return BLOCK_LABEL[self.code]


@dataclass
class WarningIssue:
    code: WarningCode
    message: str
    file_id: int | None = None
    file_name: str | None = None
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    expected: Any = None
    actual: Any = None

    @property
    def label(self) -> str:
        return WARNING_LABEL[self.code]


@dataclass
class PiiStatus:
    file_id: int
    file_name: str
    counts: dict[str, int]
    items: list[dict[str, Any]]
    acknowledged: bool


@dataclass
class EntityNote:
    """용어사전 확인 대상 이름 (사전에 없음·모호·폐지). 차단·경고와 별도이며 상태에 영향 없음."""

    value: str
    field: str
    status: str  # unknown(사전에 없음) / ambiguous(여러 항목) / abolished(기준일에 유효하지 않음)
    note: str | None
    candidates: list[dict[str, Any]]
    files: list[str]
    count: int


@dataclass
class ValidationResult:
    status: Status
    blocks: list[BlockIssue]
    warnings: list[WarningIssue]
    pii: list[PiiStatus]
    notes: list[str]
    entities: list[EntityNote] = field(default_factory=list)


# ---------------------------------------------------------------- 입력 타입


@dataclass
class MappedColumn:
    index: int
    name: str
    field_key: str
    qualifier: str | None


@dataclass
class SheetInput:
    sheet: SheetResult
    columns: list[MappedColumn]  # 확정·자동 적용된 매핑만

    def by_field(self, key: str) -> list[MappedColumn]:
        return [c for c in self.columns if c.field_key == key]


@dataclass
class FileInput:
    file_id: int
    file_name: str
    sheets: list[SheetInput]
    pii_counts: dict[str, int] = field(default_factory=dict)
    pii_items: list[dict[str, Any]] = field(default_factory=list)
    pii_acknowledged: bool = False


@dataclass
class ValidationParams:
    expected_period: str | None = None  # 'YYYY' 또는 'YYYY-MM'
    required_entities: list[str] = field(default_factory=list)
    entity_field: str | None = None  # None이면 자동 (기관명 → 시군구 → 행정구역 → 시도)
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD


# ---------------------------------------------------------------- 공통 도구


def _norm(text: str) -> str:
    return re.sub(r"[\s·ㆍ]+", "", unicodedata.normalize("NFKC", text)).lower()


def _is_number(cell: Cell) -> bool:
    return isinstance(cell, int | float) and not isinstance(cell, bool)


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _decimals(value: float) -> int:
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _additive(sheet: SheetInput, index: int) -> bool:
    name = sheet.sheet.columns[index]
    mapped = next((c for c in sheet.columns if c.index == index), None)
    if mapped is not None and mapped.field_key in NON_ADDITIVE_FIELDS:
        return False
    return not _RATE_NAME.search(name)


def _data_rows(sheet: SheetResult) -> list[dict[str, Any]]:
    return [r for r in sheet.body if r["kind"] == "data"]


# ---------------------------------------------------------------- 차단: 합계 불일치

_SUBTOTAL_WORDS = re.compile(r"(소계|합계|총계|누계|계)$")


def _subtotal_group(cells: list[Cell]) -> tuple[int, str] | None:
    """소계 행이 묶는 그룹 (열 번호, 그룹 값). None이면 전체 합계(전국·합계·총계)."""
    label_idx = next((j for j, c in enumerate(cells) if is_subtotal_label(c)), None)
    for j, c in enumerate(cells):  # '서울특별시 | 계' 형태: 라벨이 아닌 다른 텍스트가 그룹 값
        if j != label_idx and isinstance(c, str) and not is_missing(c) and not is_subtotal_label(c):
            return j, c
    if label_idx is not None:  # '영업1팀 소계' 형태: 라벨에서 합계 단어를 뗀 나머지가 그룹 값
        rest = _SUBTOTAL_WORDS.sub("", str(cells[label_idx]).strip()).strip()
        if rest and rest != str(cells[label_idx]).strip():
            return label_idx, rest
    return None


def check_sums(file: FileInput, sheet: SheetInput, notes: list[str]) -> list[BlockIssue]:
    issues: list[BlockIssue] = []
    data = _data_rows(sheet.sheet)
    columns = sheet.sheet.columns
    subtotals = [r for r in sheet.sheet.body if r["kind"] == "subtotal"]
    groups = {id(r): _subtotal_group(r["cells"]) for r in subtotals}
    group_rows = [r for r in subtotals if groups[id(r)] is not None]
    for row in subtotals:
        cells: list[Cell] = row["cells"]
        group = groups[id(row)]
        if group is None:
            # 전체 합계 = 그룹 소계 행 + 어느 그룹에도 속하지 않은 데이터 행
            # (하위 행이 일부만 실린 '서울특별시 계' 같은 소계도 올바르게 반영된다)
            def grouped(r: dict[str, Any]) -> bool:
                return any(r["cells"][g[0]] == g[1] for g in (groups[id(x)] for x in group_rows) if g)

            children = group_rows + [r for r in data if not grouped(r)]
            label = next((str(c) for c in cells if is_subtotal_label(c)), "합계")
        else:
            col, value = group
            children = [r for r in data if r["cells"][col] == value]
            label = " ".join(str(c) for c in cells if isinstance(c, str) and not is_missing(c))
        if not children:
            notes.append(
                f"{file.file_name} / {sheet.sheet.name} {row['row']}행 '{label}': "
                "하위 데이터 행을 찾지 못해 합계 검사 생략"
            )
            continue
        for j, total in enumerate(cells):
            if not _is_number(total) or not _additive(sheet, j):
                continue
            parts = [r["cells"][j] for r in children]
            if any(isinstance(p, str) and is_missing(p) and p not in ZERO_MARKERS for p in parts):
                notes.append(
                    f"{file.file_name} / {sheet.sheet.name} {row['row']}행 '{columns[j]}': "
                    "하위 행에 비공개(x, … 등) 값이 있어 합계 검사 생략"
                )
                continue
            numbers = [p for p in parts if _is_number(p)]
            if not numbers:
                continue
            computed = math.fsum(numbers)
            # 표시 단위 반올림 허용: 하위 행 수 × 마지막 자리의 절반
            unit = 10 ** -max([_decimals(total), *(_decimals(n) for n in numbers)])
            tolerance = 0.5 * unit * len(numbers) + 1e-9 * abs(computed)
            if abs(computed - total) > tolerance:
                issues.append(
                    BlockIssue(
                        "sum_mismatch",
                        f"'{label}' 행의 '{columns[j]}' 값 {_fmt(total)}이(가) 하위 {len(numbers)}행 합계 "
                        f"{_fmt(computed)}와(과) 다릅니다 (차이 {_fmt(total - computed)}).",
                        file.file_id,
                        file.file_name,
                        sheet.sheet.name,
                        row["row"],
                        columns[j],
                        expected=computed,
                        actual=total,
                    )
                )
    return issues


# ---------------------------------------------------------------- 차단: 필수 기관 누락


def _pick_entity_field(files: list[FileInput], requested: str | None) -> str | None:
    if requested:
        return requested
    for key in ("org_name", "sigungu", "region", "sido"):
        if any(s.by_field(key) for f in files for s in f.sheets):
            return key
    return None


class EntityResolver:
    """기관·지역 값 → 비교 키. 용어사전이 있으면 항목 코드('org:1741000', 'region:11500'),
    없거나 해석되지 않으면 정규화한 문자열('text:행안부')을 쓴다."""

    def __init__(self, glossary: Glossary | None, period: str | None) -> None:
        self.glossary = glossary
        self.at = period_end(period)
        self._cache: dict[tuple[str, str], Resolution] = {}

    def resolve(self, name: str, field_key: str | None) -> Resolution | None:
        if self.glossary is None:
            return None
        prefer = prefer_for_field(field_key)
        cache_key = (name, prefer)
        if cache_key not in self._cache:
            self._cache[cache_key] = self.glossary.resolve(name, self.at, prefer)
        return self._cache[cache_key]

    def key(self, name: str, field_key: str) -> str:
        if field_key == "region_code" and name.isdigit():
            return f"region:{name}"
        resolution = self.resolve(name, field_key)
        if resolution is not None and resolution.key:
            return resolution.key
        return f"text:{_norm(name)}"


def _row_entity_names(sheet: SheetInput, cells: list[Cell]) -> list[tuple[str, str]]:
    """행의 기관·지역 값들. 시군구는 같은 행의 시도와 합쳐 '서울특별시 강서구'처럼 만든다 (동명 시군구 구분)."""
    sido = next((str(cells[c.index]) for c in sheet.by_field("sido") if isinstance(cells[c.index], str)), None)
    names = []
    for key in ENTITY_FIELDS:
        for c in sheet.by_field(key):
            value = cells[c.index]
            if value is None or (isinstance(value, str) and (is_missing(value) or is_subtotal_label(value))):
                continue
            text = str(value)
            if key == "sigungu" and sido:
                text = f"{sido} {text}"
            names.append((key, text))
    return names


def check_required(
    files: list[FileInput], params: ValidationParams, notes: list[str], resolver: EntityResolver
) -> list[BlockIssue]:
    required = [r.strip() for r in params.required_entities if r.strip()]
    if not required:
        notes.append("필수 기관 목록이 없어 누락 검사를 하지 않았습니다.")
        return []
    key = _pick_entity_field(files, params.entity_field)
    present_keys: set[str] = set()
    present_texts: set[str] = set()
    for f in files:
        for s in f.sheets:
            for row in s.sheet.body:
                if row["kind"] not in ("data", "subtotal"):
                    continue
                for field_key, name in _row_entity_names(s, row["cells"]):
                    present_keys.add(resolver.key(name, field_key))
                    present_texts.add(_norm(name))
    file_names = [_norm(f.file_name) for f in files]

    issues = []
    for name in required:
        n = _norm(name)
        res = resolver.resolve(name, key)
        ref_key = res.key if res is not None else None
        canonical = _norm(res.ref.name) if res is not None and res.ref is not None else None
        if (
            (ref_key is not None and ref_key in present_keys)
            or n in present_texts
            or any(n in fn or (canonical is not None and canonical in fn) for fn in file_names)  # 기관별 파일명
        ):
            continue
        where = "기관·지역 열이 매핑되지 않아 파일명으로만 확인" if key is None else "데이터와 파일명 어디에도 없음"
        detail = ""
        if res is not None:
            if res.status == "resolved" and res.ref is not None:
                detail = f" — 용어사전: {res.ref.name}({res.ref.code})"
            elif res.candidates:
                detail = " — 용어사전 후보: " + ", ".join(c.name for c in res.candidates[:3])
            if res.note:
                detail += f", {res.note}"
        issues.append(
            BlockIssue("required_entity_missing", f"필수 기관 '{name}' 누락 ({where}){detail}.", expected=name)
        )
    return issues


def collect_entity_notes(files: list[FileInput], resolver: EntityResolver) -> list[EntityNote]:
    if resolver.glossary is None:
        return []
    found: dict[tuple[str, str], EntityNote] = {}
    for f in files:
        for s in f.sheets:
            for row in _data_rows(s.sheet):
                for field_key, name in _row_entity_names(s, row["cells"]):
                    if field_key == "region_code":
                        continue
                    res = resolver.resolve(name, field_key)
                    if res is None or (res.status == "resolved" and not res.note):
                        continue
                    note = found.get((field_key, name))
                    if note is None:
                        status = res.status if res.status != "resolved" else "abolished"
                        text = res.note
                        if status == "abolished" and res.ref is not None and resolver.glossary is not None:
                            # 폐지된 이름은 자기 자신 대신 승계·유사 기관을 후보로 (개편 후 기관 연결용)
                            refs = resolver.glossary.replacements(res.ref, resolver.at)
                            text = f"{res.ref.name}({res.ref.code}): {res.note} — 개편 후 항목을 연결하세요"
                        else:
                            refs = res.candidates or ([res.ref] if res.ref else [])
                        candidates = [
                            {"kind": c.kind, "code": c.code, "name": c.name, "type": c.type_label} for c in refs
                        ]
                        note = EntityNote(name, field_key, status, text, candidates, [], 0)
                        found[(field_key, name)] = note
                    note.count += 1
                    if f.file_name not in note.files:
                        note.files.append(f.file_name)
    return list(found.values())


# ---------------------------------------------------------------- 차단: 기간 불일치

_TEXT_YM = re.compile(r"((?:19|20)\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/]|$|\s|\))")
_TEXT_Y = re.compile(r"((?:19|20)\d{2})\s*년")
_DATE = re.compile(r"^((?:19|20)\d{2})\s*[.\-/]\s*(\d{1,2})")


@dataclass
class PeriodEvidence:
    period: str  # YYYY 또는 YYYY-MM
    source: str  # 사람이 읽을 출처
    sheet: str | None = None
    row: int | None = None
    column: str | None = None


def _text_periods(text: str) -> list[str]:
    out = [f"{y}-{int(m):02d}" for y, m in _TEXT_YM.findall(text) if 1 <= int(m) <= 12]
    if not out:
        out = list(_TEXT_Y.findall(text))
    return out


def _year_of(value: Cell) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1900 <= value <= 2100:
        return str(value)
    if isinstance(value, str):
        m = re.match(r"^((?:19|20)\d{2})\s*년?$", value.strip())
        return m.group(1) if m else None
    return None


def _month_of(value: Cell) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 12:
        return value
    if isinstance(value, str):
        m = re.match(r"^(\d{1,2})\s*월?$", value.strip())
        if m and 1 <= int(m.group(1)) <= 12:
            return int(m.group(1))
    return None


def collect_periods(file: FileInput) -> list[PeriodEvidence]:
    evidence: list[PeriodEvidence] = []
    for p in _text_periods(file.file_name):
        evidence.append(PeriodEvidence(p, "파일명"))
    for s in file.sheets:
        sheet = s.sheet
        for p in _text_periods(sheet.name):
            evidence.append(PeriodEvidence(p, "시트명", sheet.name))
        for text in sheet.title_texts:
            for p in _text_periods(text):
                evidence.append(PeriodEvidence(p, f"제목 '{text[:30]}'", sheet.name))
        years, months, dates = s.by_field("year"), s.by_field("month"), s.by_field("base_date")
        for row in _data_rows(sheet):
            cells = row["cells"]
            year = next((y for c in years if (y := _year_of(cells[c.index]))), None)
            month = next((m for c in months if (m := _month_of(cells[c.index])) is not None), None)
            if year and month:
                col = months[0].name
                evidence.append(PeriodEvidence(f"{year}-{month:02d}", "연·월 열", sheet.name, row["row"], col))
            elif year:
                evidence.append(PeriodEvidence(year, "연도 열", sheet.name, row["row"], years[0].name))
            for c in dates:
                v = cells[c.index]
                m = _DATE.match(str(v)) if isinstance(v, str) else None
                if m and 1 <= int(m.group(2)) <= 12:
                    evidence.append(
                        PeriodEvidence(
                            f"{m.group(1)}-{int(m.group(2)):02d}", "기준일 열", sheet.name, row["row"], c.name
                        )
                    )
    return evidence


def _period_matches(found: str, expected: str) -> bool:
    if len(found) == 4 or len(expected) == 4:
        return found[:4] == expected[:4]
    return found == expected


def check_periods(files: list[FileInput], params: ValidationParams, notes: list[str]) -> list[BlockIssue]:
    issues: list[BlockIssue] = []
    dominant: dict[int, str] = {}
    for f in files:
        evidence = collect_periods(f)
        if not evidence:
            notes.append(f"{f.file_name}: 기간 정보를 찾지 못해 기간 검사를 하지 않았습니다.")
            continue
        if params.expected_period:
            # 같은 값은 첫 위치만 보고한다 (행마다 반복 보고하지 않음).
            reported: set[tuple[str, str, str | None]] = set()
            for e in evidence:
                if _period_matches(e.period, params.expected_period):
                    continue
                key = (e.period, e.source, e.column)
                if key in reported:
                    continue
                reported.add(key)
                issues.append(
                    BlockIssue(
                        "period_mismatch",
                        f"{e.source}의 기간 {e.period}이(가) 기준 기간 {params.expected_period}과(와) 다릅니다.",
                        f.file_id,
                        f.file_name,
                        e.sheet,
                        e.row,
                        e.column,
                        expected=params.expected_period,
                        actual=e.period,
                    )
                )
        else:
            months = [e.period for e in evidence if len(e.period) == 7]
            pool = months or [e.period for e in evidence]
            dominant[f.file_id] = statistics.mode(pool)
    if not params.expected_period and len(set(dominant.values())) > 1:
        detail = ", ".join(f"{f.file_name}={dominant[f.file_id]}" for f in files if f.file_id in dominant)
        issues.append(
            BlockIssue(
                "period_mismatch", f"기준 기간이 지정되지 않았고 파일 간 기간이 다릅니다: {detail}", actual=detail
            )
        )
    return issues


# ---------------------------------------------------------------- 경고: 전월 대비 이상치


def _entity_key(sheet: SheetInput, cells: list[Cell], resolver: EntityResolver) -> tuple[str, ...] | None:
    keys = sorted({resolver.key(name, field_key) for field_key, name in _row_entity_names(sheet, cells)})
    return tuple(keys) or None


def _numeric_fields(sheet: SheetInput) -> dict[str, int]:
    """합산 가능한 숫자 항목 → 열 번호. 같은 항목이 여러 열(예: 연도별)이면 비교 대상에서 뺀다."""
    counts: dict[str, list[int]] = defaultdict(list)
    for c in sheet.columns:
        if c.field_key not in NON_ADDITIVE_FIELDS and c.field_key not in ENTITY_FIELDS and _additive(sheet, c.index):
            counts[c.field_key].append(c.index)
    return {k: v[0] for k, v in counts.items() if len(v) == 1}


def check_mom(
    files: list[FileInput],
    baseline: list[FileInput],
    params: ValidationParams,
    notes: list[str],
    resolver: EntityResolver,
) -> list[WarningIssue]:
    if not baseline:
        notes.append("전월 자료가 지정되지 않아 전월 대비 이상치 검사를 하지 않았습니다.")
        return []
    previous: dict[tuple[str, ...], dict[str, float]] = {}
    for f in baseline:
        for s in f.sheets:
            fields = _numeric_fields(s)
            for row in _data_rows(s.sheet):
                key = _entity_key(s, row["cells"], resolver)
                if key is None:
                    continue
                values = {k: row["cells"][i] for k, i in fields.items() if _is_number(row["cells"][i])}
                previous.setdefault(key, {}).update(values)

    issues: list[WarningIssue] = []
    matched = 0
    for f in files:
        for s in f.sheets:
            fields = _numeric_fields(s)
            for row in _data_rows(s.sheet):
                key = _entity_key(s, row["cells"], resolver)
                if key is None or key not in previous:
                    continue
                matched += 1
                for field_key, i in fields.items():
                    cur, prev = row["cells"][i], previous[key].get(field_key)
                    if not _is_number(cur) or prev is None:
                        continue
                    if prev == 0:
                        if cur == 0:
                            continue
                        change_text = "전월 0"
                    else:
                        change = (cur - prev) / abs(prev)
                        if abs(change) <= params.change_threshold:
                            continue
                        change_text = f"{change:+.0%}"
                    entity = ", ".join(dict.fromkeys(name for _, name in _row_entity_names(s, row["cells"])))
                    issues.append(
                        WarningIssue(
                            "mom_outlier",
                            f"{entity} '{s.sheet.columns[i]}' 전월 {_fmt(prev)} → {_fmt(cur)} ({change_text}, "
                            f"기준 ±{params.change_threshold:.0%})",
                            f.file_id,
                            f.file_name,
                            s.sheet.name,
                            row["row"],
                            s.sheet.columns[i],
                            expected=prev,
                            actual=cur,
                        )
                    )
    if matched == 0:
        notes.append("전월 자료와 같은 기관·지역 행을 찾지 못해 전월 비교를 하지 못했습니다.")
    return issues


# ---------------------------------------------------------------- 경고: 공백 값


def check_blanks(file: FileInput, sheet: SheetInput) -> list[WarningIssue]:
    issues = []
    data = _data_rows(sheet.sheet)
    for c in sheet.columns:
        rows = [r["row"] for r in data if r["cells"][c.index] is None]
        if not rows:
            continue
        shown = ", ".join(str(r) for r in rows[:5]) + (" …" if len(rows) > 5 else "")
        issues.append(
            WarningIssue(
                "blank_value",
                f"'{c.name}' 열에 빈 값 {len(rows)}개 (행 {shown})",
                file.file_id,
                file.file_name,
                sheet.sheet.name,
                rows[0],
                c.name,
                actual=len(rows),
            )
        )
    return issues


# ---------------------------------------------------------------- 경고: 단위 추정

UNIT_SCALE: dict[str, tuple[str, float]] = {
    "원": ("원", 1),
    "천원": ("원", 1e3),
    "만원": ("원", 1e4),
    "백만원": ("원", 1e6),
    "천만원": ("원", 1e7),
    "억원": ("원", 1e8),
    "십억원": ("원", 1e9),
    "조원": ("원", 1e12),
    "명": ("명", 1),
    "천명": ("명", 1e3),
    "만명": ("명", 1e4),
    "건": ("건", 1),
    "천건": ("건", 1e3),
    "가구": ("가구", 1),
    "세대": ("세대", 1),
    "개": ("개", 1),
}
_PAREN = re.compile(r"[(\[]([^)\]]+)[)\]]")
_UNIT_DECL = re.compile(r"단위\s*[:：]\s*([^)\]]+)")


def _parse_unit(text: str) -> str | None:
    unit = re.sub(r"\s+", "", text)
    return unit if unit in UNIT_SCALE else None


def _sheet_unit(sheet: SheetResult) -> str | None:
    for text in [*sheet.title_texts, *sheet.note_texts]:
        m = _UNIT_DECL.search(text)
        if m:
            units = [u for u in (_parse_unit(p) for p in re.split(r"[,，/]", m.group(1))) if u]
            if len(units) == 1:  # '단위: 명, %'처럼 여러 개면 열마다 다르므로 쓰지 않는다
                return units[0]
    return None


@dataclass
class _UnitSample:
    file: FileInput
    sheet: SheetInput
    column: MappedColumn
    unit: str | None
    declared_in: str | None
    median: float


def check_units(files: list[FileInput]) -> list[WarningIssue]:
    by_field: dict[str, list[_UnitSample]] = defaultdict(list)
    for f in files:
        for s in f.sheets:
            sheet_unit = _sheet_unit(s.sheet)
            for field_key, i in _numeric_fields(s).items():
                col = next(c for c in s.columns if c.index == i)
                unit, declared = None, None
                for m in _PAREN.finditer(col.name):
                    unit = _parse_unit(m.group(1)) or unit
                if unit:
                    declared = "열 이름"
                elif sheet_unit:
                    unit, declared = sheet_unit, "표 단위 표기"
                values = [abs(r["cells"][i]) for r in _data_rows(s.sheet) if _is_number(r["cells"][i])]
                if values:
                    by_field[field_key].append(_UnitSample(f, s, col, unit, declared, statistics.median(values)))

    issues: list[WarningIssue] = []
    for samples in by_field.values():
        for a_idx, a in enumerate(samples):
            for b in samples[a_idx + 1 :]:
                message = _compare_units(a, b)
                if message:
                    issues.append(
                        WarningIssue(
                            "unit_estimate",
                            message,
                            b.file.file_id,
                            b.file.file_name,
                            b.sheet.sheet.name,
                            None,
                            b.column.name,
                            expected=a.unit,
                            actual=b.unit,
                        )
                    )
        for s in samples:  # 금액 항목인데 단위 표기가 전혀 없는 경우
            if s.unit is None and s.column.field_key in MONEY_FIELDS:
                issues.append(
                    WarningIssue(
                        "unit_estimate",
                        f"'{s.column.name}'에 단위 표기가 없습니다 (값 중앙값 {_fmt(s.median)}). "
                        "원·천원·백만원 중 확인이 필요합니다.",
                        s.file.file_id,
                        s.file.file_name,
                        s.sheet.sheet.name,
                        None,
                        s.column.name,
                    )
                )
    return issues


def _describe(s: _UnitSample) -> str:
    unit = f"{s.unit}, {s.declared_in}" if s.unit else "단위 표기 없음"
    return f"{s.file.file_name}/{s.sheet.sheet.name} '{s.column.name}'({unit}, 중앙값 {_fmt(s.median)})"


def _compare_units(a: _UnitSample, b: _UnitSample) -> str | None:
    if a.unit and b.unit:
        base_a, scale_a = UNIT_SCALE[a.unit]
        base_b, scale_b = UNIT_SCALE[b.unit]
        if base_a == base_b and scale_a != scale_b:
            return f"같은 항목의 단위가 다릅니다: {_describe(a)} vs {_describe(b)}"
        return None
    if not a.median or not b.median:
        return None
    ratio = max(a.median, b.median) / min(a.median, b.median)
    if ratio < UNIT_RATIO_THRESHOLD:
        return None
    power = round(math.log10(ratio))
    return f"같은 항목의 값 규모가 약 {10**power:,}배 다릅니다 — 단위가 다를 가능성: {_describe(a)} vs {_describe(b)}"


# ---------------------------------------------------------------- 실행


def validate(
    files: list[FileInput],
    params: ValidationParams,
    baseline: list[FileInput] | None = None,
    glossary: Glossary | None = None,
) -> ValidationResult:
    """glossary가 주어지면 기관·지역 이름을 용어사전 항목 코드로 비교한다 (기준일 = 기준 기간 말일)."""
    resolver = EntityResolver(glossary, params.expected_period)
    notes: list[str] = []
    blocks: list[BlockIssue] = []
    warnings: list[WarningIssue] = []

    for f in files:
        for s in f.sheets:
            blocks.extend(check_sums(f, s, notes))
    blocks.extend(check_required(files, params, notes, resolver))
    blocks.extend(check_periods(files, params, notes))

    warnings.extend(check_mom(files, baseline or [], params, notes, resolver))
    for f in files:
        for s in f.sheets:
            warnings.extend(check_blanks(f, s))
    warnings.extend(check_units(files))

    pii = [
        PiiStatus(f.file_id, f.file_name, f.pii_counts, f.pii_items, f.pii_acknowledged)
        for f in files
        if any(f.pii_counts.values())
    ]

    status: Status
    if blocks:
        status = "blocked"
    elif any(not p.acknowledged for p in pii):
        status = "pii_confirmation_required"
    elif warnings:
        status = "passed_with_warnings"
    else:
        status = "passed"
    entities = collect_entity_notes(files, resolver)
    return ValidationResult(status, blocks, warnings, pii, notes, entities)
