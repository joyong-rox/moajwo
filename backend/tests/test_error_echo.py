"""S-02: 오류 응답과 접근 기록이 보낸 값(비밀번호·이메일·전화·검색어)을 다시 내보내지 않는다.

재현 (수정 전 main adebb6c): 요청 형식 오류(422)의 기본 응답이 'input'에 보낸 본문을 그대로 담았다 —
- 로그인에서 아이디를 빠뜨리면 응답에 비밀번호가 나왔다.
- 가입 신청에 형식이 틀린 값이 있으면 비밀번호·이메일·전화번호가 함께 나왔다.
- 접근 기록(uvicorn)은 주소의 검색어(관리자 사용자 검색 q 등)를 그대로 남겼다.
필드 위치(loc)·오류 종류(type)·안내(msg)는 그대로 둔다 (화면은 msg를 쓴다).
"""

import logging

from fastapi.testclient import TestClient

from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import app_real_login  # noqa: F401

SECRET = "Sx-very-secret-pass-9"
EMAIL, PHONE = "kim1@example.com", "010-1234-5678"


def test_login_validation_error_does_not_echo_the_password(app_real_login) -> None:  # noqa: F811
    c = TestClient(app_real_login)
    res = c.post("/auth/login", json={"password": SECRET})
    assert res.status_code == 422
    assert SECRET not in res.text
    (error,) = res.json()["detail"]
    assert (error["type"], error["loc"], error["msg"]) == ("missing", ["body", "username"], "Field required")
    assert "input" not in error


def test_signup_validation_error_does_not_echo_personal_values(app_real_login) -> None:  # noqa: F811
    c = TestClient(app_real_login)
    body = {"username": "A!", "password": SECRET, "display_name": "x", "email": EMAIL, "phone": PHONE}
    res = c.post("/auth/signup", json=body)
    assert res.status_code == 422
    assert not [v for v in (SECRET, EMAIL, PHONE) if v in res.text], res.text
    assert {tuple(e["loc"]) for e in res.json()["detail"]} >= {("body", "password_confirm"), ("body", "email")}


def test_logged_in_validation_error_does_not_echo_the_body(owner) -> None:  # noqa: F811
    res = owner.post("/glossary/aliases", json={"alias": f"담당 {EMAIL}"})  # 필드 누락 — 기본 응답은 본문 전체를 담았다
    assert res.status_code == 422 and EMAIL not in res.text, res.text
    assert [e["loc"] for e in res.json()["detail"]] == [["body", "target_code"]]


def test_access_log_keeps_query_keys_but_not_values(app_real_login) -> None:  # noqa: F811
    access = logging.getLogger("uvicorn.access")
    args = ("127.0.0.1:50000", "GET", "/users?q=%ED%99%8D%EA%B8%B8%EB%8F%99&status=active", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d', args, None)
    assert all(f.filter(record) for f in access.filters)
    assert record.getMessage() == '127.0.0.1:50000 - "GET /users?q=…&status=… HTTP/1.1" 200'
    plain = ("127.0.0.1:50000", "GET", "/reviews/3", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d', plain, None)
    assert all(f.filter(record) for f in access.filters)
    assert record.getMessage() == '127.0.0.1:50000 - "GET /reviews/3 HTTP/1.1" 200'


def test_api_docs_are_off_unless_enabled(app_real_login) -> None:  # noqa: F811
    """S-03: API 문서·스키마(경로 75개)가 로그인 없이 열렸다. 기본은 끄고, 개발·진단에서만 MOAJWO_API_DOCS=1로 켠다."""
    c = TestClient(app_real_login)
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert c.get(path).status_code == 404, path
    assert c.get("/health").status_code == 200
