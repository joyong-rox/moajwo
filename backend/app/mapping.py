"""열 이름 → 표준 사전 매핑 엔진.

1) 표준 사전 정확 일치 → 2) rapidfuzz + bge-m3 임베딩 유사도 → 3) 1·2단계 미해결 열만 로컬 LLM(Ollama)
날짜 필터: 샘플이 모두 날짜·기간 형식이면 지역·용어 판별 없이 날짜 항목(기준일자·월·연도)으로 정한다.
  사용자가 이 파일에서 확정한 열, 사용자의 학습 별칭, 열 이름이 이미 날짜 항목인 경우는 그대로 둔다.
LLM에는 미해결 열 하나씩, 열 이름과 샘플 값 5개만 보낸다 (전체 데이터를 넘기지 않는다).
"""

from __future__ import annotations

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


# Qwen이 답변 중간에 중국어 등으로 넘어가는 경우가 있어, 한자·가나가 나오면 그 앞까지만 쓴다.
_FOREIGN_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def clean_reason(text: str) -> str:
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
    def _ask_llm(self, name: ColumnName, result: ColumnMapping) -> None:
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
    ) -> MappingResult:
        """learned: 정규화된 열 이름 → field key (이 사용자의 학습 별칭). decisions: 열 이름 → 확정된 field key
        (None=해당 없음). standard: 전사 표준 사전 (승격된 열 이름 → field key)."""
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
                    self._ask_llm(name, result)
                except ollama.OllamaError as exc:
                    logger.warning("llm unavailable: %s", exc)
                    llm_status = "unavailable"
                    for _, rest in pending[idx:]:
                        rest.reason = "LLM을 사용할 수 없어 미해결로 남김"
                    break

        return MappingResult(results, embedding_status, llm_status)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


@lru_cache(maxsize=1)
def get_engine() -> MappingEngine:
    # 임베딩은 항상 로컬 Ollama, 3단계 판정은 설정된 LLM 제공자(기본 로컬 Ollama)
    return MappingEngine(load_dictionary(), embed_fn=ollama.embed, llm_fn=llm.chat_json)
