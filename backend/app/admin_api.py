"""관리 API: 표준 사전 승격 심사(관리자·소유자), 전사 표준 사전, 설정(소유자).

결과 데이터(실행 기록·결과 파일·변경 기록)를 바꾸는 API는 여기에도 없다.
"""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.auth import ManagerDep, OwnerDep
from app.config import OLLAMA_EMBED_MODEL
from app.db import get_session
from app.mapping import get_engine
from app.models import AppSetting, Automation, MappingApproval, PromotionCandidate, StandardTerm, User
from app.pii import mask_text
from app.promotion import PROMOTION_THRESHOLD, PromotionError, approve, reject
from app.timefmt import utc_to_kst

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

BASELINE_KEY = "manual_baseline_minutes"


# ---------------------------------------------------------------- 승격 심사


class CandidateOut(BaseModel):
    id: int
    column_name: str
    normalized: str
    field_key: str
    field_label: str | None
    approver_count: int
    approvers: list[str]
    status: str
    created_at: datetime
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {i: n for i, n in db.execute(select(User.id, User.display_name).where(User.id.in_(ids)))}


def candidate_out(db: Session, c: PromotionCandidate) -> CandidateOut:
    labels = {f.key: f.label for f in get_engine().fields}
    approver_ids = list(
        db.scalars(
            select(MappingApproval.user_id).where(
                MappingApproval.normalized == c.normalized, MappingApproval.field_key == c.field_key
            )
        )
    )
    names = _names(db, set(approver_ids) | ({c.reviewed_by} if c.reviewed_by else set()))
    return CandidateOut(
        id=c.id,
        column_name=mask_text(c.column_name),
        normalized=mask_text(c.normalized),
        field_key=c.field_key,
        field_label=labels.get(c.field_key),
        approver_count=c.approver_count,
        approvers=[names.get(i, f"#{i}") for i in approver_ids],
        status=c.status,
        created_at=utc_to_kst(c.created_at),
        reviewed_by_name=names.get(c.reviewed_by) if c.reviewed_by else None,
        reviewed_at=utc_to_kst(c.reviewed_at),
        review_note=c.review_note,
    )


class PromotionList(BaseModel):
    threshold: int
    items: list[CandidateOut]


@router.get("/admin/promotions", response_model=PromotionList)
def list_promotions(
    _: ManagerDep,
    db: Annotated[Session, Depends(get_session)],
    status_: Annotated[Literal["pending", "approved", "rejected", "all"], Query(alias="status")] = "pending",
) -> PromotionList:
    stmt = select(PromotionCandidate).order_by(PromotionCandidate.id.desc())
    if status_ != "all":
        stmt = stmt.where(PromotionCandidate.status == status_)
    return PromotionList(threshold=PROMOTION_THRESHOLD, items=[candidate_out(db, c) for c in db.scalars(stmt)])


class ReviewNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _candidate(db: Session, candidate_id: int) -> PromotionCandidate:
    c = db.get(PromotionCandidate, candidate_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "승격 후보를 찾을 수 없습니다.")
    return c


@router.post("/admin/promotions/{candidate_id}/approve", response_model=CandidateOut)
def approve_promotion(
    candidate_id: int, body: ReviewNoteIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> CandidateOut:
    """승인하면 전사 표준 사전(standard_terms)에 반영되어 모든 사용자의 매핑 1단계에 적용된다."""
    c = _candidate(db, candidate_id)
    try:
        approve(db, get_engine(), c, actor.id, body.note)
    except PromotionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return candidate_out(db, c)


@router.post("/admin/promotions/{candidate_id}/reject", response_model=CandidateOut)
def reject_promotion(
    candidate_id: int, body: ReviewNoteIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> CandidateOut:
    c = _candidate(db, candidate_id)
    try:
        reject(db, c, actor.id, body.note)
    except PromotionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return candidate_out(db, c)


# ---------------------------------------------------------------- 전사 표준 사전


class StandardTermOut(BaseModel):
    id: int
    term: str
    normalized: str
    field_key: str
    field_label: str | None
    approved_by_name: str | None
    created_at: datetime


@router.get("/admin/standard-terms", response_model=list[StandardTermOut])
def list_standard_terms(_: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> list[StandardTermOut]:
    labels = {f.key: f.label for f in get_engine().fields}
    terms = list(db.scalars(select(StandardTerm).order_by(StandardTerm.id.desc())))
    names = _names(db, {t.approved_by for t in terms if t.approved_by})
    return [
        StandardTermOut(
            id=t.id,
            term=mask_text(t.term),
            normalized=mask_text(t.normalized),
            field_key=t.field_key,
            field_label=labels.get(t.field_key),
            approved_by_name=names.get(t.approved_by) if t.approved_by else None,
            created_at=utc_to_kst(t.created_at),
        )
        for t in terms
    ]


@router.delete("/admin/standard-terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_standard_term(term_id: int, _: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> Response:
    """전사 표준 사전에서 뺀다 (이미 만든 결과 파일·기록은 그대로)."""
    term = db.get(StandardTerm, term_id)
    if term is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "표준 사전 항목을 찾을 수 없습니다.")
    db.delete(term)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- 설정 (소유자)


def get_setting(db: Session, key: str) -> object | None:
    row = db.get(AppSetting, key)
    return json.loads(row.value) if row is not None else None


def baseline_minutes(db: Session) -> float | None:
    value = get_setting(db, BASELINE_KEY)
    return float(value) if isinstance(value, int | float) else None


class AutomationBaselineOut(BaseModel):
    id: int
    name: str
    owner_name: str | None
    manual_baseline_minutes: float | None


class SettingsOut(BaseModel):
    manual_baseline_minutes: float | None  # 실행 1건을 사람이 직접 할 때 걸리는 시간 (분)
    automations: list[AutomationBaselineOut]  # 자동화별로 다르게 정한 기준 (없으면 기본값)
    updated_at: datetime | None
    updated_by_name: str | None


def settings_out(db: Session) -> SettingsOut:
    row = db.get(AppSetting, BASELINE_KEY)
    automations = list(db.scalars(select(Automation).order_by(Automation.id)))
    names = _names(
        db, {a.owner_id for a in automations if a.owner_id} | ({row.updated_by} if row and row.updated_by else set())
    )
    return SettingsOut(
        manual_baseline_minutes=baseline_minutes(db),
        automations=[
            AutomationBaselineOut(
                id=a.id,
                name=a.name,
                owner_name=names.get(a.owner_id) if a.owner_id else None,
                manual_baseline_minutes=a.manual_baseline_minutes,
            )
            for a in automations
        ],
        updated_at=utc_to_kst(row.updated_at) if row is not None else None,
        updated_by_name=names.get(row.updated_by) if row is not None and row.updated_by else None,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(_: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> SettingsOut:
    return settings_out(db)


class BaselineIn(BaseModel):
    # 0 초과 ~ 7일. None = 정하지 않음 (절약 시간을 계산하지 않음)
    manual_baseline_minutes: float | None = Field(default=None, gt=0, le=10080)


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: BaselineIn, actor: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> SettingsOut:
    row = db.get(AppSetting, BASELINE_KEY)
    if body.manual_baseline_minutes is None:
        if row is not None:
            db.delete(row)
    else:
        if row is None:
            row = AppSetting(key=BASELINE_KEY, value="null")
            db.add(row)
        row.value = json.dumps(body.manual_baseline_minutes)
        row.updated_by, row.updated_at = actor.id, datetime.now(UTC)
    db.commit()
    return settings_out(db)


@router.put("/settings/automations/{automation_id}", response_model=SettingsOut)
def update_automation_baseline(
    automation_id: int, body: BaselineIn, _: OwnerDep, db: Annotated[Session, Depends(get_session)]
) -> SettingsOut:
    automation = db.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자동화를 찾을 수 없습니다.")
    automation.manual_baseline_minutes = body.manual_baseline_minutes
    db.commit()
    return settings_out(db)


# ---------------------------------------------------------------- LLM 제공자 (소유자)


class LlmSettingsOut(BaseModel):
    mode: Literal["ollama", "openai", "anthropic", "custom"]
    mode_label: str
    model: str | None  # 입력한 모델 (비우면 기본 모델)
    effective_model: str | None
    base_url: str | None
    effective_base_url: str
    api_key_set: bool  # API 키 자체는 돌려주지 않는다
    api_key_hint: str | None  # 끝 4자리만
    is_external: bool
    modes: dict[str, str]
    default_models: dict[str, str]
    embedding: str
    updated_at: datetime | None
    updated_by_name: str | None


def llm_settings_out(db: Session) -> LlmSettingsOut:
    config = llm.load(db)
    row = db.get(AppSetting, llm.SETTING_KEY)
    names = _names(db, {row.updated_by} if row is not None and row.updated_by else set())
    return LlmSettingsOut(
        mode=config.mode,  # type: ignore[arg-type]
        mode_label=llm.MODE_LABEL[config.mode],
        model=config.model,
        effective_model=config.effective_model,
        base_url=config.base_url,
        effective_base_url=config.effective_base_url,
        api_key_set=bool(config.api_key),
        api_key_hint=llm.masked_key(config.api_key),
        is_external=config.is_external,
        modes=llm.MODE_LABEL,
        default_models={k: v for k, v in llm.DEFAULT_MODEL.items() if v},
        embedding=f"로컬 Ollama {OLLAMA_EMBED_MODEL} (모든 모드 공통)",
        updated_at=utc_to_kst(row.updated_at) if row is not None else None,
        updated_by_name=names.get(row.updated_by) if row is not None and row.updated_by else None,
    )


@router.get("/settings/llm", response_model=LlmSettingsOut)
def read_llm_settings(_: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> LlmSettingsOut:
    return llm_settings_out(db)


class LlmSettingsIn(BaseModel):
    mode: Literal["ollama", "openai", "anthropic", "custom"]
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    # None = 저장된 키 유지, "" = 키 삭제, 값 = 새 키
    api_key: str | None = Field(default=None, max_length=500)


@router.put("/settings/llm", response_model=LlmSettingsOut)
def update_llm_settings(
    body: LlmSettingsIn, actor: OwnerDep, db: Annotated[Session, Depends(get_session)]
) -> LlmSettingsOut:
    """LLM 제공자를 바꾼다. 기본(로컬 Ollama) 외의 모드는 열 이름·샘플 값이 외부로 전송된다."""
    previous = llm.load(db)
    keep_key = body.api_key is None and previous.mode == body.mode
    api_key = previous.api_key if keep_key else (body.api_key or "").strip() or None
    config = llm.LlmConfig(
        mode=body.mode,
        model=(body.model or "").strip() or None,
        base_url=(body.base_url or "").strip().rstrip("/") or None,
        api_key=api_key if body.mode != "ollama" else None,
    )
    try:
        llm.validate(config)
    except llm.LlmError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = db.get(AppSetting, llm.SETTING_KEY)
    if row is None:
        row = AppSetting(key=llm.SETTING_KEY, value="{}")
        db.add(row)
    row.value = config.to_json()
    row.updated_by, row.updated_at = actor.id, datetime.now(UTC)
    db.commit()
    llm.set_current(config)
    logger.info("llm provider set to %s by user %s", config.mode, actor.id)  # 키·주소는 기록하지 않는다
    return llm_settings_out(db)


class LlmTestOut(BaseModel):
    ok: bool
    mode_label: str
    model: str | None
    latency_ms: int
    message: str


@router.post("/settings/llm/test", response_model=LlmTestOut)
def test_llm_settings(_: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> LlmTestOut:
    """저장된 설정으로 짧은 질문 하나를 보내 연결을 확인한다 (자료 내용은 보내지 않음)."""
    config = llm.load(db)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    started = time.perf_counter()
    try:
        answer = llm.chat_json("연결 확인용 요청입니다.", 'JSON {"ok": true} 로만 답하세요.', schema, config=config)
        ok, message = bool(answer.get("ok")), "연결되었습니다."
    except llm.LlmError as exc:
        ok, message = False, str(exc)
    return LlmTestOut(
        ok=ok,
        mode_label=llm.MODE_LABEL[config.mode],
        model=config.effective_model,
        latency_ms=round((time.perf_counter() - started) * 1000),
        message=message,
    )
