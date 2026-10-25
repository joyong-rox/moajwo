"""매핑 규칙 승격: 개인 승인 → (서로 다른 사용자 3명 이상) 심사 대기열 → 관리자 승인 → 전사 표준 사전(standard_terms).

- 승인 기록: 검토 패널에서 열 매핑을 승인하거나, 매핑 화면에서 확정할 때 (사용자·열 이름·표준 항목당 1건)
- 기본 표준 사전과 이미 같은 매핑, 이미 표준 사전에 있는 이름, 헤더 없는 열('열3')은 기록하지 않는다.
- 반려한 후보는 다시 대기열에 올리지 않는다 (승인 수만 갱신).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.mapping import MappingEngine, split_column_name
from app.models import MappingApproval, PromotionCandidate, StandardTerm
from app.name_limits import LEARN_KEY_MAX, LEARN_NAME_MAX
from app.pii import mask_text

logger = logging.getLogger(__name__)

PROMOTION_THRESHOLD = 3  # 서로 다른 사용자 수
_GENERATED_PREFIX = "열"


class PromotionError(Exception):
    pass


def _key(column: str) -> str:
    return split_column_name(column).primary


def load_standard(db: Session) -> dict[str, str]:
    """전사 표준 사전: 정규화된 열 이름 → 표준 항목 key."""
    return dict(db.execute(select(StandardTerm.normalized, StandardTerm.field_key)).all())


def record_approval(
    db: Session, engine: MappingEngine, user_id: int | None, column: str, field_key: str
) -> PromotionCandidate | None:
    """승인 1건을 기록하고, 기준을 넘으면 심사 대기열에 올린다 (커밋은 호출한 쪽에서)."""
    if user_id is None:
        return None
    normalized = _key(column)
    if not normalized or (column.startswith(_GENERATED_PREFIX) and column[len(_GENERATED_PREFIX) :].isdigit()):
        return None
    if len(normalized) > LEARN_KEY_MAX or len(column) > LEARN_NAME_MAX:
        return None  # 저장 한도를 넘는 이름은 승격 근거로 남기지 않는다 (확정은 저장됨 — app/name_limits.py)
    if mask_text(column) != column:
        return None  # 개인정보가 든 열 이름은 모두가 보는 승격 심사·전사 사전으로 올리지 않는다
    if engine.exact_key(normalized) is not None:
        return None  # 기본 표준 사전이 정한 이름 (같으면 필요 없고, 다르면 사전이 우선)
    if db.scalar(select(StandardTerm.id).where(StandardTerm.normalized == normalized)) is not None:
        return None
    exists = db.scalar(
        select(MappingApproval.id).where(
            MappingApproval.normalized == normalized,
            MappingApproval.field_key == field_key,
            MappingApproval.user_id == user_id,
        )
    )
    if exists is None:
        db.add(MappingApproval(normalized=normalized, column_name=column, field_key=field_key, user_id=user_id))
        db.flush()
    approvers = (
        db.scalar(
            select(func.count(func.distinct(MappingApproval.user_id))).where(
                MappingApproval.normalized == normalized, MappingApproval.field_key == field_key
            )
        )
        or 0
    )
    candidate = db.scalar(
        select(PromotionCandidate).where(
            PromotionCandidate.normalized == normalized, PromotionCandidate.field_key == field_key
        )
    )
    if candidate is not None:
        candidate.approver_count = approvers
        return candidate
    if approvers < PROMOTION_THRESHOLD:
        return None
    candidate = PromotionCandidate(
        normalized=normalized, column_name=column, field_key=field_key, approver_count=approvers, status="pending"
    )
    db.add(candidate)
    db.flush()
    logger.info("promotion candidate %s queued (%d approvers)", candidate.id, approvers)
    return candidate


def approve(
    db: Session, engine: MappingEngine, candidate: PromotionCandidate, reviewer_id: int, note: str | None
) -> StandardTerm:
    if candidate.status != "pending":
        raise PromotionError("심사 대기 중인 후보만 처리할 수 있습니다.")
    builtin = engine.exact_key(candidate.normalized)
    if builtin is not None and builtin != candidate.field_key:
        raise PromotionError("기본 표준 사전에서 다른 항목의 이름이라 반영할 수 없습니다. 반려하세요.")
    existing = db.scalar(select(StandardTerm).where(StandardTerm.normalized == candidate.normalized))
    if existing is not None and existing.field_key != candidate.field_key:
        raise PromotionError(
            f"이미 전사 표준 사전에서 다른 항목({existing.field_key})으로 쓰는 이름입니다. "
            "기존 항목을 지운 뒤 승인하세요."
        )
    now = datetime.now(UTC)
    term = existing
    if term is None:
        term = StandardTerm(
            normalized=candidate.normalized,
            term=candidate.column_name,
            field_key=candidate.field_key,
            candidate_id=candidate.id,
            approved_by=reviewer_id,
        )
        db.add(term)
    candidate.status, candidate.reviewed_by, candidate.reviewed_at = "approved", reviewer_id, now
    candidate.review_note = note
    db.commit()
    return term


def reject(db: Session, candidate: PromotionCandidate, reviewer_id: int, note: str | None) -> None:
    if candidate.status != "pending":
        raise PromotionError("심사 대기 중인 후보만 처리할 수 있습니다.")
    candidate.status, candidate.reviewed_by, candidate.reviewed_at = "rejected", reviewer_id, datetime.now(UTC)
    candidate.review_note = note
    db.commit()
