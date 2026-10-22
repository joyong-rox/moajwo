"""확정된 열로 하는 데이터 분석 (1차): 합계·평균·최솟값·최댓값·유효 숫자 개수, 그룹별 집계, 필터, 구간 분류.

모델(임베딩·LLM)을 부르지 않는다. 사용자가 확정한 매핑(mapping_decisions)과 마스킹한 파싱 결과(file_view)만 읽고,
매핑·학습 별칭·승격 근거·열 매핑 분석 기록은 바꾸지 않는다.

숫자가 맞아도 의미가 틀린 결과를 막는 규칙 (판정은 서버가 하고, 화면은 결과와 사유를 보여 준다):
- 열: 화면 위치·열 이름이 아니라 파일·시트·열 식별자와 **사용자가 확정한** 표준 항목으로 고른다
  ('자동 제안'은 쓰지 않음).
  같은 항목이라도 열 이름의 연도·구분 단어(2단 머리글의 각 부분, 괄호 안 사전 단어)가 다르면 따로 계산한다.
- 단위: 확인된 같은 단위끼리만 계산한다 (자동 환산 없음). 표에 단위가 없으면 사용자가 그 시트의 단위를 확인해
  적어야 쓴다.
- 기간: 행(연도·월·기준일 열) 또는 시트(시트명·제목, 없으면 파일명)에서 기간을 확인하고,
  기간이 여럿이면 기간별로 나눈다.
  시점 값(인구·등록대수 등)을 월별로 더해 연간 값으로 만들지 않고, 같은 달이라도 기준일이 다르면 함께 계산하지 않는다.
  연도·월·기준일 열끼리 맞지 않으면 실행하지 않는다. 기간을 못 찾으면 사용자가 적어야 쓴다.
- 집계 대상: 지역·기관·구분 값으로 한 행이 무엇인지 확인한다. 지역은 매핑 구조(시도·시군구·행정구역 열)와 관계없이
  행정구역 코드로 비교한다. 합계·소계 행, '계·합계·전체' 행은 빼고 사유를 남긴다.
- 상위·하위 지역: 같은 기간·같은 구분(기관·차종 등) 안에서만 비교한다. 하위 지역 합계가 상위 지역 값과 정확히 같으면
  상위 행을 중복으로 빼고(허용 오차 없음 — exact_number), 다르거나 확인할 수 없으면(하위 지역 일부만 있음 등) 사용자가
  집계 수준(하위만/상위만)을 고르게 한다. 시점 값은 기준일이 다른 행끼리 상위·하위로 판정하지 않는다.
- 기준일 검사는 사용자가 조건으로 뺀 행만 빼고, 시스템이 자동으로 뺀 행(상위·하위 지역, 합계 행 등)까지 근거로 본다
  (자동 제외 때문에 기준일 충돌이 가려지지 않게). 사용자 조건(지역·기관·기간·숫자 범위)은 시스템 제외 사유와 따로
  모든 행에 적용한다 — 합계·소계 행도 사용자가 조건으로 뺐으면 기준일 근거에서 빠진다.
- 대상 범위: 사용자 조건(지역·기관·구분·기간·숫자 범위)이 먼저 정한 행 안에서만 모든 판정을 한다.
  반복·중복·상위·하위 판정(_candidate)과 계산은 사용자 조건 안의 행만, 기준일 검사(_date_scope)는 그중 시스템이
  뺀 행(합계·소계·상위 지역 등)까지 본다. 그래서 같은 행을 남기는 숫자 필터와 지역 필터는 같은 판정·결과를 낸다.
- 중복: 같은 내용의 파일이나 같은 대상(기간·지역·기관·구분)이 다른 자료에도 있으면 실행하지 않고 사용자가 고르게 한다.
  한 시트 안에서 같은 대상이 반복되면 중복된 통계인지 서로 다른 기록인지 알 수 없으므로, 사용자가 '서로 다른 기록'이라고
  확인하기 전에는 계산하지 않는다 (임의로 합치거나 지우지 않음).
- 결측: 빈칸·비공개 표기·숫자가 아닌 값·가린 개인정보는 0으로 바꾸지 않고 사유와 함께 뺀다 (뺀 값이 있으면 '부분 집계').
- 평균 = 유효한 숫자의 합 ÷ 그 개수 (파일별 평균을 다시 평균하지 않음). 유효한 숫자가 없으면 '계산할 값 없음'.
- 사용자가 적은 글자(구간 이름·필터 값·단위·구분)는 저장·표시·다운로드 전에 기존 개인정보 마스킹(mask_text)으로 가린다.
- 가린 값과 대상의 동일성은 따로 본다 (analysis-policy-8): 표의 값은 가린 보기에서 읽지만, 가림으로 글자가 바뀐 칸은
  원문 구분 표지(mapping_review.raw_mark — 서버 안에서만 쓰는 키의 HMAC)를 함께 읽어, 가린 표시가 같아도 원문이 다르면
  다른 대상·다른 그룹으로 본다. 화면·저장·Excel에는 원문 대신 가린 값에 '(가림 구분 n)' 번호를 붙인다
  (번호는 이 결과 안에서 같은 원문을 가리키고, 목록·쪽·근거 행·Excel에서 같다). 번호 없는 가린 값으로 고른 필터처럼
  구분할 수 없는 조건과, 가린 뒤 같아지는 서로 다른 열 이름 구분은 계산하지 않고 막는다.
"""

import decimal
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.admin_codes import infer_region_codes
from app.auth import CurrentUser
from app.decisions import load_decisions
from app.files import column_id, get_file_for
from app.formulas import TOTAL_FORMULAS, check_totals, unique_columns
from app.glossary import parse_date_value
from app.mail_fields import MAIL_MEASURE_FIELDS
from app.mapping import TOTAL_WORDS, load_dictionary, normalize, split_column_name
from app.mapping_review import SheetView, declared_units, file_view, raw_mark, sheet_id
from app.parsing import is_missing
from app.pii import mask_text
from app.quantity import quantity_value
from app.validation import STOCK_FIELDS, _fmt, _month_of, _text_periods, _year_of

# 판정 규칙이 바뀌면 올린다 (저장한 결과에 함께 남음).
# 7: 메일 지표·용량 단위 구분 (PR #21) · 8: 가린 값이 같아도 원문이 다르면 다른 대상 (F-1)
# 9: 합계·최솟값·최댓값을 정확한 수로 (C-02 — 0.1 + 0.2 = 0.3, 큰 정수도 끝자리까지)
POLICY = "analysis-policy-9"
MASKED_NO = "가림 구분"  # 가린 표시가 같은 서로 다른 값에 붙이는 번호의 이름 ('가팀 k***@example.com (가림 구분 2)')
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
) | MAIL_MEASURE_FIELDS
REGION_FIELDS = ("sido", "sigungu", "region", "region_code")
IDENTITY_FIELDS = (
    *REGION_FIELDS, "org_name", "org_code", "organization_code", "organization_name",
    "department", "vehicle_type", "vehicle_use", "fuel_type",
)
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
_COLUMN_UNIT = re.compile(
    r"\(\s*((?:천|만|백만|억|조)?(?:명|대|원|건|개|개소|곳|가구|세대|회|톤|일)"
    r"|%|㎡|km|ha|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*\)"
)

# 제외 사유: (설명, 자료 문제 여부). 자료 문제로 뺀 값이 있으면 그 결과는 '부분 집계'
REASONS: dict[str, tuple[str, bool]] = {
    "blank": ("빈칸", True),
    "missing_marker": ("비공개·해당 없음 표기 ('-', 'x', '…' 등)", True),
    "not_number": ("숫자로 읽을 수 없는 값 (앞자리 0이 있는 숫자 글자·문자 등)", True),
    "pii": ("개인정보 형식이라 가린 값", True),
    "period_unknown": ("기간을 확인할 수 없는 행", True),
    "period_conflict": ("기간 열(연도·월·기준일)이 서로 맞지 않는 행 (기간을 추정하지 않음)", True),
    "no_identity": ("지역·기관·구분 값이 비어 무엇인지 알 수 없는 행", True),
    "subtotal": ("합계·소계 행 (상세 행과 중복 방지)", False),
    "total_like": ("'계·합계·전체'로 적힌 행 (확인 필요 — 중복 방지로 제외)", False),
    "upper_region": ("같은 기간·같은 구분의 하위 지역 행과 함께 있는 상위 지역 행 (중복 방지)", False),
    "lower_region": ("집계 수준 '상위 지역만'을 골라 뺀 하위 지역 행 (중복 방지)", False),
    "filtered": ("고른 지역·기관·기간 조건 밖", False),
    "value_range": ("숫자 범위 조건 밖", False),
}
# 어디에 속하는지 알 수 없어 중복·상위 지역 판단에도 쓰지 않는 행
UNPLACED = frozenset({"period_unknown", "period_conflict", "no_identity"})
OTHER_IDENTITY = tuple(k for k in IDENTITY_FIELDS if k not in REGION_FIELDS)
_DAY = re.compile(r"((?:19|20)\d{2})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})")


@lru_cache(maxsize=1)
def field_labels() -> dict[str, str]:
    return {f.key: f.label for f in load_dictionary()}


@lru_cache(maxsize=1)
def _dictionary_words() -> frozenset[str]:
    return frozenset(normalize(t) for f in load_dictionary() for t in (f.label, *f.aliases))


@lru_cache(maxsize=1)
def _identity_names() -> dict[str, str]:
    """구분 항목(지역·기관·조직·부서 등)의 사전 이름·동의어 → 항목 key.
    반복 안내에서 쓰지 않은 구분 열을 찾는 데만 쓴다."""
    names: dict[str, str] = {}
    for f in load_dictionary():
        if f.key in IDENTITY_FIELDS:
            for name in (f.label, *f.aliases):
                names.setdefault(normalize(name), f.key)
    return names


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
    # 같은 대상의 행이 반복될 때, 사용자가 '서로 다른 기록(거래·발생 건)'이라고 확인했으면 모두 더한다
    rows_are_records: bool = False


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
    # 상위·하위 지역 행이 함께 있고 하위 합계가 상위 값과 다를 때 사용자가 고른 집계 수준 (None = 고르지 않음)
    region_level: Literal["lower", "upper"] | None = None


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


def bin_labels(bins: BinsIn) -> list[str]:
    """구간마다 보여 줄 이름. 가린 뒤 같아졌거나 같은 이름을 여러 번 적었으면(또는 '미분류' 등과 같으면)
    번호를 붙여 구분한다. 계산·건수는 구간 번호로 세므로 이름이 같아도 서로 합쳐지지 않는다."""
    names = [bin_label(b) for b in bins.ranges]
    reserved = {UNCLASSIFIED, NO_VALUE}
    return [f"{n} ({i + 1}번 구간)" if names.count(n) > 1 or n in reserved else n for i, n in enumerate(names)]


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


def _shown(text: str) -> str:
    """오류 메시지에 되돌려 보여 주는 사용자 입력 (개인정보 형식은 가리고 길이를 줄인다)."""
    return mask_text(text)[:60]


def normalize_request(req: AnalysisIn) -> None:
    """사용자가 적은 글자를 저장·표시·비교 전에 가린다 (기존 마스킹 정책과 같은 mask_text).
    표의 값(지역·기관 등)은 가린 보기의 표시(가린 뒤 같아진 값은 '(가림 구분 n)' 번호를 붙인 것)와 맞춰 보므로,
    필터 값도 같은 규칙으로 가린다 (번호가 붙은 값은 다시 가려도 그대로)."""
    if req.variant is not None:
        req.variant = mask_text(req.variant)
    if req.unit is not None:
        req.unit = mask_text(req.unit.strip())
    for t in req.targets:
        if t.unit is not None:
            t.unit = mask_text(t.unit.strip())
    req.filters.dims = {k: [mask_text(dim_value(v)) for v in vals] for k, vals in req.filters.dims.items()}
    if req.bins is not None:
        for b in req.bins.ranges:
            if b.label is not None:
                b.label = mask_text(b.label.strip()) or None


def validate_request(req: AnalysisIn) -> None:
    normalize_request(req)
    if req.field_key not in MEASURE_FIELDS:
        raise _bad("분석할 수 있는 항목이 아닙니다 (1차는 더해서 의미가 있는 수량 항목만 — 비율·증감률은 후속 기능).")
    if any(k not in GROUP_FIELDS for k in req.group_by):
        raise _bad(
            "그룹 기준으로 쓸 수 없는 항목입니다: "
            + ", ".join(_shown(k) for k in req.group_by if k not in GROUP_FIELDS)
        )
    if len(set(req.group_by)) != len(req.group_by):
        raise _bad("같은 그룹 기준을 두 번 골랐습니다.")
    if not req.aggregates or len(set(req.aggregates)) != len(req.aggregates):
        raise _bad("계산 방식을 하나 이상, 겹치지 않게 고르세요.")
    for key, values in req.filters.dims.items():
        if key not in IDENTITY_FIELDS:
            raise _bad(f"필터에 쓸 수 없는 항목입니다: {_shown(key)}")
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


def exact_number(value: object) -> Decimal | None:
    """상위·하위 합계 일치 판정용 정확한 수 (계산 결과에는 쓰지 않고 원본 값도 바꾸지 않는다).

    - 정수 칸(인원·대수·건수·금액 등)은 정수 그대로 — 1원·1명 차이도 다름으로 본다.
    - 소수 칸은 원본에 적힌 자릿수(그 값을 그대로 되살리는 가장 짧은 십진 표기)로 읽는다. 그래서 이진 부동소수의
      표현 오차(0.1 + 0.2 = 0.30000000000000004)는 생기지 않고,
      적힌 자릿수 안의 실제 차이(0.3 vs 0.31)는 그대로 다름이다.
    - 값 크기에 비례하는 허용 오차나 반올림은 쓰지 않는다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(repr(value))
    return None


SAFE_INTEGER = 2**53  # 브라우저(JavaScript)·배정밀도가 정수를 끝자리까지 정확히 담는 한계
EXCEL_DIGITS = 15  # Excel 숫자 칸이 정확히 담는 유효 숫자 자릿수


def _beyond_safe(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) > SAFE_INTEGER


def excel_number(value: object) -> object:
    """Excel 칸에 쓸 값: 16자리 이상 정수는 글자로 (Excel 숫자 칸은 15자리까지만 정확 — 자릿수를 지킨다).
    그 밖의 수는 숫자 칸 그대로."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 10**EXCEL_DIGITS:
        return str(value)
    return value


def _plain(d: Decimal | None) -> int | float | None:
    """정확한 값을 화면·저장용 숫자로 (정수면 정수, 아니면 소수)."""
    if d is None:
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def column_unit(column: str) -> str | None:
    found = [m.group(1) for m in _COLUMN_UNIT.finditer(unicodedata.normalize("NFKC", column))]
    found = [u for u in found if normalize(u) not in _dictionary_words()]
    return found[0] if len(set(found)) == 1 else None


def variant_of(column: str) -> str:
    """같은 표준 항목이라도 따로 계산할 구분. 열 이름의 연도, 2단 머리글 'A_B'의 모든 부분(보수적), 괄호 안의 사전 단어.

    '인구수'와 '주민등록인구'는 구분이 없어 함께 계산할 수 있고,
    '인구_남'·'인구_여'·'승용(관용)'·'2024년 인구'는 따로다. 보여 주고 저장하는 값이라 가린다.
    """
    return mask_text(_variant_words(column))


def _variant_words(column: str) -> str:
    """variant_of의 가리기 전 글자 (서버 안에서 가린 뒤 같아진 구분을 가려내는 데만 쓴다)."""
    name = split_column_name(column)
    words: list[str] = []
    if name.qualifier:
        words.append(name.qualifier)
    if name.parts:
        words.extend(p.strip() for p in name.raw_parts)
    words.extend(b for b in name.bracketed if normalize(b) in _dictionary_words())
    return " · ".join(dict.fromkeys(w for w in words if w))


def _cell_time(value: object) -> tuple[str, str, str | None] | None:
    """기간 칸 → (정밀도 'year'·'month'·'day', 기간 'YYYY' 또는 'YYYY-MM', 날짜 'YYYY-MM-DD' 또는 None).

    분석은 같은 달 안의 기준일까지 비교해야 해서, 공용 parse_date_value(월까지만 돌려줌)를 바꾸지 않고
    여기서 날짜를 더 읽는다.
    """
    if isinstance(value, datetime | date):
        return "day", f"{value.year}-{value.month:02d}", f"{value.year}-{value.month:02d}-{value.day:02d}"
    if isinstance(value, str):
        parsed = parse_date_value(value)
        if parsed is None:
            return None
        kind, period = parsed
        if kind != "date":
            return kind, period, None
        m = _DAY.search(unicodedata.normalize("NFKC", value))
        return "day", period, f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
    if isinstance(value, int) and not isinstance(value, bool):
        year = _year_of(value)
        return ("year", year, None) if year else None
    return None


def sheet_periods(file_name: str, sheet: SheetView) -> list[tuple[str, str]]:
    """파일명·시트명·제목에 적힌 기간 → [(기간, 출처)]. 서로 다른 기간이 여럿이면 호출하는 쪽이 판단하지 않고 멈춘다."""
    found: list[tuple[str, str]] = [(p, "파일명") for p in _text_periods(file_name)]
    found += [(p, "시트명") for p in _text_periods(sheet.name)]
    for text in sheet.title_texts:
        found += [(p, "제목") for p in _text_periods(text)]
    return found


def region_key(dims: dict[str, str]) -> tuple[str, str] | None:
    """행의 지역 → 비교 키. 시도·시군구 열로 나눠 적었든 '행정구역' 한 열에 적었든 같은 지역이면 같은 키
    (행정구역 정식 명칭이면 시도 2자리·시군구 5자리 코드, 아니면 공백을 뺀 글자). 지역 값이 없으면 None."""
    names = [dims[k] for k in ("sido", "sigungu", "region") if compact(dims.get(k))]
    if not names:
        code = compact(dims.get("region_code"))
        return ("code", code) if code else None
    text = compact("".join(names))
    if text in NATION:
        return ("nation", "")
    codes = infer_region_codes(text)
    if codes is None and len(names) > 1:  # '서울특별시' + '서울특별시 종로구'처럼 겹쳐 적은 경우
        codes = infer_region_codes(compact(names[-1]))
    return ("code", codes[0] + (codes[1] or "")) if codes else ("name", text)


def region_level(key: tuple[str, str] | None) -> tuple[str, str | None] | None:
    """지역 키 → ('nation'|'sido'|'sigungu', 상위 시도 코드). 코드로 확인할 수 없으면 None (판단하지 않음)."""
    if key is None:
        return None
    kind, value = key
    if kind == "nation":
        return "nation", None
    if kind == "code" and value.isdigit() and len(value) in (2, 5):
        return ("sido", value) if len(value) == 2 else ("sigungu", value[:2])
    return None


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
    day: str | None = None  # 기준일 (날짜까지 적힌 경우)
    exact: Decimal | None = None  # 상위·하위 일치 판정용 정확한 값 (exact_number)
    # 사용자 조건(지역·기관·구분 필터, 기간, 숫자 범위) 밖인가. 시스템 제외 사유(reason)와 따로 정한다 —
    # 합계·소계 행처럼 시스템이 뺀 행에도 같은 조건을 적용해, 사용자가 뺀 행은 기준일 근거에서도 빠지게 한다.
    user_out: bool = False
    # 가림으로 글자가 바뀐 구분 값의 원문 구분 표지 (항목 → 표지). 같은 대상 비교에만 쓰고 내보내지 않는다
    marks: dict[str, str] = field(default_factory=dict)
    # 필터·그룹·근거 행에 쓰는 표시 (가린 값, 가린 뒤 같아진 서로 다른 값은 '(가림 구분 n)'). dims와 같은 항목
    keys: dict[str, str] = field(default_factory=dict)


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
    records: bool = False  # 사용자가 '반복 행은 서로 다른 기록'이라고 확인함
    repeated: dict[str, Any] | None = None  # 같은 대상의 행이 반복된 정도 (화면에 보여 줌)

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
            "rows_are_records": self.records,
            "repeated": self.repeated,
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
    level_conflicts: list[dict[str, Any]] = field(default_factory=list)  # 하위 합계 ≠ 상위 값 (예시 일부)
    level_mismatch: int = 0  # 하위 합계가 상위 값과 다르거나 확인할 수 없는 상위 지역 수
    date_conflict: bool = False  # 시점 값의 기준일 충돌로 실행을 막음 (_base_dates)
    base_dates: dict[str, str] = field(default_factory=dict)  # 기간 → 기준일 (시점 값, 날짜가 적힌 경우)
    # (항목, 가린 표시, 원문 표지) → 번호 붙인 표시 — 가린 뒤 같아진 서로 다른 값에만 (_value_labels)
    labels: dict[tuple[str, str, str], str] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def ok(self) -> list[TargetEval]:
        return [t for t in self.targets if t.status == "ok"]


def _sheet_targets(db: Session, user: CurrentUser, req: AnalysisIn) -> list[TargetEval]:
    targets: list[TargetEval] = []
    seen: set[tuple[int, int]] = set()
    total_rows = 0
    for t in req.targets:
        record = get_file_for(db, user, t.file_id)  # 볼 수 없는 파일은 없는 것처럼 404
        if record.uploaded_by != user.id:
            # 최고 관리자는 남의 파일·결과를 조회만 한다 — 남의 자료로 새 분석 결과를 만들지 않음 (저장 전에 막음)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "다른 사용자의 파일로는 분석을 만들 수 없습니다 (다른 사용자의 기존 분석 결과는 조회만 가능).",
            )
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
    # 기간: 행의 연도·월·기준일 열이 있으면 행마다, 없으면 시트명·제목, 그것도 없으면 파일명.
    # 파일명의 날짜는 발행·작성 시점일 수 있어 통계 기준 시점(제목 등)과 같은 뜻으로 비교하지 않고, 쓴 근거를 남긴다.
    if not ev.period_columns or ("month" in ev.dims and "year" not in ev.dims and "base_date" not in ev.dims):
        found = sheet_periods(file_name, sv)
        inner = [(p, s) for p, s in found if s != "파일명"]
        use = inner or found
        distinct = sorted({p for p, _ in use})
        if len(distinct) > 1:
            ev.status = "excluded"
            where = "시트명·제목" if inner else "파일명"
            ev.reasons.append(f"{where}의 기간이 서로 다름 ({', '.join(distinct)}) — 추정하지 않음")
        elif distinct:
            ev.period, ev.period_source = distinct[0], next(s for p, s in use if p == distinct[0])
            period = ev.period
            other = sorted(
                {p for p, s in found if s == "파일명" and not (p.startswith(period) or period.startswith(p))}
            )
            if inner and other:
                ev.period_source += f" (파일명의 {', '.join(other)}은 발행·작성 시점일 수 있어 쓰지 않음)"
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


def _row_period(ev: TargetEval, cells: list[Any]) -> tuple[str | None, str | None, str | None]:
    """행의 기간 → (기간, 기준일, 충돌 설명). 연도·월·기준일 열이 서로 맞지 않으면 기간을 정하지 않고 충돌을 돌려준다
    (어느 열이 맞는지 추정하지 않음)."""
    d = ev.dims
    if not any(k in d for k in PERIOD_FIELDS):
        return ev.period, None, None  # 행 기간 열이 없으면 시트 전체의 기간 (시트명·제목·파일명 또는 사용자 확인)
    seen: list[tuple[str, str, str, str | None]] = []  # (열, 정밀도, 기간, 날짜)
    if "base_date" in d and (x := _cell_time(cells[d["base_date"]])):
        seen.append(("기준일", *x))
    if "year" in d and (y := _year_of(cells[d["year"]])):
        seen.append(("연도", "year", y, None))
    month_no = None
    if "month" in d:
        v = cells[d["month"]]
        x = _cell_time(v) if isinstance(v, str | datetime | date) else None
        if x and x[0] != "year":
            seen.append(("월", *x))
        elif (month_no := _month_of(v)) is not None:
            seen.append(("월", "month_no", f"{month_no}월", None))
    years = {p[:4] for _, kind, p, _ in seen if kind != "month_no"}
    months = {p for _, kind, p, _ in seen if kind in ("month", "day")}
    days = {day for *_, day in seen if day}
    clash = len(years) > 1 or len(months) > 1 or len(days) > 1
    if month_no is not None and any(int(p[5:7]) != month_no for p in months):
        clash = True
    if clash:
        return None, None, " · ".join(f"{col} {day or p}" for col, _, p, day in seen)
    if months:
        return next(iter(months)), next(iter(days), None), None
    if "month" in d:
        if month_no is None:
            return None, None, None
        base_year = next(iter(years), None) or (
            ev.period[:4] if ev.period else None
        )  # 월 숫자만 있으면 연도는 시트 기간
        return (f"{base_year}-{month_no:02d}" if base_year else None), None, None
    return next(iter(years), None), None, None


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
        t.records = bool(tin and tin.rows_are_records)
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
    # 열 이름의 구분: 가린 뒤 같아진 서로 다른 구분(열 이름에 개인정보 형식)은 한 구분으로 계산하지 않는다
    raw_variants: dict[str, set[str]] = defaultdict(set)
    for t in ev.ok:
        if t.column_id is not None:
            words = _variant_words(t._view.columns[int(t.column_id[1:])])  # type: ignore[attr-defined]
            raw_variants[t.variant or ""].add(raw_mark(words) if mask_text(words) != words else "")
    for shown, found in sorted(raw_variants.items()):
        if len(found) > 1:
            ev.problems.append(
                f"열 이름의 구분 '{shown or '구분 없음'}'이(가) 가린 뒤 같아진 서로 다른 구분 {len(found)}개입니다 "
                "(열 이름에 개인정보 형식이 있음) — 한 구분으로 계산하지 않습니다. "
                "그 열의 매핑이나 자료의 열 이름을 확인하세요"
            )
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


def _outside_conditions(
    dims: dict[str, str], period: str | None, number: float | None, wanted: dict[str, set[str]], f: FiltersIn
) -> bool:
    """사용자 조건 밖인가 (행의 시스템 제외 사유와 관계없이 같은 규칙).
    기간·숫자를 알 수 없으면 그 조건으로는 빼지 않는다
    (결측·비공개 값을 0으로 보고 숫자 범위를 판단하지 않음)."""
    if any(dims.get(k, "") not in vals for k, vals in wanted.items()):
        return True
    if period and (
        (f.period_from and period < f.period_from) or (f.period_to and period[: len(f.period_to)] > f.period_to)
    ):
        return True
    return number is not None and (
        (f.value_min is not None and number < f.value_min) or (f.value_max is not None and number > f.value_max)
    )


def _value_labels(ev: Evaluation) -> dict[tuple[str, str], int]:
    """가린 표시가 같은데 원문이 다른 구분 값에 이 결과 안에서 일관된 번호를 붙인다 (ev.labels).
    번호는 자료 순서(고른 순서 → 행 순서)에서 처음 나온 순서라, 같은 자료·같은 요청이면 미리보기와 실행에서 같다.
    가림으로 바뀐 칸이 없으면 아무것도 하지 않는다.
    돌려주는 값: (항목, 가린 표시) → 그 표시를 쓰는 서로 다른 값의 수."""
    if not any(t._view.marks for t in ev.ok):  # type: ignore[attr-defined]
        return {}
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in ev.ok:
        sv: SheetView = t._view  # type: ignore[attr-defined]
        dims = [(k, j) for k, j in t.dims.items() if k in IDENTITY_FIELDS]
        for pos, (_, kind, cells) in enumerate(sv.rows):
            if kind not in ("data", "subtotal"):
                continue
            marks = sv.marks.get(pos, {})
            for k, j in dims:
                found = seen[(k, dim_value(cells[j]))]
                if (mark := marks.get(j, "")) not in found:
                    found.append(mark)
    for (k, shown), found in seen.items():
        if len(found) > 1:
            for n, mark in enumerate(found, 1):
                ev.labels[(k, shown, mark)] = f"{shown} ({MASKED_NO} {n})"
    return {key: len(found) for key, found in seen.items() if len(found) > 1}


def _read_rows(ev: Evaluation) -> None:
    req = ev.req
    f = req.filters
    wanted = {k: {dim_value(v) for v in vals} for k, vals in f.dims.items() if vals}
    shared = _value_labels(ev)
    if shared:
        ev.warnings.append(
            f"가린 뒤 표시가 같아진 서로 다른 구분 값이 있어 '({MASKED_NO} n)' 번호를 붙여 따로 판정·계산했습니다 "
            f"({len(shared)}개 표시, 값 {sum(shared.values())}개) — 원문은 보여 주지 않고, "
            "번호는 이 결과 안에서만 같은 값을 가리킵니다"
        )
    for k, vals in wanted.items():
        for v in sorted(vals):
            if (k, v) in shared:
                ev.problems.append(
                    f"필터 '{label_of(k)}: {v}'은(는) 가린 뒤 같아진 서로 다른 값 {shared[(k, v)]}개를 가리킵니다 — "
                    f"구분할 수 없어 계산하지 않습니다. 목록에서 '({MASKED_NO} n)' 번호가 붙은 값으로 고르세요"
                )
    values: dict[str, set[str]] = defaultdict(set)
    periods: set[str] = set()
    clashes: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for t in ev.ok:
        sv: SheetView = t._view  # type: ignore[attr-defined]
        col = int(t.column_id[1:]) if t.column_id else None
        for pos, (row_no, kind, cells) in enumerate(sv.rows):
            if kind not in ("data", "subtotal"):
                continue
            dims = {k: dim_value(cells[j]) for k, j in t.dims.items() if k in IDENTITY_FIELDS}
            period, day, clash = _row_period(t, cells)
            rec = RowRec(t.index, row_no, period, dims, day=day)
            if raw := sv.marks.get(pos):
                rec.marks = {k: raw[j] for k, j in t.dims.items() if k in IDENTITY_FIELDS and j in raw}
            rec.keys = {k: ev.labels.get((k, v, rec.marks.get(k, "")), v) for k, v in dims.items()}
            t.rows.append(rec)
            # 측정 열은 사용자가 확정한 수량 열 — 숫자로 읽을 수 있는 글자는 수량으로 (캐시의 셀은 바꾸지 않음)
            value = quantity_value(cells[col]) if col is not None else None
            measured = classify_value(value) if col is not None else (None, "blank", None)
            rec.user_out = _outside_conditions(rec.keys, period, measured[0], wanted, f)
            if kind == "subtotal":
                rec.reason = "subtotal"
                continue
            if any(compact(v) in TOTAL_LIKE for v in dims.values()):
                rec.reason = "total_like"
                continue
            if clash:
                rec.reason = "period_conflict"
                clashes[t.index].append((row_no, clash))
                continue
            if rec.period is None:
                rec.reason = "period_unknown"
                continue
            if dims and not any(compact(v) for v in dims.values()):
                rec.reason = "no_identity"
                continue
            periods.add(rec.period)
            for k, v in rec.keys.items():
                if v and len(values[k]) < MAX_FILTER_VALUES:
                    values[k].add(v)
            if any(rec.keys.get(k, "") not in vals for k, vals in wanted.items()):
                rec.reason = "filtered"
                continue
            if (f.period_from and rec.period < f.period_from) or (
                f.period_to and rec.period[: len(f.period_to)] > f.period_to
            ):
                rec.reason = "filtered"
                continue
            rec.level = region_level(region_key(dims)) if any(k in dims for k in REGION_FIELDS) else None
            rec.value, rec.reason, rec.shown = measured
            if rec.value is not None and col is not None:
                rec.exact = exact_number(value)
            if rec.reason is None and rec.user_out:
                # 숫자 범위도 지역·기간 필터와 같은 시점에 적용한다 — 이후 반복·중복·상위·하위 판정과 계산이
                # 같은 행 범위를 쓰게 (뒤늦게 적용하면 사용자가 뺀 행이 비교를 바꾸고, 비교 뒤에 빠진다)
                rec.reason = "value_range"
    ev.periods = sorted(periods)
    ev.filter_values = {k: sorted(v) for k, v in values.items()}
    for t in ev.ok:
        if found := clashes.get(t.index):
            row, text = found[0]
            ev.problems.append(
                f"#{t.file_id} {t.sheet}: 기간 열(연도·월·기준일)이 서로 맞지 않는 행 {len(found)}개 "
                f"(예: {row}행 {text}) — 어느 기간인지 추정하지 않습니다. 매핑이나 자료를 확인하세요"
            )
    _repeated(ev)  # 순서: 한 시트 안의 반복 → 자료 사이의 중복 → 기준일 → 상위·하위 지역 (중복·충돌을 먼저 드러낸다)
    _overlaps(ev)
    _base_dates(ev)  # 범위(_date_scope)가 자동 제외와 무관해 순서에 기대지 않지만, 근거를 먼저 확인한다
    _region_levels(ev)
    _formula_warnings(ev)


def _formula_warnings(ev: Evaluation) -> None:
    """고른 항목이 합계 항목(발신건수합계 등)이면 시트마다 산식(구성 항목의 합)과 맞는지 보고, 다르면 근거와 함께
    알린다 (app.formulas). 계산은 막지 않는다 — 확정한 열의 값 그대로 계산하되, 산식이 다른 합계를 함께 더하지 않게
    확인을 돕는다. 구성 항목도 사용자가 확정한 시트만 본다 (분석은 확정한 매핑만 쓴다)."""
    if ev.req.field_key not in TOTAL_FORMULAS:
        return
    for t in ev.ok:
        if t.column_id is None:
            continue
        sv: SheetView = t._view  # type: ignore[attr-defined]
        columns = unique_columns((t.decisions.get(name), j) for j, name in enumerate(sv.columns))
        columns[ev.req.field_key] = int(t.column_id[1:])  # 이 결과가 계산하는 열
        rows = [(row_no, cells) for row_no, kind, cells in sv.rows if kind == "data"]
        for check in check_totals(rows, columns):
            if check.total != ev.req.field_key or not check.differ:
                continue
            parts = " + ".join(label_of(p) for p in TOTAL_FORMULAS[check.total])
            text = (
                f"'{label_of(check.total)}' #{t.file_id} {t.sheet}: '{t.column}' 열이 {parts}의 합과 "
                f"{len(check.differ)}/{check.checked}행에서 다릅니다 (차이 합계 {_fmt(check.difference)})"
            )
            if check.alternative:
                alt, matched = check.alternative
                text += (
                    f" — {' + '.join(label_of(p) for p in TOTAL_FORMULAS[alt])}(으)로는 {matched}행이 맞습니다. "
                    f"산식이 다른 합계일 수 있으니 '{label_of(alt)}'(으)로 확정할지 확인하세요"
                )
            else:
                text += " — 입력 오류로 단정하지 말고 원본의 산식·포함 범위를 확인하세요"
            ev.warnings.append(text + " (산식이 다른 합계를 함께 더하면 뜻이 섞입니다)")


def _candidate(r: RowRec) -> bool:
    """반복·중복·상위·하위 판단에 쓰는 행: 사용자 조건 안에서 구조·조건으로 빠지지 않은 행
    (값의 결측 여부와 관계없이 — 결측은 0으로 보지 않고 '확인할 수 없음'으로 판정에 남는다)."""
    if r.user_out:
        return False
    return r.reason is None or (REASONS[r.reason][1] and r.reason not in UNPLACED)


def other_key(r: RowRec) -> tuple[tuple[str, str, str], ...]:
    """지역 말고 행이 무엇인지 정하는 값 (기관·부서·차종·용도·연료). 확정한 항목 이름과 값으로 비교한다
    (가림으로 바뀐 값은 원문 구분 표지까지 — 가린 표시가 같아도 원문이 다르면 다른 대상)."""
    return tuple((k, compact(r.dims[k]), r.marks.get(k, "")) for k in OTHER_IDENTITY if k in r.dims)


def target_key(r: RowRec) -> tuple[Any, ...]:
    """같은 대상인지 비교하는 키: 기간 + 지역(매핑 구조와 관계없는 행정구역 키) + 그 밖의 구분.
    한 시트 안의 반복과 자료 사이의 중복을 같은 키로 본다. 지역 칸에 가림으로 바뀐 값이 있으면 그 원문 구분 표지를
    더한다 (지역 이름이든 지역 코드 칸이든 — 가린 표시가 같아도 원문이 다르면 다른 대상)."""
    region = region_key(r.dims) if any(k in r.dims for k in REGION_FIELDS) else None
    if region is not None and any(k in r.marks for k in REGION_FIELDS):
        region = (*region, tuple(r.marks.get(k, "") for k in REGION_FIELDS))
    return (r.period, region, other_key(r))


def _target_text(r: RowRec) -> str:
    return " · ".join(v for v in r.keys.values() if v) or "(구분 없음)"


def _value_of(r: RowRec) -> float | str | None:
    return r.value if r.value is not None else r.shown


def _by_key(t: TargetEval) -> dict[tuple[Any, ...], list[RowRec]]:
    by: dict[tuple[Any, ...], list[RowRec]] = defaultdict(list)
    for r in t.rows:
        if _candidate(r):
            by[target_key(r)].append(r)
    return by


def _repeated(ev: Evaluation) -> None:
    """한 시트 안에서 같은 대상(기간·지역·기관·구분)의 행이 반복되면, 중복된 통계 행인지
    서로 다른 기록(거래·발생 건)인지 표만으로는 알 수 없다.
    사용자가 '서로 다른 기록'이라고 확인하기 전에는 계산하지 않는다 (임의로 합치거나 지우지 않음)."""
    for t in ev.ok:
        rep = [rows for rows in _by_key(t).values() if len(rows) > 1]
        if not rep:
            continue
        same = sum(1 for rows in rep if len({(x.value, x.shown) for x in rows}) == 1)
        t.repeated = {
            "keys": len(rep),
            "rows": sum(len(rows) for rows in rep),
            "same_value": same,
            "different_value": len(rep) - same,
            "samples": [
                {
                    "period": rows[0].period,
                    "target": _target_text(rows[0]),
                    "rows": [x.row for x in rows[:5]],
                    "values": [_value_of(x) for x in rows[:5]],
                }
                for rows in rep[:5]
            ],
        }
        if t.records:
            continue  # 사용자가 서로 다른 기록이라고 확인함 → 모두 더한다 (확인 사실·근거는 결과 조건에 남김)
        t.status = "needs_input"
        unused = _unused_identity(t)
        t.reasons.append(
            f"같은 대상(기간·지역·기관·구분)의 행이 반복됩니다 — {len(rep)}개 대상 {t.repeated['rows']}행 "
            f"(값 같음 {same}, 다름 {len(rep) - same}). "
            "중복된 통계인지 서로 다른 기록인지 알 수 없어 계산하지 않습니다 — "
            "서로 다른 기록이면 '반복 행 모두 더함'을 고르고, 아니면 자료나 구분 열 매핑을 확인하세요"
            + (
                f". 이 시트에서 대상 구분에 쓰지 않은 열: {', '.join(unused)} — 그 항목으로 확정하면 따로 판정합니다"
                if unused
                else ""
            )
        )


def _unused_identity(t: TargetEval) -> list[str]:
    """대상 구분에 쓰지 않은 구분 열 — 이름이 사전의 구분 항목(조직코드 등)과 정확히 같은데 그 항목으로 확정하지
    않은 열. 반복 행 안내에 적어 그 열을 확정하면 따로 판정된다는 것을 알린다 (그 열을 자동으로 구분에 쓰지는 않음)."""
    sv: SheetView = t._view  # type: ignore[attr-defined]
    found = []
    for col in sv.columns:
        key = _identity_names().get(normalize(col))
        if key and key not in t.dims and t.decisions.get(col) != key:
            found.append(f"'{mask_text(col)}' 열 → '{label_of(key)}'")
    return found


def records_text(t: TargetEval) -> str:
    """'반복 행은 서로 다른 기록' 확인과 그 근거 (반복된 대상·행 수·값이 같은 대상 수) — 결과 조건·Excel에 남긴다."""
    rep = t.repeated or {}
    return (
        f"#{t.file_id} {t.sheet} (같은 대상 {rep.get('keys', 0)}개 {rep.get('rows', 0)}행 — "
        f"값 같음 {rep.get('same_value', 0)}, 다름 {rep.get('different_value', 0)})"
    )


def _overlaps(ev: Evaluation) -> None:
    """같은 대상이 다른 자료에도 있으면 중복 의심. 반복 행을 서로 다른 기록으로 확인한 시트도 대상 키로 똑같이 비교한다
    (행이 반복된다고 비교를 느슨하게 하지 않음). 자동으로 고르지 않고 실행을 막는다."""
    # 반복 행 확인을 기다리는 시트도 비교한다 (확인 뒤에야 다른 자료와의 중복이 드러나지 않게 한 번에 보여 줌)
    keyed = [(t, _by_key(t)) for t in ev.targets if t.status == "ok" or (t.status == "needs_input" and t.repeated)]
    for i, (a, ka) in enumerate(keyed):
        for b, kb in keyed[i + 1 :]:
            common = [k for k in ka if k in kb]
            if not common:
                continue
            same = sum(
                1 for k in common if sorted(map(str, map(_value_of, ka[k]))) == sorted(map(str, map(_value_of, kb[k])))
            )
            samples = []
            for k in common[:5]:
                ra, rb = ka[k][0], kb[k][0]
                samples.append(
                    {
                        "period": ra.period,
                        "target": _target_text(ra),
                        "a": {"row": ra.row, "value": _value_of(ra), "rows": len(ka[k])},
                        "b": {"row": rb.row, "value": _value_of(rb), "rows": len(kb[k])},
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


def _region_levels(ev: Evaluation) -> None:
    """상위·하위 지역 행의 중복 방지. 같은 기간·같은 구분(기관·차종 등) 안에서만 비교한다.

    - 하위 지역 합계가 상위 지역 값과 같으면 상위 행을 중복으로 뺀다 (하위 지역이 모두 있다고 확인됨).
    - 다르거나 값이 빠져 확인할 수 없으면(하위 지역 일부만 있음 등) 사용자가 집계 수준을 고른다:
      'lower' = 상위 행을 빼고 있는 하위 지역만, 'upper' = 하위 행을 빼고 상위 지역만. 고르지 않으면 실행하지 않는다.
    """
    choice = ev.req.region_level
    stock = ev.req.field_key in STOCK_FIELDS
    ctx: dict[tuple[Any, ...], list[RowRec]] = defaultdict(list)
    for t in ev.ok:
        for r in t.rows:
            if _candidate(r) and r.level is not None:
                ctx[(r.period, other_key(r))].append(r)
    auto = mismatch = dropped = skipped = 0
    for (period, _), rows in sorted(ctx.items(), key=lambda kv: str(kv[0])):
        sidos = [r for r in rows if r.level and r.level[0] == "sido"]
        sigs = [r for r in rows if r.level and r.level[0] == "sigungu"]
        nations = [r for r in rows if r.level and r.level[0] == "nation"]
        sido_codes = {r.level[1] for r in sidos if r.level}
        pairs: list[tuple[list[RowRec], list[RowRec], list[RowRec]]] = []  # (상위, 합계를 비교할 하위, 뺄 하위)
        for code in sorted(c for c in sido_codes if c):
            lows = [r for r in sigs if r.level and r.level[1] == code]
            if lows:
                pairs.append(([r for r in sidos if r.level and r.level[1] == code], lows, lows))
        if nations and (sidos or sigs):
            orphans = [r for r in sigs if r.level and r.level[1] not in sido_codes]
            pairs.append((nations, sidos + orphans, sidos + sigs))
        # 모든 쌍을 먼저 판정한 뒤 뺀다 (시도 행을 먼저 빼면 같은 기간의 '전국' 비교에서 그 값이 사라지므로)
        judged = [(ups, lows, below, *_level_match(ups, lows, stock)) for ups, lows, below in pairs]
        for ups, lows, below, match, dated_apart, lower_sum in judged:
            if dated_apart:
                # 기준일이 다른 시점 값은 상위·하위 중복으로 판정하지 않는다 (뺄 행을 정하지 않음).
                # 판정 대상(_candidate)은 기준일 검사 범위(_date_scope) 안이므로 _base_dates가 실행을 막는다
                # (아래에서 한 번 더 확인)
                skipped += 1
                continue
            if not match:
                mismatch += 1
                if len(ev.level_conflicts) < 20:
                    up = ups[0]
                    missing = sum(1 for x in lows if x.exact is None)
                    exact_diff = len(ups) == 1 and up.exact is not None and lower_sum is not None and not missing
                    ev.level_conflicts.append(
                        {
                            "period": period,
                            "upper": {
                                "target": _target_text(up),
                                "file_id": ev.targets[up.t].file_id,
                                "row": up.row,
                                "value": _plain(up.exact) if up.exact is not None else _value_of(up),
                                "rows": len(ups),
                            },
                            "lower_count": len(lows),
                            "lower_sum": _plain(lower_sum),
                            "lower_missing": missing,
                            # 상위 값 − 하위 합계 (값이 빠졌거나 상위 행이 여럿이면 계산하지 않음)
                            "difference": _plain(up.exact - lower_sum) if exact_diff else None,  # type: ignore[operator]
                        }
                    )
            if choice == "upper":
                for x in below:
                    if x.reason != "upper_region":
                        x.reason, x.value, x.shown, x.exact = "lower_region", None, None, None
                        dropped += 1
            elif choice == "lower" or match:
                for x in ups:
                    if x.reason != "upper_region":
                        x.reason, x.value, x.shown, x.exact = "upper_region", None, None, None
                        dropped += 1
                        if match and choice is None:
                            auto += 1
    ev.level_mismatch = mismatch
    if skipped and not ev.date_conflict:
        # 비교를 건너뛴 상위·하위 행이 함께 합계에 들어가지 않도록, 막을 근거가 없으면 여기서 막는다 (안전장치)
        ev.problems.append(
            f"기준일이 다른 상위·하위 지역 행 {skipped}곳은 중복 여부를 판정할 수 없습니다 — "
            "기준일이 같은 자료만 고르세요"
        )
    if mismatch and choice is None:
        ev.problems.append(
            "상위·하위 지역 행이 함께 있는데 하위 지역 합계가 상위 지역 값과 다르거나 확인할 수 없는 곳이 "
            f"{mismatch}곳 있습니다 (하위 지역이 일부만 있거나 값이 빠졌을 수 있음) — "
            "집계 수준을 고르세요: 하위 지역만 / 상위 지역만"
        )
    elif choice == "lower" and dropped:
        note = (
            f" — 하위 지역 합계가 상위 지역 값과 다른 곳 {mismatch}곳은 있는 하위 지역만의 합입니다" if mismatch else ""
        )
        ev.warnings.append(f"집계 수준 '하위 지역만': 상위 지역 행 {dropped}개를 뺐습니다{note}")
    elif choice == "upper" and dropped:
        ev.warnings.append(f"집계 수준 '상위 지역만': 상위 지역과 함께 있는 하위 지역 행 {dropped}개를 뺐습니다")
    elif auto:
        ev.warnings.append(
            f"하위 지역 합계가 상위 지역 값과 같은 상위 지역(시도·전국) 행 {auto}개를 중복 방지로 뺐습니다 "
            "(같은 기간·같은 구분 안에서 비교) — 제외 목록에서 확인하세요"
        )


def _level_match(ups: list[RowRec], lows: list[RowRec], stock: bool) -> tuple[bool, bool, Decimal | None]:
    """상위·하위 쌍 판정 → (정확히 일치, 기준일이 서로 다름, 하위 합계).

    일치는 정확한 값(exact_number)의 합이 상위 값과 같을 때만이다 (허용 오차 없음). 상위 행이 여럿이거나 값이 하나라도
    빠졌으면 확인할 수 없으므로 일치가 아니다. 시점 값에서 쌍 안의 기준일이 둘 이상이면 비교하지 않는다.
    """
    dated_apart = stock and len({x.day for x in (*ups, *lows) if x.day}) > 1
    valid = [x.exact for x in lows if x.exact is not None]
    with decimal.localcontext() as dc:
        dc.prec = 80  # 원 단위 큰 금액·긴 소수도 반올림 없이 더한다
        lower_sum = sum(valid, Decimal(0)) if valid else None
        match = (
            not dated_apart
            and len(ups) == 1
            and ups[0].exact is not None
            and len(valid) == len(lows)
            and lower_sum == ups[0].exact
        )
    return match, dated_apart, lower_sum


def _user_filtered(r: RowRec) -> bool:
    """사용자가 조건(지역·기관·기간 필터, 숫자 범위)으로 직접 뺀 행. 시스템이 자동으로 뺀 행(합계·소계·상위 지역 등)과
    구분하되, 그런 행도 사용자 조건 밖이면 여기에 든다 (user_out은 행을 읽을 때 사유와 따로 정함)."""
    return r.user_out or r.reason in ("filtered", "value_range")


def _date_scope(ev: Evaluation) -> list[RowRec]:
    """기준일 검사에 쓰는 행: 고른 자료에서 어디에 속하는지 알 수 있는 행 전부 중 사용자가 조건으로 뺀 행만 제외한다.
    상위·하위 지역 자동 제외, 합계·소계 행, 결측 등 시스템이 뺀 행도 사용자 조건 안이면 넣는다
    — 자동 제외가 충돌의 근거를 지우지 않게."""
    return [r for t in ev.ok for r in t.rows if r.period and r.reason not in UNPLACED and not _user_filtered(r)]


def _base_dates(ev: Evaluation) -> None:
    """시점 값(인구·보유량 등)은 같은 달이라도 기준일이 다르면 함께 계산하지 않는다 (일 단위 분석은 지원하지 않음)."""
    if ev.req.field_key not in STOCK_FIELDS:
        return
    days: dict[str, set[str]] = defaultdict(set)
    undated: set[str] = set()
    for r in _date_scope(ev):
        (days[r.period].add(r.day) if r.day else undated.add(r.period))
    for period, found in sorted(days.items()):
        if len(found) > 1:
            ev.date_conflict = True
            ev.problems.append(
                f"같은 기간 {period}에 기준일이 서로 다른 자료가 있습니다 ({', '.join(sorted(found)[:5])}) — "
                "시점 값은 기준일이 같을 때만 함께 계산합니다 (일 단위 분석은 지원하지 않음). "
                "기준일이 같은 자료만 고르세요"
            )
        else:
            ev.base_dates[period] = next(iter(found))
            if period in undated:
                ev.warnings.append(
                    f"기간 {period}: 기준일({ev.base_dates[period]})이 적힌 자료와 날짜 없이 월만 적힌 자료가 "
                    "함께 있습니다 — 같은 기준일인지 확인하세요"
                )


def fingerprint(ev: Evaluation) -> str:
    """미리보기 뒤 조건·파일·매핑이 바뀌었는지 확인하는 값
    (요청 조건 전체 + 파일 내용 해시 + 그 시트의 확정 매핑 전체 + 판정 규칙)."""
    request = ev.req.model_dump(include=set(AnalysisIn.model_fields))
    payload = [POLICY, request] + [
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
        "source_columns": source_columns(ev),
        "targets": [t.out() for t in ev.targets],
        "problems": ev.problems,
        "warnings": ev.warnings,
        "overlaps": ev.overlaps,
        "periods": ev.periods,
        "filter_values": ev.filter_values,
        "available_groups": ev.available_groups,
        "region_levels": {
            "choice": ev.req.region_level,
            "mismatch": ev.level_mismatch,
            "conflicts": ev.level_conflicts,
        },
        "base_dates": ev.base_dates,
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
    labels = bin_labels(req.bins) if req.bins is not None else []

    def key_of(r: RowRec) -> tuple[str, ...]:
        # 표시(keys)로 묶는다 — 가린 뒤 같아진 서로 다른 값은 '(가림 구분 n)'으로 이미 다르다 (먼저 합친 뒤 나누지 않음)
        return tuple((r.period or "") if k == "period" else (r.keys.get(k) or "(값 없음)") for k in group_fields)

    included: list[RowRec] = []
    excluded: list[RowRec] = []
    for t in ev.ok:
        for r in t.rows:
            if r.reason is None:
                included.append(r)
                g = groups.setdefault(key_of(r), {"values": [], "problem": 0, "files": set()})
                g["values"].append(r.exact if r.exact is not None else exact_number(r.value))
                g["files"].add(t.file_id)
            else:
                excluded.append(r)
                if REASONS[r.reason][1] and r.reason not in UNPLACED:
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
        # 정확한 수로 더한다 (C-02): 정수는 2^53을 넘어도 끝자리까지, 소수는 이진 표현 오차 없이 (0.1 + 0.2 = 0.3).
        # 평균은 정확한 합을 개수로 나눈 뒤 배정밀도로 (화면 자릿수만 줄이지 않는다 — docs/NUMBERS.md)
        total = sum(vals, Decimal(0)) if n else None
        row["sum"] = _plain(total)
        row["mean"] = float(total / n) if total is not None else None
        row["min"] = _plain(min(vals)) if n else None
        row["max"] = _plain(max(vals)) if n else None
        if req.bins is not None:
            # 구간은 번호로 정한다 (보여 주는 이름이 같아도 다른 구간끼리 합치지 않음)
            x = row[req.bins.on]
            index = None if x is None else next((i for i, b in enumerate(req.bins.ranges) if _contains(b, x)), None)
            row["bin_index"] = index
            row["bin"] = NO_VALUE if x is None else (labels[index] if index is not None else UNCLASSIFIED)
        out_groups.append(row)
    bin_counts = None
    if req.bins is not None:
        # 값이 없는 그룹은 "none", 어느 구간에도 들지 않으면 None, 그 밖에는 구간 번호
        c = Counter("none" if g[req.bins.on] is None else g["bin_index"] for g in out_groups)
        bin_counts = [{"index": i, "label": lab, "groups": c.get(i, 0)} for i, lab in enumerate(labels)]
        bin_counts += [
            {"index": None, "label": UNCLASSIFIED, "groups": c.get(None, 0)},
            {"index": None, "label": NO_VALUE, "groups": c.get("none", 0)},
        ]
    reason_counts = Counter(r.reason for r in excluded)
    problem_rows = sum(n for k, n in reason_counts.items() if REASONS[k][1])

    def ref(r: RowRec) -> dict[str, Any]:
        t = targets[r.t]
        return {"file_id": t.file_id, "file_name": t.file_name, "sheet": t.sheet, "row": r.row, "period": r.period}

    detail = [
        {**ref(r), "keys": list(key_of(r)), "value": _plain(r.exact) if r.exact is not None else r.value}
        for r in included[:KEEP_ROWS]
    ]
    if any(_beyond_safe(g[a]) for g in out_groups for a in ("sum", "min", "max")) or any(
        _beyond_safe(d["value"]) for d in detail
    ):
        note = (
            f"절댓값이 {SAFE_INTEGER:,}(2^53)를 넘는 정수가 있습니다 — 계산은 정수로 정확히 했고 Excel에는 "
            "16자리 이상을 글자로 적어 자릿수를 지키지만, 화면(브라우저)은 끝자리를 정확히 보이지 못할 수 있습니다"
        )
        if note not in ev.warnings:
            ev.warnings.append(note)
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
        "region_level": req.region_level,
        "level_mismatch": ev.level_mismatch if req.region_level else 0,
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
            "ranges": [
                {**b.model_dump(), "label": lab} for b, lab in zip(req.bins.ranges, bin_labels(req.bins), strict=True)
            ],
        },
        "region_level": req.region_level,
        "level_conflicts": ev.level_conflicts,  # 하위 합계가 상위 값과 다르거나 확인할 수 없는 곳 (차이 포함)
        "base_dates": ev.base_dates,
        "records": [records_text(t) for t in ev.ok if t.records and t.repeated],
        "mean_denominator": "그룹마다 유효한 숫자의 개수 (빈칸·비공개·숫자가 아닌 값은 분모에 넣지 않음)",
        "reason_labels": {k: v[0] for k, v in REASONS.items()},
        "warnings": ev.warnings,
        "source_columns": source_columns(ev),
    }


def source_columns(ev: Evaluation) -> list[dict[str, Any]]:
    """계산에 쓴 원본 열 — 표준 항목 이름('건수')만으로는 어느 지표('발신Mail건수')를 계산했는지 알 수 없어 함께 남긴다.

    [{"column": 가린 열 이름, "sheets": 그 열을 쓴 시트 수, "files": 파일 번호}] — 자료 순서에서 처음 나온 순서.
    가린 뒤 같아진 서로 다른 열 이름은 '(가림 구분 n)'으로 나눈다 (원래 이름은 내보내지 않음).
    이름이 서로 다르면 같은 지표인지 사용자가 확인해야 한다 — 모두 이 표준 항목으로 사용자가 확정한 열이다.
    """
    seen: dict[str, list[str]] = defaultdict(list)  # 가린 이름 → 서로 다른 원래 이름 (공백·전각 차이는 같게)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for t in ev.ok:
        if t.column_id is None or t.column is None:
            continue
        raw = compact(t._view.columns[int(t.column_id[1:])])  # type: ignore[attr-defined]
        if raw not in seen[t.column]:
            seen[t.column].append(raw)
        row = rows.setdefault((t.column, raw), {"column": t.column, "sheets": 0, "files": []})
        row["sheets"] += 1
        if t.file_id not in row["files"]:
            row["files"].append(t.file_id)
    for (shown, raw), row in rows.items():
        if len(seen[shown]) > 1:
            row["column"] = f"{shown} ({MASKED_NO} {seen[shown].index(raw) + 1})"
    return list(rows.values())


def inputs(ev: Evaluation) -> list[dict[str, Any]]:
    return [
        {**t.out(), "sha256": t.sha256, "decisions": sorted([mask_text(k), v] for k, v in t.decisions.items())}
        for t in ev.targets
    ]
