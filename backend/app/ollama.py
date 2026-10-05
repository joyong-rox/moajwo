"""로컬 Ollama HTTP 클라이언트 (임베딩 / JSON 응답 채팅). 외부 서비스는 호출하지 않는다."""

import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from app.config import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL, OLLAMA_LLM_MODEL

EMBED_TIMEOUT = 60
CHAT_TIMEOUT = 180  # 첫 호출은 모델 로딩 시간이 포함된다

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"}


class OllamaError(Exception):
    pass


def _check_local(base_url: str) -> None:
    # 폐쇄망 원칙: 설정 실수로 외부 엔드포인트를 부르지 않도록 로컬/사설 호스트만 허용한다.
    host = urlparse(base_url).hostname or ""
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_loopback or ip.is_private):
        return
    raise OllamaError(f"로컬 Ollama 엔드포인트만 사용할 수 있습니다: {host}")


def _post(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    _check_local(OLLAMA_BASE_URL)
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise OllamaError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise OllamaError(f"Ollama 연결 실패: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama 응답을 해석할 수 없습니다.") from exc


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    body = _post("/api/embed", {"model": OLLAMA_EMBED_MODEL, "input": texts}, EMBED_TIMEOUT)
    vectors = body.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise OllamaError("임베딩 응답 형식이 올바르지 않습니다.")
    return vectors


def chat_json(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    body = _post(
        "/api/chat",
        {
            "model": OLLAMA_LLM_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        },
        CHAT_TIMEOUT,
    )
    content = (body.get("message") or {}).get("content", "")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaError("LLM이 JSON이 아닌 응답을 반환했습니다.") from exc
    if not isinstance(result, dict):
        raise OllamaError("LLM 응답 형식이 올바르지 않습니다.")
    return result
