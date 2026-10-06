"""사용자 매핑 확정 저장 + 별칭 학습.

확정한 열 이름은 learned_aliases에 쌓여 다음 매핑부터 1단계(정확 일치)에서 바로 잡힌다.
표준 사전 파일(standard_dictionary.json)은 수정하지 않는다.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mapping import MappingEngine, split_column_name
from app.models import LearnedAlias, MappingDecision

_GENERATED_PREFIX = "열"


@dataclass
class DecisionInput:
    sheet: str
    column: str
    field_key: str | None


@dataclass
class SaveResult:
    saved: int = 0
    learned: list[str] = field(default_factory=list)
    not_learned: list[dict[str, str]] = field(default_factory=list)


def load_learned(session: Session, user_id: int | None = None) -> dict[str, str]:
    """이 사용자의 학습 별칭 (+ 계정 도입 전에 학습한 공용 별칭). 개인 별칭이 공용보다 우선한다."""
    rows = session.execute(
        select(LearnedAlias.normalized, LearnedAlias.field_key, LearnedAlias.user_id).where(
            (LearnedAlias.user_id.is_(None)) | (LearnedAlias.user_id == user_id)
        )
    ).all()
    out: dict[str, str] = {}
    for normalized, key, _owner in sorted(rows, key=lambda r: r[2] is not None):
        out[normalized] = key
    return out


def load_decisions(session: Session, file_id: int) -> dict[str, dict[str, str | None]]:
    """시트명 → {열 이름 → field_key}"""
    rows = session.scalars(select(MappingDecision).where(MappingDecision.source_file_id == file_id))
    out: dict[str, dict[str, str | None]] = {}
    for d in rows:
        out.setdefault(d.sheet_name, {})[d.column_name] = d.field_key
    return out


def save_decisions(
    session: Session,
    engine: MappingEngine,
    file_id: int,
    items: list[DecisionInput],
    learn: bool = True,
    user_id: int | None = None,
) -> SaveResult:
    """확정 저장. learn=False면 이 파일의 확정만 저장하고 열 이름은 학습하지 않는다.

    (검토 패널에서 '다음부터 이 형식 기억하기'를 선택하지 않은 경우)
    """
    result = SaveResult()
    for item in items:
        decision = session.scalar(
            select(MappingDecision).where(
                MappingDecision.source_file_id == file_id,
                MappingDecision.sheet_name == item.sheet,
                MappingDecision.column_name == item.column,
            )
        )
        if decision is None:
            decision = MappingDecision(source_file_id=file_id, sheet_name=item.sheet, column_name=item.column)
            session.add(decision)
        decision.field_key = item.field_key
        session.flush()  # 같은 요청에 같은 열이 다시 오면 위 조회가 이 행을 찾도록 (autoflush 꺼짐)
        result.saved += 1

        if learn and item.field_key is not None:
            reason = learn_column(session, engine, file_id, item.column, item.field_key, user_id)
            if reason is None:
                result.learned.append(item.column)
            else:
                result.not_learned.append({"column": item.column, "reason": reason})
    session.commit()
    return result


def learn_column(
    session: Session, engine: MappingEngine, file_id: int, column: str, key: str, user_id: int | None = None
) -> str | None:
    """열 이름을 이 사용자의 별칭으로 학습한다. 학습하지 않으면 그 이유를 돌려준다."""
    name = split_column_name(column)
    normalized = name.primary
    if not normalized or (column.startswith(_GENERATED_PREFIX) and column[len(_GENERATED_PREFIX) :].isdigit()):
        return "헤더가 없는 열이라 이름을 학습하지 않음"
    builtin = engine.exact_key(normalized)
    if builtin == key:
        return "이미 표준 사전에 있는 이름"
    if builtin is not None:
        # 표준 사전과 충돌하면 사전을 우선한다 (이 파일의 확정은 저장됨).
        return f"표준 사전에서 다른 항목({builtin})의 이름이라 학습하지 않음"
    owner = LearnedAlias.user_id.is_(None) if user_id is None else LearnedAlias.user_id == user_id
    alias = session.scalar(select(LearnedAlias).where(LearnedAlias.normalized == normalized, owner))
    if alias is None:
        session.add(
            LearnedAlias(normalized=normalized, alias=column, field_key=key, source_file_id=file_id, user_id=user_id)
        )
        session.flush()
    else:  # 같은 이름을 다른 항목으로 다시 확정하면 최신 확정을 따른다
        alias.field_key, alias.alias, alias.source_file_id = key, column, file_id
    return None
