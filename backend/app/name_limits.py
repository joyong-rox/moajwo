"""파일에서 온 이름(시트·열 이름, 용어사전 이름)의 저장 한도 — 두 DB에서 같은 한도로, 쓰기 전에 거절한다.

PostgreSQL은 varchar(n)보다 긴 값을 쓰는 순간 DB 오류(22001 → 500)를 내고, SQLite는 길이를 강제하지 않아 그대로
저장한다. 같은 파일이 DB에 따라 되거나 안 되지 않게, 확정·검토·통합·용어사전 반영 전에 같은 규칙으로 거절한다.

- 한도는 글자 수다 (PostgreSQL varchar(n)도 바이트가 아니라 글자 — 한글 255자는 765바이트지만 들어간다).
- 이름을 자르거나 바꾸거나 해시로 줄이지 않는다 — 서로 다른 열이 같은 열이 될 수 있다. 원본에서 줄여 다시 올리게 한다.
- 거절 문구에는 위치·글자 수·한도만 쓴다 (이름 원문을 되돌려 보내지 않음).
- 한도는 모델 정의에서 읽는다 — 스키마를 넓히면 함께 바뀐다. 긴 이름 자체를 받으려면 확정·학습·승격·고유 키를
  함께 바꾸는 별도 설계가 필요하다 (고유 제약·색인이 걸린 열이라 한 열만 넓혀서는 안 됨).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from sqlalchemy import Column

from app.models import (
    GlossaryAlias,
    GlossaryEntry,
    LearnedAlias,
    MappingApproval,
    MappingDecision,
    PromotionCandidate,
    ReviewItem,
    RunChange,
    StandardTerm,
)
from app.pii import column_letter, mask_text


def _limit(*columns: Column) -> int:
    return min(c.type.length for c in columns)


# 확정·검토 항목·변경 기록의 시트·열 이름 — 검토 항목의 이름이 확정 키로 다시 쓰이므로 모두 같은 한도
SHEET_MAX = _limit(MappingDecision.__table__.c.sheet_name, ReviewItem.__table__.c.sheet, RunChange.__table__.c.sheet)
COLUMN_MAX = _limit(
    MappingDecision.__table__.c.column_name, ReviewItem.__table__.c.column, RunChange.__table__.c.source_column
)
LABEL_MAX = RunChange.__table__.c.column.type.length  # 결과 파일의 열 이름 (변경 기록)
FILE_NAME_MAX = _limit(ReviewItem.__table__.c.file_name, RunChange.__table__.c.file_name)
# 학습·승격: 정규화한 키와 사용자가 본 이름 (키는 NFKC 정규화로 원래 이름보다 길어질 수 있다 — '㍿' 1자 → 4자)
LEARN_KEY_MAX = _limit(
    LearnedAlias.__table__.c.normalized,
    MappingApproval.__table__.c.normalized,
    PromotionCandidate.__table__.c.normalized,
    StandardTerm.__table__.c.normalized,
)
LEARN_NAME_MAX = _limit(
    LearnedAlias.__table__.c.alias,
    MappingApproval.__table__.c.column_name,
    PromotionCandidate.__table__.c.column_name,
    StandardTerm.__table__.c.term,
)
# 용어사전 (업로드한 사전 파일·검토 완료 학습의 기관명)
GLOSSARY_NAME_MAX = _limit(
    GlossaryEntry.__table__.c.name,
    GlossaryEntry.__table__.c.normalized,
    GlossaryAlias.__table__.c.alias,
    GlossaryAlias.__table__.c.normalized,
)
GLOSSARY_TYPE_MAX = GlossaryEntry.__table__.c.type_label.type.length
GLOSSARY_NOTE_MAX = GlossaryEntry.__table__.c.note.type.length
GLOSSARY_PREVIOUS_MAX = GlossaryEntry.__table__.c.previous_codes.type.length

FIX_HINT = "원본 파일에서 이름을 줄인 뒤 다시 올리세요."


class NameTooLong(ValueError):
    """저장 한도를 넘는 이름 — 문구에는 위치·글자 수·한도만 있다 (원문 없음)."""


class SheetLike(Protocol):
    """파서 결과(SheetResult)와 화면용 보기(SheetView)에 공통인 부분."""

    name: str
    columns: Sequence[str]
    source_columns: Sequence[int]
    header_row: int | None
    header_row_end: int | None


def too_long(sheet: str, column: str | None = None) -> str | None:
    """시트·열 이름이 저장 한도를 넘으면 그 사유(원문 없이), 아니면 None."""
    if len(sheet) > SHEET_MAX:
        return f"시트 이름이 {len(sheet):,}자로 저장 한도 {SHEET_MAX}자를 넘습니다"
    if column is not None and len(column) > COLUMN_MAX:
        return f"열 이름이 {len(column):,}자로 저장 한도 {COLUMN_MAX}자를 넘습니다"
    return None


def sheet_place(name: str, index: int) -> str:
    """시트 위치: 이름이 한도 안이면 가린 이름, 넘으면 순번 (긴 이름을 그대로 되돌려 보내지 않음)."""
    return f"시트 '{mask_text(name)}'" if len(name) <= SHEET_MAX else f"{index + 1}번째 시트"


def column_place(sheet: SheetLike, j: int) -> str:
    """열 위치: 원본의 열 문자와 머리글 행 (병합으로 뺀 복제 열이 있어도 원본 위치)."""
    source = sheet.source_columns[j] if j < len(sheet.source_columns) else j
    place = f"{column_letter(source)}열"
    if sheet.header_row is not None:
        end = sheet.header_row_end or sheet.header_row
        rows = f"{sheet.header_row}행" if end == sheet.header_row else f"{sheet.header_row}~{end}행"
        place += f"(머리글 {rows})"
    return place


def file_problems(file_name: str, sheets: Iterable[SheetLike]) -> list[str]:
    """한 파일의 시트·열 이름 중 저장 한도를 넘는 것의 위치와 글자 수 (원문 없이)."""
    out = []
    shown = mask_text(file_name)
    for i, s in enumerate(sheets):
        if len(s.name) > SHEET_MAX:
            out.append(f"'{shown}' · {i + 1}번째 시트: 시트 이름 {len(s.name):,}자 (한도 {SHEET_MAX}자)")
        for j, c in enumerate(s.columns):
            if len(c) > COLUMN_MAX:
                place = f"{sheet_place(s.name, i)} · {column_place(s, j)}"
                out.append(f"'{shown}' · {place}: 열 이름 {len(c):,}자 (한도 {COLUMN_MAX}자)")
    return out


def check_files(files: Iterable[tuple[str, Iterable[SheetLike]]], action: str) -> None:
    """여러 파일의 시트·열 이름을 검사한다. 넘는 것이 있으면 NameTooLong (위치 최대 3곳 + 나머지 개수)."""
    problems = [p for name, sheets in files for p in file_problems(name, sheets)]
    if problems:
        shown = " / ".join(problems[:3]) + (f" 외 {len(problems) - 3}곳" if len(problems) > 3 else "")
        raise NameTooLong(f"이름이 저장 한도를 넘어 {action} — {shown}. {FIX_HINT}")


def fit_names(names: Sequence[str], limit: int = FILE_NAME_MAX) -> str:
    """여러 이름을 ', '로 이어 한도 안에 담는다 — 넘으면 앞에서부터 담을 수 있는 만큼 + '외 n개'.
    표시 전용 값에만 쓴다 (확정 키처럼 다시 찾는 값에는 쓰지 않음)."""
    joined = ", ".join(names)
    if len(joined) <= limit:
        return joined
    for k in range(len(names) - 1, 0, -1):
        text = f"{', '.join(names[:k])} 외 {len(names) - k}개"
        if len(text) <= limit:
            return text
    return f"파일 {len(names)}개"
