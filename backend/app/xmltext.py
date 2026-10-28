"""결과 엑셀(XML 1.0)에 쓸 수 없는 글자 — C0 제어 문자(탭·줄바꿈·CR 제외), 대리 쌍 코드, 비문자 U+FFFE·U+FFFF.

openpyxl의 ILLEGAL_CHARACTERS_RE는 C0 제어 문자만 본다. 그래서 U+FFFE·U+FFFF는 예외 없이 저장돼 다시 열리지 않는 파일이
된다 (lxml 없는 저장 경로). 결과 칸·이름처럼 원본 값은 거절하고(has_illegal), 요약·메모 같은 설명 글만 printable로
바꿔 쓴다."""

import re
from typing import Any

ILLEGAL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def has_illegal(value: Any) -> bool:
    """엑셀에 쓸 수 없는 글자가 든 문자열인지 (문자열이 아니면 거짓)."""
    return isinstance(value, str) and ILLEGAL_RE.search(value) is not None


def printable(value: Any) -> Any:
    """설명 글에서 쓸 수 없는 글자를 '\ufffd'로 — 원본 값이 아닌 글(경고 사유·기준 기간·메모)에만 쓴다."""
    return ILLEGAL_RE.sub("\ufffd", value) if isinstance(value, str) else value
