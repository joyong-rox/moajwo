"""직접 입력 글자(JSON 본문·쿼리)의 NUL(0x00) — 두 DB에서 같은 요청 검증으로, 저장 전에, 값 되돌림 없이 거절한다.

수정 전(185f0ba): SQLite는 NUL이 든 표시 이름·사유·자동화 이름·심사 메모를 그대로 저장했고(2xx), PostgreSQL은 드라이버가
매개변수를 보내지 않아 main의 안전망이 문자열 detail 422(+ '반영됐는지' 안내)로 알렸다 — 어느 필드인지(loc) 알 수 없고,
로그인·검색처럼 저장하지 않는 요청(SELECT 매개변수)에도 '반영 여부' 안내가 붙었다. 같은 시험이 두 DB에서 돈다
(MOAJWO_TEST_DATABASE_URL — sqlite_only 표시 없음).

수정 뒤: pydantic 422 목록형 — type 'value_has_nul', 정확한 loc, msg 끝 [VALUE_HAS_NUL], input·ctx 없음, 응답에 NUL·원문
없음, 아무것도 저장하지 않음(부분 저장 없음). 막는 글자는 NUL 하나(한글·줄바꿈·탭·이모지는 그대로 저장)이고 값을 고치지
않는다. 파일 바이트·셀 값·파일에서 온 이름은 기존 규칙(app/name_limits.py·review._check_item_values)이 그대로 맡는다.
"""

import asyncio
import json
from collections.abc import Iterator
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from tests.conftest import PG_ADMIN, POSTGRES, db_connect, db_rows, pg_database
from tests.test_accounts import _legacy_pending, _uid, owner  # noqa: F401
from tests.test_auth import ROWS, _client, _upload, app_real_login  # noqa: F401

CODE = "[VALUE_HAS_NUL]"
NUL = "\x00"
PASSWORD = "nul-pass-1234"


# ---------------------------------------------------------------- 보조


def _no_echo(text: str, *raws: str) -> None:
    """응답에 NUL(JSON 이스케이프 포함)도, 보낸 원문(NUL을 뺀 것 포함)도 없다."""
    assert "\\u0000" not in text and NUL not in text, text[:300]
    for raw in raws:
        assert raw not in text and raw.replace(NUL, "") not in text, (raw.replace(NUL, "")[:12], text[:300])


def _refused(res: Any, *loc: str | int, raws: tuple[str, ...] = (), type_: str = "value_has_nul") -> dict:
    """pydantic 422 목록형 거절 하나: type·loc 정확, (NUL이면) msg 끝 코드·ctx 없음, input 없음, 되돌림 없음,
    '반영 여부' 안내(안전망 문구) 없음."""
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert isinstance(detail, list) and len(detail) == 1, detail
    (error,) = detail
    assert error["type"] == type_ and error["loc"] == list(loc), error
    assert "input" not in error, error
    if type_ == "value_has_nul":
        assert error["msg"].endswith(CODE) and "ctx" not in error, error
    _no_echo(res.text, *raws)
    assert "반영됐는지" not in res.text  # PostgreSQL 안전망(문자열 detail)의 문구가 아니다
    return error


def _count(table: str, where: str = "", *args: Any) -> int:
    return db_rows(f"select count(*) from {table}" + (f" where {where}" if where else ""), *args)[0][0]


def _approve_all(c: TestClient, review: dict) -> dict:
    for item in [i for i in review["items"] if i["category"] == "pending" and i["decision"] is None]:
        candidates = item["payload"].get("candidates") or []
        if item["kind"] == "mapping":
            choice = item["payload"].get("field_key")
        else:
            choice = candidates[0].get("code") if candidates else None
        decision = "approved" if (choice or item["kind"] == "pii") else "rejected"
        res = c.post(
            f"/reviews/{review['id']}/items/{item['id']}/decision", json={"decision": decision, "choice": choice}
        )
        assert res.status_code == 200, res.text
        review = res.json()
    return review


def _completed_run(c: TestClient, name: str) -> dict:
    """업로드 → 검토 → 완료 → 통합 실행 1건 (자동화 저장·변경 기록 조회의 바탕)."""
    review = c.post("/reviews", json={"file_ids": [_upload(c, ROWS, name)]}).json()
    _approve_all(c, review)
    assert c.post(f"/reviews/{review['id']}/complete").status_code == 200
    res = c.post(f"/reviews/{review['id']}/integrate", json={})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
def blank_app(tmp_path) -> Iterator[tuple[Any, Any]]:  # noqa: ANN001
    """계정이 하나도 없는 빈 DB에 붙인 앱 — 최초 설정(/auth/setup)을 HTTP로 검사한다.
    SQLite 실행이면 임시 파일, PostgreSQL 실행이면 일회용 DB (관리자 주소가 없으면 건너뜀)."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker

    from app.db import Base, get_session
    from app.main import app

    name = "moajwo_nul_setup"
    if POSTGRES:
        if not PG_ADMIN:
            pytest.skip("빈 PostgreSQL DB를 만들 관리자 주소(MOAJWO_TEST_PG_ADMIN_URL)가 없음")
        engine = create_engine(make_url(pg_database(name)).set(drivername="postgresql+psycopg"))
    else:
        url = f"sqlite:///{(tmp_path / 'blank.db').as_posix()}"
        engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def session() -> Iterator[Any]:
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = session
    try:
        yield app, engine
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()
        if POSTGRES:
            pg_database(name, drop=True)


def _scalar(engine: Any, sql: str) -> Any:
    from sqlalchemy import text

    with engine.connect() as con:
        return con.execute(text(sql)).scalar()


# ---------------------------------------------------------------- 저장 필드 (수정 전: SQLite 2xx 저장 · PG 안전망 422)


def test_init_display_name_nul_is_refused_before_any_row(blank_app) -> None:  # noqa: ANN001
    # 설치 초기화(create_owner)는 app_settings(owner_setup)를 먼저 flush한 뒤 계정을 넣는다 — 규칙 검사에서 거절하므로
    # 두 표 모두 0건 (PostgreSQL에서 먼저 flush한 행도 남지 않음). 공개 최초 설정 요청은 종료돼 본문을 읽지 않고
    # 410이다.
    from sqlalchemy.orm import sessionmaker

    from app import auth

    app, engine = blank_app
    Session = sessionmaker(bind=engine)
    with Session() as db:
        with pytest.raises(auth.AuthError):
            auth.create_owner(db, "first-owner", "소유\x00자", "password-123", reason="시험")
        with pytest.raises(auth.AuthError):
            auth.create_owner(db, "first-owner", "소유자", "pass\x00word-123", reason="시험")
    for sql in ("select count(*) from users", "select count(*) from account_events",
                "select count(*) from app_settings where key = 'owner_setup'"):  # fmt: skip
        assert _scalar(engine, sql) == 0, sql
    c = TestClient(app)
    res = c.post(
        "/auth/setup", json={"username": "first-owner", "display_name": "소유\x00자", "password": "password-123"}
    )
    assert res.status_code == 410 and "\\u0000" not in res.text and "set-cookie" not in res.headers
    assert _scalar(engine, "select count(*) from users") == 0
    with Session() as db:
        made = auth.create_owner(db, "first-owner", "소유자", "password-123", reason="시험")
        assert made.display_name == "소유자"
    assert _scalar(engine, "select count(*) from users") == 1
    assert _scalar(engine, "select count(*) from app_settings where key = 'owner_setup'") == 1


def test_provision_display_name_nul_is_refused_and_username_rule_stays(owner) -> None:  # noqa: ANN001, F811
    users, events = _count("users"), _count("account_events")
    res = owner.post("/users", json={"username": "nulsign1", "display_name": "가입\x00자"})
    _refused(res, "body", "display_name", raws=("가입\x00자",))
    assert (_count("users"), _count("account_events")) == (users, events)
    # 아이디의 NUL은 전처럼 아이디 규칙(문자열 detail)이 막는다 — 값은 되돌리지 않음
    res = owner.post("/users", json={"username": "nul\x00sign", "display_name": "가입자"})
    assert res.status_code == 422 and isinstance(res.json()["detail"], str), res.text
    assert "아이디는" in res.json()["detail"] and "\\u0000" not in res.text
    assert _count("users") == users
    assert owner.post("/users", json={"username": "nulsign1", "display_name": "가입자"}).status_code == 201


def test_user_create_and_patch_display_name_nul(owner) -> None:  # noqa: ANN001, F811
    users = _count("users")
    body = {"username": "nuluser1", "display_name": "표시\x00이름"}
    _refused(owner.post("/users", json=body), "body", "display_name", raws=("표시\x00이름",))
    assert _count("users") == users and not db_rows("select 1 from users where username = 'nuluser1'")
    res = owner.post("/users", json={**body, "display_name": "표시이름"})
    assert res.status_code == 201, res.text
    uid = res.json()["user"]["id"]
    events = _count("account_events", "target_id = ?", uid)
    _refused(owner.patch(f"/users/{uid}", json={"display_name": "바뀐\x00이름"}), "body", "display_name",
             raws=("바뀐\x00이름",))  # fmt: skip
    assert db_rows("select display_name from users where id = ?", uid) == [("표시이름",)]
    assert _count("account_events", "target_id = ?", uid) == events
    assert owner.patch(f"/users/{uid}", json={"display_name": "바뀐 이름"}).status_code == 200
    assert db_rows("select display_name from users where id = ?", uid) == [("바뀐 이름",)]


def test_status_change_reason_nul_changes_nothing(app_real_login, owner) -> None:  # noqa: ANN001, F811
    for name in ("nulpend1", "nulpend2"):  # 예전 공개 가입으로 남은 승인 대기 계정 (지금은 API로 만들 수 없음)
        _legacy_pending(name)
    a, b = _uid(owner, "nulpend1"), _uid(owner, "nulpend2")

    def row(uid: int) -> tuple:
        return db_rows("select status, status_reason, auth_version from users where id = ?", uid)[0]

    def refused_then_ok(uid: int, action: str, expected: str) -> None:
        before, events = row(uid), _count("account_events", "target_id = ?", uid)
        res = owner.post(f"/users/{uid}/{action}", json={"reason": f"사유\x00{action}"})
        _refused(res, "body", "reason", raws=(f"사유\x00{action}",))
        assert row(uid) == before  # 상태·사유·버전 그대로
        assert _count("account_events", "target_id = ?", uid) == events  # denied 이력도 없음
        res = owner.post(f"/users/{uid}/{action}", json={"reason": f"확인 {action}"})
        assert res.status_code == 200 and res.json()["status"] == expected, res.text
        assert row(uid)[:2] == (expected, f"확인 {action}")

    refused_then_ok(a, "approve", "active")
    refused_then_ok(a, "suspend", "suspended")
    refused_then_ok(a, "resume", "active")
    refused_then_ok(b, "reject", "rejected")


def test_automation_name_nul_is_refused_and_plain_text_is_kept(owner) -> None:  # noqa: ANN001, F811
    run = _completed_run(owner, "널자동화.xlsx")
    automations = _count("automations")
    res = owner.post(f"/runs/{run['id']}/automation", json={"name": "규칙\x00이름"})
    _refused(res, "body", "name", raws=("규칙\x00이름",))
    assert _count("automations") == automations
    # NUL만 뺀 같은 요청은 저장된다. 탭·줄바꿈·이모지·대시는 그대로 (막는 글자는 NUL 하나)
    name = "규칙\t이름\n— 😀"
    res = owner.post(f"/runs/{run['id']}/automation", json={"name": name})
    assert res.status_code == 201 and res.json()["name"] == name, res.text
    assert db_rows("select name from automations where id = ?", res.json()["id"]) == [(name,)]


def test_promotion_note_nul_leaves_candidate_and_dictionary_alone(owner) -> None:  # noqa: ANN001, F811
    # 승격 후보는 사용자 셋의 확정으로 쌓이지만, 여기서는 행을 직접 심는다 (심사 메모 필드만 본다)
    ids = {}
    with db_connect() as con:
        for name in ("널시험승격열", "널시험반려열"):
            con.execute(
                "insert into promotion_candidates (normalized, column_name, field_key, approver_count, status) "
                "values (?, ?, ?, ?, ?)",
                (name, name, "note", 3, "pending"),
            )
    for name in ("널시험승격열", "널시험반려열"):
        ids[name] = db_rows("select id from promotion_candidates where normalized = ?", name)[0][0]
    terms = _count("standard_terms")
    for name, action in (("널시험승격열", "approve"), ("널시험반려열", "reject")):
        cid, before = ids[name], _count("standard_terms")
        res = owner.post(f"/admin/promotions/{cid}/{action}", json={"note": "메모\x00"})
        _refused(res, "body", "note", raws=("메모\x00",))
        assert db_rows("select status, review_note from promotion_candidates where id = ?", cid) == [("pending", None)]
        assert _count("standard_terms") == before  # approve의 같은 트랜잭션 INSERT가 남지 않음 (부분 저장 없음)
        res = owner.post(f"/admin/promotions/{cid}/{action}", json={"note": f"메모 {action}"})
        assert res.status_code == 200 and res.json()["review_note"] == f"메모 {action}", res.text
    assert _count("standard_terms") == terms + 1  # 정상 승인 한 건만 반영
    assert _count("standard_terms", "normalized = ?", "널시험승격열") == 1


# ---------------------------------------------------------------- 비교 전용 (수정 전: SQLite 401/200 · PG 안전망 422)


def test_login_username_nul_is_a_field_error_not_a_write_warning(app_real_login, owner) -> None:  # noqa: ANN001, F811
    made = owner.post("/users", json={"username": "nullogin1", "display_name": "로그인 시험"})
    assert made.status_code == 201, made.text
    temp = made.json()["temp_password"]  # 발급 → 임시 비밀번호로 로그인 → 본인 비밀번호(PASSWORD)로 변경
    first = _client(app_real_login, "nullogin1", temp)
    body = {"current_password": temp, "new_password": PASSWORD, "new_password_confirm": PASSWORD}
    assert first.post("/auth/password", json=body).status_code == 204
    anon = _client(app_real_login)
    res = anon.post("/auth/login", json={"username": "nul\x00login1", "password": PASSWORD})
    _refused(res, "body", "username", raws=("nul\x00login1",))
    assert db_rows("select failed_logins from users where username = 'nullogin1'") == [(0,)]
    # 비밀번호는 SQL 매개변수가 아니라 해시 비교다 — 전처럼 401 (검사 대상 아님)
    assert anon.post("/auth/login", json={"username": "nullogin1", "password": "x\x00" + PASSWORD}).status_code == 401
    assert anon.post("/auth/login", json={"username": "nullogin1", "password": PASSWORD}).status_code == 200


def test_user_list_query_and_status_nul(owner) -> None:  # noqa: ANN001, F811
    from app.auth import STATUSES
    from app.auth_api import AccountStatus

    assert set(get_args(AccountStatus)) == set(STATUSES)
    _refused(owner.get("/users", params={"q": "널\x00검색"}), "query", "q", raws=("널\x00검색",))
    _refused(owner.get("/users", params={"status": "act\x00ive"}), "query", "status", type_="literal_error")
    assert owner.get("/users", params={"q": "널검색", "status": "active"}).status_code == 200


def test_glossary_entries_query_nul(owner) -> None:  # noqa: ANN001, F811
    _refused(owner.get("/glossary/entries", params={"q": "행안\x00부"}), "query", "q", raws=("행안\x00부",))
    assert owner.get("/glossary/entries", params={"q": "행안부"}).status_code == 200


def test_run_changes_kind_nul(owner) -> None:  # noqa: ANN001, F811
    from app.integration import KIND_LABEL
    from app.integration_api import ChangeKind

    assert set(get_args(ChangeKind)) == set(KIND_LABEL)
    run = _completed_run(owner, "널변경기록.xlsx")
    _refused(owner.get(f"/runs/{run['id']}/changes", params={"kind": "tr\x00im"}), "query", "kind",
             type_="literal_error")  # fmt: skip
    assert owner.get(f"/runs/{run['id']}/changes", params={"kind": "trim"}).status_code == 200


# ---------------------------------------------------------------- JSON 저장 필드 (두 DB 같았지만 늦게·다르게 거절)


def test_required_entities_nul_is_refused_the_same_way_by_validation_and_reviews(owner) -> None:  # noqa: ANN001, F811
    # 수정 전: POST /validation은 200으로 저장(결과 메시지에 NUL), POST /reviews는 검증·매핑을 다 돈 뒤 422 문자열
    fid = _upload(owner, ROWS, "널필수기관.xlsx")
    before = {t: _count(t) for t in ("validation_runs", "review_sessions", "review_items")}
    for path in ("/validation", "/reviews"):
        res = owner.post(path, json={"file_ids": [fid], "required_entities": ["행정안전부", "널시험\x00기관"]})
        _refused(res, "body", "required_entities", 1, raws=("널시험\x00기관",))
    assert {t: _count(t) for t in before} == before
    body = {"file_ids": [fid], "required_entities": ["행정안전부", "널시험기관"]}
    assert owner.post("/validation", json=body).status_code == 200
    assert owner.post("/reviews", json=body).status_code == 201


def test_llm_settings_nul(owner) -> None:  # noqa: ANN001, F811
    stored = db_rows("select value from app_settings where key = 'llm_provider'")
    cases = (
        ({"mode": "ollama", "model": "모델\x00"}, "model"),
        ({"mode": "custom", "model": "m", "base_url": "http://127.0.0.1:9/v\x001"}, "base_url"),
        ({"mode": "openai", "model": "m", "api_key": "sk-\x00key"}, "api_key"),
    )
    for body, field in cases:
        _refused(owner.put("/settings/llm", json=body), "body", field, raws=(body[field],))
    assert db_rows("select value from app_settings where key = 'llm_provider'") == stored
    try:
        res = owner.put("/settings/llm", json={"mode": "ollama", "model": "널시험모델"})
        assert res.status_code == 200 and res.json()["model"] == "널시험모델", res.text
    finally:  # 다른 시험이 보는 설정을 되돌린다 (로컬 Ollama 기본)
        assert owner.put("/settings/llm", json={"mode": "ollama", "model": ""}).status_code == 200


# ---------------------------------------------------------------- 되돌림 방지 (수정 전: 가린 입력을 문구에 되돌림)


def test_mapping_decision_strings_nul(owner) -> None:  # noqa: ANN001, F811
    fid = _upload(owner, ROWS, "널확정.xlsx")
    decisions = _count("mapping_decisions")
    cases = (
        ({"sheet_id": "s0", "column_id": "c0", "field_key": "org\x00_name"}, "field_key"),
        ({"sheet_id": "s\x000", "column_id": "c0", "field_key": None}, "sheet_id"),
        ({"sheet_id": "s0", "column_id": "c\x000", "field_key": None}, "column_id"),
        ({"sheet": "현\x00황", "column": "기관명", "field_key": None}, "sheet"),
        ({"sheet": "현황", "column": "기관\x00명", "field_key": None}, "column"),
    )
    for decision, field in cases:
        res = owner.put(f"/files/{fid}/mapping/decisions", json={"decisions": [decision]})
        _refused(res, "body", "decisions", 0, field, raws=(decision[field],))
    assert _count("mapping_decisions") == decisions
    body = {"decisions": [{"sheet_id": "s0", "column_id": "c0", "field_key": "org_name"}]}
    assert owner.put(f"/files/{fid}/mapping/decisions", json=body).status_code == 200


def test_batch_mapping_strings_nul(owner) -> None:  # noqa: ANN001, F811
    item = {"file_id": 1, "sheet_id": "s0", "column_id": "c0", "field_key": None}
    apply = {**item, "expected_confirmed": False, "expected_field_key": None}
    for path, base, field, value in (
        ("/mapping/batch/preview", item, "sheet_id", "s\x000"),
        ("/mapping/batch/preview", item, "field_key", "org\x00_name"),
        ("/mapping/batch/apply", apply, "column_id", "c\x000"),
        ("/mapping/batch/apply", apply, "expected_field_key", "org\x00_name"),
    ):
        res = owner.post(path, json={"items": [{**base, field: value}]})
        _refused(res, "body", "items", 0, field, raws=(value,))


def test_analysis_request_strings_nul(owner) -> None:  # noqa: ANN001, F811
    base = {"field_key": "population", "targets": [{"file_id": 1}]}
    cases = (
        ({"variant": "구\x00분"}, ("variant",), "구\x00분"),
        ({"unit": "명\x00"}, ("unit",), "명\x00"),
        ({"targets": [{"file_id": 1, "unit": "명\x00"}]}, ("targets", 0, "unit"), "명\x00"),
        ({"group_by": ["sido", "org\x00_name"]}, ("group_by", 1), "org\x00_name"),
        ({"filters": {"dims": {"sido": ["서울\x00"]}}}, ("filters", "dims"), "서울\x00"),
        ({"filters": {"dims": {"si\x00do": ["서울"]}}}, ("filters", "dims"), "si\x00do"),
        (
            {"bins": {"on": "sum", "ranges": [{"label": "구간\x00", "min": 0}]}},
            ("bins", "ranges", 0, "label"),
            "구간\x00",
        ),
    )
    for extra, loc, raw in cases:
        _refused(owner.post("/analysis/preview", json={**base, **extra}), "body", *loc, raws=(raw,))


def test_bundle_request_strings_nul(owner) -> None:  # noqa: ANN001, F811
    item = {"field_key": "population", "aggregates": ["sum"]}
    link = {"file_id": 1, "sheet_id": "s0"}
    cases = (
        ({"variant": "구\x00분"}, ("variant",), "구\x00분"),
        ({"unit": "명\x00"}, ("unit",), "명\x00"),
        ({"field_key": "popu\x00lation"}, ("field_key",), "popu\x00lation"),
        ({"links": [{**link, "unit": "명\x00"}]}, ("links", 0, "unit"), "명\x00"),
        (
            {"links": [{**link, "exclude": True, "exclude_reason": "사유\x00"}]},
            ("links", 0, "exclude_reason"),
            "사유\x00",
        ),
    )
    for extra, loc, raw in cases:
        res = owner.post("/bundle/plan", json={"file_ids": [1], "summary": {"items": [{**item, **extra}]}})
        _refused(res, "body", "summary", "items", 0, *loc, raws=(raw,))


def test_review_decision_choice_nul_and_length(owner) -> None:  # noqa: ANN001, F811
    # 요청 검증이 세션 조회보다 먼저다 — 없는 검토 번호라도 NUL·길이는 422, 정상 값이면 404
    path = "/reviews/999999/items/1/decision"
    _refused(owner.post(path, json={"decision": "approved", "choice": "org\x00_name"}), "body", "choice",
             raws=("org\x00_name",))  # fmt: skip
    res = owner.post(path, json={"decision": "approved", "choice": "k" * 65})
    assert res.status_code == 422 and res.json()["detail"][0]["type"] == "string_too_long", res.text
    assert owner.post(path, json={"decision": "approved", "choice": "org_name"}).status_code == 404


# ---------------------------------------------------------------- 정상값·길이 한도 회귀


def test_plain_text_is_stored_unchanged_and_length_errors_keep_their_type(owner) -> None:  # noqa: ANN001, F811
    name = "홍\t길동\n— 😀"
    res = owner.post("/users", json={"username": "nulplain1", "display_name": name})
    assert res.status_code == 201 and res.json()["user"]["display_name"] == name, res.text
    uid = res.json()["user"]["id"]
    assert db_rows("select display_name from users where id = ?", uid) == [(name,)]
    too_long = (
        ("patch", f"/users/{uid}", {"display_name": "가" * 65}, "display_name"),
        ("post", f"/users/{uid}/suspend", {"reason": "가" * 501}, "reason"),
        ("post", "/runs/999999/automation", {"name": "가" * 101}, "name"),
        ("post", "/admin/promotions/999999/approve", {"note": "가" * 501}, "note"),
        ("put", "/settings/llm", {"mode": "ollama", "model": "가" * 201}, "model"),
    )
    for method, path, body, field in too_long:
        res = getattr(owner, method)(path, json=body)
        assert res.status_code == 422, (path, res.text)
        (error,) = res.json()["detail"]
        assert (error["type"], error["loc"]) == ("string_too_long", ["body", field]), (path, error)
        assert "String should have at most" in error["msg"], error
    assert db_rows("select display_name, status from users where id = ?", uid) == [(name, "active")]


# ---------------------------------------------------------------- PostgreSQL 안전망 (마지막 방어선) — 단위 시험


def test_database_safety_net_is_kept_as_the_last_resort() -> None:
    """드라이버가 NUL 매개변수를 거절한 DataError(SQLSTATE 없음, 'NUL (0x00)')만 422 — 쓰기는 반영 여부 안내를 붙이고
    읽기는 붙이지 않는다. SQLSTATE가 있는 DataError(서버 오류)와 다른 문구는 전처럼 500(그대로 던짐)."""
    from sqlalchemy.exc import DataError
    from starlette.requests import Request

    from app import main

    class Driver(Exception):
        sqlstate = None

    class Server(Exception):
        sqlstate = "22021"

    def run(method: str, orig: Exception) -> Any:
        scope = {"type": "http", "method": method, "path": "/x", "headers": [], "query_string": b""}
        exc = DataError("INSERT INTO t (c) VALUES (?)", {"c": "…"}, orig, hide_parameters=True)
        return asyncio.run(main.database_failure(Request(scope), exc))

    refused = Driver("PostgreSQL text fields cannot contain NUL (0x00) bytes")
    res = run("POST", refused)
    assert res.status_code == 422 and json.loads(res.body) == {"detail": main.NUL_REFUSED + main.WRITE_UNCERTAIN}
    res = run("GET", refused)
    assert res.status_code == 422 and json.loads(res.body) == {"detail": main.NUL_REFUSED}
    assert "0x00" in main.NUL_REFUSED and main.NUL_REFUSED.endswith(CODE)
    with pytest.raises(DataError):
        run("POST", Server('invalid byte sequence for encoding "UTF8": 0x00'))
    with pytest.raises(DataError):
        run("POST", Driver("value too long for type character varying(255)"))


def test_psycopg_nul_data_error_matches_the_safety_net_condition() -> None:
    """안전망 조건의 전제: psycopg 3가 NUL이 든 글자 매개변수를 서버로 보내지 않고 SQLSTATE 없는 DataError를 낸다."""
    if not POSTGRES:
        pytest.skip("PostgreSQL 실행에서만 — 드라이버의 실제 오류를 본다")
    from sqlalchemy import text
    from sqlalchemy.exc import DataError

    from app import main
    from app.db import engine

    with engine.connect() as con, pytest.raises(DataError) as info:
        con.execute(text("SELECT CAST(:v AS VARCHAR(10))"), {"v": "널\x00"})
    orig = info.value.orig
    assert getattr(orig, "sqlstate", None) is None and "NUL (0x00)" in str(orig), (type(orig).__name__, str(orig))
    assert main._nul_refused(info.value) and main.db_failure(info.value) is None
