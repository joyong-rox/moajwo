import math
from typing import Any

import pytest

from app import ollama
from app.mapping import (
    MappingEngine,
    StandardField,
    load_dictionary,
    normalize,
    split_column_name,
)

FIELDS = [
    StandardField("budget", "예산액", "예산 금액", ("예산",)),
    StandardField("org_name", "기관명", "부처 이름", ("부처", "기관")),
    StandardField("phone", "연락처", "전화번호", ("전화번호",)),
    StandardField("population", "인구수", "인구", ("인구",)),
    StandardField("sido", "시도", "광역", ("시·도",)),
]

_TOPICS = [("예산",), ("부처", "기관"), ("전화", "연락"), ("인구",), ("시도", "광역")]


def fake_embed(texts: list[str]) -> list[list[float]]:
    """주제 키워드가 들어 있으면 해당 축의 단위 벡터, 아니면 모든 축에 고르게 퍼진 벡터."""
    out = []
    for t in texts:
        compact = t.replace(" ", "").replace("·", "")
        vec = [1.0 if any(k in compact for k in topic) else 0.0 for topic in _TOPICS]
        if not any(vec):
            vec = [1.0] * len(_TOPICS)
        norm = math.sqrt(sum(v * v for v in vec))
        out.append([v / norm for v in vec])
    return out


class FakeLlm:
    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(user)
        assert "none" in schema["properties"]["key"]["enum"]
        for column, answer in self.answers.items():
            if f"열 이름: {column}\n" in user:
                return answer
        return {"key": "none", "confidence": 0, "reason": "모름"}


def _by_column(result):
    return {c.column: c for c in result.columns}


def test_normalize_and_split() -> None:
    assert normalize(" 시·도 (단위: 개) ") == "시도"
    name = split_column_name("인구_2025")
    assert (name.primary, name.qualifier, name.display) == ("인구", "2025", "인구")
    assert split_column_name("행정구역_시도").parts == ("행정구역", "시도")


def test_three_stage_mapping() -> None:
    llm = FakeLlm({"열3": {"key": "phone", "confidence": 0.8, "reason": "전화번호 형식"}})
    engine = MappingEngine(FIELDS, fake_embed, llm)
    columns = ["부처", "인구_2025", "인구_남", "예산 액수", "세출 예산 금액", "강수량(mm)", "열3"]
    samples = [["행안부"], [100], [50], [1], [2], [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5], ["02-1234-5678"]]
    result = engine.map_columns(columns, samples)
    m = _by_column(result)

    # 1단계: 정확 일치
    assert (m["부처"].status, m["부처"].method, m["부처"].field_key) == ("auto", "exact", "org_name")
    assert (m["인구_2025"].field_key, m["인구_2025"].qualifier, m["인구_2025"].status) == ("population", "2025", "auto")
    assert (m["인구_남"].status, m["인구_남"].method) == ("review", "exact_part")
    # 2단계: 유사도 높음 → 자동, 중간 → 확인
    assert (m["예산 액수"].status, m["예산 액수"].method, m["예산 액수"].field_key) == ("auto", "similarity", "budget")
    assert (m["세출 예산 금액"].status, m["세출 예산 금액"].field_key) == ("review", "budget")
    # 3단계: 미해결만 LLM, LLM 결과는 항상 확인 대상
    assert (m["열3"].status, m["열3"].method, m["열3"].field_key) == ("review", "llm", "phone")
    assert (m["강수량(mm)"].status, m["강수량(mm)"].method, m["강수량(mm)"].field_key) == ("unresolved", "llm", None)
    assert result.embedding == "ok" and result.llm == "ok"

    # LLM에는 미해결 열만, 한 번에 한 열씩, 샘플 5개까지만 간다.
    assert len(llm.calls) == 2
    rain = next(c for c in llm.calls if "강수량" in c)
    assert "부처" not in rain.split("열 이름:")[1]
    assert "샘플 값(5개)" in rain and "6.5" not in rain


def test_candidates_attached_even_for_exact() -> None:
    engine = MappingEngine(FIELDS, fake_embed, None)
    m = _by_column(engine.map_columns(["예산"], [[1]]))
    assert m["예산"].candidates[0].key == "budget"
    assert len(m["예산"].candidates) == 3


def test_ambiguous_top_candidates_not_auto() -> None:
    fields = [
        StandardField("a", "시군구", "", ()),
        StandardField("b", "시군구명", "", ()),
    ]
    engine = MappingEngine(fields, None, None)
    m = _by_column(engine.map_columns(["시군구 이름"], [[]]))
    assert m["시군구 이름"].status != "auto"  # 1·2위 차이가 작으면 자동 적용하지 않는다


def test_embedding_unavailable_falls_back_to_fuzzy() -> None:
    def broken(_: list[str]) -> list[list[float]]:
        raise ollama.OllamaError("down")

    engine = MappingEngine(FIELDS, broken, None)
    result = engine.map_columns(["예산 액수", "부처"], [[], []])
    m = _by_column(result)
    assert result.embedding == "unavailable" and result.llm == "skipped"
    assert m["부처"].status == "auto"
    assert m["예산 액수"].candidates[0].embedding is None


def test_llm_unavailable_keeps_unresolved() -> None:
    def broken(*_: Any) -> dict[str, Any]:
        raise ollama.OllamaError("down")

    engine = MappingEngine(FIELDS, fake_embed, broken)
    result = engine.map_columns(["강수량", "풍속"], [[1], [2]])
    assert result.llm == "unavailable"
    assert all(c.status == "unresolved" and "LLM을 사용할 수 없어" in (c.reason or "") for c in result.columns)


def test_llm_invalid_key_ignored() -> None:
    llm = FakeLlm({"강수량": {"key": "rainfall", "confidence": 0.9, "reason": "?"}})
    engine = MappingEngine(FIELDS, fake_embed, llm)
    m = _by_column(engine.map_columns(["강수량"], [[1]]))
    assert m["강수량"].status == "unresolved" and m["강수량"].field_key is None


def test_use_llm_false_skips_llm() -> None:
    llm = FakeLlm({})
    engine = MappingEngine(FIELDS, fake_embed, llm)
    result = engine.map_columns(["강수량"], [[1]], use_llm=False)
    assert result.llm == "skipped" and llm.calls == []


def test_default_dictionary_loads_and_is_unique() -> None:
    fields = load_dictionary()
    assert len(fields) >= 20
    engine = MappingEngine(fields, None, None)
    m = _by_column(engine.map_columns(["시·도", "기준 년도", "집행율(%)"], [[], [], []]))
    assert [m[c].field_key for c in ("시·도", "기준 년도", "집행율(%)")] == ["sido", "year", "execution_rate"]


@pytest.mark.parametrize("url", ["https://api.openai.com", "http://example.com:11434", "http://8.8.8.8:11434"])
def test_only_local_ollama_allowed(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(ollama, "OLLAMA_BASE_URL", url)
    with pytest.raises(ollama.OllamaError):
        ollama.embed(["x"])


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://host.docker.internal:11434", "http://172.17.0.1:11434"]
)
def test_local_hosts_pass_check(url: str) -> None:
    ollama._check_local(url)


def test_clean_reason_drops_foreign_script() -> None:
    from app.mapping import clean_reason

    assert clean_reason("평균기온은 표준 항목 중任何语言的版本") == "평균기온은 표준 항목 중"
    assert clean_reason("정상 설명입니다.") == "정상 설명입니다"
    assert clean_reason("任何") == ""


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        (["11500", "11680", "26440"], "같은 길이(5자리)의 숫자 문자열 — 코드 형식"),
        (["2026-01-31", "2026-02-28"], "날짜 형식"),
        ([12.5, 13], "소수가 있는 숫자 — 측정값·비율 형식"),
        ([1200, 980], "정수 — 수량·금액 형식"),
        (["홍길동", "김철수"], "텍스트"),
        ([], "알 수 없음"),
    ],
)
def test_sample_format(samples: list, expected: str) -> None:
    from app.mapping import sample_format

    assert sample_format(samples) == expected


def test_llm_prompt_contains_only_one_column_and_format_hint() -> None:
    llm = FakeLlm({})
    engine = MappingEngine(FIELDS, None, llm)
    engine.map_columns(["강수량", "풍속"], [[1.5, 2.5], [3.1]])
    assert len(llm.calls) == 2
    assert "샘플 형식: 소수가 있는 숫자" in llm.calls[0]
    assert "풍속" not in llm.calls[0]


@pytest.mark.parametrize(
    ("column", "samples", "expected"),
    [
        ("열5", ["11500", "11680", "26440"], True),  # 시군구 5자리 (서울 강서·강남, 부산 강서)
        ("열5", [11500, 11680, 26440], True),  # 숫자형이어도 열 이름이 없으면 인정
        ("행정코드", ["1150052000", "2644051000"], True),  # 행정동 10자리
        ("열8", ["11500520", "26440510"], True),  # 읍면동 8자리
        ("열2", ["11", "26", "12"], True),  # 시도 2자리 문자열 (12 = 전남광주통합특별시)
        ("열5", ["29110", "46110"], True),  # 2026-07-01 말소된 광주·전남 코드 (과거 자료)
        ("열5", ["11500", "11510"], False),  # 11510은 코드표에 없는 시군구
        ("행정코드", ["1150010100", "2644010100"], False),  # 법정동 코드는 행정동 코드표에 없음
        ("열2", [11, 26, 41], False),  # 2자리 숫자는 나이·수량과 구분 불가
        ("금액", [11500, 26440, 41000], False),  # 의미 있는 이름의 숫자형은 금액일 수 있음
        ("열5", ["1741000", "1311000"], False),  # 7자리 = 행정기관코드와 겹침
        ("열5", ["99500", "11680"], False),  # 없는 시도 코드
        ("열5", ["11500", "116800"], False),  # 길이 불일치
        ("열5", ["11500"], False),  # 샘플 1개는 판단하지 않음
    ],
)
def test_looks_like_region_code(column: str, samples: list, expected: bool) -> None:
    from app.mapping import looks_like_region_code

    assert looks_like_region_code(column, samples) is expected


def _region_fields() -> list[StandardField]:
    return [*FIELDS, StandardField("region_code", "행정구역코드", "지역 코드", ("지역코드",))]


def test_region_code_pattern_before_llm() -> None:
    llm = FakeLlm({})
    engine = MappingEngine(_region_fields(), None, llm)
    m = _by_column(engine.map_columns(["열5", "열6"], [["11500", "11680", "26440"], ["11500", "26440"]]))
    # 공식 코드표와 3개 이상 일치 → 자동, 2개뿐 → 확인 대상
    assert (m["열5"].status, m["열5"].method, m["열5"].field_key) == ("auto", "pattern", "region_code")
    assert "11500 서울특별시 강서구" in (m["열5"].reason or "")
    assert (m["열6"].status, m["열6"].field_key) == ("review", "region_code")
    assert llm.calls == []  # 규칙으로 결정되면 LLM을 부르지 않는다


def test_region_code_reason_mentions_abolished() -> None:
    engine = MappingEngine(_region_fields(), None, None)
    m = _by_column(engine.map_columns(["열5"], [["29110", "46110", "11500"]]))
    assert "말소 코드 2개" in (m["열5"].reason or "")


def test_learned_alias_and_decisions() -> None:
    llm = FakeLlm({})
    engine = MappingEngine(FIELDS, None, llm)
    result = engine.map_columns(
        ["자치구명", "강수량", "부처"],
        [["강서구"], [1.5], ["행안부"]],
        learned={"자치구명": "sido"},
        decisions={"강수량": None, "부처": "budget"},
    )
    m = _by_column(result)
    assert (m["자치구명"].status, m["자치구명"].method, m["자치구명"].field_key) == ("auto", "learned", "sido")
    assert (m["강수량"].status, m["강수량"].method, m["강수량"].field_key) == ("confirmed", "user", None)
    # 확정은 사전 일치보다 우선한다
    assert (m["부처"].status, m["부처"].field_key) == ("confirmed", "budget")
    assert llm.calls == []  # 확정된 열은 LLM으로 가지 않는다
