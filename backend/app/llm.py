"""LLM 제공자 선택 (열 매핑 3단계의 JSON 판정에 쓴다).

- 기본값은 로컬 내장형(Ollama). 폐쇄망 원칙대로 설정을 바꾸지 않으면 외부로 아무것도 보내지 않는다.
- 소유자가 「설정」에서 고른 경우에만 외부 API(OpenAI, Anthropic) 또는 Custom Endpoint(OpenAI 호환)를 쓴다.
  보내는 내용: 열 이름, 샘플 값 최대 5개(개인정보 패턴은 마스킹된 값), 표준 항목 목록.
- 임베딩(bge-m3)은 모드와 관계없이 항상 로컬 Ollama를 쓴다.
- 설정은 app_settings(key=llm_provider)에 저장한다. API 키는 응답·로그로 내보내지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse

from app import ollama
from app.config import OLLAMA_BASE_URL, OLLAMA_LLM_MODEL

logger = logging.getLogger(__name__)

SETTING_KEY = "llm_provider"
MODES = ("ollama", "openai", "anthropic", "custom")
MODE_LABEL = {
    "ollama": "로컬 내장형 (Ollama)",
    "openai": "외부 API · OpenAI",
    "anthropic": "외부 API · Anthropic",
    "custom": "Custom Endpoint (OpenAI 호환)",
}
DEFAULT_BASE_URL = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
DEFAULT_MODEL = {"ollama": OLLAMA_LLM_MODEL, "anthropic": "claude-haiku-4-5-20251001"}
ANTHROPIC_VERSION = "2023-06-01"
EXTERNAL_TIMEOUT = 60


class LlmError(ollama.OllamaError):
    """매핑 엔진은 OllamaError를 'LLM 사용 불가'로 처리하므로 같은 계열로 둔다."""


@dataclass(frozen=True)
class LlmConfig:
    mode: str = "ollama"
    model: str | None = None  # 비우면 모드별 기본 모델 (OpenAI·Custom은 필수)
    base_url: str | None = None  # OpenAI·Anthropic은 비우면 공식 주소, Custom은 필수, Ollama는 환경 변수
    api_key: str | None = None

    @property
    def effective_model(self) -> str | None:
        return self.model or DEFAULT_MODEL.get(self.mode)

    @property
    def effective_base_url(self) -> str:
        if self.mode == "ollama":
            return OLLAMA_BASE_URL
        return (self.base_url or DEFAULT_BASE_URL.get(self.mode, "")).rstrip("/")

    @property
    def is_external(self) -> bool:
        return self.mode != "ollama"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> LlmConfig:
        data = json.loads(text)
        mode = data.get("mode") if data.get("mode") in MODES else "ollama"
        return cls(mode=mode, model=data.get("model"), base_url=data.get("base_url"), api_key=data.get("api_key"))


def validate(config: LlmConfig) -> None:
    """저장 전에 모드별 필수 값을 확인한다."""
    if config.mode not in MODES:
        raise LlmError(f"알 수 없는 LLM 제공자입니다: {config.mode}")
    if config.mode in ("openai", "anthropic") and not config.api_key:
        raise LlmError("외부 API 모드는 API Key가 필요합니다.")
    if config.mode in ("openai", "custom") and not config.model:
        raise LlmError("이 모드는 모델 이름을 입력해야 합니다.")
    if config.mode == "custom" and not config.base_url:
        raise LlmError("Custom Endpoint 모드는 엔드포인트 URL이 필요합니다.")
    url = config.base_url
    if url and urlparse(url).scheme not in ("http", "https"):
        raise LlmError("엔드포인트 URL은 http:// 또는 https:// 로 시작해야 합니다.")
    if config.mode in ("openai", "anthropic") and url and urlparse(url).scheme != "https":
        raise LlmError("외부 API 주소는 https:// 만 허용합니다 (API Key 보호).")


# ---------------------------------------------------------------- 현재 설정 (프로세스 안 캐시)

_lock = threading.Lock()
_current: LlmConfig | None = None


def load(db: Any) -> LlmConfig:
    from app.models import AppSetting

    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        return LlmConfig()
    try:
        return LlmConfig.from_json(row.value)
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("llm_provider 설정을 읽지 못해 로컬 Ollama를 씁니다.")
        return LlmConfig()


def current() -> LlmConfig:
    global _current
    with _lock:
        if _current is None:
            from app.db import SessionLocal

            try:
                with SessionLocal() as db:
                    _current = load(db)
            except Exception:  # DB가 아직 준비되지 않았으면 기본값(로컬)으로 동작
                logger.exception("llm_provider 설정을 불러오지 못했습니다.")
                return LlmConfig()
        return _current


def set_current(config: LlmConfig | None) -> None:
    """설정을 저장한 뒤 호출한다 (None이면 다음 호출 때 DB에서 다시 읽음)."""
    global _current
    with _lock:
        _current = config


def masked_key(key: str | None) -> str | None:
    if not key:
        return None
    return "•" * 8 + key[-4:] if len(key) > 8 else "•" * 8


# ---------------------------------------------------------------- 호출


ERROR_DETAIL_CHARS = 200
# 오류 본문에 섞일 수 있는 인증 정보: 'Bearer …', 'x-api-key: …', 'sk-…' 형태의 키
_AUTH_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+[^\s\"',;}]+")
_KEY_FIELD = re.compile(r"(?i)\b(x-api-key|api[_-]?key|authorization)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+")
_KEY_LIKE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


def redact(text: str, secret: str | None) -> str:
    """오류 메시지에서 API 키·인증 헤더 값을 지운다 (길이를 자르기 전에 호출)."""
    if secret:
        text = text.replace(secret, "[API Key]")
    text = _AUTH_VALUE.sub(r"\1 [API Key]", text)
    text = _KEY_FIELD.sub(r"\1\2[API Key]", text)
    return _KEY_LIKE.sub("[API Key]", text)


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float, secret: str | None) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )

    def clean(text: str) -> str:
        return redact(text, secret)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 비밀값을 먼저 지운 뒤 길이를 자른다 (자른 뒤에 지우면 경계에 걸친 키 앞부분이 남는다)
        detail = clean(exc.read().decode("utf-8", "replace"))[:ERROR_DETAIL_CHARS]
        raise LlmError(f"LLM HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise LlmError(clean(f"LLM 연결 실패: {exc}")) from exc
    except json.JSONDecodeError as exc:
        raise LlmError("LLM 응답을 해석할 수 없습니다.") from exc


def _parse_object(text: str) -> dict[str, Any]:
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LlmError("LLM이 JSON이 아닌 응답을 반환했습니다.") from exc
    if not isinstance(result, dict):
        raise LlmError("LLM 응답 형식이 올바르지 않습니다.")
    return result


def _openai_compatible(config: LlmConfig, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    if config.mode == "openai":
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": False},
        }
    else:  # 호환 서버마다 지원 범위가 달라 가장 널리 쓰이는 JSON 모드 + 스키마 안내
        response_format = {"type": "json_object"}
        guide = json.dumps(schema, ensure_ascii=False)
        system = f"{system}\n\n반드시 다음 JSON 스키마에 맞는 JSON 객체 하나만 출력하세요:\n{guide}"
    body = _post(
        f"{config.effective_base_url}/chat/completions",
        {
            "model": config.effective_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": response_format,
        },
        headers,
        EXTERNAL_TIMEOUT,
        config.api_key,
    )
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError("LLM 응답 형식이 올바르지 않습니다.") from exc
    return _parse_object(content)


def _anthropic(config: LlmConfig, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    # 도구 하나를 강제로 쓰게 해 스키마에 맞는 JSON(도구 입력)을 받는다
    body = _post(
        f"{config.effective_base_url}/v1/messages",
        {
            "model": config.effective_model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "answer", "description": "판정 결과를 돌려준다.", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "answer"},
        },
        {"x-api-key": config.api_key or "", "anthropic-version": ANTHROPIC_VERSION},
        EXTERNAL_TIMEOUT,
        config.api_key,
    )
    for block in body.get("content", []) if isinstance(body, dict) else []:
        if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
            return block["input"]
    raise LlmError("LLM 응답에 판정 결과가 없습니다.")


def chat_json(system: str, user: str, schema: dict[str, Any], config: LlmConfig | None = None) -> dict[str, Any]:
    """매핑 엔진의 llm_fn. 현재 설정된 제공자로 JSON 판정을 받는다."""
    config = config or current()
    if config.mode == "ollama":
        return ollama.chat_json(system, user, schema, model=config.model)
    if config.mode == "anthropic":
        return _anthropic(config, system, user, schema)
    return _openai_compatible(config, system, user, schema)


def with_updates(base: LlmConfig, **changes: Any) -> LlmConfig:
    return replace(base, **changes)
