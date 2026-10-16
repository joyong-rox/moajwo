"""'기관코드' 제안·후보를 서버가 열 이름·값 형식·용어사전으로 다시 판정한다 (유사도·LLM·대체 경로 모두 같은 판정).

재현 1 (PR #8): 무인민원발급기 자료의 관리번호(23자리)·발급기번호(3자리)·초기메뉴번호(5자리)를 로컬 qwen2.5:7b가
'기관코드'(확신도 0.7~0.8)로 제안했다.
재현 2 (보완): '발급기관식별부호'·'등기기관식별부호'가 '발급기'·'기기' 부분 문자열 때문에 다른 식별자로 기각됐고,
유사도·문자열 유사도·use_llm=false 경로에는 검사가 없었으며, 검토 경로에는 용어사전 근거가 전달되지 않았다.
결정적인 시험을 위해 LLM은 어떤 열이든 기관코드를 확신도 0.99로 답하도록 고정한다 (실제 모델 시험은 격리 환경에서 따로).
"""

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import ollama
from app.mapping import (
    MAX_LLM_COLUMNS,
    KnownOrgCodes,
    MappingEngine,
    load_dictionary,
    normalize,
    org_code_evidence,
    other_id_word,
)

CONFIDENT_ORG = {"key": "org_code", "confidence": 0.99, "reason": "기관을 식별하는 코드로 보입니다"}
KNOWN = KnownOrgCodes(frozenset({"1741000", "1360000"}))


class FarEmbed:
    """표준 사전(첫 호출)과 열 이름(이후 호출)의 벡터가 직교 → 임베딩 유사도 0.
    실제 재현(bge-m3)처럼 이 열들이 유사도 단계에서 정해지지 않고 LLM으로 넘어가게 한다."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.0] if self.calls == 1 else [0.0, 1.0] for _ in texts]


def near_embed(texts: list[str]) -> list[list[float]]:
    """모든 벡터가 같음 → 임베딩 유사도 1. 문자열 유사도 1순위가 '확인 필요' 이상으로 올라간다 (유사도 경로 시험)."""
    return [[1.0, 0.0] for _ in texts]


def down_embed(texts: list[str]) -> list[list[float]]:
    raise ollama.OllamaError("embedding down")


def down_llm(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    raise ollama.OllamaError("connection refused")


class AlwaysOrgLlm:
    def __init__(self) -> None:
        self.columns: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.columns.append(user.split("열 이름: ", 1)[1].split("\n", 1)[0])
        return dict(CONFIDENT_ORG)


_FAR = object()


def _engine(llm: Any, embed: Any = _FAR) -> MappingEngine:
    return MappingEngine(load_dictionary(), FarEmbed() if embed is _FAR else embed, llm)


def _map(
    columns: list[str], samples: list[list[Any]], llm: Any | None = None, embed: Any = _FAR, **kwargs: Any
) -> dict[str, Any]:
    result = _engine(llm or AlwaysOrgLlm(), embed).map_columns(columns, samples, **kwargs)
    return {c.column: c for c in result.columns}


def _candidate_keys(col: Any) -> list[str]:
    return [c.key for c in col.candidates]


KIOSK_COLUMNS = ["관리번호", "발급기번호", "초기메뉴번호"]
KIOSK_SAMPLES = [
    ["32200002018000000000123", "32200002019000000000456", "31800002020000000000789"],
    ["507", "506", "503"],
    ["00210", "00200", "00240"],
]


# ================================================================ 새 결함 1: 정상 기관 이름을 다른 식별자로 오인


@pytest.mark.parametrize(
    ("column", "word"),
    [
        ("발급기관식별부호", None),  # '발급기' + '관'이 아니라 '발급' + '기관'
        ("등기기관식별부호", None),  # '기기'가 아니라 '등기' + '기관'
        ("발급 기관 식별부호", None),
        ("관리기관코드", None),
        ("기관관리번호", "관리번호"),  # 기관의 관리번호 — 다른 식별자
        ("발급기관관리번호", "관리번호"),
        ("기기관리번호", "관리번호"),  # '기기' + '관리번호' ('기관'은 두 단어의 경계)
        ("기관기기번호", "기기"),
        ("발급기번호", "발급기"),
        ("초기메뉴번호", "메뉴"),
        ("전기기기번호", "기기"),
        ("기관 일련번호", "일련번호"),
    ],
)
def test_other_id_word_reads_column_name_context(column: str, word: str | None) -> None:
    assert other_id_word(column) == word


@pytest.mark.parametrize("column", ["발급기관식별부호", "등기기관식별부호"])
@pytest.mark.parametrize(
    ("values", "known", "text"),
    [
        (["1741000", "1360000"], KNOWN, "용어사전에서 확인: 항목 코드 2개·개편 전 코드 0개"),
        (["B551011", "B552015"], KNOWN, "용어사전에서 확인: 항목 코드 0개·개편 전 코드 0개"),  # 사전에 없어도 무효 아님
        (["B551011", "B552015"], None, "값 2개 모두 7자리 코드 형식"),
    ],
)
def test_issuer_and_registrar_org_names_are_accepted(column: str, values: list[str], known: Any, text: str) -> None:
    verdict, reason = org_code_evidence(column, values, known)
    assert verdict == "accept" and text in reason, reason


def test_issuer_and_registrar_llm_proposals_stay_review() -> None:
    m = _map(
        ["발급기관식별부호", "등기기관식별부호"], [["1741000", "1360000"], ["B551011", "B552015"]], org_codes=KNOWN
    )
    for column, hits in (("발급기관식별부호", "항목 코드 2개"), ("등기기관식별부호", "항목 코드 0개")):
        col = m[column]
        assert (col.status, col.field_key, col.method, col.score) == ("review", "org_code", "llm", 0.99), column
        assert hits in col.reason and "현재 유효한 코드인지는 판단하지 않음" in col.reason
        assert col.org_note is None


def test_previous_codes_are_reported_apart_from_entry_codes() -> None:
    known = KnownOrgCodes(frozenset({"1053000"}), frozenset({"1051000"}))
    verdict, reason = org_code_evidence("소관기관식별부호", ["1053000", "1051000"], known)
    assert verdict == "accept" and "항목 코드 1개·개편 전 코드 1개" in reason


# ================================================================ 새 결함 2: 유사도·대체 경로에 검사가 없었음

PATHS = [
    # (경로, 임베딩, LLM, use_llm)
    ("임베딩 유사도·use_llm=false", _FAR, None, False),
    ("문자열 유사도만(임베딩 없음)", None, None, False),
    ("임베딩 장애", down_embed, None, False),
    ("LLM 사용 불가", _FAR, down_llm, True),
    ("LLM이 기관코드 제안", _FAR, "always_org", True),
]


@pytest.mark.parametrize(("path", "embed", "llm", "use_llm"), PATHS, ids=[p[0] for p in PATHS])
def test_other_identifiers_are_rejected_on_every_path(path: str, embed: Any, llm: Any, use_llm: bool) -> None:
    columns = [*KIOSK_COLUMNS, "관리 번호"]
    samples = [*KIOSK_SAMPLES, ["523", "525"]]  # 문자열 유사도만으로 '기관코드 · 확인 필요'가 되던 자료
    engine = _engine(AlwaysOrgLlm() if llm == "always_org" else llm, embed)
    result = engine.map_columns(columns, samples, use_llm=use_llm, org_codes=KNOWN)
    for col in result.columns:
        assert (col.status, col.field_key) == ("unresolved", None), (path, col.column, col.reason)
        assert "org_code" not in _candidate_keys(col), (path, col.column)  # 기본 선택·추천으로 다시 채택되지 않게
        assert "'기관코드'" in col.reason and col.org_note in col.reason, (path, col.column, col.reason)


def test_llm_call_limit_skipped_columns_are_checked_too() -> None:
    fillers = [f"참고열{i}" for i in range(MAX_LLM_COLUMNS)]
    m = _map([*fillers, "관리번호"], [["x"]] * MAX_LLM_COLUMNS + [KIOSK_SAMPLES[0]])
    col = m["관리번호"]
    assert (col.status, col.field_key) == ("unresolved", None)
    assert col.reason.startswith(f"LLM 요청 상한({MAX_LLM_COLUMNS}열) 초과로 건너뜀 · 추천 후보에서 '기관코드'를 뺌")
    assert "org_code" not in _candidate_keys(col)


@pytest.mark.parametrize("embed", [near_embed, None], ids=["임베딩", "문자열만"])
def test_similarity_proposal_for_other_identifier(embed: Any) -> None:
    """'기관관리번호'는 유사도로 '기관코드'가 되던 열. 사전 근거가 없으면 기각, 값이 모두 사전 코드면 근거 부족."""
    rejected = _map(["기관관리번호"], [["1741000", "1360000"]], embed=embed, use_llm=False)["기관관리번호"]
    assert (rejected.status, rejected.field_key, rejected.score) == ("unresolved", None, None)
    assert "org_code" not in _candidate_keys(rejected)
    assert rejected.reason.startswith(
        "유사도 제안 '기관코드'를 쓰지 않음: 열 이름이 기관코드가 아닌 다른 식별자('관리번호')"
    )
    conflict = _map(["기관관리번호"], [["1741000", "1360000"]], embed=embed, use_llm=False, org_codes=KNOWN)[
        "기관관리번호"
    ]
    assert (conflict.status, conflict.field_key) == ("unresolved", None)  # 후보는 참고로 남지만 선택된 상태가 아님
    assert "org_code" in _candidate_keys(conflict) and "근거 부족" in conflict.reason and "충돌" in conflict.reason


def test_similarity_proposal_with_insufficient_values_is_not_selected() -> None:
    m = _map(
        ["기관번호코드", "기관식별부호"], [["1741000"], ["1741000", "12345", "ABC"]], embed=near_embed, use_llm=False
    )
    for column in ("기관번호코드", "기관식별부호"):
        col = m[column]
        assert (col.status, col.field_key, col.method) == ("unresolved", None, "none"), column
        assert "org_code" in _candidate_keys(col) and "유사도 제안 '기관코드'의 근거 부족" in col.reason


def test_similarity_proposal_with_good_codes_keeps_existing_policy() -> None:
    """수용: 유사도 경로의 기존 정책 그대로 (형식 검사 통과만으로 자동 적용하지 않음) + 근거 덧붙임."""
    columns = ["발급기관식별부호", "등기기관식별부호", "수요처코드"]
    samples = [["1741000", "1360000"], ["B551011", "B552015"], ["0012345", "0098765"]]
    m = _map(columns, samples, embed=near_embed, use_llm=False, org_codes=KNOWN)
    for column in columns:
        col = m[column]
        assert (col.status, col.field_key, col.method) == ("review", "org_code", "similarity"), column
        assert col.reason.startswith("유사도 중간 (사용자 확인 필요) [근거: 값 2개 모두 7자리 코드 형식"), col.reason
    assert m["수요처코드"].samples == ["0012345", "0098765"]  # 선행 0 그대로


def test_llm_then_similarity_notes_are_consistent_when_llm_proposes_other_field() -> None:
    class NoteLlm:
        def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {"key": "note", "confidence": 0.7, "reason": "비고"}

    col = _map(["관리번호"], [KIOSK_SAMPLES[0]], NoteLlm())["관리번호"]
    assert (col.status, col.field_key, col.method, col.score) == ("review", "note", "llm", 0.7)  # 다른 제안은 그대로
    assert col.reason.startswith("LLM 제안: 비고 · 추천 후보에서 '기관코드'를 뺌")
    assert "org_code" not in _candidate_keys(col)


# ================================================================ PR #8 사례 (LLM 경로)


def test_reported_kiosk_identifiers_are_not_proposed_as_org_code() -> None:
    llm = AlwaysOrgLlm()
    m = _map(KIOSK_COLUMNS, KIOSK_SAMPLES, llm)
    assert llm.columns == KIOSK_COLUMNS  # 세 열 모두 실제로 LLM 단계에서 기관코드를 받았다
    for column in KIOSK_COLUMNS:
        col = m[column]
        assert (col.status, col.field_key, col.method, col.score) == ("unresolved", None, "llm", None), column
        assert "org_code" not in _candidate_keys(col), column
        assert col.reason.startswith("LLM 제안 '기관코드'를 쓰지 않음") and "판단하지 못함" in col.reason, column
    assert "'관리번호'" in m["관리번호"].reason
    assert "'발급기'" in m["발급기번호"].reason
    assert "'메뉴'" in m["초기메뉴번호"].reason


def test_seven_digit_identifier_with_other_purpose_is_rejected() -> None:
    m = _map(["접수번호", "고객번호"], [["2026001", "2026002", "2026003"], ["1000001", "1000002"]])
    for column in ("접수번호", "고객번호"):
        assert (m[column].status, m[column].field_key) == ("unresolved", None), column
        assert "다른 식별자" in m[column].reason
        assert "org_code" not in _candidate_keys(m[column])


def test_values_not_in_code_format_are_rejected() -> None:
    col = _map(["기관식별부호"], [["507", "506", "503"]])["기관식별부호"]
    assert (col.status, col.field_key) == ("unresolved", None)
    assert "기관코드 형식(숫자·영문 대문자 7자리)이 아님 (3자리)" in col.reason


def test_conflicting_name_and_glossary_values_are_insufficient() -> None:
    col = _map(["관리번호"], [["1741000", "1360000"]], org_codes=KNOWN)["관리번호"]
    assert (col.status, col.field_key, col.method) == ("unresolved", None, "llm")
    assert "근거 부족" in col.reason and "충돌" in col.reason
    assert "org_code" in _candidate_keys(col)


# ================================================================ 보존: 정상 기관코드는 확인 대상


def test_numeric_org_codes_stay_a_review_proposal() -> None:
    col = _map(["기관식별부호"], [[1741000, 1360000, 1310000]])["기관식별부호"]
    assert (col.status, col.field_key, col.method, col.score) == ("review", "org_code", "llm", 0.99)
    assert col.reason.startswith("LLM 제안: ") and "근거: 값 3개 모두 7자리 코드 형식" in col.reason


def test_alphanumeric_codes_in_glossary_format_are_accepted() -> None:
    known = KnownOrgCodes(frozenset({"B551011", "A123456"}))
    col = _map(["수요처코드"], [["B551011", "A123456"]], org_codes=known)["수요처코드"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert "항목 코드 2개" in col.reason


def test_well_formed_code_missing_from_glossary_is_not_invalid() -> None:
    col = _map(["수요처코드"], [["9990001", "9990002"]], org_codes=KNOWN)["수요처코드"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert "항목 코드 0개·개편 전 코드 0개" in col.reason


def test_leading_zero_codes_kept_as_strings() -> None:
    samples = ["0012345", "0098765"]
    col = _map(["기관식별부호"], [list(samples)])["기관식별부호"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert col.samples == samples  # 값을 자르거나 바꾸지 않음


def test_number_cell_that_lost_leading_zeros_is_not_padded() -> None:
    col = _map(["기관식별부호"], [[12345, 98765]])["기관식별부호"]
    assert (col.status, col.field_key) == ("unresolved", None)
    assert "(5자리)" in col.reason
    assert col.samples == [12345, 98765]


@pytest.mark.parametrize(
    ("column", "samples", "known", "verdict", "text"),
    [
        ("기관식별부호", [1741000, "1360000"], None, "accept", "값 2개 모두 7자리 코드 형식"),
        ("기관식별부호", ["1741000", None, "", "  ", "1360000"], None, "accept", "값 2개 모두"),
        ("기관식별부호", [" 1741000 ", "1360000"], KnownOrgCodes(frozenset({"1741000"})), "accept", "항목 코드 1개"),
        ("기관식별부호", ["1741000", None, ""], None, "insufficient", "2개 미만"),
        ("기관식별부호", [], None, "insufficient", "2개 미만"),
        ("기관식별부호", ["1741000", "12345", "ABC"], None, "insufficient", "섞여 있음 (7자리 코드 형식 1/3개)"),
        ("기관식별부호", ["1741000", 1741000.5], None, "insufficient", "섞여 있음"),
        ("기관식별부호", ["b551011", "a123456"], None, "reject", "형식"),  # 소문자는 형식이 아님 (바꾸지 않음)
        ("기관식별부호", ["17410001", "13600002"], None, "reject", "(8자리)"),  # 자르지 않음
        ("기관식별부호", [True, False], None, "reject", "코드가 아닌 값"),
        ("발급기번호", ["1741000", "1360000"], None, "reject", "'발급기'"),
        ("관리번호", ["1741000", "9999999"], KnownOrgCodes(frozenset({"1741000"})), "reject", "'관리번호'"),
        ("관리번호", ["1741000", "1360000"], KNOWN, "insufficient", "충돌"),
        ("관리번호", ["1741000", "1051000"], KnownOrgCodes(frozenset({"1741000"}), frozenset({"1051000"})),
         "insufficient", "항목 코드 1개·개편 전 코드 1개"),
    ],
)  # fmt: skip
def test_org_code_evidence_rules(column: str, samples: list, known: KnownOrgCodes | None, verdict: str, text: str):
    got, reason = org_code_evidence(column, samples, known)
    assert got == verdict and text in reason, (got, reason)


# ================================================================ 보존: 지키는 기존 동작


@pytest.mark.parametrize("embed", [_FAR, near_embed, None], ids=["임베딩0", "임베딩1", "문자열만"])
def test_region_codes_are_not_turned_into_org_code(embed: Any) -> None:
    llm = AlwaysOrgLlm()
    sgg = ["11500", "11680", "26440"]
    m = _map(["행정동코드", "열5", "코드"], [["1168064000", "1150061000"], sgg, sgg], llm, embed=embed, org_codes=KNOWN)
    assert (m["행정동코드"].field_key, m["행정동코드"].method) == ("region_code", "exact")
    for column in ("열5", "코드"):
        assert (m[column].field_key, m[column].status, m[column].method) == ("region_code", "auto", "pattern"), column
        assert m[column].reason.startswith("샘플이 모두 행정동 코드표")  # 행정구역코드 사유만 (기관코드 메모 없음)
    assert llm.columns == []  # 행정구역코드로 정해진 열은 LLM으로 가지 않는다


def test_user_decisions_and_none_are_kept() -> None:
    llm = AlwaysOrgLlm()
    m = _map(KIOSK_COLUMNS, KIOSK_SAMPLES, llm, decisions={"관리번호": "org_code", "발급기번호": None}, org_codes=KNOWN)
    # 사용자 확정은 서버 판정보다 우선한다 (사용자가 기관코드로 확정한 열도 그대로)
    assert (m["관리번호"].status, m["관리번호"].field_key, m["관리번호"].method) == ("confirmed", "org_code", "user")
    assert (m["발급기번호"].status, m["발급기번호"].field_key) == ("confirmed", None)
    assert m["발급기번호"].reason == "사용자가 '해당 없음'으로 확정"  # 시스템 '판단하지 못함'과 구분
    assert m["관리번호"].org_note is None and m["발급기번호"].org_note is None
    assert llm.columns == ["초기메뉴번호"]


@pytest.mark.parametrize("embed", [_FAR, near_embed, None], ids=["임베딩0", "임베딩1", "문자열만"])
def test_dictionary_standard_and_learned_aliases_keep_priority(embed: Any) -> None:
    llm = AlwaysOrgLlm()
    m = _map(
        ["기관 코드", "발급기번호", "관리번호"],
        [KIOSK_SAMPLES[0][:2], ["507", "506"], ["32200002018000000000123", "32200002019000000000456"]],
        llm,
        embed=embed,
        standard={normalize("발급기번호"): "note"},
        learned={normalize("관리번호"): "org_code"},
        org_codes=KNOWN,
    )
    # 표준 사전 일치는 값 형식과 관계없이 그대로 (사전 우선순위는 이번 검사 대상이 아님)
    assert (m["기관 코드"].field_key, m["기관 코드"].method, m["기관 코드"].status) == ("org_code", "exact", "auto")
    assert (m["발급기번호"].field_key, m["발급기번호"].method) == ("note", "standard")
    assert (m["관리번호"].field_key, m["관리번호"].method, m["관리번호"].status) == ("org_code", "learned", "auto")
    assert all(m[c].org_note is None for c in m)
    assert llm.columns == []


@pytest.mark.parametrize(("embed", "status"), [(near_embed, "auto"), (None, "review")], ids=["임베딩1", "문자열만"])
def test_other_standard_fields_are_unchanged(embed: Any, status: str) -> None:
    col = _map(["집행 실적액"], [[100, 200]], embed=embed, use_llm=False)["집행 실적액"]
    assert (col.field_key, col.status, col.method, col.org_note) == ("expenditure", status, "similarity", None)
    assert "[근거" not in col.reason


def test_llm_unavailable_fallback_is_unchanged() -> None:
    result = _engine(down_llm).map_columns(KIOSK_COLUMNS, KIOSK_SAMPLES)
    assert result.llm == "unavailable"
    for col in result.columns:
        assert (col.status, col.field_key) == ("unresolved", None)
        # 기존 대체 사유는 그대로 앞에 두고, 기관코드 후보를 뺀 사실을 덧붙인다
        assert col.reason.startswith("LLM을 사용할 수 없어 미해결로 남김 · 추천 후보에서 '기관코드'를 뺌")


# ================================================================ API: 새 파일 업로드 → 분석 → 검토 (확정·학습 없음)


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


# 다른 시험과 겹치지 않는 시험 전용 코드: Z990001·Z990003 = 항목 코드, Z990002 = Z990001의 개편 전 코드
API_KNOWN = ["Z990001", "Z990003"]
API_UNKNOWN = ["B551011", "B552015"]
API_HEADER = [
    "발급기관식별부호",
    "등기기관식별부호",
    "관리번호",
    "발급기번호",
    "초기메뉴번호",
    "기관관리번호",
    "시군구명",
]


def _api_rows(issuer: list[str], registrar: list[str]) -> list[list[str]]:
    kiosk = [["32200002018000000000123", "507", "00210"], ["32200002019000000000456", "506", "00200"]]
    return [API_HEADER] + [
        [issuer[i], registrar[i], *kiosk[i], API_KNOWN[i], ["서울특별시 강남구", "서울특별시 영등포구"][i]]
        for i in range(2)
    ]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    from app import files, integration, review, validation_api
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models import GlossaryEntry

    Base.metadata.create_all(engine)
    llm = AlwaysOrgLlm()
    for module in (files, review, integration, validation_api):
        monkeypatch.setattr(module, "get_engine", lambda: _engine(llm))
    # 시험 DB는 다른 시험과 함께 쓰므로, 다른 시험이 쓰지 않는 코드로 넣고 넣은 행만 지운다
    with SessionLocal() as db:
        entries = [
            GlossaryEntry(code="Z990001", name="시험기관", normalized="시험기관", previous_codes="Z990002"),
            GlossaryEntry(code="Z990003", name="시험기관2", normalized="시험기관2"),
        ]
        db.add_all(entries)
        db.commit()
        ids = [e.id for e in entries]
    try:
        with TestClient(app) as c:
            yield c, llm
    finally:
        with SessionLocal() as db:
            db.query(GlossaryEntry).filter(GlossaryEntry.id.in_(ids)).delete()
            db.commit()


def _upload_rows(c: TestClient, name: str, rows: list[list[str]]) -> int:
    res = c.post("/files", files=[("files", (name, _csv(rows), "text/csv"))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _columns(c: TestClient, fid: int, use_llm: bool = True) -> dict[str, dict]:
    res = c.post(f"/files/{fid}/mapping", params={"use_llm": str(use_llm).lower()})
    assert res.status_code == 200, res.text
    return {col["column"]: col for col in res.json()["sheets"][0]["columns"]}


def _review_mapping_items(c: TestClient, fid: int) -> tuple[int, dict[str, dict]]:
    res = c.post("/reviews", json={"file_ids": [fid]})
    assert res.status_code == 201, res.text
    body = res.json()
    return body["id"], {i["before"]: i for i in body["items"] if i["kind"] == "mapping"}


def _org_part(reason: str) -> str:
    """사유에서 기관코드 검사 부분 (파일 분석·검토 화면 비교용)."""
    for marker in ("LLM 제안 '기관코드'", "유사도 제안 '기관코드'", "추천 후보에서 '기관코드'", "[근거: "):
        if marker in reason:
            return reason[reason.index(marker) :]
    return ""


@pytest.mark.parametrize("swap", [False, True], ids=["발급기관=사전 코드", "등기기관=사전 코드"])
def test_new_file_analysis_then_review_issuer_and_registrar(api, swap: bool) -> None:
    c, _ = api
    issuer, registrar = (API_UNKNOWN, API_KNOWN) if swap else (API_KNOWN, API_UNKNOWN)
    fid = _upload_rows(c, f"기관식별_{int(swap)}.csv", _api_rows(issuer, registrar))
    cols = _columns(c, fid)
    rid, items = _review_mapping_items(c, fid)
    for column, values in (("발급기관식별부호", issuer), ("등기기관식별부호", registrar)):
        hits = "항목 코드 2개" if values == API_KNOWN else "항목 코드 0개"
        col, item = cols[column], items[column]
        assert (col["status"], col["field_key"], col["method"]) == ("review", "org_code", "llm"), (
            column,
            col["reason"],
        )
        assert hits in col["reason"]
        assert (item["category"], item["payload"]["field_key"]) == ("pending", "org_code")
        assert _org_part(item["reason"]) == _org_part(col["reason"]) != ""
    for column in KIOSK_COLUMNS:
        col, item = cols[column], items[column]
        assert (col["status"], col["field_key"]) == ("unresolved", None)
        assert "org_code" not in [x["key"] for x in col["candidates"]]
        assert (item["category"], item["payload"]["field_key"]) == ("pending", None)
        assert "org_code" not in [x["key"] for x in item["payload"]["candidates"]]
        assert item["reason"] == col["reason"] and item["reason_source"] == "rule"  # 서버 판정 문장 (LLM 문장 아님)
    # 기관관리번호: 값이 모두 사전 코드 → 근거 충돌, 두 경로 모두 같은 판정
    col, item = cols["기관관리번호"], items["기관관리번호"]
    assert (col["status"], col["field_key"], item["payload"]["field_key"]) == ("unresolved", None, None)
    assert "충돌" in col["reason"] and _org_part(item["reason"]) == _org_part(col["reason"])


@pytest.mark.parametrize("use_llm", [False, True], ids=["use_llm=false", "use_llm=true"])
def test_file_analysis_and_review_give_same_org_judgment(api, use_llm: bool) -> None:
    c, _ = api
    fid = _upload_rows(c, f"판정일치_{int(use_llm)}.csv", _api_rows(API_KNOWN, API_UNKNOWN))
    cols = _columns(c, fid, use_llm=use_llm)
    _, items = _review_mapping_items(c, fid)  # 검토는 LLM을 쓴다 → use_llm=true 분석과 같아야 함
    if not use_llm:
        cols = _columns(c, fid, use_llm=True)
    for column in API_HEADER:
        col, item = cols[column], items[column]
        assert col["field_key"] == item["payload"]["field_key"], column
        assert [x["key"] for x in col["candidates"]] == [x["key"] for x in item["payload"]["candidates"]], column
        assert _org_part(item["reason"]) == _org_part(col["reason"]), column


def test_validation_review_and_integration_use_same_glossary_evidence(api, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mapping import MappingEngine as Engine

    c, _ = api
    seen: list[Any] = []
    original = Engine.map_columns

    def spy(self: Engine, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("org_codes"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "map_columns", spy)
    fid = _upload_rows(c, "근거전달.csv", _api_rows(API_KNOWN, API_UNKNOWN))
    _columns(c, fid)
    paths = {"분석": len(seen)}
    assert c.post("/validation", json={"file_ids": [fid]}).status_code == 200
    paths["검증"] = len(seen)
    rid, items = _review_mapping_items(c, fid)
    paths["검토"] = len(seen)
    for item in c.get(f"/reviews/{rid}").json()["items"]:
        if item["category"] == "pending":
            assert (
                c.post(f"/reviews/{rid}/items/{item['id']}/decision", json={"decision": "rejected"}).status_code == 200
            )
    assert c.post(f"/reviews/{rid}/complete").status_code == 200
    run = c.post(f"/reviews/{rid}/integrate", json={"include_unmapped": True})
    assert run.status_code == 201, run.text
    paths["통합"] = len(seen)
    assert paths["분석"] < paths["검증"] < paths["검토"] < paths["통합"], paths  # 네 경로 모두 다시 매핑했다
    assert all(isinstance(k, KnownOrgCodes) for k in seen), seen
    assert len(set(seen)) == 1  # 같은 사전 상태 → 같은 근거
    assert {"Z990001", "Z990003"} <= seen[0].entry and "Z990002" in seen[0].previous


def test_approving_unresolved_item_without_choice_is_refused_and_saves_nothing(api) -> None:
    from app.db import SessionLocal
    from app.models import LearnedAlias, MappingApproval, MappingDecision

    c, _ = api
    fid = _upload_rows(c, "선택없이승인.csv", _api_rows(API_KNOWN, API_UNKNOWN))
    _columns(c, fid)
    rid, items = _review_mapping_items(c, fid)

    def counts() -> tuple[int, int, int]:
        with SessionLocal() as db:
            return (
                db.query(MappingDecision).filter(MappingDecision.source_file_id == fid).count(),
                db.query(MappingApproval).count(),
                db.query(LearnedAlias).count(),
            )

    before = counts()
    for column in ("관리번호", "기관관리번호"):  # 기각·근거 부족 → 연결 항목 없음
        item = items[column]
        assert item["payload"]["field_key"] is None
        for body in (
            {"decision": "approved"},
            {"decision": "approved", "choice": None},
            {"decision": "approved", "choice": ""},
        ):
            res = c.post(f"/reviews/{rid}/items/{item['id']}/decision", json=body)
            assert res.status_code == 409, (column, body, res.text)
    assert counts() == before
    after = {i["before"]: i for i in c.get(f"/reviews/{rid}").json()["items"] if i["kind"] == "mapping"}
    assert after["관리번호"]["decision"] is None and after["기관관리번호"]["decision"] is None


# ================================================================ API: PR #8 공통 검토·다시 분석


KIOSK_HEADER = ["개방자치단체코드", "관리번호", "시군구명", "발급기번호", "초기메뉴번호", "기관식별부호"]
KIOSK_ROWS = [
    ["3220000", "32200002018000000000123", "서울특별시 강남구", "507", "00210", "Z990001"],
    ["3220000", "32200002019000000000456", "서울특별시 강남구", "506", "00200", "Z990002"],
    ["3180000", "31800002020000000000789", "서울특별시 영등포구", "503", "00240", "Z990001"],
]


def test_api_mapping_and_common_review_with_confident_org_llm(api) -> None:
    c, llm = api
    a = _upload_rows(c, "발급기A.csv", [KIOSK_HEADER, *KIOSK_ROWS])
    b = _upload_rows(c, "발급기B.csv", [KIOSK_HEADER, *KIOSK_ROWS])
    cols = _columns(c, a)
    _columns(c, b)
    assert {"관리번호", "발급기번호", "초기메뉴번호", "기관식별부호"} <= set(llm.columns)
    for column in KIOSK_COLUMNS:
        col = cols[column]
        assert (col["status"], col["field_key"], col["method"]) == ("unresolved", None, "llm"), column
        assert "org_code" not in [cd["key"] for cd in col["candidates"]], column
        assert "판단하지 못함" in col["reason"], column
    ok = cols["기관식별부호"]
    assert (ok["status"], ok["field_key"]) == ("review", "org_code")
    # 표본은 중복을 뺀 값 (항목 코드 Z990001 + 개편 전 코드 Z990002, 영문 혼합) — 사전 근거를 따로 센다
    assert "값 2개 모두 7자리 코드 형식, 용어사전에서 확인: 항목 코드 1개·개편 전 코드 1개" in ok["reason"]

    latest = c.get(f"/files/{a}/mapping/latest")
    assert latest.status_code == 200, latest.text
    saved = {col["column"]: col for sheet in latest.json()["sheets"] for col in sheet["columns"]}
    assert all(saved[k]["field_key"] is None for k in KIOSK_COLUMNS)

    # 여러 파일 공통 검토: 두 파일의 관리번호가 '기관코드' 공통 그룹으로 묶여 일괄 확정되지 않는다
    res = c.post("/mapping/common", json={"file_ids": [a, b]})
    assert res.status_code == 200, res.text
    groups = {g["name"]: g for g in res.json()["groups"]}
    for column in KIOSK_COLUMNS:
        g = groups[column]
        assert (g["category"], g["field_key"]) == ("unresolved", None), column
        assert all(m["field_key"] is None and not m["confirmed"] for m in g["members"]), column
    assert (groups["기관식별부호"]["category"], groups["기관식별부호"]["field_key"]) == ("common", "org_code")


def test_api_reanalysis_keeps_user_decisions(api) -> None:
    c, llm = api
    fid = _upload_rows(c, "발급기C.csv", [KIOSK_HEADER, *KIOSK_ROWS])
    _columns(c, fid)
    res = c.put(
        f"/files/{fid}/mapping/decisions",
        json={
            "decisions": [
                {"sheet": "발급기C", "column": "관리번호", "field_key": None},
                {"sheet": "발급기C", "column": "기관식별부호", "field_key": "org_code"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    llm.columns.clear()
    cols = _columns(c, fid)
    assert (cols["관리번호"]["status"], cols["관리번호"]["field_key"]) == ("confirmed", None)
    assert (cols["기관식별부호"]["status"], cols["기관식별부호"]["field_key"]) == ("confirmed", "org_code")
    assert "관리번호" not in llm.columns and "기관식별부호" not in llm.columns
    assert (cols["발급기번호"]["status"], cols["발급기번호"]["field_key"]) == ("unresolved", None)
