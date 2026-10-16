"""LLM의 '기관코드' 제안을 서버가 열 이름·값 형식·용어사전으로 다시 판정한다.

재현: 무인민원발급기 자료의 관리번호(23자리)·발급기번호(3자리)·초기메뉴번호(5자리)를 로컬 qwen2.5:7b가
'기관코드'(확신도 0.7~0.8)로 제안했다. 아래 시험은 모델이 어떤 열이든 기관코드를 확신도 0.99로 답하도록 고정한다.
"""

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import ollama
from app.mapping import MappingEngine, load_dictionary, normalize, org_code_evidence

CONFIDENT_ORG = {"key": "org_code", "confidence": 0.99, "reason": "기관을 식별하는 코드로 보입니다"}


class FarEmbed:
    """표준 사전(첫 호출)과 열 이름(이후 호출)의 벡터가 직교 → 임베딩 유사도 0.
    실제 재현(bge-m3)처럼 이 열들이 유사도 단계에서 정해지지 않고 LLM으로 넘어가게 한다."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.0] if self.calls == 1 else [0.0, 1.0] for _ in texts]


class AlwaysOrgLlm:
    def __init__(self) -> None:
        self.columns: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.columns.append(user.split("열 이름: ", 1)[1].split("\n", 1)[0])
        return dict(CONFIDENT_ORG)


def _engine(llm: Any) -> MappingEngine:
    return MappingEngine(load_dictionary(), FarEmbed(), llm)


def _map(columns: list[str], samples: list[list[Any]], llm: Any | None = None, **kwargs: Any) -> dict[str, Any]:
    result = _engine(llm or AlwaysOrgLlm()).map_columns(columns, samples, **kwargs)
    return {c.column: c for c in result.columns}


def _candidate_keys(col: Any) -> list[str]:
    return [c.key for c in col.candidates]


KIOSK_COLUMNS = ["관리번호", "발급기번호", "초기메뉴번호"]
KIOSK_SAMPLES = [
    ["32200002018000000000123", "32200002019000000000456", "31800002020000000000789"],
    ["507", "506", "503"],
    ["00210", "00200", "00240"],
]


# ---------------------------------------------------------------- 기존 오제안 (수정 전 실패)


def test_reported_kiosk_identifiers_are_not_proposed_as_org_code() -> None:
    llm = AlwaysOrgLlm()
    m = _map(KIOSK_COLUMNS, KIOSK_SAMPLES, llm)
    assert llm.columns == KIOSK_COLUMNS  # 세 열 모두 실제로 LLM 단계에서 기관코드를 받았다
    for column in KIOSK_COLUMNS:
        col = m[column]
        assert (col.status, col.field_key, col.method, col.score) == ("unresolved", None, "llm", None), column
        assert "org_code" not in _candidate_keys(col), column  # 기본 선택·추천으로 다시 채택되지 않게
        assert "LLM 제안 '기관코드'를 쓰지 않음" in col.reason and "판단하지 못함" in col.reason, column
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
    m = _map(["기관식별부호"], [["507", "506", "503"]])
    col = m["기관식별부호"]
    assert (col.status, col.field_key) == ("unresolved", None)
    assert "기관코드 형식(숫자·영문 대문자 7자리)이 아님 (3자리)" in col.reason


def test_conflicting_name_and_glossary_values_are_insufficient() -> None:
    """이름은 다른 식별자인데 값이 모두 용어사전 기관코드 → 근거가 충돌해 판단하지 못함 (후보는 남김)."""
    m = _map(["관리번호"], [["1741000", "1360000"]], org_codes=frozenset({"1741000", "1360000"}))
    col = m["관리번호"]
    assert (col.status, col.field_key, col.method) == ("unresolved", None, "llm")
    assert "근거 부족" in col.reason and "충돌" in col.reason
    assert "org_code" in _candidate_keys(col)


# ---------------------------------------------------------------- 정상 기관코드는 그대로 확인 대상


def test_numeric_org_codes_stay_a_review_proposal() -> None:
    m = _map(["기관식별부호"], [[1741000, 1360000, 1310000]])
    col = m["기관식별부호"]
    assert (col.status, col.field_key, col.method, col.score) == ("review", "org_code", "llm", 0.99)
    assert col.reason.startswith("LLM 제안: ") and "근거: 값 3개 모두 7자리 코드 형식" in col.reason


def test_alphanumeric_codes_in_glossary_format_are_accepted() -> None:
    known = frozenset({"B551011", "A123456"})
    m = _map(["수요처코드"], [["B551011", "A123456"]], org_codes=known)
    col = m["수요처코드"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert "용어사전에서 확인된 기관코드 2개" in col.reason


def test_well_formed_code_missing_from_glossary_is_not_invalid() -> None:
    m = _map(["수요처코드"], [["9990001", "9990002"]], org_codes=frozenset({"1741000"}))
    col = m["수요처코드"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert "용어사전에서 확인된 기관코드 0개" in col.reason


def test_leading_zero_codes_kept_as_strings() -> None:
    samples = ["0012345", "0098765"]
    m = _map(["기관식별부호"], [list(samples)])
    col = m["기관식별부호"]
    assert (col.status, col.field_key) == ("review", "org_code")
    assert col.samples == samples  # 값을 자르거나 바꾸지 않음


def test_number_cell_that_lost_leading_zeros_is_not_padded() -> None:
    m = _map(["기관식별부호"], [[12345, 98765]])
    col = m["기관식별부호"]
    assert (col.status, col.field_key) == ("unresolved", None)
    assert "(5자리)" in col.reason
    assert col.samples == [12345, 98765]


# ---------------------------------------------------------------- 판정 규칙 (숫자·문자열 셀, 빈 값, 혼합 형식)


@pytest.mark.parametrize(
    ("column", "samples", "known", "verdict", "text"),
    [
        ("기관식별부호", [1741000, "1360000"], None, "accept", "값 2개 모두 7자리 코드 형식"),
        ("기관식별부호", ["1741000", None, "", "  ", "1360000"], None, "accept", "값 2개 모두"),
        ("기관식별부호", [" 1741000 ", "1360000"], frozenset({"1741000"}), "accept", "확인된 기관코드 1개"),
        ("기관식별부호", ["1741000", None, ""], None, "insufficient", "2개 미만"),
        ("기관식별부호", [], None, "insufficient", "2개 미만"),
        ("기관식별부호", ["1741000", "12345", "ABC"], None, "insufficient", "섞여 있음 (7자리 코드 형식 1/3개)"),
        ("기관식별부호", ["1741000", 1741000.5], None, "insufficient", "섞여 있음"),
        ("기관식별부호", ["b551011", "a123456"], None, "reject", "형식"),  # 소문자는 형식이 아님 (바꾸지 않음)
        ("기관식별부호", ["17410001", "13600002"], None, "reject", "(8자리)"),  # 자르지 않음
        ("기관식별부호", [True, False], None, "reject", "코드가 아닌 값"),
        ("발급기번호", ["1741000", "1360000"], None, "reject", "'발급기'"),
        ("관리번호", ["1741000", "9999999"], frozenset({"1741000"}), "reject", "'관리번호'"),
        ("관리번호", ["1741000", "1360000"], frozenset({"1741000", "1360000"}), "insufficient", "충돌"),
    ],
)
def test_org_code_evidence_rules(column: str, samples: list, known: frozenset[str] | None, verdict: str, text: str):
    got, reason = org_code_evidence(column, samples, known)
    assert got == verdict and text in reason, (got, reason)


# ---------------------------------------------------------------- 지키는 기존 동작


def test_region_codes_are_not_turned_into_org_code() -> None:
    llm = AlwaysOrgLlm()
    m = _map(["행정동코드", "열5"], [["1168064000", "1150061000"], ["11500", "11680", "26440"]], llm)
    assert (m["행정동코드"].field_key, m["행정동코드"].method) == ("region_code", "exact")
    assert (m["열5"].field_key, m["열5"].status, m["열5"].method) == ("region_code", "auto", "pattern")
    assert llm.columns == []  # 행정구역코드로 정해진 열은 LLM으로 가지 않는다


def test_user_decisions_and_none_are_kept() -> None:
    llm = AlwaysOrgLlm()
    m = _map(KIOSK_COLUMNS, KIOSK_SAMPLES, llm, decisions={"관리번호": "org_code", "발급기번호": None})
    # 사용자 확정은 서버 판정보다 우선한다 (사용자가 기관코드로 확정한 열도 그대로)
    assert (m["관리번호"].status, m["관리번호"].field_key, m["관리번호"].method) == ("confirmed", "org_code", "user")
    assert (m["발급기번호"].status, m["발급기번호"].field_key) == ("confirmed", None)
    assert m["발급기번호"].reason == "사용자가 '해당 없음'으로 확정"  # 시스템 '판단하지 못함'과 구분
    assert llm.columns == ["초기메뉴번호"]


def test_dictionary_standard_and_learned_aliases_keep_priority() -> None:
    llm = AlwaysOrgLlm()
    m = _map(
        ["기관 코드", "발급기번호", "관리번호"],
        [["1741000", "1360000"], ["507", "506"], ["32200002018000000000123", "32200002019000000000456"]],
        llm,
        standard={normalize("발급기번호"): "note"},
        learned={normalize("관리번호"): "org_code"},
    )
    assert (m["기관 코드"].field_key, m["기관 코드"].method) == ("org_code", "exact")
    assert (m["발급기번호"].field_key, m["발급기번호"].method) == ("note", "standard")
    assert (m["관리번호"].field_key, m["관리번호"].method, m["관리번호"].status) == ("org_code", "learned", "auto")
    assert llm.columns == []


def test_other_llm_proposals_are_unchanged() -> None:
    class NoteLlm:
        def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            return {"key": "note", "confidence": 0.7, "reason": "비고"}

    m = _map(["관리번호"], [["32200002018000000000123", "32200002019000000000456"]], NoteLlm())
    col = m["관리번호"]
    assert (col.status, col.field_key, col.method, col.score) == ("review", "note", "llm", 0.7)
    assert col.reason == "LLM 제안: 비고"


def test_llm_unavailable_fallback_is_unchanged() -> None:
    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise ollama.OllamaError("connection refused")

    result = _engine(down).map_columns(KIOSK_COLUMNS, KIOSK_SAMPLES)
    assert result.llm == "unavailable"
    for col in result.columns:
        assert (col.status, col.field_key, col.reason) == ("unresolved", None, "LLM을 사용할 수 없어 미해결로 남김")
        assert "org_code" in _candidate_keys(col)  # 유사도 후보는 그대로 (사용자가 고를 수 있음)


# ---------------------------------------------------------------- API: 업로드 → 매핑 → 공통 검토 → 다시 분석


KIOSK_HEADER = ["개방자치단체코드", "관리번호", "시군구명", "발급기번호", "초기메뉴번호", "기관식별부호"]
KIOSK_ROWS = [
    ["3220000", "32200002018000000000123", "서울특별시 강남구", "507", "00210", "Z990001"],
    ["3220000", "32200002019000000000456", "서울특별시 강남구", "506", "00200", "Z990002"],
    ["3180000", "31800002020000000000789", "서울특별시 영등포구", "503", "00240", "Z990001"],
]


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    from app import files
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models import GlossaryEntry

    Base.metadata.create_all(engine)
    llm = AlwaysOrgLlm()
    monkeypatch.setattr(files, "get_engine", lambda: _engine(llm))
    # 시험 DB는 다른 시험과 함께 쓰므로, 다른 시험이 쓰지 않는 코드로 넣고 넣은 행만 지운다
    with SessionLocal() as db:
        entry = GlossaryEntry(code="Z990001", name="시험기관", normalized="시험기관", previous_codes="Z990002")
        db.add(entry)
        db.commit()
        entry_id = entry.id
    try:
        with TestClient(app) as c:
            yield c, llm
    finally:
        with SessionLocal() as db:
            db.query(GlossaryEntry).filter(GlossaryEntry.id == entry_id).delete()
            db.commit()


def _upload_kiosk(c: TestClient, name: str) -> int:
    res = c.post("/files", files=[("files", (name, _csv([KIOSK_HEADER, *KIOSK_ROWS]), "text/csv"))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _columns(c: TestClient, fid: int) -> dict[str, dict]:
    res = c.post(f"/files/{fid}/mapping")
    assert res.status_code == 200, res.text
    return {col["column"]: col for col in res.json()["sheets"][0]["columns"]}


def test_api_mapping_and_common_review_with_confident_org_llm(api) -> None:
    c, llm = api
    a, b = _upload_kiosk(c, "발급기A.csv"), _upload_kiosk(c, "발급기B.csv")
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
    # 표본은 중복을 뺀 값 (현행 코드 Z990001 + 개편 전 코드 Z990002, 영문 혼합) — 둘 다 용어사전 근거로 센다
    assert "값 2개 모두 7자리 코드 형식, 용어사전에서 확인된 기관코드 2개" in ok["reason"]

    # 저장된 분석(작업 화면 다시 열기)도 같은 상태
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
    fid = _upload_kiosk(c, "발급기C.csv")
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
