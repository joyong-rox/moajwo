"""LLM 제공자 설정: 기본 로컬, 소유자만 변경, API 키 비노출, 제공자별 요청 형식. 실제 외부 호출은 하지 않는다."""

import json
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.config import DATA_DIR

SCHEMA = {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}
SECRET = "sk-test-0123456789abcdef"


@pytest.fixture()
def client():
    from app.db import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    # 다른 테스트에 영향이 없도록 로컬 기본값으로 되돌린다
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        con.execute("delete from app_settings where key = 'llm_provider'")
    llm.set_current(None)


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _capture(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> list[Any]:
    sent: list[Any] = []

    def fake_urlopen(req, timeout):  # noqa: ANN001
        sent.append(req)
        return FakeResponse(body)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_default_is_local_ollama(client: TestClient) -> None:
    res = client.get("/settings/llm").json()
    assert res["mode"] == "ollama" and res["is_external"] is False and res["api_key_set"] is False
    assert "Ollama" in res["embedding"]
    assert llm.current().mode == "ollama"


def test_only_owner_can_change(client: TestClient) -> None:
    from app.auth import CurrentUser, current_user
    from app.main import app

    original = app.dependency_overrides[current_user]
    try:
        for role in ("user", "admin"):
            app.dependency_overrides[current_user] = lambda role=role: CurrentUser(999, role, role, role)
            assert client.get("/settings/llm").status_code == 403
            assert client.put("/settings/llm", json={"mode": "openai"}).status_code == 403
            assert client.post("/settings/llm/test").status_code == 403
    finally:
        app.dependency_overrides[current_user] = original


def test_save_external_mode_without_leaking_key(client: TestClient) -> None:
    assert client.put("/settings/llm", json={"mode": "anthropic"}).status_code == 422  # 키 필요
    assert client.put("/settings/llm", json={"mode": "openai", "api_key": SECRET}).status_code == 422  # 모델 필요
    bad = {"mode": "openai", "model": "m", "api_key": SECRET, "base_url": "http://api.example.com/v1"}
    assert client.put("/settings/llm", json=bad).status_code == 422  # 외부 API는 https만

    res = client.put("/settings/llm", json={"mode": "anthropic", "api_key": SECRET})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert saved["mode"] == "anthropic" and saved["is_external"] and saved["api_key_set"]
    assert saved["effective_model"] == llm.DEFAULT_MODEL["anthropic"]
    assert SECRET not in res.text and saved["api_key_hint"].endswith("cdef")
    assert SECRET not in client.get("/settings/llm").text
    assert llm.current().mode == "anthropic"  # 저장하면 바로 적용

    # 키를 다시 보내지 않으면 저장된 키를 유지, 모델만 바꾼다
    res = client.put("/settings/llm", json={"mode": "anthropic", "model": "claude-sonnet-5-5"})
    assert res.json()["api_key_set"] and llm.current().api_key == SECRET
    assert llm.current().effective_model == "claude-sonnet-5-5"

    # 로컬로 되돌리면 키를 지운다
    res = client.put("/settings/llm", json={"mode": "ollama"}).json()
    assert res["mode"] == "ollama" and not res["api_key_set"] and llm.current().api_key is None


def test_custom_endpoint_requires_url(client: TestClient) -> None:
    assert client.put("/settings/llm", json={"mode": "custom", "model": "local-model"}).status_code == 422
    bad = {"mode": "custom", "model": "m", "base_url": "ftp://x"}
    assert client.put("/settings/llm", json=bad).status_code == 422
    ok = {"mode": "custom", "model": "m", "base_url": "http://10.0.0.5:8000/v1/"}
    res = client.put("/settings/llm", json=ok).json()
    assert res["effective_base_url"] == "http://10.0.0.5:8000/v1" and not res["api_key_set"]


def test_anthropic_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, {"content": [{"type": "tool_use", "name": "answer", "input": {"key": "budget"}}]})
    config = llm.LlmConfig(mode="anthropic", api_key=SECRET)
    assert llm.chat_json("sys", "user", SCHEMA, config=config) == {"key": "budget"}
    req = sent[0]
    body = json.loads(req.data)
    assert req.full_url == "https://api.anthropic.com/v1/messages"
    assert req.get_header("X-api-key") == SECRET and req.get_header("Anthropic-version") == "2023-06-01"
    assert body["tool_choice"] == {"type": "tool", "name": "answer"} and body["tools"][0]["input_schema"] == SCHEMA
    assert body["model"] == llm.DEFAULT_MODEL["anthropic"]


def test_openai_and_custom_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, {"choices": [{"message": {"content": '{"key": "note"}'}}]})
    openai = llm.LlmConfig(mode="openai", model="gpt-x", api_key=SECRET)
    assert llm.chat_json("sys", "user", SCHEMA, config=openai) == {"key": "note"}
    req = sent[0]
    body = json.loads(req.data)
    assert req.full_url == "https://api.openai.com/v1/chat/completions"
    assert req.get_header("Authorization") == f"Bearer {SECRET}"
    assert body["response_format"]["type"] == "json_schema" and body["model"] == "gpt-x"

    custom = llm.LlmConfig(mode="custom", model="local", base_url="http://10.0.0.5:8000/v1")
    llm.chat_json("sys", "user", SCHEMA, config=custom)
    req = sent[1]
    body = json.loads(req.data)
    assert req.full_url == "http://10.0.0.5:8000/v1/chat/completions" and req.get_header("Authorization") is None
    assert body["response_format"] == {"type": "json_object"} and "JSON 스키마" in body["messages"][0]["content"]


def test_errors_never_contain_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(req, timeout):  # noqa: ANN001
        raise llm.urllib.error.URLError(f"refused for {SECRET}")

    monkeypatch.setattr(llm.urllib.request, "urlopen", failing)
    with pytest.raises(llm.LlmError) as info:
        llm.chat_json("s", "u", SCHEMA, config=llm.LlmConfig(mode="openai", model="m", api_key=SECRET))
    assert SECRET not in str(info.value)
    assert isinstance(info.value, llm.ollama.OllamaError)  # 매핑 엔진은 'LLM 사용 불가'로 처리


def test_connection_test_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.put("/settings/llm", json={"mode": "openai", "model": "gpt-x", "api_key": SECRET})
    _capture(monkeypatch, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    res = client.post("/settings/llm/test").json()
    assert res["ok"] and res["model"] == "gpt-x" and "OpenAI" in res["mode_label"]


def test_engine_uses_selected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mapping import get_engine

    calls: list[str] = []
    monkeypatch.setattr(llm, "_anthropic", lambda c, s, u, sc: calls.append("anthropic") or {"key": "none"})
    monkeypatch.setattr(llm.ollama, "chat_json", lambda *a, **k: calls.append("ollama") or {"key": "none"})
    engine = get_engine()
    llm.set_current(llm.LlmConfig(mode="anthropic", api_key=SECRET))
    try:
        engine._llm_fn("s", "u", SCHEMA)  # noqa: SLF001
        llm.set_current(llm.LlmConfig())
        engine._llm_fn("s", "u", SCHEMA)  # noqa: SLF001
    finally:
        llm.set_current(None)
    assert calls == ["anthropic", "ollama"]
