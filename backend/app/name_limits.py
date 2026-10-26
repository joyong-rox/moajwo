"""파일에서 온 이름(시트·열 이름, 용어사전 이름)의 저장 규칙 — 두 DB에서 같은 규칙으로, 쓰기 전에 거절한다.

PostgreSQL은 varchar(n)보다 긴 값을 쓰는 순간 DB 오류(22001 → 500)를 내고, NUL(0x00)이 든 글자는 드라이버가 보내지
않는다(DataError → 500). SQLite는 둘 다 그대로 저장한다. 같은 파일이 DB에 따라 되거나 안 되지 않게, 실제로 저장할 값만
확정·검토·통합·용어사전 반영 전에 같은 규칙으로 거절한다 (비교·계산에만 쓰고 저장하지 않는 이름은 보지 않는다).

- 한도는 글자 수다 (PostgreSQL varchar(n)도 바이트가 아니라 글자 — 한글 255자는 765바이트지만 들어간다).
- 이름을 자르거나 바꾸거나 NUL을 지우지 않는다 — 서로 다른 열이 같은 열이 될 수 있다. 원본에서 고쳐 다시 올리게 한다.
  막는 글자는 NUL 하나다 (한글·줄바꿈·탭·여러 줄 머리글은 그대로).
- 거절 문구에는 고정 오류 코드와 위치(파일 번호·시트 순번·열 위치)·글자 수·한도만 쓴다 — 이름 원문도 가린 이름도 넣지
  않는다 (가림이 제어 문자가 섞인 글자까지 안전하게 가린다고 보지 않는다).
- 한도는 모델 정의에서 읽는다 — 스키마를 넓히면 함께 바뀐다. 긴 이름 자체를 받으려면 확정·학습·승격·고유 키를
  함께 바꾸는 별도 설계가 필요하다 (고유 제약·색인이 걸린 열이라 한 열만 넓혀서는 안 됨).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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
    SourceFile,
    StandardTerm,
)
from app.pii import column_letter


def _limit(*columns: Column) -> int:
    return min(c.type.length for c in columns)


# 확정·검토 항목·변경 기록의 시트·열 이름 — 검토 항목의 이름이 확정 키로 다시 쓰이므로 모두 같은 한도
SHEET_MAX = _limit(MappingDecision.__table__.c.sheet_name, ReviewItem.__table__.c.sheet, RunChange.__table__.c.sheet)
COLUMN_MAX = _limit(
    MappingDecision.__table__.c.column_name, ReviewItem.__table__.c.column, RunChange.__table__.c.source_column
)
LABEL_MAX = RunChange.__table__.c.column.type.length  # 결과 파일의 열 이름 (변경 기록)
FILE_NAME_MAX = _limit(
    SourceFile.__table__.c.original_name, ReviewItem.__table__.c.file_name, RunChange.__table__.c.file_name
)
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

NUL = "\x00"
# 고정 오류 코드 — 문구 끝에 [코드]로 붙인다 (문구가 바뀌어도 같은 원인을 찾을 수 있게)
TOO_LONG = "NAME_TOO_LONG"  # 이름이 저장 한도를 넘음
HAS_NUL = "NAME_HAS_NUL"  # 이름에 NUL이 있음
VALUE_HAS_NUL = "VALUE_HAS_NUL"  # 저장할 칸 값·사유에 NUL이 있음
HINTS = {
    TOO_LONG: "원본 파일에서 이름을 줄인 뒤 다시 올리세요.",
    HAS_NUL: "원본 파일에서 이름의 NUL(0x00) 문자를 지운 뒤 다시 올리세요.",
}


class NameNotStorable(ValueError):
    """저장할 수 없는 이름 — 문구에는 코드·위치·글자 수·한도만 있다 (원문 없음)."""


@dataclass(frozen=True)
class Problem:
    code: str  # TOO_LONG / HAS_NUL
    text: str  # 원문 없는 사유 ('열 이름이 256자로 저장 한도 255자를 넘습니다')


class SheetLike(Protocol):
    """파서 결과(SheetResult)와 화면용 보기(SheetView)에 공통인 부분."""

    name: str
    tab: int
    columns: Sequence[str]
    source_columns: Sequence[int]
    header_row: int | None
    header_row_end: int | None


def name_problem(what: str, name: str, limit: int) -> Problem | None:
    """이름 하나를 그 열에 저장할 수 없으면 사유, 아니면 None. what은 받침으로 끝나는 말 ('열 이름'·'정규화한 이름')."""
    if NUL in name:
        return Problem(HAS_NUL, f"{what}에 NUL(0x00) 문자가 있습니다")
    if len(name) > limit:
        return Problem(TOO_LONG, f"{what}이 {len(name):,}자로 저장 한도 {limit}자를 넘습니다")
    return None


def sheet_column_problem(sheet: str, column: str | None = None) -> Problem | None:
    """확정·검토 항목·변경 기록에 저장할 시트·열 이름의 사유 (저장할 수 있으면 None)."""
    problem = name_problem("시트 이름", sheet, SHEET_MAX)
    if problem is None and column is not None:
        problem = name_problem("열 이름", column, COLUMN_MAX)
    return problem


def hint(problems: Iterable[Problem]) -> str:
    """고칠 방법과 코드: '… 다시 올리세요. [NAME_TOO_LONG]'."""
    codes = list(dict.fromkeys(p.code for p in problems))
    text = HINTS[codes[0]] if len(codes) == 1 else "원본 파일에서 이름을 고친 뒤 다시 올리세요."
    return f"{text} [{', '.join(codes)}]"


def refusal(action: str, problems: Sequence[tuple[str, Problem]]) -> str:
    """'{action} — {위치}: {사유} / …(최대 3곳) 외 n곳. {고칠 방법} [코드]'. 위치가 빈 문자열이면 사유만."""
    shown = " / ".join(f"{where}: {p.text}" if where else p.text for where, p in problems[:3])
    if len(problems) > 3:
        shown += f" 외 {len(problems) - 3}곳"
    return f"{action} — {shown}. {hint(p for _, p in problems)}"


def sheet_place(sheet: SheetLike) -> str:
    """시트 위치: 원본 통합 문서의 탭 순번 (파서가 건너뛴 설명 시트·숨긴 시트도 센다 — 이름은 쓰지 않음)."""
    return f"{sheet.tab + 1}번째 시트"


def _source(sheet: SheetLike, j: int) -> int:
    return sheet.source_columns[j] if j < len(sheet.source_columns) else j


def column_place(sheet: SheetLike, j: int) -> str:
    """열 위치: 원본의 열 문자와 머리글 행 (병합으로 뺀 복제 열이 있어도 원본 위치)."""
    place = f"{column_letter(_source(sheet, j))}열"
    if sheet.header_row is not None:
        end = sheet.header_row_end or sheet.header_row
        rows = f"{sheet.header_row}행" if end == sheet.header_row else f"{sheet.header_row}~{end}행"
        place += f"(머리글 {rows})"
    return place


def cell_place(sheet: SheetLike, row: int | None, j: int | None) -> str:
    """칸 위치: '5행 · C열' (원본 행 번호·원본 열 문자 — 둘 중 모르는 것은 뺀다)."""
    parts = [f"{row}행"] if row is not None else []
    if j is not None and 0 <= j < len(sheet.columns):
        parts.append(f"{column_letter(_source(sheet, j))}열")
    return " · ".join(parts)


def file_problems(file_id: int, sheets: Iterable[SheetLike]) -> list[tuple[str, Problem]]:
    """한 파일의 시트·열 이름 중 저장할 수 없는 것의 위치와 사유 (원문 없이)."""
    out = []
    for s in sheets:
        where = f"파일 #{file_id} · {sheet_place(s)}"
        if (p := name_problem("시트 이름", s.name, SHEET_MAX)) is not None:
            out.append((where, p))
        for j, c in enumerate(s.columns):
            if (p := name_problem("열 이름", c, COLUMN_MAX)) is not None:
                out.append((f"{where} · {column_place(s, j)}", p))
    return out


def check_files(files: Iterable[tuple[int, Iterable[SheetLike]]], action: str) -> None:
    """여러 파일(번호, 시트)의 시트·열 이름을 검사한다. 저장할 수 없는 것이 있으면 NameNotStorable."""
    problems = [p for file_id, sheets in files for p in file_problems(file_id, sheets)]
    if problems:
        raise NameNotStorable(refusal(f"이름을 저장할 수 없어 {action}", problems))


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
