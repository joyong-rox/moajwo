"""확정된 열로 하는 데이터 분석 (1차): 합계·평균·최솟값·최댓값·유효 숫자 개수, 그룹별 집계, 필터, 구간 분류.

모델(임베딩·LLM)을 부르지 않는다. 사용자가 확정한 매핑(mapping_decisions)과 마스킹한 파싱 결과(file_view)만 읽고,
매핑·학습 별칭·승격 근거·열 매핑 분석 기록은 바꾸지 않는다.

숫자가 맞아도 의미가 틀린 결과를 막는 규칙 (판정은 서버가 하고, 화면은 결과와 사유를 보여 준다):
- 열: 화면 위치·열 이름이 아니라 파일·시트·열 식별자와 **사용자가 확정한** 표준 항목으로 고른다
  ('자동 제안'은 쓰지 않음).
  같은 항목이라도 열 이름의 연도·구분 단어(2단 머리글의 각 부분, 괄호 안 사전 단어)가 다르면 따로 계산한다.
- 단위: 확인된 같은 단위끼리만 계산한다 (자동 환산 없음). 표에 단위가 없으면 사용자가 그 시트의 단위를 확인해
  적어야 쓴다.
- 기간: 행(연도·월·기준일 열) 또는 시트(파일명·시트명·제목)에서 기간을 확인하고, 기간이 여럿이면 기간별로 나눈다.
  시점 값(인구·등록대수 등)을 월별로 더해 연간 값으로 만들지 않는다. 기간을 못 찾으면 사용자가 적어야 쓴다.
- 집계 대상: 지역·기관·구분 값으로 한 행이 무엇인지 확인한다. 합계·소계 행, '계·합계·전체' 행, 같은 분석의 하위 지역과
  함께 있는 상위 지역 행은 중복을 막으려고 빼고 사유를 남긴다.
- 중복: 같은 내용의 파일이나 같은 대상(기간·지역·기관·구분)이 다른 자료에도 있으면 실행하지 않고 사용자가 고르게 한다.
- 결측: 빈칸·비공개 표기·숫자가 아닌 값·가린 개인정보는 0으로 바꾸지 않고 사유와 함께 뺀다 (뺀 값이 있으면 '부분 집계').
- 평균 = 유효한 숫자의 합 ÷ 그 개수 (파일별 평균을 다시 평균하지 않음). 유효한 숫자가 없으면 '계산할 값 없음'.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.admin_codes import infer_region_codes
from app.auth import CurrentUser
from app.decisions import load_decisions
from app.files import column_id, get_file_for
from app.glossary import parse_date_value
from app.mapping import TOTAL_WORDS, load_dictionary, normalize, split_column_name
from app.mapping_review import SheetView, declared_units, file_view, sheet_id
from app.parsing import is_missing
from app.pii import mask_text
from app.validation import STOCK_FIELDS, _month_of, _text_periods, _year_of

POLICY = "analysis-policy-1"
MAX_TARGETS = 20  # 한 번에 고를 수 있는 파일·시트
MAX_ROWS = 300_000  # 고른 시트의 데이터 행 합계 상한
MAX_GROUPS = 5_000
MAX_FILTER_VALUES = 500  # 필터 선택지로 보여 주는 값 (차원마다)
KEEP_ROWS = 20_000  # 저장·표시하는 근거 행·제외 행 목록의 상한 (계산은 전체 행으로 한다)

# 1차 범위: 더해서 의미가 있는 수량 항목. 비율·증감률·집행률은 합계·평균이 맞지 않아 후속 기능으로 둔다.
MEASURE_FIELDS = frozenset(
    {
        "population",
        "population_male",
        "population_female",
        "households",
        "headcount",
        "budget",
        "expenditure",
        "amount",
        "count",
        "registration_count",
        "vehicles_total",
        "passenger_car",
        "van",
        "truck",
        "special_vehicle",
        "motorcycle",
        "eco_friendly_car",
        "electric_car",
        "hydrogen_car",
        "hybrid_car",
    }
)
REGION_FIELDS = ("sido", "sigungu", "region", "region_code")
IDENTITY_FIELDS = (*REGION_FIELDS, "org_name", "org_code", "department", "vehicle_type", "vehicle_use", "fuel_type")
PERIOD_FIELDS = ("year", "month", "base_date")
GROUP_FIELDS = (*IDENTITY_FIELDS, "period")
AGGREGATES = ("sum", "mean", "min", "max", "count")
AGG_LABEL = {"sum": "합계", "mean": "평균", "min": "최솟값", "max": "최댓값", "count": "유효 숫자 개수"}
NO_VALUE = "계산할 값 없음"
UNCLASSIFIED = "미분류"
TOTAL_LIKE = frozenset({*TOTAL_WORDS, "총합", "전체합계"})
NATION = frozenset({"전국", "전국계"})
# 숫자 칸에 적힌 비공개·해당 없음 낱말 (파서의 결측 표기 '-'·'x'·'…'와 같은 사유로 뺀다)
NOT_DISCLOSED = frozenset({"비공개", "미공개", "해당없음", "자료없음", "집계중", "미집계", "없음"})
# 열 이름 괄호 안의 단위 ('인구(명)'). 사전 단어가 아닌 괄호만 단위로 본다
_COLUMN_UNIT = re.compile(r"\(\s*((?:천|만|백만|억|조)?(?:명|대|원|건|개|개소|곳|가구|세대|회|톤|일)|%|㎡|km|ha)\s*\)")

# 제외 사유: (설명, 자료 문제 여부). 자료 문제로 뺀 값이 있으면 그 결과는 '부분 집계'
REASONS: dict[str, tuple[str, bool]] = {
    "blank": ("빈칸", True),
    "missing_marker": ("비공개·해당 없음 표기 ('-', 'x', '…' 등)", True),
    "not_number": ("숫자가 아닌 값 (글자로 저장된 숫자 포함)", True),
    "pii": ("개인정보 형식이라 가린 값", True),
    "period_unknown": ("기간을 확인할 수 없는 행", True),
    "no_identity": ("지역·기관·구분 값이 비어 무엇인지 알 수 없는 행", True),
    "subtotal": ("합계·소계 행 (상세 행과 중복 방지)", False),
    "total_like": ("'계·합계·전체'로 적힌 행 (확인 필요 — 중복 방지로 제외)", False),
    "upper_region": ("같은 분석의 하위 지역과 함께 있는 상위 지역 행 (중복 방지)", False),
    "filtered": ("고른 지역·기관·기간 조건 밖", False),
    "value_range": ("숫자 범위 조건 밖", False),
}


@lru_cache(maxsize=1)
def field_labels() -> dict[str, str]:
    return {f.key: f.label for f in load_dictionary()}


@lru_cache(maxsize=1)
def _dictionary_words() -> frozenset[str]:
    return frozenset(normalize(t) for f in load_dictionary() for t in (f.label, *f.aliases))


def label_of(key: str) -> str:
    return "기간" if key == "period" else field_labels().get(key, key)


# ---------------------------------------------------------------- 요청


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int
    sheet_id: str | None = Field(default=None, pattern=r"^s\d{1,3}$")  # 없으면 이 항목이 확정된 시트 모두
    column_id: str | None = Field(default=None, pattern=r"^c\d{1,4}$")  # 같은 구분의 열이 여러 개일 때만
    unit: str | None = Field(default=None, min_length=1, max_length=20)  # 표에 단위가 없을 때 사용자가 확인한 단위
    period: str | None = Field(default=None, pattern=r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$")  # 기간을 못 찾을 때만


class BinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=40)
    min: float | None = None
    max: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = False


class BinsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: Literal["sum", "mean", "min", "max", "count"]
    ranges: list[BinIn] = Field(min_length=1, max_length=20)


class FiltersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dims: dict[str, list[str]] = Field(default_factory=dict)  # 차원 항목 → 고른 값 (비어 있으면 거르지 않음)
    period_from: str | None = Field(default=None, pattern=r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$")
    period_to: str | None = Field(default=None, pattern=r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$")
    value_min: float | None = None
    value_max: float | None = None


class AnalysisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(max_length=64)
    variant: str | None = Field(default=None, max_length=200)  # 열 이름의 구분 ('' = 구분 없음, None = 하나뿐이면 자동)
    unit: str | None = Field(default=None, max_length=20)  # 단위가 다른 자료가 섞였을 때 계산할 단위
    targets: list[TargetIn] = Field(min_length=1, max_length=MAX_TARGETS)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    filters: FiltersIn = Field(default_factory=FiltersIn)
    aggregates: list[Literal["sum", "mean", "min", "max", "count"]] = Field(default_factory=lambda: list(AGGREGATES))
    bins: BinsIn | None = None


class RunIn(AnalysisIn):
    expected_fingerprint: str = Field(min_length=16, max_length=64)


def _bad(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def bin_label(b: BinIn) -> str:
    if b.label:
        return b.label
    lo = None if b.min is None else f"{_num(b.min)} {'이상' if b.min_inclusive else '초과'}"
    hi = None if b.max is None else f"{_num(b.max)} {'이하' if b.max_inclusive else '미만'}"
    return " ".join(x for x in (lo, hi) if x) or "전체"


def _num(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,}"


def _contains(b: BinIn, x: float) -> bool:
    if b.min is not None and (x < b.min or (x == b.min and not b.min_inclusive)):
        return False
    return not (b.max is not None and (x > b.max or (x == b.max and not b.max_inclusive)))


def validate_bins(bins: BinsIn, aggregates: list[str]) -> None:
    if bins.on not in aggregates:
        raise _bad(f"구간 분류에 쓸 결과 열({AGG_LABEL[bins.on]})을 계산 방식에서 고르세요.")
    for b in bins.ranges:
        if b.min is None and b.max is None:
            raise _bad("구간마다 아래 경계나 위 경계 중 하나는 적어야 합니다.")
        if (
            b.min is not None
            and b.max is not None
            and (b.min > b.max or (b.min == b.max and not (b.min_inclusive and b.max_inclusive)))
        ):
            raise _bad(f"구간 '{bin_label(b)}'의 아래 경계가 위 경계보다 크거나 같습니다.")
    for i, a in enumerate(bins.ranges):
        for b in bins.ranges[i + 1 :]:
            if _overlap(a, b):
                raise _bad(
                    f"구간 '{bin_label(a)}'과(와) '{bin_label(b)}'이(가) 겹칩니다. 한 값은 한 구간에만 들어가야 합니다."
                )


def _overlap(a: BinIn, b: BinIn) -> bool:
    """두 구간에 함께 들어가는 값이 있는가 (경계 포함 여부까지)."""
    a_lo, b_lo = (-math.inf if a.min is None else a.min), (-math.inf if b.min is None else b.min)
    a_hi, b_hi = (math.inf if a.max is None else a.max), (math.inf if b.max is None else b.max)
    if a_lo != b_lo:
        lo, lo_in = (a_lo, a.min_inclusive) if a_lo > b_lo else (b_lo, b.min_inclusive)
    else:
        lo, lo_in = a_lo, a.min_inclusive and b.min_inclusive
    if a_hi != b_hi:
        hi, hi_in = (a_hi, a.max_inclusive) if a_hi < b_hi else (b_hi, b.max_inclusive)
    else:
        hi, hi_in = a_hi, a.max_inclusive and b.max_inclusive
    return lo < hi or (lo == hi and lo_in and hi_in)


def validate_request(req: AnalysisIn) -> None:
    if req.field_key not in MEASURE_FIELDS:
        raise _bad("분석할 수 있는 항목이 아닙니다 (1차는 더해서 의미가 있는 수량 항목만 — 비율·증감률은 후속 기능).")
    if any(k not in GROUP_FIELDS for k in req.group_by):
        raise _bad(
            "그룹 기준으로 쓸 수 없는 항목입니다: " + ", ".join(k for k in req.group_by if k not in GROUP_FIELDS)
        )
    if len(set(req.group_by)) != len(req.group_by):
        raise _bad("같은 그룹 기준을 두 번 골랐습니다.")
    if not req.aggregates or len(set(req.aggregates)) != len(req.aggregates):
        raise _bad("계산 방식을 하나 이상, 겹치지 않게 고르세요.")
    for key, values in req.filters.dims.items():
        if key not in IDENTITY_FIELDS:
            raise _bad(f"필터에 쓸 수 없는 항목입니다: {key}")
        if len(values) > MAX_FILTER_VALUES:
            raise _bad(f"필터 값은 {MAX_FILTER_VALUES}개까지 고를 수 있습니다.")
    f = req.filters
    if f.period_from and f.period_to and f.period_from > f.period_to:
        raise _bad("기간 범위의 시작이 끝보다 늦습니다.")
    if f.value_min is not None and f.value_max is not None and f.value_min > f.value_max:
        raise _bad("숫자 범위의 최솟값이 최댓값보다 큽니다.")
    if req.bins is not None:
        validate_bins(req.bins, list(req.aggregates))


# ---------------------------------------------------------------- 값 해석


def compact(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return "".join(text.split())


def dim_value(value: object) -> str:
    """차원 값 (지역·기관 등)을 비교·표시용 글자로. 마스킹한 보기의 값이라 개인정보는 이미 가려져 있다."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime | date):
        return value.isoformat()[:10]
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def classify_value(value: object) -> tuple[float | None, str | None, str | None]:
    """측정 값 → (숫자, 제외 사유, 숫자가 아닐 때 보여 줄 글자). 0은 유효한 숫자다."""
    if isinstance(value, bool):
        return None, "not_number", str(value)
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None, "not_number", str(value)
        return float(value), None, None
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "blank", None
    text = str(value).strip()
    if is_missing(text) or compact(text) in NOT_DISCLOSED:
        return None, "missing_marker", text[:40]
    if "*" in text:  # 파싱 보기에서 개인정보 형식이라 가린 값
        return None, "pii", text[:40]
    return None, "not_number", mask_text(text[:40])


def column_unit(column: str) -> str | None:
    found = [m.group(1) for m in _COLUMN_UNIT.finditer(unicodedata.normalize("NFKC", column))]
    found = [u for u in found if normalize(u) not in _dictionary_words()]
    return found[0] if len(set(found)) == 1 else None


def variant_of(column: str) -> str:
    """같은 표준 항목이라도 따로 계산할 구분. 열 이름의 연도, 2단 머리글 'A_B'의 모든 부분(보수적), 괄호 안의 사전 단어.

    '인구수'와 '주민등록인구'는 구분이 없어 함께 계산할 수 있고,
    '인구_남'·'인구_여'·'승용(관용)'·'2024년 인구'는 따로다.
    """
    name = split_column_name(column)
    words: list[str] = []
    if name.qualifier:
        words.append(name.qualifier)
    if name.parts:
        words.extend(p.strip() for p in name.raw_parts)
    words.extend(b for b in name.bracketed if normalize(b) in _dictionary_words())
    return mask_text(" · ".join(dict.fromkeys(w for w in words if w)))


def _period_from_cell(value: object) -> str | None:
    if isinstance(value, datetime | date):
        return f"{value.year}-{value.month:02d}"
    if isinstance(value, str):
        parsed = parse_date_value(value)
        return parsed[1] if parsed else None
    if isinstance(value, int) and not isinstance(value, bool):
        return _year_of(value)
    return None


def sheet_periods(file_name: str, sheet: SheetView) -> list[tuple[str, str]]:
    """파일명·시트명·제목에 적힌 기간 → [(기간, 출처)]. 서로 다른 기간이 여럿이면 호출하는 쪽이 판단하지 않고 멈춘다."""
    found: list[tuple[str, str]] = [(p, "파일명") for p in _text_periods(file_name)]
    found += [(p, "시트명") for p in _text_periods(sheet.name)]
    for text in sheet.title_texts:
        found += [(p, "제목") for p in _text_periods(text)]
    return found


def region_level(dims: dict[str, str]) -> tuple[str, str | None] | None:
    """지역 단위 → ('nation'|'sido'|'sigungu', 상위 시도 코드). 정식 명칭을 못 찾으면 None (판단하지 않음)."""
    sido, sigungu, region = dims.get("sido", ""), dims.get("sigungu", ""), dims.get("region", "")
    if "sigungu" in dims:
        parent = infer_region_codes(compact(sido)) if sido else None
        if compact(sigungu) and compact(sigungu) not in TOTAL_LIKE:
            own = infer_region_codes(compact(f"{sido}{sigungu}")) if sido else infer_region_codes(compact(sigungu))
            code = (own or parent or (None, None))[0]
            return "sigungu", code
        if sido and parent:
            return "sido", parent[0]
        return None
    value = region or sido
    if compact(value) in NATION:
        return "nation", None
    codes = infer_region_codes(compact(value)) if value else None
    if codes is None:
        return None
    return ("sido", codes[0]) if codes[1] is None else ("sigungu", codes[0])


# ---------------------------------------------------------------- 대상 평가


@dataclass
class RowRec:
    t: int  # 대상 번호
    row: int  # 원본 시트 행 번호
    period: str | None
    dims: dict[str, str]
    value: float | None = None
    shown: str | None = None
    reason: str | None = None
    level: tuple[str, str | None] | None = None


@dataclass
class TargetEval:
    index: int
    file_id: int
    file_name: str
    sha256: str
    sheet_id: str | None = None
    sheet: str | None = None
    column_id: str | None = None
    column: str | None = None
    variant: str | None = None
    unit: str | None = None
    unit_source: str | None = None
    period: str | None = None  # 시트 전체의 기간 (행 기간 열이 없을 때)
    period_source: str | None = None
    period_columns: list[str] = field(default_factory=list)
    dims: dict[str, int] = field(default_factory=dict)  # 차원 항목 → 열 번호
    status: str = "ok"  # ok · excluded(사유와 함께 뺌) · needs_input(사용자 확인 필요)
    reasons: list[str] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)
    decisions: dict[str, str | None] = field(default_factory=dict)
    data_rows: int = 0
    rows: list[RowRec] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "sheet_id": self.sheet_id,
            "sheet": self.sheet,
            "column_id": self.column_id,
            "column": self.column,
            "variant": self.variant,
            "unit": self.unit,
            "unit_source": self.unit_source,
            "period": self.period,
            "period_source": self.period_source,
            "period_columns": self.period_columns,
            "dims": [k for k in IDENTITY_FIELDS if k in self.dims],
            "status": self.status,
            "reasons": self.reasons,
            "candidates": self.candidates,
            "data_rows": self.data_rows,
        }


@dataclass
class Evaluation:
    req: AnalysisIn
    targets: list[TargetEval]
    variant: str | None = None
    variants: list[str] = field(default_factory=list)
    unit: str | None = None
    units: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # 실행을 막는 문제 (사용자가 골라야 함)
    warnings: list[str] = field(default_factory=list)
    overlaps: list[dict[str, Any]] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    filter_values: dict[str, list[str]] = field(default_factory=dict)
    available_groups: list[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def ok(self) -> list[TargetEval]:
        return [t for t in self.targets if t.status == "ok"]


def _sheet_targets(db: Session, user: CurrentUser, req: AnalysisIn) -> list[TargetEval]:
    targets: list[TargetEval] = []
    seen: set[tuple[int, int]] = set()
    total_rows = 0
    for t in req.targets:
        record = get_file_for(db, user, t.file_id)
        view = file_view(db, record)
        decisions = load_decisions(db, record.id)
        name = mask_text(record.original_name)
        if t.sheet_id is None:
            indexes = [i for i, s in enumerate(view.sheets) if req.field_key in decisions.get(s.name, {}).values()]
            if not indexes:
                targets.append(
                    TargetEval(
                        len(targets),
                        record.id,
                        name,
                        record.sha256,
                        status="excluded",
                        reasons=[
                            f"'{label_of(req.field_key)}'(으)로 확정된 열이 있는 시트가 없음 — "
                            "열 매핑 화면에서 확정하세요 ('자동 제안'은 쓰지 않음)"
                        ],
                    )
                )
                continue
        else:
            index = int(t.sheet_id[1:])
            if index >= len(view.sheets):
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"#{record.id} 파일에 없는 시트입니다: {t.sheet_id}")
            indexes = [index]
        for i in indexes:
            if (record.id, i) in seen:
                raise _bad(f"같은 파일·시트(#{record.id} {sheet_id(i)})를 두 번 골랐습니다.")
            seen.add((record.id, i))
            sv = view.sheets[i]
            total_rows += sv.data_rows
            if total_rows > MAX_ROWS:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"고른 시트의 데이터 행이 {MAX_ROWS:,}행을 넘습니다. 파일 수를 줄여 나눠 분석하세요.",
                )
            ev = TargetEval(
                len(targets),
                record.id,
                name,
                record.sha256,
                sheet_id=sheet_id(i),
                sheet=mask_text(sv.name),
                decisions=decisions.get(sv.name, {}),
                data_rows=sv.data_rows,
            )
            _resolve_sheet(ev, t, sv, record.original_name, req)
            ev.rows = []
            targets.append(ev)
            ev._view = sv  # type: ignore[attr-defined]  # 행 읽기용 (응답·저장에는 넣지 않음)
    return targets


def _resolve_sheet(ev: TargetEval, t: TargetIn, sv: SheetView, file_name: str, req: AnalysisIn) -> None:
    ev._units = declared_units(sv)  # type: ignore[attr-defined]  # (상태, 단위, 여러 개일 때 적힌 단위들)
    measure = [j for j, c in enumerate(sv.columns) if ev.decisions.get(c) == req.field_key]
    ev.candidates = [
        {"column_id": column_id(j), "column": mask_text(sv.columns[j]), "variant": variant_of(sv.columns[j])}
        for j in measure
    ]
    if not measure:
        ev.status = "excluded"
        ev.reasons.append(f"'{label_of(req.field_key)}'(으)로 확정된 열이 없음 ('자동 제안'은 쓰지 않음)")
        return
    if t.column_id is not None and t.column_id not in {c["column_id"] for c in ev.candidates}:
        ev.status = "excluded"
        ev.reasons.append(f"고른 열({t.column_id})이 '{label_of(req.field_key)}'(으)로 확정되어 있지 않음")
        return
    for key in (*IDENTITY_FIELDS, *PERIOD_FIELDS):
        cols = [j for j, c in enumerate(sv.columns) if ev.decisions.get(c) == key]
        if len(cols) == 1:
            ev.dims[key] = cols[0]
        elif len(cols) > 1:
            ev.status = "excluded"
            ev.reasons.append(f"'{label_of(key)}'(으)로 확정된 열이 여러 개라 행을 구분할 열이 불명확")
    ev.period_columns = [label_of(k) for k in PERIOD_FIELDS if k in ev.dims]
    if not any(k in ev.dims for k in IDENTITY_FIELDS) and not ev.period_columns:
        ev.status = "excluded"
        ev.reasons.append("지역·기관·구분·기간 열이 확정되지 않아 한 행이 무엇을 나타내는지 확인할 수 없음")
    # 기간: 행의 연도·월·기준일 열이 있으면 행마다, 없으면 파일명·시트명·제목
    if not ev.period_columns or ("month" in ev.dims and "year" not in ev.dims and "base_date" not in ev.dims):
        found = sheet_periods(file_name, sv)
        distinct = sorted({p for p, _ in found})
        if len(distinct) > 1:
            ev.status = "excluded"
            ev.reasons.append(f"파일명·시트명·제목의 기간이 서로 다름 ({', '.join(distinct)}) — 추정하지 않음")
        elif distinct:
            ev.period, ev.period_source = distinct[0], next(s for p, s in found if p == distinct[0])
            if t.period is not None and t.period != ev.period:
                ev.status = "needs_input" if ev.status == "ok" else ev.status
                ev.reasons.append(f"적은 기간({t.period})이 {ev.period_source}의 기간({ev.period})과 다름")
        elif t.period is not None:
            ev.period, ev.period_source = t.period, "사용자 확인"
        elif not ev.period_columns:
            ev.status = "needs_input" if ev.status == "ok" else ev.status
            ev.reasons.append("기간을 확인할 수 없음 — 파일명·제목·열에 기간이 없어 이 시트의 기간을 적어야 함")


def _resolve_units(ev: TargetEval, t: TargetIn, sv: SheetView) -> None:
    if ev.column_id is None:
        return
    state, sheet_unit, note = ev._units  # type: ignore[attr-defined]
    col_unit = column_unit(sv.columns[int(ev.column_id[1:])])
    detected = {u for u in (col_unit, sheet_unit if state == "one" else None) if u}
    if state == "ambiguous":
        ev.status = "excluded"
        ev.reasons.append(f"표의 단위 표기가 여러 개라 판단할 수 없음 ({note})")
    elif len(detected) > 1:
        ev.status = "excluded"
        ev.reasons.append(f"열 이름의 단위({col_unit})와 표의 단위({sheet_unit})가 다름")
    elif detected:
        ev.unit = detected.pop()
        ev.unit_source = "열 이름" if col_unit else "표 제목·주석"
        if t.unit is not None and t.unit != ev.unit:
            ev.status = "needs_input" if ev.status == "ok" else ev.status
            ev.reasons.append(f"적은 단위({t.unit})가 {ev.unit_source}의 단위({ev.unit})와 다름")
    elif t.unit is not None:
        ev.unit, ev.unit_source = mask_text(t.unit), "사용자 확인"
    else:
        ev.status = "needs_input" if ev.status == "ok" else ev.status
        ev.reasons.append("단위를 확인할 수 없음 — 표에 '단위: …'가 없어 이 시트의 단위를 적어야 함")


def _row_period(ev: TargetEval, cells: list[Any]) -> str | None:
    d = ev.dims
    if not any(k in d for k in PERIOD_FIELDS):
        return ev.period  # 행 기간 열이 없으면 시트 전체의 기간 (파일명·시트명·제목 또는 사용자 확인)
    if "base_date" in d and (p := _period_from_cell(cells[d["base_date"]])):
        return p
    year = _year_of(cells[d["year"]]) if "year" in d else None
    if "month" in d:
        v = cells[d["month"]]
        if isinstance(v, str) and (full := parse_date_value(v)) and full[0] != "year":
            return full[1]
        month = _month_of(v)
        base_year = year or (ev.period[:4] if ev.period else None)  # 월 열만 있으면 연도는 시트 기간에서
        return f"{base_year}-{month:02d}" if month is not None and base_year else None
    return year


def evaluate(db: Session, user: CurrentUser, req: AnalysisIn) -> Evaluation:
    """고른 자료를 확인한다 (계산·저장 없음). 실행 전 미리보기와 실행이 같은 판정을 쓴다."""
    validate_request(req)
    targets = _sheet_targets(db, user, req)
    ev = Evaluation(req, targets)
    # 구분: 고른 열의 구분이 하나뿐이면 그것, 여럿이면 사용자가 고른다
    all_variants = sorted({c["variant"] for t in targets if t.status != "excluded" for c in t.candidates})
    ev.variants = all_variants
    if req.variant is not None:
        ev.variant = req.variant
    elif len(all_variants) == 1:
        ev.variant = all_variants[0]
    elif len(all_variants) > 1:
        shown = ", ".join(v or "구분 없음" for v in all_variants)
        ev.problems.append(f"같은 항목이지만 열 이름의 구분이 다른 열이 있습니다 ({shown}) — 분석할 구분을 고르세요")
    for t, tin in _pairs(targets, req):
        if t.status == "excluded":
            continue
        cands = [c for c in t.candidates if ev.variant is None or c["variant"] == ev.variant]
        if tin is not None and tin.column_id is not None:
            cands = [c for c in cands if c["column_id"] == tin.column_id]
        if t.candidates and ev.variant is not None and not cands:
            t.status = "excluded"
            t.reasons.append(
                f"고른 구분('{ev.variant or '구분 없음'}')의 열이 없음 (이 시트: "
                f"{', '.join(c['variant'] or '구분 없음' for c in t.candidates)})"
            )
        elif len(cands) > 1:
            t.status = "needs_input" if t.status == "ok" else t.status
            t.reasons.append("같은 항목·구분으로 확정된 열이 여러 개 — 분석할 열을 고르세요")
        elif len(cands) == 1:
            t.column_id, t.column, t.variant = cands[0]["column_id"], cands[0]["column"], cands[0]["variant"]
            _resolve_units(t, tin or TargetIn(file_id=t.file_id), t._view)  # type: ignore[attr-defined]
    # 단위: 확인된 같은 단위끼리만
    ev.units = sorted({t.unit for t in targets if t.status == "ok" and t.unit})
    if req.unit is not None:
        ev.unit = req.unit
        for t in ev.ok:
            if t.unit != req.unit:
                t.status = "excluded"
                t.reasons.append(f"단위가 다름 ({t.unit}) — 이번 분석은 '{req.unit}'만 (자동 환산 없음)")
    elif len(ev.units) == 1:
        ev.unit = ev.units[0]
    elif len(ev.units) > 1:
        counts = Counter(t.unit for t in ev.ok)
        ev.problems.append(
            "단위가 다른 자료가 섞여 있습니다 ("
            + ", ".join(f"{u} {n}개" for u, n in sorted(counts.items()))
            + ") — 계산할 단위를 고르세요 (자동 환산 없음)"
        )
    # 같은 내용의 파일을 두 번 고름
    sha: dict[str, list[TargetEval]] = defaultdict(list)
    for t in ev.ok:
        sha[t.sha256].append(t)
    for same in sha.values():
        if len({t.file_id for t in same}) > 1:
            ev.problems.append(
                "같은 내용의 파일을 함께 골랐습니다 ("
                + ", ".join(f"#{t.file_id} {t.file_name}" for t in same)
                + ") — 하나만 고르세요"
            )
    # 그룹·필터 기준은 쓰는 모든 자료에 있어야 한다
    used = [k for k in (*req.group_by, *req.filters.dims) if k != "period"]
    for key in dict.fromkeys(used):
        missing = [t for t in ev.ok if key not in t.dims]
        if missing:
            ev.problems.append(
                f"'{label_of(key)}' 열이 없는 자료가 있습니다 ("
                + ", ".join(f"#{t.file_id} {t.sheet}" for t in missing[:5])
                + ") — 그 자료를 빼거나 기준을 바꾸세요"
            )
    ev.available_groups = [k for k in IDENTITY_FIELDS if ev.ok and all(k in t.dims for t in ev.ok)] + ["period"]
    _read_rows(ev)
    ev.fingerprint = fingerprint(ev)
    return ev


def _pairs(targets: list[TargetEval], req: AnalysisIn) -> list[tuple[TargetEval, TargetIn | None]]:
    """평가한 시트 ↔ 요청 항목 (시트를 정해 고른 항목은 그 시트, 파일 단위로 고른 항목은 그 파일의 시트들)."""
    out: list[tuple[TargetEval, TargetIn | None]] = []
    for t in targets:
        tin = next((x for x in req.targets if x.file_id == t.file_id and x.sheet_id == t.sheet_id), None) or next(
            (x for x in req.targets if x.file_id == t.file_id and x.sheet_id is None), None
        )
        out.append((t, tin))
    return out


def _read_rows(ev: Evaluation) -> None:
    req = ev.req
    f = req.filters
    wanted = {k: {dim_value(v) for v in vals} for k, vals in f.dims.items() if vals}
    values: dict[str, set[str]] = defaultdict(set)
    periods: set[str] = set()
    for t in ev.ok:
        sv: SheetView = t._view  # type: ignore[attr-defined]
        col = int(t.column_id[1:]) if t.column_id else None
        for row_no, kind, cells in sv.rows:
            if kind not in ("data", "subtotal"):
                continue
            dims = {k: dim_value(cells[j]) for k, j in t.dims.items() if k in IDENTITY_FIELDS}
            rec = RowRec(t.index, row_no, _row_period(t, cells), dims)
            t.rows.append(rec)
            if kind == "subtotal":
                rec.reason = "subtotal"
                continue
            if any(compact(v) in TOTAL_LIKE for v in dims.values()):
                rec.reason = "total_like"
                continue
            if rec.period is None:
                rec.reason = "period_unknown"
                continue
            if dims and not any(compact(v) for v in dims.values()):
                rec.reason = "no_identity"
                continue
            periods.add(rec.period)
            for k, v in dims.items():
                if v and len(values[k]) < MAX_FILTER_VALUES:
                    values[k].add(v)
            if any(dims.get(k, "") not in vals for k, vals in wanted.items()):
                rec.reason = "filtered"
                continue
            if (f.period_from and rec.period < f.period_from) or (
                f.period_to and rec.period[: len(f.period_to)] > f.period_to
            ):
                rec.reason = "filtered"
                continue
            rec.level = region_level(dims) if any(k in dims for k in REGION_FIELDS) else None
            rec.value, rec.reason, rec.shown = classify_value(cells[col]) if col is not None else (None, "blank", None)
    ev.periods = sorted(periods)
    ev.filter_values = {k: sorted(v) for k, v in values.items()}
    _upper_regions(ev)
    _overlaps(ev)
    for t in ev.ok:
        for r in t.rows:
            if r.reason is None and r.value is not None:
                if (f.value_min is not None and r.value < f.value_min) or (
                    f.value_max is not None and r.value > f.value_max
                ):
                    r.reason = "value_range"


def _candidate(r: RowRec) -> bool:
    """중복·상위 지역 판단에 쓰는 행: 구조·조건으로 빠지지 않은 행 (값의 결측 여부와 관계없이)."""
    return r.reason is None or REASONS[r.reason][1] and r.reason not in ("period_unknown", "no_identity")


def _upper_regions(ev: Evaluation) -> None:
    rows = [r for t in ev.ok for r in t.rows if _candidate(r) and r.level is not None]
    with_children = {r.level[1] for r in rows if r.level and r.level[0] == "sigungu" and r.level[1]}
    lower = any(r.level and r.level[0] in ("sido", "sigungu") for r in rows)
    excluded = 0
    for r in rows:
        if r.level and ((r.level[0] == "sido" and r.level[1] in with_children) or (r.level[0] == "nation" and lower)):
            r.reason, r.value, r.shown = "upper_region", None, None
            excluded += 1
    if excluded:
        ev.warnings.append(
            f"하위 지역과 함께 있는 상위 지역(시도·전국) 행 {excluded}개를 중복 방지로 뺐습니다 — "
            "제외 목록에서 확인하세요"
        )


def _row_key(r: RowRec) -> tuple[Any, ...]:
    parts = []
    for k in IDENTITY_FIELDS:
        if k in r.dims:
            v = r.dims[k]
            parts.append((k, compact(v)))
    return (r.period, tuple(parts))


def _overlaps(ev: Evaluation) -> None:
    """같은 대상이 다른 자료에도 있으면 중복 의심. 한 시트 안에서 대상이 겹치지 않는 표는 대상(기간·지역·기관·구분)으로,
    대상이 반복되는 목록형 표는 행 전체(대상+값)가 같은지로 본다. 자동으로 고르지 않고 실행을 막는다."""
    sigs: list[tuple[TargetEval, dict[Any, RowRec]]] = []
    for t in ev.ok:
        rows = [r for r in t.rows if _candidate(r)]
        keys = Counter(_row_key(r) for r in rows)
        unique = all(n == 1 for n in keys.values())
        sig = {(_row_key(r) if unique else (_row_key(r), r.value, r.shown)): r for r in rows}
        sigs.append((t, sig))
    for i, (a, sa) in enumerate(sigs):
        for b, sb in sigs[i + 1 :]:
            common = [k for k in sa if k in sb]
            if not common:
                continue
            same = sum(1 for k in common if sa[k].value == sb[k].value)
            samples = []
            for k in common[:5]:
                ra, rb = sa[k], sb[k]
                samples.append(
                    {
                        "period": ra.period,
                        "target": " · ".join(v for v in ra.dims.values() if v),
                        "a": {"row": ra.row, "value": ra.value if ra.value is not None else ra.shown},
                        "b": {"row": rb.row, "value": rb.value if rb.value is not None else rb.shown},
                    }
                )
            ev.overlaps.append(
                {
                    "a": {"file_id": a.file_id, "file_name": a.file_name, "sheet": a.sheet},
                    "b": {"file_id": b.file_id, "file_name": b.file_name, "sheet": b.sheet},
                    "count": len(common),
                    "same_value": same,
                    "different_value": len(common) - same,
                    "samples": samples,
                }
            )
            ev.problems.append(
                f"#{a.file_id} {a.sheet}와(과) #{b.file_id} {b.sheet}에 "
                f"같은 대상(기간·지역·기관·구분) {len(common)}건이"
                f" 있습니다 (값 같음 {same}, 다름 {len(common) - same}) — 두 번 더하지 않도록 사용할 자료를 고르세요"
            )


def fingerprint(ev: Evaluation) -> str:
    """미리보기 뒤 파일·매핑이 바뀌었는지 확인하는 값 (파일 내용 해시 + 그 시트의 확정 매핑 전체 + 판정 규칙)."""
    payload = [POLICY] + [
        [t.file_id, t.sha256, t.sheet_id, sorted((k, v) for k, v in t.decisions.items())] for t in ev.targets
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, default=str).encode()).hexdigest()


def preview_out(ev: Evaluation) -> dict[str, Any]:
    rows = [r for t in ev.ok for r in t.rows]
    reasons = Counter(r.reason for r in rows if r.reason)
    return {
        "field_key": ev.req.field_key,
        "field_label": label_of(ev.req.field_key),
        "stock": ev.req.field_key in STOCK_FIELDS,
        "variant": ev.variant,
        "variants": ev.variants,
        "unit": ev.unit,
        "units": ev.units,
        "targets": [t.out() for t in ev.targets],
        "problems": ev.problems,
        "warnings": ev.warnings,
        "overlaps": ev.overlaps,
        "periods": ev.periods,
        "filter_values": ev.filter_values,
        "available_groups": ev.available_groups,
        "rows": {"candidate": sum(1 for r in rows if r.reason is None), "excluded": dict(reasons)},
        "reason_labels": {k: v[0] for k, v in REASONS.items()},
        "fingerprint": ev.fingerprint,
        "limits": {"targets": MAX_TARGETS, "rows": MAX_ROWS, "groups": MAX_GROUPS, "kept_rows": KEEP_ROWS},
    }


# ---------------------------------------------------------------- 계산


def compute(ev: Evaluation) -> dict[str, Any]:
    """평가를 바탕으로 결과를 만든다 (요청한 전체 대상 행으로 — 미리보기 표의 일부 행이 아님)."""
    req = ev.req
    if ev.problems:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"message": "실행할 수 없습니다 — 먼저 해결하세요.", "problems": ev.problems}
        )
    blocked = [t for t in ev.targets if t.status == "needs_input"]
    if blocked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "확인이 필요한 자료가 있습니다 (단위·기간·열을 적거나 그 자료를 빼세요).",
                "problems": [f"#{t.file_id} {t.sheet}: {'; '.join(t.reasons)}" for t in blocked],
            },
        )
    if not ev.ok:
        raise _bad("분석할 수 있는 자료가 없습니다 (모든 자료가 제외됨 — 사유를 확인하세요).")
    group_fields = list(req.group_by)
    split_period = len(ev.periods) > 1 and "period" not in group_fields
    if split_period:
        group_fields = ["period", *group_fields]
    targets = {t.index: t for t in ev.ok}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}

    def key_of(r: RowRec) -> tuple[str, ...]:
        return tuple((r.period or "") if k == "period" else (r.dims.get(k) or "(값 없음)") for k in group_fields)

    included: list[RowRec] = []
    excluded: list[RowRec] = []
    for t in ev.ok:
        for r in t.rows:
            if r.reason is None:
                included.append(r)
                g = groups.setdefault(key_of(r), {"values": [], "problem": 0, "files": set()})
                g["values"].append(r.value)
                g["files"].add(t.file_id)
            else:
                excluded.append(r)
                if REASONS[r.reason][1] and r.reason not in ("period_unknown", "no_identity"):
                    g = groups.setdefault(key_of(r), {"values": [], "problem": 0, "files": set()})
                    g["problem"] += 1
                    g["files"].add(t.file_id)
            if len(groups) > MAX_GROUPS:
                raise _bad(f"그룹이 {MAX_GROUPS:,}개를 넘습니다 — 그룹 기준을 줄이거나 필터를 쓰세요.")
    out_groups = []
    for key in sorted(groups):
        g = groups[key]
        vals = g["values"]
        n = len(vals)
        row: dict[str, Any] = {
            "keys": list(key),
            "count": n,
            "problem_rows": g["problem"],
            "partial": g["problem"] > 0,
            "files": len(g["files"]),
        }
        total = math.fsum(vals) if n else None
        row["sum"] = total
        row["mean"] = total / n if n else None
        row["min"] = min(vals) if n else None
        row["max"] = max(vals) if n else None
        if req.bins is not None:
            x = row[req.bins.on]
            row["bin"] = (
                NO_VALUE
                if x is None
                else next((bin_label(b) for b in req.bins.ranges if _contains(b, x)), UNCLASSIFIED)
            )
        out_groups.append(row)
    bin_counts = None
    if req.bins is not None:
        labels = [bin_label(b) for b in req.bins.ranges] + [UNCLASSIFIED, NO_VALUE]
        c = Counter(g["bin"] for g in out_groups)
        bin_counts = [{"label": lab, "groups": c.get(lab, 0)} for lab in labels]
    reason_counts = Counter(r.reason for r in excluded)
    problem_rows = sum(n for k, n in reason_counts.items() if REASONS[k][1])

    def ref(r: RowRec) -> dict[str, Any]:
        t = targets[r.t]
        return {"file_id": t.file_id, "file_name": t.file_name, "sheet": t.sheet, "row": r.row, "period": r.period}

    detail = [{**ref(r), "keys": list(key_of(r)), "value": r.value} for r in included[:KEEP_ROWS]]
    excluded_out = [
        {**ref(r), "reason": r.reason, "value": r.value if r.value is not None else r.shown}
        for r in sorted(excluded, key=lambda r: (r.t, r.row))[:KEEP_ROWS]
    ]
    return {
        "group_fields": group_fields,
        "group_labels": [label_of(k) for k in group_fields],
        "period_split": split_period,
        "aggregates": list(req.aggregates),
        "groups": out_groups,
        "bins": bin_counts,
        "partial": problem_rows > 0,
        "rows_included": len(included),
        "rows_excluded": len(excluded),
        "problem_rows": problem_rows,
        "reason_counts": dict(reason_counts),
        "excluded_targets": [t.out() for t in ev.targets if t.status == "excluded"],
        "detail": detail,
        "detail_total": len(included),
        "excluded": excluded_out,
        "excluded_total": len(excluded),
    }


def conditions(ev: Evaluation) -> dict[str, Any]:
    """결과와 함께 저장하는 계산 조건 (현재 매핑이 바뀌어도 이 결과가 어떤 조건이었는지 재현할 수 있게)."""
    req = ev.req
    return {
        "policy": POLICY,
        "field_key": req.field_key,
        "field_label": label_of(req.field_key),
        "stock": req.field_key in STOCK_FIELDS,
        "variant": ev.variant,
        "unit": ev.unit,
        "periods": ev.periods,
        "group_by": list(req.group_by),
        "filters": req.filters.model_dump(),
        "aggregates": list(req.aggregates),
        "bins": None
        if req.bins is None
        else {
            "on": req.bins.on,
            "on_label": AGG_LABEL[req.bins.on],
            "ranges": [{**b.model_dump(), "label": bin_label(b)} for b in req.bins.ranges],
        },
        "mean_denominator": "그룹마다 유효한 숫자의 개수 (빈칸·비공개·숫자가 아닌 값은 분모에 넣지 않음)",
        "reason_labels": {k: v[0] for k, v in REASONS.items()},
        "warnings": ev.warnings,
    }


def inputs(ev: Evaluation) -> list[dict[str, Any]]:
    return [
        {**t.out(), "sha256": t.sha256, "decisions": sorted([mask_text(k), v] for k, v in t.decisions.items())}
        for t in ev.targets
    ]
