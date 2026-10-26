"""열 이름 → 표준 사전 매핑 엔진.

1) 표준 사전 정확 일치 → 2) rapidfuzz + bge-m3 임베딩 유사도 → 3) 1·2단계 미해결 열만 로컬 LLM(Ollama)
날짜 필터: 샘플이 모두 날짜·기간 형식이면 지역·용어 판별 없이 날짜 항목(기준일자·월·연도)으로 정한다.
  사용자가 이 파일에서 확정한 열, 사용자의 학습 별칭, 열 이름이 이미 날짜 항목인 경우는 그대로 둔다.
LLM에는 미해결 열 하나씩, 열 이름과 샘플 값 5개만 보낸다 (전체 데이터를 넘기지 않는다).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from rapidfuzz import fuzz

from app import admin_codes, llm, ollama
from app.glossary import parse_date_value
from app.mail_fields import MAIL_MB_FIELDS
from app.parsing import Cell
from app.pii import mask_text

logger = logging.getLogger(__name__)

DICTIONARY_PATH = Path(__file__).with_name("standard_dictionary.json")

# 유사도 점수(0~1) 기준. 높음 → 자동 적용, 중간 → 사용자 확인, 낮음 → LLM으로 넘김
# bge-m3 실측(2026-10): 의미가 같은 열 0.82~1.0, 사전에 없는 열 0.61~0.75 → 가중 평균 후 아래 기준 사용
AUTO_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.75
EMBEDDING_WEIGHT = 0.6
AUTO_MARGIN = 0.05  # 1·2위 후보 점수 차가 이보다 작으면 자동 적용하지 않는다
TOP_CANDIDATES = 3
SAMPLE_SIZE = 5
MAX_LLM_COLUMNS = 15  # 요청 하나에서 LLM으로 넘기는 열 수 상한

Status = Literal["auto", "review", "unresolved", "confirmed"]
Method = Literal["exact", "standard", "learned", "exact_part", "similarity", "pattern", "llm", "user", "none"]
EngineStatus = Literal["ok", "unavailable", "skipped"]

EmbedFn = Callable[[list[str]], list[list[float]]]
LlmFn = Callable[[str, str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class StandardField:
    key: str
    label: str
    description: str
    aliases: tuple[str, ...]
    modifier: bool = False  # 관용·자가용처럼 다른 항목의 구분으로 붙는 항목 ('승용_관용' → 승용차 (관용))

    @property
    def texts(self) -> tuple[str, ...]:
        return (self.label, *self.aliases)


@dataclass
class Candidate:
    key: str
    label: str
    score: float
    fuzzy: float
    embedding: float | None


@dataclass
class ColumnMapping:
    column: str
    samples: list[Cell]
    status: Status = "unresolved"
    method: Method = "none"
    field_key: str | None = None
    field_label: str | None = None
    score: float | None = None
    qualifier: str | None = None  # 열 이름에서 떼어낸 연도 등 (예: '인구_2025' → '2025')
    reason: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    org_note: str | None = (
        None  # 기관코드 제안·후보 검사 결과 (기각·보류 사유 또는 수용 근거, 없으면 None) — reason에도 있음
    )


@dataclass
class MappingResult:
    columns: list[ColumnMapping]
    embedding: EngineStatus
    llm: EngineStatus


# ---------------------------------------------------------------- 사전


def load_dictionary(path: Path = DICTIONARY_PATH) -> list[StandardField]:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = [
        StandardField(
            f["key"], f["label"], f.get("description", ""), tuple(f.get("aliases", [])), bool(f.get("modifier"))
        )
        for f in data["fields"]
    ]
    keys = [f.key for f in fields]
    if len(keys) != len(set(keys)):
        raise ValueError("표준 사전에 중복된 key가 있습니다.")
    return fields


@lru_cache(maxsize=1)
def dictionary_digest() -> str:
    """표준 사전 파일의 지문 — 검토가 검사할 때와 지금 사전이 같은지 본다 (배포로 사전이 바뀌면 다시 검사)."""
    return hashlib.sha256(DICTIONARY_PATH.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- 정규화

_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)\s*년?")
_PAREN = re.compile(r"[(\[（［【][^)\]）］】]*[)\]）］】]")
_SEPARATORS = re.compile(r"[\s·ㆍ・.\-_/]+")


def normalize(text: str) -> str:
    """정확 일치용 키: NFKC, 괄호(단위) 제거, 소문자, 공백·가운뎃점·구분자 제거."""
    text = unicodedata.normalize("NFKC", text)
    text = _PAREN.sub("", text)
    return _SEPARATORS.sub("", text).lower()


@dataclass(frozen=True)
class ColumnName:
    raw: str
    primary: str  # 연도·단위를 뗀 전체 이름의 정규화 키
    parts: tuple[str, ...]  # 2단 헤더 'A_B'의 각 부분 (뒤쪽 = 더 구체적)
    display: str  # 임베딩/LLM에 보낼 사람이 읽는 형태
    qualifier: str | None
    raw_parts: tuple[str, ...] = ()  # parts의 원래 글자 (구분 표시용)
    bracketed: tuple[str, ...] = ()  # 괄호 안 글자 ('승용(관용)' → '관용'). 사전 단어일 때만 구분으로 쓴다


def split_column_name(column: str) -> ColumnName:
    text = unicodedata.normalize("NFKC", column).strip()
    years = [m.group(0).replace("년", "").strip() for m in _YEAR.finditer(text)]
    cleaned = _PAREN.sub("", _YEAR.sub("", text))
    raw_parts = [p for p in re.split(r"[_/]", cleaned) if normalize(p)]
    parts = tuple(normalize(p) for p in raw_parts) if len(raw_parts) > 1 else ()
    display = " ".join(p.strip() for p in raw_parts) or text
    bracketed = tuple(m.group(0)[1:-1].strip() for m in _PAREN.finditer(_YEAR.sub("", text)))
    shown = tuple(p.strip() for p in raw_parts) if parts else ()
    return ColumnName(column, normalize(cleaned), parts, display, ",".join(years) or None, shown, bracketed)


# 2단 헤더에서 구분으로만 쓰이는 합계 단어 ('승용_계' → 승용차 (계)). 이 단어만으로 된 이름('총계_계')은 합계 항목
TOTAL_WORDS = frozenset({"계", "소계", "합계", "총계", "총합계", "전체"})
TOTAL_KEY = "total"
# '화물'·'특수'처럼 차량 외 통계(물동량 등)에도 쓰이는 짧은 이름 → 같은 표에 차량 관련 열이 있을 때만 자동 적용
CONTEXT_TERMS: dict[str, frozenset[str]] = {
    "truck": frozenset({"화물"}),
    "special_vehicle": frozenset({"특수"}),
}
VEHICLE_CONTEXT = ("승용", "승합", "자동차", "차량", "이륜", "차종")
DATE_FIELDS = frozenset({"base_date", "month", "year"})


def date_field_for(samples: list[Cell]) -> str | None:
    """샘플이 모두 날짜·기간 글자면 맞는 날짜 항목 key: '2026년' → year, '2026-08' → month, 그 밖 → base_date.

    숫자 셀(2024 등)은 수량과 헷갈리므로 보지 않고, 8자리 숫자 글자('20260831')는 코드와 겹쳐 날짜로 보지 않는다.
    """
    values = [s for s in samples if s is not None]
    if not values or not all(isinstance(v, str) for v in values):
        return None
    texts = [str(v).strip() for v in values]
    if any(t.isdigit() for t in texts):
        return None
    parsed = [parse_date_value(t) for t in texts]  # 실제로 없는 월·일이면 날짜가 아니다 (기간 검사와 같은 규칙)
    if any(p is None for p in parsed):
        return None
    kinds = {p[0] for p in parsed if p is not None}
    if kinds == {"year"}:
        return "year"
    if kinds == {"month"}:
        return "month"
    return "base_date"


# ---------------------------------------------------------------- 유사도


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def fuzzy_score(name: ColumnName, text: str) -> float:
    target = normalize(text)
    if not name.primary or not target:
        return 0.0
    return fuzz.ratio(name.primary, target) / 100


def combine(fuzzy: float, embedding: float | None) -> float:
    """두 유사도를 하나의 점수로. 임베딩이 없으면 문자열 유사도만 쓴다."""
    if embedding is None:
        return fuzzy
    return EMBEDDING_WEIGHT * embedding + (1 - EMBEDDING_WEIGHT) * fuzzy


# ---------------------------------------------------------------- LLM

_LLM_SYSTEM = (
    "너는 한국 정부·지자체 통계표의 열 이름을 표준 항목에 매핑하는 도우미다. "
    "열 이름과 샘플 값을 보고 의미가 같은 표준 항목 key 하나를 고른다. "
    "열 이름이 뜻하는 측정 대상이 표준 항목 설명과 같을 때만 고른다. "
    "샘플 값이 숫자라는 등 형식이 비슷하다는 이유만으로 고르지 않는다. "
    "단위가 다른 측정값(예: mm, ℃, ㎢, kg)은 금액·건수·인구 같은 항목이 아니다. "
    "열 이름이 '열3'처럼 의미가 없을 때만 샘플 값의 형식(코드, 날짜, 이름 등)으로 판단한다. "
    '가장 가까운 것을 억지로 고르지 말고, 확실하지 않거나 맞는 항목이 없으면 key를 "none"으로 한다. '
    "reason은 반드시 한국어로만 한 문장으로 쓰고 중국어·영어 문장을 섞지 않는다. 반드시 JSON으로만 답한다."
)


_DIGITS = re.compile(r"^\d+$")
_DATE = re.compile(r"^\d{4}[-./]\d{1,2}([-./]\d{1,2})?")


def sample_format(samples: list[Cell]) -> str:
    """샘플 값의 형식을 규칙으로 판별한 힌트. LLM이 숫자 형식만 보고 오판하는 것을 줄인다."""
    if not samples:
        return "알 수 없음"
    if all(isinstance(s, str) and _DIGITS.match(s) for s in samples):
        lengths = {len(str(s)) for s in samples}
        if len(lengths) == 1:
            return f"같은 길이({lengths.pop()}자리)의 숫자 문자열 — 코드 형식"
        return "숫자 문자열 — 코드 또는 번호 형식"
    if all(isinstance(s, str) and _DATE.match(s) for s in samples):
        return "날짜 형식"
    if all(isinstance(s, int | float) and not isinstance(s, bool) for s in samples):
        has_fraction = any(isinstance(s, float) for s in samples)
        return "소수가 있는 숫자 — 측정값·비율 형식" if has_fraction else "정수 — 수량·금액 형식"
    if all(isinstance(s, str) for s in samples):
        return "텍스트"
    return "혼합"


def _llm_prompt(fields: list[StandardField], name: ColumnName, samples: list[Cell]) -> str:
    options = "\n".join(f"- {f.key}: {f.label} ({f.description})" for f in fields)
    sample_text = ", ".join(json.dumps(s, ensure_ascii=False) for s in samples) or "(샘플 없음)"
    return (
        f"표준 항목 목록:\n{options}\n\n열 이름: {mask_text(name.raw)}\n"
        f"샘플 값({len(samples)}개): {sample_text}\n샘플 형식: {sample_format(samples)}"
    )


# ---------------------------------------------------------------- 샘플 패턴 (LLM 전 규칙 판별)

# 시도 2자리, 시군구 5자리, 읍면동 8자리, 행정동 10자리. 7자리는 행정기관(부처)코드와 겹쳐 제외한다.
REGION_CODE_LENGTHS = frozenset({2, 5, 8, 10})
REGION_CODE_AUTO_MIN_SAMPLES = 3  # 공식 코드표와 모두 일치하는 샘플이 이만큼 있으면 자동 적용
_GENERATED_NAME = re.compile(r"^열\d+$")


@dataclass(frozen=True)
class RegionCodeMatch:
    examples: list[str]  # 예: '11500 서울특별시 강서구'
    abolished: int  # 말소된 코드 수 (과거 통계)
    count: int
    length: int  # 코드 자릿수

    @property
    def strong(self) -> bool:
        # 2자리 시도 코드는 우연히 맞기 쉬워 자동 적용하지 않는다.
        return self.count >= REGION_CODE_AUTO_MIN_SAMPLES and self.length >= 5


def match_region_codes(column: str, samples: list[Cell]) -> RegionCodeMatch | None:
    """샘플이 모두 행정안전부 행정동 코드표(말소 포함)에 실제로 있는 코드면 일치 정보를 돌려준다.

    숫자형 셀은 금액 등과 헷갈리기 쉬우므로, 열 이름이 비어 있거나('열3') '코드'가 들어간 경우에만 인정한다.
    """
    values = [s for s in samples if s is not None]
    if len(values) < 2:
        return None
    all_text = all(isinstance(v, str) for v in values)
    if not all_text:
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return None
        if not (_GENERATED_NAME.match(column) or "코드" in column):
            return None
    texts = [str(v) for v in values]
    lengths = {len(t) for t in texts}
    if len(lengths) != 1 or not all(_DIGITS.match(t) for t in texts):
        return None
    length = lengths.pop()
    if length not in REGION_CODE_LENGTHS or (length == 2 and not all_text):
        return None
    table = admin_codes.load_table()
    areas = [table.lookup(t) for t in texts]
    if any(a is None for a in areas):
        return None
    found = [a for a in areas if a is not None]
    examples = [f"{t} {a.full_name}" for t, a in zip(texts[:2], found[:2], strict=True)]
    return RegionCodeMatch(examples, sum(1 for a in found if a.abolished), len(found), length)


def looks_like_region_code(column: str, samples: list[Cell]) -> bool:
    return match_region_codes(column, samples) is not None


# ---------------------------------------------------------------- 기관코드 제안·후보 검사

ORG_KEY = "org_code"
# 기관코드는 숫자·영문 대문자 7자리 (용어사전 code와 같은 형식). 값을 자르거나 0을 붙여 맞추지 않는다.
ORG_CODE_FORMAT = re.compile(r"^[0-9A-Z]{7}$")
# 기관코드가 아닌 다른 식별자를 뜻하는 열 이름 단어 (정규화한 이름에 들어 있으면 기관코드 제안을 쓰지 않음)
OTHER_ID_WORDS = (
    "관리번호", "일련번호", "순번", "발급기", "기기", "단말", "메뉴", "분류번호", "접수번호", "신청번호",
    "문서번호", "주문번호", "사업자", "등록번호", "차량번호", "카드번호", "계좌", "전화", "우편",
    "고객번호", "회원번호",
)  # fmt: skip
# 다른 식별자 단어가 이 단어를 가로지르면(일부만 겹치면) 그 자리의 단어로 보지 않는다
# ('발급기관'의 '발급기', '등기기관'의 '기기'). 단어가 들어 있다는 이유만으로 허용하지는 않는다.
ORG_WORDS = ("기관",)
# '기관' 뒤 글자가 이것으로 시작하면 '앞 단어의 기 + 관리…'로도 읽힌다 ('발급기관리코드' = '발급기' + '관리코드'?).
# 원래 이름에 경계가 없고 '기관' 앞에 같은 토막의 글자가 있으면 모호하다고 보고 '기관'으로 인정하지 않는다.
_ORG_AMBIGUOUS_NEXT = ("관리",)
# 검사하지 않는 판정: 사용자 확정·표준 사전·전사 표준·학습 별칭·날짜 패턴 (기존 우선순위 유지)
ORG_CHECK_EXEMPT = ("user", "exact", "exact_part", "standard", "learned", "pattern")
OrgVerdict = Literal["accept", "reject", "insufficient"]


@dataclass(frozen=True)
class KnownOrgCodes:
    """용어사전에서 확인되는 코드. 사전에 있다는 사실만 근거로 적고, 현재 유효한 코드인지는 판단하지 않는다.

    entry: 항목 코드 (현행·과거 항목 모두 — 존속 기간은 보지 않음)
    previous: 다른 항목의 개편 전 코드(previous_codes)로 적힌 코드 (그 자체로 과거 항목 코드이기도 할 수 있음)
    """

    entry: frozenset[str] = frozenset()
    previous: frozenset[str] = frozenset()


def _occurrences(text: str, word: str) -> list[int]:
    out, start = [], text.find(word)
    while start != -1:
        out.append(start)
        start = text.find(word, start + 1)
    return out


def _token_starts(column: str, name: str) -> set[int]:
    """원래 이름의 공백·구분자·괄호 경계를 정규화한 이름(name)의 위치로 옮긴 것 (각 토막이 시작하는 위치).
    정규화 결과와 글자 수가 맞지 않으면(드문 문자) 경계 정보 없이 처음 위치만 돌려준다."""
    text = unicodedata.normalize("NFKC", column)
    tokens = [t.lower() for t in _SEPARATORS.split(_PAREN.sub(" ", text)) if t]
    if "".join(tokens) != name:
        return {0}
    starts, pos = set(), 0
    for t in tokens:
        starts.add(pos)
        pos += len(t)
    return starts


def other_id_word(column: str) -> str | None:
    """열 이름(정규화)에서 다른 식별자를 뜻하는 단어. 단어가 진짜 '기관' 단어를 가로지르면 그 자리는 세지 않는다.

    '기관'이 진짜 단어가 아닌 경우 (이때는 가로지르는 다른 식별자 단어를 그대로 센다):
    - 원래 이름의 경계(공백·구분자·괄호)를 가로지름 ('발급기 관리코드' → '발급기' + '관리코드')
    - 그 '기관'의 뒤쪽 글자에서 시작해 밖으로 이어지는 다른 식별자 단어가 있음 ('기기관리번호' = '기기' + '관리번호')
    - 경계 없이 붙어 있어 '앞 단어의 기 + 관리…'로도 읽힘 ('발급기관리코드' — 모호하면 '기관'으로 보지 않음)
    """
    name = normalize(column)
    starts = _token_starts(column, name)
    hits = [(w, i, i + len(w)) for w in OTHER_ID_WORDS for i in _occurrences(name, w)]

    def genuine(k: int, e: int) -> bool:
        if any(k < s < e for s in starts):
            return False
        if any(k < i < e < j for _, i, j in hits):
            return False
        return k in starts or not name[k + 1 :].startswith(_ORG_AMBIGUOUS_NEXT)

    org_spans = [(k, k + len(o)) for o in ORG_WORDS for k in _occurrences(name, o) if genuine(k, k + len(o))]

    def cuts(i: int, j: int) -> bool:
        return any(k < j and i < e and not (i <= k and e <= j) for k, e in org_spans)

    for w in OTHER_ID_WORDS:  # 목록 순서대로 (사유에 적는 단어가 일정하도록)
        if any(not cuts(i, j) for ww, i, j in hits if ww == w):
            return w
    return None


def org_code_evidence(column: str, samples: list[Cell], known: KnownOrgCodes | None = None) -> tuple[OrgVerdict, str]:
    """'기관코드'를 제안·추천한 열의 근거 → (판정, 이유). 제안한 경로(유사도·LLM)와 확신도에 관계없이 같은 판정.

    - reject: 열 이름이 다른 식별자(관리번호·발급기·메뉴 등)를 뜻하거나, 값이 하나도 7자리 코드 형식이 아님
    - insufficient: 값 표본 부족(2개 미만), 형식이 섞임, 이름과 값의 근거가 서로 충돌 → 판단하지 못함
    - accept: 다른 식별자 근거가 없고 값이 모두 7자리 코드 형식 → 기존 경로의 제안 정책대로 둔다
      (7자리라는 사실만으로 확정하지 않고, 용어사전에 없다는 이유만으로 무효로 보지 않는다)
    known: 용어사전에서 확인되는 코드. 주어지면 근거로 함께 적는다.
    """
    other = other_id_word(column)
    texts: list[str | None] = []
    for s in samples:
        if s is None or (isinstance(s, str) and not s.strip()):
            continue  # 빈 값은 근거로 세지 않는다
        if isinstance(s, str):
            texts.append(s.strip())
        elif isinstance(s, int) and not isinstance(s, bool):
            texts.append(str(s))  # 숫자 셀은 적힌 자릿수 그대로 (사라진 선행 0을 채우지 않음)
        else:
            texts.append(None)  # 소수·날짜 등은 코드 형식이 아님
    valid = [t for t in texts if t is not None and ORG_CODE_FORMAT.match(t)]
    entry = [t for t in valid if known is not None and t in known.entry]
    previous = [t for t in valid if known is not None and t in known.previous]
    both = [t for t in entry if t in previous]
    found = len(entry) + len(previous) - len(both)  # 용어사전 어디엔가 있는 값
    in_glossary = f"항목 코드 {len(entry)}개·개편 전 코드 {len(previous)}개" + (
        f"(둘 다인 값 {len(both)}개)" if both else ""
    )
    lengths = sorted({len(t) for t in texts if t is not None})
    if other is not None:
        if texts and len(valid) == len(texts) and known is not None and found == len(valid):
            return (
                "insufficient",
                f"열 이름은 다른 식별자('{other}')인데 값은 모두 용어사전에 있는 코드({in_glossary}) — 근거 충돌",
            )
        return "reject", f"열 이름이 기관코드가 아닌 다른 식별자('{other}')를 뜻함"
    if len(texts) < 2:
        return "insufficient", "값이 2개 미만이라 기관코드 형식을 확인할 수 없음"
    if not valid:
        shown = ", ".join(f"{n}자리" for n in lengths) or "코드가 아닌 값"
        return "reject", f"값이 기관코드 형식(숫자·영문 대문자 7자리)이 아님 ({shown})"
    if len(valid) < len(texts):
        return "insufficient", f"값의 형식이 섞여 있음 (7자리 코드 형식 {len(valid)}/{len(texts)}개)"
    note = f"값 {len(valid)}개 모두 7자리 코드 형식"
    if known is not None:
        note += f", 용어사전에서 확인: {in_glossary} (현재 유효한 코드인지는 판단하지 않음)"
    return "accept", note


# Qwen이 답변 중간에 중국어 등으로 넘어가는 경우가 있어, 한자·가나가 나오면 그 앞까지만 쓴다.
_FOREIGN_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # 저장(NUL)·엑셀(XML)에 쓸 수 없는 글자


def clean_reason(text: str) -> str:
    """모델이 만든 근거 문장 다듬기 — 다른 문자 체계 뒤는 자르고, 저장·엑셀에 쓸 수 없는 제어 문자는 지운다 (원본 값이
    아니라 모델 출력이라 지워도 된다 — 남기면 원본 파일에 없는 글자 때문에 검토가 거절된다)."""
    text = _CONTROL_CHARS.sub("", text)
    match = _FOREIGN_SCRIPT.search(text)
    if match:
        text = text[: match.start()]
    return text.strip(" ,.:;，：")[:200]


def _llm_schema(fields: list[StandardField]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": [f.key for f in fields] + ["none"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["key", "confidence", "reason"],
    }


# ---------------------------------------------------------------- 엔진


class MappingEngine:
    def __init__(self, fields: list[StandardField], embed_fn: EmbedFn | None, llm_fn: LlmFn | None) -> None:
        self.fields = fields
        self._by_key = {f.key: f for f in fields}
        self._exact = {normalize(t): f.key for f in fields for t in f.texts}
        self._embed_fn = embed_fn
        self._llm_fn = llm_fn
        self._field_vectors: dict[str, list[list[float]]] | None = None
        self._lock = threading.Lock()

    def exact_key(self, normalized: str) -> str | None:
        """표준 사전(학습 별칭 제외)에서 정규화된 이름의 key."""
        return self._exact.get(normalized)

    # -- 임베딩 (사전 쪽은 한 번 계산해 메모리에 보관, 실패하면 다음 요청에서 재시도)
    def _dictionary_vectors(self) -> dict[str, list[list[float]]]:
        with self._lock:
            if self._field_vectors is None:
                assert self._embed_fn is not None
                texts = [(f.key, t) for f in self.fields for t in f.texts]
                vectors = self._embed_fn([t for _, t in texts])
                grouped: dict[str, list[list[float]]] = {}
                for (key, _), vec in zip(texts, vectors, strict=True):
                    grouped.setdefault(key, []).append(vec)
                self._field_vectors = grouped
            return self._field_vectors

    def _embed_columns(self, names: list[ColumnName]) -> tuple[list[dict[str, float]] | None, EngineStatus]:
        if self._embed_fn is None:
            return None, "skipped"
        try:
            field_vectors = self._dictionary_vectors()
            # 열 이름에 든 개인정보(이메일·휴대전화·주민등록번호)는 모델 입력 전에 가린다 (식별자는 원래 이름 그대로)
            column_vectors = self._embed_fn([mask_text(n.display) for n in names])
        except ollama.OllamaError as exc:
            logger.warning("embedding unavailable: %s", exc)
            return None, "unavailable"
        sims = [
            {key: max(_cosine(cv, fv) for fv in vecs) for key, vecs in field_vectors.items()} for cv in column_vectors
        ]
        return sims, "ok"

    # -- 1단계 (표준 사전 + 전사 표준 사전(승격된 이름) + 사용자 확정에서 학습한 별칭)
    def _exact_match(
        self, name: ColumnName, result: ColumnMapping, learned: dict[str, str], standard: dict[str, str]
    ) -> bool:
        key = self._exact.get(name.primary)
        if key is not None:
            key = self._with_bracketed(name, result, key)
            self._apply(result, key, "auto", "exact", 1.0, "표준 사전과 정확히 일치")
            return True
        key = standard.get(name.primary)
        if key in self._by_key:
            self._apply(result, key, "auto", "standard", 1.0, "전사 표준 사전(관리자 승인으로 승격된 이름)과 일치")
            return True
        key = learned.get(name.primary)
        if key in self._by_key:
            self._apply(result, key, "auto", "learned", 1.0, "사용자가 확정한 열 이름(학습된 별칭)과 일치")
            return True
        return self._part_match(name, result)

    def _with_bracketed(self, name: ColumnName, result: ColumnMapping, key: str) -> str:
        """괄호 안이 사전 단어면 단위가 아니라 구분이다.

        '승용(관용)' → 승용차 (관용), '관용(승용차)' → 승용차 (관용). '예산(백만원)'의 단위 괄호는 그대로 무시한다.
        """
        words = [(b, k) for b in name.bracketed if (k := self._exact.get(normalize(b))) is not None]
        if not words:
            return key
        if self._by_key[key].modifier:  # 구분 단어가 앞에 온 형태: 괄호 안의 주 항목으로 바꾼다
            main = next(((b, k) for b, k in words if not self._by_key[k].modifier), None)
            if main is not None:
                words = [(name.display, key), *[w for w in words if w != main]]
                key = main[1]
        result.qualifier = " ".join(q for q in (result.qualifier, *(b for b, _ in words)) if q)
        return key

    def _part_match(self, name: ColumnName, result: ColumnMapping) -> bool:
        """2단 헤더 'A_B'의 일부가 사전과 일치. 아래(뒤) 부분이 더 구체적이지만, 관용·계 같은 구분 단어보다
        승용차 같은 주 항목을 먼저 고른다. 나머지 부분이 구분 단어뿐이면 자동 적용, 아니면 확인 대상."""
        hits = [(i, k) for i, part in enumerate(name.parts) if (k := self._exact.get(part)) is not None]
        if not hits:
            return False
        index, key = ([h for h in hits if not self._by_key[h[1]].modifier] or hits)[-1]
        others = [i for i in range(len(name.parts)) if i != index]

        def is_qualifier(part: str) -> bool:
            if part in TOTAL_WORDS:
                return True
            k = self._exact.get(part)
            return k is not None and self._by_key[k].modifier and not self._by_key[key].modifier

        if others and all(is_qualifier(name.parts[i]) for i in others):
            # 합계 항목에는 '총계'·'계' 같은 합계 단어를 구분으로 다시 붙이지 않는다 ('총계_계' → 합계)
            shown = [i for i in others if not (key == TOTAL_KEY and name.parts[i] in TOTAL_WORDS)]
            extra = " ".join(name.raw_parts[i] for i in shown)
            result.qualifier = " ".join(q for q in (result.qualifier, extra) if q) or None
            self._apply(
                result, key, "auto", "exact_part", 1.0, "열 이름이 표준 항목과 구분 단어(관용·계 등)로 이루어짐"
            )
        else:
            self._apply(result, key, "review", "exact_part", 0.9, "열 이름의 일부가 표준 사전과 일치")
        return True

    # -- 도메인 문맥: '화물'·'특수' 같은 짧은 이름은 차량 통계일 때만 자동 적용
    def _guard_context(self, names: list[ColumnName], results: list[ColumnMapping], columns: list[str]) -> None:
        vehicle = any(term in normalize(c) for c in columns for term in VEHICLE_CONTEXT)
        if vehicle:
            return
        for name, result in zip(names, results, strict=True):
            short = CONTEXT_TERMS.get(result.field_key or "")
            if short is None or result.status != "auto" or result.method not in ("exact", "exact_part", "similarity"):
                continue
            tokens = {name.primary, *name.parts, *(normalize(b) for b in name.bracketed)}
            clear = {normalize(t) for t in self._by_key[result.field_key].texts} - short
            if tokens & clear:  # '화물차'·'화물자동차'처럼 뜻이 분명한 이름은 그대로
                continue
            result.status = "review"
            result.reason = (
                f"'{result.column}'은(는) 차량 외 통계(물동량 등)에도 쓰이는 이름이라, 같은 표에 차량 관련 열"
                "(승용·승합·자동차·차량·이륜 등)이 없으면 자동 적용하지 않음 (사용자 확인 필요)"
            )

    # -- 2단계
    def _similarity(self, name: ColumnName, sims: dict[str, float] | None, result: ColumnMapping) -> None:
        scored = []
        for f in self.fields:
            fz = max(fuzzy_score(name, t) for t in f.texts)
            emb = sims.get(f.key) if sims is not None else None
            scored.append(Candidate(f.key, f.label, round(combine(fz, emb), 4), round(fz, 4), _round(emb)))
        scored.sort(key=lambda c: c.score, reverse=True)
        result.candidates = scored[:TOP_CANDIDATES]
        if result.status != "unresolved" or not scored:
            return  # 1단계에서 결정됨: 후보만 참고로 붙인다
        top = scored[0]
        margin = top.score - (scored[1].score if len(scored) > 1 else 0.0)
        if top.score >= AUTO_THRESHOLD and margin >= AUTO_MARGIN:
            self._apply(result, top.key, "auto", "similarity", top.score, "유사도 높음 (자동 적용)")
        elif top.score >= REVIEW_THRESHOLD:
            reason = "유사도 중간 (사용자 확인 필요)"
            if top.score >= AUTO_THRESHOLD:
                reason = "유사도는 높지만 후보 간 차이가 작음 (사용자 확인 필요)"
            self._apply(result, top.key, "review", "similarity", top.score, reason)

    # -- 3단계
    # -- 기관코드 제안·후보 검사 (유사도 직후와 LLM 응답 때 같은 판정)
    def _check_org_code(
        self, name: ColumnName, result: ColumnMapping, org_codes: KnownOrgCodes | None, source: str
    ) -> None:
        """확정·사전·학습·날짜로 정해지지 않은 열에서 '기관코드'가 적용 항목이거나 추천 후보이면 근거를 검사한다.

        reject: 적용 항목과 추천 후보에서 뺀다. insufficient: 적용 항목에서만 빼 미해결로 둔다 (후보는 참고용).
        accept: 기존 경로의 제안 정책 그대로 (자동/확인 필요 상태를 바꾸지 않음), 적용 항목이면 근거를 덧붙인다.
        """
        if result.method in ORG_CHECK_EXEMPT:
            return
        applied = result.field_key == ORG_KEY
        if not applied and all(c.key != ORG_KEY for c in result.candidates):
            return
        verdict, evidence = org_code_evidence(name.raw, result.samples, org_codes)
        if verdict == "accept":
            if applied:  # 근거는 분석 사유와 검토 사유에 같은 문장으로 남긴다
                result.org_note = f"[근거: {evidence}]"
                result.reason = f"{(result.reason or '').rstrip()} {result.org_note}".lstrip()
            return
        if verdict == "reject":
            # 기각한 항목은 추천 후보에서도 빼서 기본 선택·추천으로 다시 채택되지 않게 한다
            result.candidates = [c for c in result.candidates if c.key != ORG_KEY]
        if applied:
            result.status, result.field_key, result.field_label, result.score = "unresolved", None, None, None
            if result.method == "similarity":
                result.method = "none"
            what = "를 쓰지 않음" if verdict == "reject" else "의 근거 부족"
            result.org_note = f"{source} 제안 '기관코드'{what}: {evidence} (판단하지 못함 — 직접 고르세요)"
            result.reason = result.org_note
        elif verdict == "reject":
            result.org_note = f"추천 후보에서 '기관코드'를 뺌: {evidence}"

    def _ask_llm(self, name: ColumnName, result: ColumnMapping, org_codes: KnownOrgCodes | None = None) -> None:
        assert self._llm_fn is not None
        answer = self._llm_fn(_LLM_SYSTEM, _llm_prompt(self.fields, name, result.samples), _llm_schema(self.fields))
        key = answer.get("key")
        reason = clean_reason(str(answer.get("reason") or ""))
        try:
            confidence = min(max(float(answer.get("confidence", 0)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if key in self._by_key:
            # LLM 결과는 자동 적용하지 않고 항상 사용자 확인 대상으로 둔다.
            self._apply(result, key, "review", "llm", round(confidence, 4), f"LLM 제안: {reason}")
            if key == ORG_KEY:
                # 기관코드는 확신도가 높아도 서버가 열 이름·값 형식·용어사전으로 다시 판정한다
                self._check_org_code(name, result, org_codes, "LLM")
        else:
            result.method = "llm"
            result.reason = f"LLM도 맞는 항목을 찾지 못함: {reason}" if reason else "LLM도 맞는 항목을 찾지 못함"

    def _apply(self, r: ColumnMapping, key: str, status: Status, method: Method, score: float, reason: str) -> None:
        r.field_key, r.field_label = key, self._by_key[key].label
        r.status, r.method, r.score, r.reason = status, method, score, reason

    def map_columns(
        self,
        columns: list[str],
        samples: list[list[Cell]],
        use_llm: bool = True,
        learned: dict[str, str] | None = None,
        decisions: dict[str, str | None] | None = None,
        standard: dict[str, str] | None = None,
        org_codes: KnownOrgCodes | None = None,
    ) -> MappingResult:
        """learned: 정규화된 열 이름 → field key (이 사용자의 학습 별칭). decisions: 열 이름 → 확정된 field key
        (None=해당 없음). standard: 전사 표준 사전 (승격된 열 이름 → field key).
        org_codes: 용어사전에서 확인되는 코드 (기관코드 제안·후보 검사 때 근거로 적는다, 없으면 생략)."""
        learned = learned or {}
        standard = standard or {}
        decisions = decisions or {}
        names = [split_column_name(c) for c in columns]
        results = [
            ColumnMapping(c, s[:SAMPLE_SIZE], qualifier=n.qualifier)
            for c, s, n in zip(columns, samples, names, strict=True)
        ]

        for name, result in zip(names, results, strict=True):
            if result.column in decisions:  # 사용자가 확정한 열은 다시 판단하지 않는다
                key = decisions[result.column]
                if key in self._by_key:
                    self._apply(result, key, "confirmed", "user", 1.0, "사용자가 확정")
                else:
                    result.status, result.method, result.reason = "confirmed", "user", "사용자가 '해당 없음'으로 확정"
                continue
            self._exact_match(name, result, learned, standard)
            date_key = date_field_for(result.samples)
            if date_key is None or result.field_key in DATE_FIELDS or result.method == "learned":
                continue  # 날짜가 아니거나, 열 이름이 이미 날짜 항목이거나, 사용자의 학습 별칭이면 그대로
            result.qualifier = name.qualifier
            example = next(str(s) for s in result.samples if s is not None)
            reason = f"샘플이 모두 날짜·기간 형식(예: {example}) — 지역·용어 판별 없이 날짜 항목으로 처리"
            self._apply(result, date_key, "auto", "pattern", 1.0, reason)

        sims, embedding_status = self._embed_columns(names)
        for i, (name, result) in enumerate(zip(names, results, strict=True)):
            self._similarity(name, sims[i] if sims is not None else None, result)
        self._guard_context(names, results, columns)
        for name, result in zip(names, results, strict=True):
            self._check_org_code(name, result, org_codes, "유사도")

        for result in results:
            if result.status != "unresolved":
                continue
            match = match_region_codes(result.column, result.samples)
            if match is None:
                continue
            table_date = admin_codes.load_table().effective_date
            reason = f"샘플이 모두 행정동 코드표({table_date} 기준)에 있는 코드: " + ", ".join(match.examples)
            if match.abolished:
                reason += f" (말소 코드 {match.abolished}개 포함 — 과거 기준 자료)"
            if match.strong:
                self._apply(result, "region_code", "auto", "pattern", 1.0, reason)
            else:
                self._apply(result, "region_code", "review", "pattern", 0.9, reason)

        llm_status: EngineStatus = "skipped"
        pending = [(n, r) for n, r in zip(names, results, strict=True) if r.status == "unresolved"]
        if use_llm and self._llm_fn is not None and pending:
            llm_status = "ok"
            for idx, (name, result) in enumerate(pending):
                if idx >= MAX_LLM_COLUMNS:
                    result.reason = f"LLM 요청 상한({MAX_LLM_COLUMNS}열) 초과로 건너뜀"
                    continue
                try:
                    self._ask_llm(name, result, org_codes)
                except ollama.OllamaError as exc:
                    logger.warning("llm unavailable: %s", exc)
                    llm_status = "unavailable"
                    for _, rest in pending[idx:]:
                        rest.reason = "LLM을 사용할 수 없어 미해결로 남김"
                    break

        # 괄호를 제거한 이름 일치만으로 GB·MiB 또는 단위 없는 용량을 MB로 연결하지 않는다.
        # 명시적인 파일별 사용자 확정은 보존하며, 분석에서는 원본 단위를 별도로 검사한다.
        for name, result in zip(names, results, strict=True):
            if result.status != "confirmed" and result.field_key in MAIL_MB_FIELDS:
                units = {b.strip() for b in name.bracketed}
                if units != {"MB"}:
                    result.field_key = result.field_label = result.score = None
                    result.status, result.method = "unresolved", "none"
                    result.candidates = []
                    result.reason = "MB 단위 용량 항목입니다 — 원본 단위를 확인하세요 (자동 환산하지 않음)"

        # 기관코드 검사로 바꾼 내용은 이후 단계의 사유에도 남긴다 (행정구역코드 패턴으로 정해진 열은 그 사유만)
        for result in results:
            note = result.org_note
            if note and result.method != "pattern" and note not in (result.reason or ""):
                result.reason = f"{result.reason} · {note}" if result.reason else note
        return MappingResult(results, embedding_status, llm_status)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


@lru_cache(maxsize=1)
def get_engine() -> MappingEngine:
    # 임베딩은 항상 로컬 Ollama, 3단계 판정은 설정된 LLM 제공자(기본 로컬 Ollama)
    return MappingEngine(load_dictionary(), embed_fn=ollama.embed, llm_fn=llm.chat_json)
