"""교차 검증(커밋 1ec3fad)에서 재현된 결함의 회귀 테스트. 번호는 검증 보고서의 항목 번호."""

import threading
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import llm
from app.glossary import AliasEntry, Entry, Glossary, period_end
from app.mapping import MappingEngine, date_field_for, load_dictionary
from app.parsing import parse_file
from app.validation import ValidationParams, reference_period, validate
from tests.test_parsing import _xlsx_rows
from tests.test_validation import codes, make_input, make_xlsx

# ---------------------------------------------------------------- ① 서로 다른 연도 열


def test_year_columns_under_merged_header_are_kept(tmp_path: Path) -> None:
    rows = [["지역", "인구", None], ["지역", 2024, 2025], ["서울", 100, 100], ["부산", 200, 200]]
    sheet = parse_file(_xlsx_rows(tmp_path, rows, ("A1:A2", "B1:C1"))).sheets[0]
    assert sheet.columns == ["지역", "인구_2024", "인구_2025"]
    assert [r["cells"] for r in sheet.body if r["kind"] == "data"] == [["서울", 100, 100], ["부산", 200, 200]]


def test_true_merge_copy_with_year_is_still_dropped(tmp_path: Path) -> None:
    """머리글 전체 의미가 같은 병합 복제 열('인구_2024'가 두 칸에 병합)은 지금처럼 하나만 남긴다."""
    rows = [
        [None, "인구", None, None, "세대"],
        ["지역", 2024, None, 2025, 2025],
        ["서울", 100, None, 110, 50],
        ["부산", 200, None, 210, 60],
    ]
    merges = ("B1:D1", "B2:C2", "B3:C3", "B4:C4")
    sheet = parse_file(_xlsx_rows(tmp_path, rows, merges)).sheets[0]
    assert sheet.columns == ["지역", "인구_2024", "인구_2025", "세대_2025"]


# ---------------------------------------------------------------- ② 위쪽 소계 예외


def test_grand_total_error_not_hidden_by_first_row(tmp_path: Path) -> None:
    rows = [["지역", "인구"], ["합계", 10], ["서울", 10], ["부산", 20]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


def test_above_subtotal_needs_complete_group(tmp_path: Path) -> None:
    """그룹 소계라도 아래 행 일부(한 그룹을 다 덮지 못한 행)와 맞는 것만으로는 통과시키지 않는다."""
    rows = [
        ["시도", "시군구", "구", "관용", "자가용"],
        ["충남", "계", "계", 112, 122934],
        ["충남", "천안시", "동남구", 112, 122934],
        ["충남", "천안시", "서북구", 168, 187891],
        ["충남", "공주시", "공주시", 241, 43046],
    ]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


# ---------------------------------------------------------------- ④ 최초 소유자 경쟁


def test_initial_owner_setup_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth_api
    from app.db import Base
    from app.models import AuthSession, User

    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    barrier = threading.Barrier(2)
    real_hash = auth_api.hash_password

    def slow_hash(password: str) -> str:  # 두 요청이 모두 '계정 0개' 검사를 통과한 뒤 저장하게 만든다
        barrier.wait(timeout=10)
        return real_hash(password)

    monkeypatch.setattr(auth_api, "hash_password", slow_hash)
    outcomes: list[Any] = []

    def attempt(name: str) -> None:
        with Session() as db:
            try:
                body = auth_api.SetupIn(username=name, display_name=name, password="password-123")
                auth_api.setup(body, Response(), db)
                outcomes.append(name)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("ownera", "ownerb")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(map(str, outcomes)) == sorted(["409", next(o for o in outcomes if isinstance(o, str))])
    with Session() as db:
        users = list(db.scalars(select(User)))
        assert [u.role for u in users] == ["owner"]
        sessions = list(db.scalars(select(AuthSession)))
        assert sessions and all(s.user_id == users[0].id for s in sessions)
    # 소유자가 있는 설치는 다시 열리지 않는다
    with Session() as db:
        with pytest.raises(HTTPException) as info:
            auth_api.setup(
                auth_api.SetupIn(username="late", display_name="late", password="password-123"), Response(), db
            )
        assert info.value.status_code == 409
        assert db.scalar(select(func.count(User.id))) == 1


# ---------------------------------------------------------------- ⑤ 열 이름 개인정보


def test_column_names_masked_before_model_input() -> None:
    prompts: list[str] = []
    embedded: list[str] = []

    def fake_llm(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        return {"key": "none", "confidence": 0, "reason": "모름"}

    def fake_embed(texts: list[str]) -> list[list[float]]:
        embedded.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    engine = MappingEngine(load_dictionary(), fake_embed, fake_llm)
    columns = ["alice@example.com", "bob@example.com", "010-1234-5678 담당", "900101-1234567"]
    result = engine.map_columns(columns, [["가"], ["나"], ["다"], ["라"]])
    sent = "\n".join(prompts + embedded)
    for raw in ("alice@example.com", "bob@example.com", "1234-5678", "1234567"):
        assert raw not in sent, raw
    assert len(prompts) == 4  # 마스킹 결과가 같아도 열마다 따로 판정
    assert [c.column for c in result.columns] == columns  # 확정·재분석에 쓰는 열 식별자는 원래 이름


# ---------------------------------------------------------------- ⑥ 날짜 매핑과 기간 검사


@pytest.mark.parametrize(
    ("value", "blocked"),
    [("2026-08", True), ("2026-09", False), ("2026년 8월", True), ("2026-08-31", True), ("2026.09.30", False)],
)
def test_date_mapped_column_feeds_period_check(tmp_path: Path, value: str, blocked: bool) -> None:
    rows = [["시점", "건수"], [value, 10]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams(expected_period="2026-09"))
    assert ("period_mismatch" in codes(result.blocks)) is blocked


def test_invalid_dates_and_codes_are_not_dates() -> None:
    assert date_field_for(["2026-13"]) is None
    assert date_field_for(["2026-02-30"]) is None
    assert date_field_for(["20260831"]) is None  # 코드형 숫자 글자
    assert date_field_for(["2026-08", "2026년 9월"]) == "month"
    assert date_field_for(["2026-02-28"]) == "base_date"


# ---------------------------------------------------------------- ⑦ 월별 실적 vs 누적 통계


def test_monthly_flow_total_is_checked(tmp_path: Path) -> None:
    rows = [["월", "건수"], ["합계", 999], ["1월", 10], ["2월", 20]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


def test_stock_series_needs_evidence_and_records_it(tmp_path: Path) -> None:
    # 시점 값 항목(승용차 등록대수) + 합계가 가장 늦은 시점(3월)의 값과 같음 → 누적 자료로 보고 근거를 남긴다
    # (병합 전 보완: 중간 시점과 같은 것만으로는 예외로 두지 않는다)
    rows = [["월별", "승용차"], ["합계", 765120], ["1월", 764786], ["2월", 765931], ["3월", 765120]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("누적 자료" in n and "승용차" in n for n in result.notes)


@pytest.mark.parametrize("column", ["전입 인구", "인구 이동", "보유 차량 증감"])
def test_broad_item_names_do_not_exempt_monthly_flow(tmp_path: Path, column: str) -> None:
    """⑦ 재검증: 열 이름의 넓은 단어('인구' 등)만으로 월별 발생 실적의 합계 오류를 예외로 두지 않는다."""
    rows = [["월", column], ["합계", 999], ["1월", 10], ["2월", 20]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


def test_stock_field_with_total_unlike_any_period_is_checked(tmp_path: Path) -> None:
    """⑦ 재검증: 시점 값 항목이라도 합계가 어느 시점 값과도 다르면(발생 실적일 수 있음) 그대로 검사한다."""
    rows = [["월별", "승용차"], ["합계", 773919], ["1월", 764786], ["2월", 765931]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


def test_lone_total_column_equal_to_last_month_is_checked(tmp_path: Path) -> None:
    """병합 전 보완: 시점 값 근거가 없는 합계 열 하나는 '마지막 시점 값과 같다'만으로 예외로 두지 않는다.
    (지난 라운드에는 항목 이름과 관계없이 예외였다 — 실제 KOSIS 형태는 test_kosis_vehicle_table_with_total_column)"""
    rows = [["월별(1)", "2024_총계_계"], ["합계", 1002889], ["1월", 994549], ["11월", 1002336], ["12월", 1002889]]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


# ---------------------------------------------------------------- ⑨ 미래 용어사전 항목


def test_future_entries_not_valid_without_reference_date() -> None:
    entries = [Entry("9990001", "미래시", "기초자치단체", valid_from=date(2099, 1, 1))]
    future_alias = {"미래별칭": [AliasEntry("9990001", date(2099, 1, 1))]}
    g = Glossary(entries, future_alias)
    res = g.resolve("미래시")
    assert res.status != "resolved" or res.note, res  # 기간 밖 항목은 그대로 확정하지 않고 안내를 붙인다
    assert g.resolve("미래별칭").status == "unknown"
    assert g.resolve("미래시", period_end("2099-01")).note is None  # 기준일이 기간 안이면 그대로
    today = date.today()
    edge = Glossary([Entry("9990002", "오늘시", valid_from=today, valid_to=today + timedelta(days=1))], {})
    assert edge.resolve("오늘시").note is None


# ---------------------------------------------------------------- ⑩ 기준일 전파


def test_reference_period_requires_every_file(tmp_path: Path) -> None:
    known = make_input(make_xlsx(tmp_path / "자료_20240331.xlsx", [["지역", "관용"], ["광주", 1]]), file_id=1)
    unknown = make_input(make_xlsx(tmp_path / "기간없음.xlsx", [["지역", "관용"], ["전남", 2]]), file_id=2)
    period, inferred, reason = reference_period([known, unknown], None)
    assert (period, inferred) == (None, False) and reason and "기간없음.xlsx" in reason
    assert reference_period([known], None)[:2] == ("2024-03", True)
    other = make_input(make_xlsx(tmp_path / "자료_20250131.xlsx", [["지역", "관용"], ["광주", 1]]), file_id=3)
    assert reference_period([known, other], None)[0] is None
    tie = make_input(make_xlsx(tmp_path / "2024년 3월_2024년 4월.xlsx", [["지역", "관용"], ["광주", 1]]), file_id=4)
    assert reference_period([tie], None)[0] is None
    assert reference_period([known, unknown], "2026-08")[:2] == ("2026-08", False)


# ---------------------------------------------------------------- ⑪ 100행 경계


@pytest.mark.parametrize("header_at", [99, 100])
def test_header_at_search_boundary(tmp_path: Path, header_at: int) -> None:
    rows: list[list] = [["통계표 제목", None]]
    rows += [[None, None]] * (header_at - 2)
    rows += [["지역", "인구"], ["서울", 100]]
    sheet = parse_file(_xlsx_rows(tmp_path, rows)).sheets[0]
    assert sheet.header_row == header_at and sheet.columns == ["지역", "인구"]


# ---------------------------------------------------------------- ⑫ 오류 메시지 키 노출

SECRET = "sk-test-0123456789abcdefXYZ"


class _HttpError(urllib.error.HTTPError):
    def __init__(self, body: str) -> None:
        super().__init__("https://api.example.invalid", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        self._body = body.encode()

    def read(self, *_: Any) -> bytes:  # type: ignore[override]
        return self._body


@pytest.mark.parametrize("offset", [0, 150, 190, 199, 400])
def test_error_message_never_contains_key_fragment(monkeypatch: pytest.MonkeyPatch, offset: int) -> None:
    body = "x" * offset + SECRET + " / again " + SECRET + " Authorization: Bearer " + SECRET

    def failing(req: Any, timeout: float) -> Any:
        raise _HttpError(body)

    monkeypatch.setattr(llm.urllib.request, "urlopen", failing)
    with pytest.raises(llm.LlmError) as info:
        llm.chat_json("s", "u", {"type": "object"}, config=llm.LlmConfig(mode="openai", model="m", api_key=SECRET))
    message = str(info.value)
    for size in range(8, len(SECRET) + 1):  # 키의 어떤 앞부분(8자 이상)도 남지 않는다
        assert SECRET[:size] not in message, (offset, message)


def test_pii_column_names_are_not_learned_or_promoted() -> None:
    """⑤ 관련: 개인정보가 든 열 이름은 개인 학습 별칭·전사 승격 후보로 퍼지지 않는다 (이 파일의 확정은 저장)."""
    from app.db import Base, SessionLocal
    from app.db import engine as db_engine
    from app.decisions import learn_column
    from app.promotion import record_approval

    Base.metadata.create_all(db_engine)
    engine = MappingEngine(load_dictionary(), None, None)
    with SessionLocal() as db:
        assert learn_column(db, engine, 1, "alice@example.com 담당", "note", None) is not None
        assert record_approval(db, engine, 1, "alice@example.com 담당", "note") is None
        db.rollback()


def test_header_search_range_edges(tmp_path: Path) -> None:
    """⑪ 경계: 범위 밖(101행) 머리글은 찾지 않고, 범위 안 머리글 + 범위 밖 데이터는 찾고, 글자만 있는 표는 예전 규칙."""
    beyond = [["제목", None]] + [[None, None]] * 99 + [["지역", "인구"], ["서울", 100]]
    assert parse_file(_xlsx_rows(tmp_path, beyond)).sheets[0].header_row != 101
    late_data = [["지역", "인구"]] + [[None, None]] * 120 + [["서울", 100]]
    sheet = parse_file(_xlsx_rows(tmp_path, late_data)).sheets[0]
    assert sheet.header_row == 1 and sheet.columns == ["지역", "인구"]
    text_only = [["기관", "담당"], ["행정안전부", "재정과"], ["기상청", "기획과"]]
    assert parse_file(_xlsx_rows(tmp_path, text_only)).sheets[0].header_row == 1


def test_url_error_and_auth_header_redacted_without_known_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑫: 저장된 키를 모르는 경우에도 'Bearer …'·'sk-…' 형태는 지운다 (URLError 경로 포함)."""

    def failing(req: Any, timeout: float) -> Any:
        raise urllib.error.URLError(f"proxy said Authorization: Bearer {SECRET} x-api-key: {SECRET}")

    monkeypatch.setattr(llm.urllib.request, "urlopen", failing)
    with pytest.raises(llm.LlmError) as info:
        llm.chat_json("s", "u", {"type": "object"}, config=llm.LlmConfig(mode="custom", model="m", base_url="http://x"))
    assert SECRET[:8] not in str(info.value)


def test_above_subtotal_with_hidden_value_is_not_accepted(tmp_path: Path) -> None:
    """②: 아래 행에 비공개 값(x)이 있으면 위쪽 소계로 확인할 수 없어 예외를 적용하지 않는다."""
    rows = [
        ["시도", "시군구", "구", "관용", "자가용"],
        ["충남", "계", "계", 280, 310825],
        ["충남", "천안시", "동남구", 112, "x"],
        ["충남", "천안시", "서북구", 168, 187891],
        ["충남", "공주시", "공주시", 241, 43046],
    ]
    result = validate([make_input(make_xlsx(tmp_path / "t.xlsx", rows))], ValidationParams())
    assert not any("위쪽에 놓인 소계" in n for n in result.notes)


def test_future_entries_are_candidates_not_keys(tmp_path: Path) -> None:
    """⑨ 재검증: 아직 시작하지 않은 항목·별칭 대상은 이름·별칭 어느 경로로도 확정 키가 되지 않고 후보로만 나온다."""
    future = Entry("9990001", "미래구청", "기관", valid_from=date(2099, 1, 1))
    g = Glossary([future, Entry("1741000", "행정안전부", "중앙행정기관")], {"미래": [AliasEntry("9990001")]})
    for name in ("미래구청", "미래"):
        res = g.resolve(name)
        assert (res.status, res.key) == ("unknown", None), (name, res)
        assert [c.code for c in res.candidates] == ["9990001"] and "부터 유효" in (res.note or "")
    f = make_input(make_xlsx(tmp_path / "t.xlsx", [["기관명", "집행액"], ["행정안전부", 1], ["미래구청", 2]]))
    notes = {e.value: e for e in validate([f], ValidationParams(), glossary=g).entities}
    assert notes["미래구청"].status == "unknown" and notes["미래구청"].candidates[0]["code"] == "9990001"
    ended = Glossary([Entry("1051000", "기획재정부", valid_to=date(2025, 12, 31))], {})
    assert ended.resolve("기획재정부").status == "resolved"  # 폐지 항목은 기존처럼 옛 자료 비교용으로 해석 + 안내


@pytest.mark.parametrize(
    "value",
    ["2026-08", "2026년 8월", "2026.08.31", "2026년 8월 31일", "2026년", "2026-13", "2026-02-30", "서울", "1월"],
)
def test_one_date_rule_for_mapping_period_and_glossary(value: str) -> None:
    """⑥ 재검증: 매핑 날짜 필터·기간 해석·용어 해석이 같은 판정을 쓴다 (실제로 없는 날짜는 셋 다 날짜 아님)."""
    from app.glossary import looks_like_date, parse_date_value

    is_date = parse_date_value(value) is not None
    assert looks_like_date(value) is is_date
    assert (date_field_for([value]) is not None) is is_date
    assert is_date is (value not in ("2026-13", "2026-02-30", "서울", "1월"))


def test_header_search_scope_is_bounded(tmp_path: Path) -> None:
    """③ 재검증: 머리글 후보는 100행, 첫 데이터 행은 200행 안에서 찾는다. 글자 목록 뒤 멀리 떨어진 숫자 한 줄로
    본문 행을 머리글로 고르지 않고(예전처럼 위 3행 규칙), 다단 머리글은 그대로 찾는다."""
    from app.parsing import DATA_SEARCH_ROWS, detect_header_row

    text_list = [["기관", "담당"]] + [[f"기관{i}", f"담당 부서 {i} 과"] for i in range(300)] + [["합계", 3]]
    assert detect_header_row(text_list) == 0
    far = [["지역", "인구"]] + [[None, None]] * (DATA_SEARCH_ROWS + 5) + [["서울", 100]]
    assert detect_header_row(far) == 0  # 데이터가 범위 밖이면 대체 규칙(위 3행)
    rows = [["통계표", None, None]] + [[None, None, None]] * 96 + [["지역", "인구", None], ["지역", 2024, 2025]]
    rows += [["서울", 100, 110]]
    sheet = parse_file(_xlsx_rows(tmp_path, rows, ("B98:C98", "A98:A99"))).sheets[0]
    assert (sheet.header_row, sheet.header_row_end) == (98, 99) and sheet.columns == ["지역", "인구_2024", "인구_2025"]


_SETUP_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from fastapi import HTTPException, Response
from app import auth_api
from app.db import SessionLocal

flag_dir, name = Path(sys.argv[1]), sys.argv[2]
real_hash = auth_api.hash_password

def gated_hash(password):  # '계정 0개' 검사를 통과한 뒤, 두 프로세스가 모두 여기 올 때까지 기다린다
    (flag_dir / f"ready-{name}").touch()
    deadline = time.time() + 20
    while len(list(flag_dir.glob("ready-*"))) < 2 and time.time() < deadline:
        time.sleep(0.02)
    return real_hash(password)

auth_api.hash_password = gated_hash
with SessionLocal() as db:
    try:
        auth_api.setup(auth_api.SetupIn(username=name, display_name=name, password="password-123"), Response(), db)
        print(json.dumps({"name": name, "result": "created"}))
    except HTTPException as exc:
        print(json.dumps({"name": name, "result": exc.status_code}))
"""


def test_initial_owner_race_across_processes(tmp_path: Path) -> None:
    """④ 재검증: 별도 프로세스·별도 DB 연결의 동시 최초 설정에서도 소유자는 한 명, 진 쪽은 409·계정/세션 없음."""
    import json
    import os
    import sqlite3
    import subprocess
    import sys

    data = tmp_path / "data"
    flags = tmp_path / "flags"
    data.mkdir()
    flags.mkdir()
    env = {**os.environ, "MOAJWO_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"}
    env.pop("DATABASE_URL", None)
    backend = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = str(backend)  # 자식 스크립트는 임시 폴더에 있으므로 app 패키지 경로를 알려 준다
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env, check=True,
                   capture_output=True)  # fmt: skip
    script = tmp_path / "child.py"
    script.write_text(_SETUP_CHILD, encoding="utf-8")
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(flags), n],
            cwd=backend,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )  # fmt: skip
        for n in ("ownera", "ownerb")
    ]
    outputs = [p.communicate(timeout=60) for p in procs]
    assert all(out.strip() for out, _ in outputs), [err[-500:] for _, err in outputs]
    results = [json.loads(out.strip().splitlines()[-1]) for out, _ in outputs]
    assert sorted(str(r["result"]) for r in results) == ["409", "created"], results
    winner = next(r["name"] for r in results if r["result"] == "created")
    with sqlite3.connect(data / "moajwo.db") as db:
        users = db.execute("SELECT id, username, role FROM users").fetchall()
        sessions = db.execute("SELECT DISTINCT user_id FROM auth_sessions").fetchall()
    assert [(u[1], u[2]) for u in users] == [(winner, "owner")]
    assert sessions == [(users[0][0],)]


def test_mapping_logs_do_not_contain_pii_column_names(caplog: pytest.LogCaptureFixture) -> None:
    """⑤ 재검증: 임베딩·LLM을 쓸 수 없는 오류 경로에서도 로그에 열 이름 원문이 남지 않는다."""
    import logging

    from app import ollama

    def broken_embed(texts: list[str]) -> list[list[float]]:
        raise ollama.OllamaError("embedding down")

    def broken_llm(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise ollama.OllamaError("llm down")

    engine = MappingEngine(load_dictionary(), broken_embed, broken_llm)
    with caplog.at_level(logging.DEBUG):
        result = engine.map_columns(["alice@example.com", "900101-1234567"], [["가"], ["나"]])
    assert result.embedding == "unavailable" and result.llm == "unavailable"
    assert "alice@example.com" not in caplog.text and "1234567" not in caplog.text


# ---------------------------------------------------------------- 병합 전 보완 ②: 누적 예외의 독립 근거


def _sum_result(tmp_path: Path, rows: list[list], name: str = "t.xlsx", title: str | None = None):
    if title:
        rows = [[title] + [None] * (len(rows[0]) - 1), *rows]
    path = make_xlsx(tmp_path / name, rows, merges=(f"A1:{chr(64 + len(rows[1]))}1",) if title else ())
    return validate([make_input(path)], ValidationParams())


def test_count_total_equal_to_last_month_is_still_checked(tmp_path: Path) -> None:
    """마지막 값과 같다는 것만으로는 누적 자료가 아니다 (건수 합계 20 vs 실제 30 → 차단)."""
    result = _sum_result(tmp_path, [["월", "건수"], ["합계", 20], ["1월", 10], ["2월", 20]])
    assert "sum_mismatch" in codes(result.blocks)
    assert not any("누적 자료" in n for n in result.notes)


@pytest.mark.parametrize("column", ["집행액", "전입 인구"])
def test_flow_totals_equal_to_last_month_are_checked(tmp_path: Path, column: str) -> None:
    result = _sum_result(tmp_path, [["월", column], ["합계", 20], ["1월", 10], ["2월", 20]])
    assert "sum_mismatch" in codes(result.blocks)


def test_stock_uses_latest_period_not_physical_last_row(tmp_path: Path) -> None:
    """역순(12월이 맨 위) 자료도 마지막 시점(12월) 값으로 판단한다."""
    rows = [["월별", "승용차"], ["합계", 1002889], ["12월", 1002889], ["11월", 1002336], ["1월", 994549]]
    result = _sum_result(tmp_path, rows)
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("마지막 시점(12월)" in n and "승용차" in n for n in result.notes)
    # 물리적 마지막 행(1월)과 같다는 이유로는 예외를 두지 않는다
    rows = [["월별", "승용차"], ["합계", 994549], ["12월", 1002889], ["11월", 1002336], ["1월", 994549]]
    assert "sum_mismatch" in codes(_sum_result(tmp_path, rows, "rev2.xlsx").blocks)


def test_mixed_period_labels_are_not_guessed(tmp_path: Path) -> None:
    """시점 순서를 알 수 없으면(서로 다른 형식이 섞임) 예외를 적용하지 않고 이유를 남긴다."""
    rows = [["월별", "승용차"], ["합계", 300], ["2024년", 100], ["2월", 300], ["1분기", 200]]
    result = _sum_result(tmp_path, rows)
    assert "sum_mismatch" in codes(result.blocks)
    assert any("시점 순서" in n for n in result.notes)


def test_mixed_nature_columns_are_judged_per_column(tmp_path: Path) -> None:
    """시점 값 열(인구)과 발생 실적 열(전입건수)이 섞인 표: 인구만 예외, 전입건수 합계 오류는 차단."""
    rows = [["월", "인구", "전입건수"], ["합계", 1200, 15], ["1월", 1100, 10], ["2월", 1200, 20]]
    result = _sum_result(tmp_path, rows)
    blocked = {b.column for b in result.blocks if b.code == "sum_mismatch"}
    assert blocked == {"전입건수"}
    assert any("누적 자료" in n and "인구" in n for n in result.notes)


def test_kosis_vehicle_table_with_total_column(tmp_path: Path) -> None:
    """KOSIS 등록대수: 항목이 모두 등록대수(승용차 등)이고 합계 열(총계_계)도 같은 성격이면 표 전체 예외."""
    rows = [
        ["월별(1)", "2024_총계_계", "2024_승용차_계", "2024_승합차_계"],
        ["합계", 1002889, 773919, 28938],
        ["1월", 994549, 764786, 29357],
        ["12월", 1002889, 773919, 28938],
    ]
    result = _sum_result(tmp_path, rows)
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("마지막 시점(12월)" in n for n in result.notes)


def test_title_wording_is_independent_evidence(tmp_path: Path) -> None:
    """항목 매핑이 없어도 제목의 '말 기준 잔액' 같은 표기는 독립 근거가 된다."""
    rows = [["월", "금액"], ["합계", 300], ["1월", 100], ["2월", 300]]
    result = _sum_result(tmp_path, rows, title="2026년 2월말 기준 대출 잔액")
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("'잔액' 표기" in n for n in result.notes)


def test_alias_api_and_learning_never_store_pii_names(client_with_entry) -> None:
    """병합 전 보완 ③: 별칭 등록 API와 검토 완료 학습 모두 개인정보 형식 이름을 별칭으로 저장하지 않는다."""
    from sqlalchemy import select as sql_select

    from app.db import SessionLocal
    from app.models import GlossaryAlias, ReviewItem, ReviewSession
    from app.review import complete

    client, code = client_with_entry
    bad = client.post("/glossary/aliases", json={"alias": "carol@example.com 담당", "target_code": code})
    assert bad.status_code == 422 and "carol@example.com" not in bad.text
    ok = client.post("/glossary/aliases", json={"alias": "정상별칭기관", "target_code": code})
    assert ok.status_code == 201, ok.text
    with SessionLocal() as db:
        session = ReviewSession(
            status="in_review", params="{}", file_ids="[]", excluded_file_ids="[]", remember_format=True
        )
        db.add(session)
        db.flush()
        db.add(
            ReviewItem(
                session_id=session.id, category="pending", kind="entity", reason="r", reason_source="rule",
                payload="{}", before="carol@example.com 담당", choice=code, decision="approved",
            )
        )  # fmt: skip
        db.commit()
        learned = complete(db, session, learn_names=True)
        stored = db.scalar(sql_select(GlossaryAlias.id).where(GlossaryAlias.alias == "carol@example.com 담당"))
    assert stored is None and learned["names"] == 0 and learned["names_skipped"] == 1


@pytest.fixture()
def client_with_entry():
    from fastapi.testclient import TestClient

    from app.db import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        csv = "코드,정식명칭,유형\n9300001,교차검증청,중앙행정기관\n".encode()
        assert c.post("/glossary/import", files=[("file", ("g.csv", csv, "text/csv"))]).json()["applied"]
        yield c, "9300001"
        entry = next(e for e in c.get("/glossary/entries", params={"q": "9300001"}).json() if e["code"] == "9300001")
        c.delete(f"/glossary/entries/{entry['id']}")
