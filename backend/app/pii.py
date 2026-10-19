"""개인정보 패턴 탐지 (주민등록번호, 휴대전화번호, 이메일).

원문 값은 반환·저장·로그하지 않는다. 위치(시트/행/열)와 마스킹한 값만 돌려준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

PiiType = Literal["rrn", "mobile", "email"]
PII_LABEL: dict[PiiType, str] = {"rrn": "주민등록번호", "mobile": "휴대전화번호", "email": "이메일"}
MAX_ITEMS = 50  # 위치 목록 상한 (건수는 모두 센다)

# 생년월일 6자리 + 성별 1~8 + 6자리. 하이픈·공백 구분은 선택.
_RRN = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})[-\s]?([1-8])\d{6}(?!\d)")
_MOBILE = re.compile(r"(?<!\d)(01[016789])[-.\s]?(\d{3,4})[-.\s]?(\d{4})(?!\d)")
# 앞 경계는 로컬 부분에 쓰는 ASCII 글자만 막는다 — 주소 중간부터 잘라 잡지 않되, 한글 등 다른 글자 바로 뒤에
# 붙은 주소('인구수kim@…', '인구수_kim@…')도 잡는다 (\w는 한글까지 포함해 예전에는 이런 주소를 놓쳤다).
# 탐지(find_in_text)와 가림(mask_text)이 같은 식을 쓴다.
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")


@dataclass
class PiiItem:
    type: PiiType
    sheet: str
    row: int  # 1-based 원본 행 번호
    column: str  # 엑셀 열 문자 (A, B, ...)
    masked: str


@dataclass
class PiiReport:
    counts: dict[str, int] = field(default_factory=lambda: {"rrn": 0, "mobile": 0, "email": 0})
    items: list[PiiItem] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return any(self.counts.values())

    def add(self, item: PiiItem) -> None:
        self.counts[item.type] += 1
        if len(self.items) < MAX_ITEMS:
            self.items.append(item)

    def merge(self, other: PiiReport) -> None:
        for key, value in other.counts.items():
            self.counts[key] += value
        self.items.extend(other.items[: max(0, MAX_ITEMS - len(self.items))])


def column_letter(index: int) -> str:
    """0-based 열 번호 → 엑셀 열 문자."""
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _valid_birth(mm: str, dd: str) -> bool:
    return 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31


def find_in_text(text: str) -> list[tuple[PiiType, str]]:
    """문자열에서 개인정보 패턴을 찾아 (종류, 마스킹 값) 목록으로 돌려준다."""
    found: list[tuple[PiiType, str]] = []
    rrn_spans = []
    for m in _RRN.finditer(text):
        if _valid_birth(m.group(2), m.group(3)):
            found.append(("rrn", f"{m.group(1)}{m.group(2)}{m.group(3)}-{m.group(4)}******"))
            rrn_spans.append(m.span())
    for m in _MOBILE.finditer(text):
        if any(s <= m.start() < e for s, e in rrn_spans):
            continue  # 주민번호 일부를 전화번호로 중복 판정하지 않는다
        found.append(("mobile", f"{m.group(1)}-****-{m.group(3)}"))
    for m in _EMAIL.finditer(text):
        local = m.group(1)
        found.append(("email", f"{local[0]}***@{m.group(2)}"))
    return found


def mask_text(text: str) -> str:
    """문자열 안의 개인정보 패턴을 마스킹한 사본을 돌려준다 (원본은 그대로)."""

    def rrn(m: re.Match[str]) -> str:
        if not _valid_birth(m.group(2), m.group(3)):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}-{m.group(4)}******"

    text = _RRN.sub(rrn, text)
    text = _MOBILE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", text)
    return _EMAIL.sub(lambda m: f"{m.group(1)[0]}***@{m.group(2)}", text)


def mask_cell(cell: object) -> object:
    """화면·API·LLM으로 내보낼 셀 값. 개인정보 패턴이 있으면 마스킹한다."""
    if isinstance(cell, str):
        return mask_text(cell)
    if isinstance(cell, int) and not isinstance(cell, bool) and len(str(cell)) == 13:
        masked = mask_text(str(cell))
        return masked if masked != str(cell) else cell
    return cell


def mask_nested(value: object, rrn_numbers: bool = False) -> object:
    """응답으로 내보낼 dict·list 안의 모든 글자를 마스킹한다 (키·숫자·코드는 그대로).

    rrn_numbers: 주민등록번호가 탐지된 파일의 기록이면 주민등록번호 형태의 13자리 숫자도 가린다 (mask_rrn_number).
    """
    if isinstance(value, dict):
        return {k: mask_nested(v, rrn_numbers) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_nested(v, rrn_numbers) for v in value]
    if rrn_numbers:
        return mask_rrn_number(value)
    if isinstance(value, str):
        return mask_text(value)
    return value


_GROUPED_13 = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3}){4}(?![\d,])")  # 9,001,011,234,567 (13자리)


def mask_rrn_number(value: object) -> object:
    """주민등록번호 형태(13자리, 생년월일·성별 자리 유효)인 숫자와 그 숫자를 쉼표로 끊은 글자만 가린다.

    금액·통계 숫자 전체를 가리지 않도록, 개인정보(주민등록번호)가 탐지된 파일의 기록에만 쓴다.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and float(value).is_integer() and len(str(int(value))) == 13:
        text = str(int(value))
        return mask_text(text) if mask_text(text) != text else value
    if isinstance(value, str):

        def one(m: re.Match[str]) -> str:
            digits = m.group(0).replace(",", "")
            return mask_text(digits) if mask_text(digits) != digits else m.group(0)

        return mask_text(_GROUPED_13.sub(one, value))
    return value


def has_pii(text: str) -> bool:
    """개인정보 형식(이메일·휴대전화·주민등록번호)이 들어 있는지."""
    return mask_text(text) != text


def scan_grid(sheet: str, grid: list[list[object]], report: PiiReport) -> None:
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell is None or isinstance(cell, bool | float):
                continue
            text = str(cell)
            if isinstance(cell, int) and len(text) != 13:  # 숫자형은 하이픈 없는 주민번호(13자리)만 해당
                continue
            for kind, masked in find_in_text(text):
                report.add(PiiItem(kind, sheet, r + 1, column_letter(c), masked))
