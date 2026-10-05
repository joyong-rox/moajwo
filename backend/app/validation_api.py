import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.decisions import load_decisions, load_learned
from app.files import load_parsed
from app.mapping import get_engine
from app.models import SourceFile, ValidationRun
from app.validation import (
    BLOCK_LABEL,
    ENTITY_FIELDS,
    WARNING_LABEL,
    FileInput,
    MappedColumn,
    SheetInput,
    ValidationParams,
    validate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["validation"])

MAX_FILES = 50


# ---------------------------------------------------------------- 파일 목록 / 개인정보 확인


class PiiOut(BaseModel):
    counts: dict[str, int]
    items: list[dict[str, Any]]
    acknowledged: bool


class FileListItem(BaseModel):
    id: int
    original_name: str
    uploaded_at: datetime
    pii: PiiOut | None


def pii_out(record: SourceFile) -> PiiOut | None:
    if not record.pii_summary:
        return None
    summary = json.loads(record.pii_summary)
    if not any(summary.get("counts", {}).values()):
        return None
    return PiiOut(counts=summary["counts"], items=summary["items"], acknowledged=record.pii_acknowledged_at is not None)


@router.get("/files", response_model=list[FileListItem])
def list_files(session: Annotated[Session, Depends(get_session)], limit: int = 100) -> list[FileListItem]:
    records = session.scalars(select(SourceFile).order_by(SourceFile.id.desc()).limit(min(max(limit, 1), 500)))
    return [
        FileListItem(id=r.id, original_name=r.original_name, uploaded_at=r.uploaded_at, pii=pii_out(r)) for r in records
    ]


@router.post("/files/{file_id}/pii/acknowledge", response_model=PiiOut)
def acknowledge_pii(file_id: int, session: Annotated[Session, Depends(get_session)]) -> PiiOut:
    """사용자가 개인정보 포함 사실을 확인했다고 기록한다 (원본·탐지 결과는 바꾸지 않는다)."""
    record = session.get(SourceFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "파일을 찾을 수 없습니다.")
    if pii_out(record) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이 파일에서는 개인정보 패턴이 발견되지 않았습니다.")
    if record.pii_acknowledged_at is None:
        record.pii_acknowledged_at = datetime.now(UTC)
        session.commit()
    result = pii_out(record)
    assert result is not None
    return result


# ---------------------------------------------------------------- 검증


class ValidationIn(BaseModel):
    file_ids: list[int] = Field(min_length=1, max_length=MAX_FILES)
    baseline_file_ids: list[int] = Field(default_factory=list, max_length=MAX_FILES)
    expected_period: str | None = Field(default=None, pattern=r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$")
    required_entities: list[str] = Field(default_factory=list, max_length=500)
    entity_field: str | None = None
    change_threshold: float = Field(default=0.5, gt=0, le=10)

    @field_validator("entity_field")
    @classmethod
    def _entity_field(cls, value: str | None) -> str | None:
        if value is not None and value not in ENTITY_FIELDS:
            raise ValueError(f"entity_field는 {', '.join(ENTITY_FIELDS)} 중 하나여야 합니다.")
        return value


class IssueOut(BaseModel):
    code: str
    label: str
    message: str
    file_id: int | None
    file_name: str | None
    sheet: str | None
    row: int | None
    column: str | None
    expected: Any
    actual: Any


class PiiStatusOut(BaseModel):
    file_id: int
    file_name: str
    counts: dict[str, int]
    items: list[dict[str, Any]]
    acknowledged: bool


class ValidationOut(BaseModel):
    id: int
    status: str
    blocks: list[IssueOut]  # 차단 — 통합 불가
    warnings: list[IssueOut]  # 경고 — 통합 진행, 표시
    pii: list[PiiStatusOut]  # 개인정보 — 사용자 확인 필요
    notes: list[str]  # 참고 — 상태에 영향 없음
    created_at: datetime


def _file_input(session: Session, file_id: int, learned: dict[str, str]) -> FileInput:
    record, parsed = load_parsed(session, file_id)
    engine = get_engine()
    decisions = load_decisions(session, file_id)
    sheets = []
    for sheet in parsed.sheets:
        result = engine.map_columns(
            sheet.columns, sheet.samples, use_llm=False, learned=learned, decisions=decisions.get(sheet.name)
        )
        columns = [
            MappedColumn(i, c.column, c.field_key, c.qualifier)
            for i, c in enumerate(result.columns)
            if c.field_key and c.status in ("confirmed", "auto")
        ]
        sheets.append(SheetInput(sheet, columns))
    summary = json.loads(record.pii_summary) if record.pii_summary else {"counts": {}, "items": []}
    return FileInput(
        record.id,
        record.original_name,
        sheets,
        pii_counts=summary["counts"],
        pii_items=summary["items"],
        pii_acknowledged=record.pii_acknowledged_at is not None,
    )


def _issue_out(issue: Any, labels: dict[str, str]) -> IssueOut:
    data = asdict(issue)
    return IssueOut(label=labels[issue.code], **data)


def _validation_out(run: ValidationRun) -> ValidationOut:
    body = json.loads(run.result)
    return ValidationOut(id=run.id, status=run.status, created_at=run.created_at, **body)


@router.post("/validation", response_model=ValidationOut)
def run_validation(body: ValidationIn, session: Annotated[Session, Depends(get_session)]) -> ValidationOut:
    overlap = set(body.file_ids) & set(body.baseline_file_ids)
    if overlap:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "같은 파일을 검증 대상과 전월 자료로 함께 쓸 수 없습니다."
        )
    learned = load_learned(session)
    files = [_file_input(session, fid, learned) for fid in dict.fromkeys(body.file_ids)]
    baseline = [_file_input(session, fid, learned) for fid in dict.fromkeys(body.baseline_file_ids)]
    params = ValidationParams(
        expected_period=body.expected_period,
        required_entities=body.required_entities,
        entity_field=body.entity_field,
        change_threshold=body.change_threshold,
    )
    result = validate(files, params, baseline)
    payload = {
        "blocks": [_issue_out(i, BLOCK_LABEL).model_dump(mode="json") for i in result.blocks],
        "warnings": [_issue_out(i, WARNING_LABEL).model_dump(mode="json") for i in result.warnings],
        "pii": [asdict(p) for p in result.pii],
        "notes": result.notes,
    }
    run = ValidationRun(
        status=result.status, params=body.model_dump_json(), result=json.dumps(payload, ensure_ascii=False)
    )
    session.add(run)
    session.commit()
    logger.info(
        "validation run=%s status=%s blocks=%d warnings=%d",
        run.id,
        result.status,
        len(result.blocks),
        len(result.warnings),
    )
    return _validation_out(run)


@router.get("/validation/{run_id}", response_model=ValidationOut)
def get_validation(run_id: int, session: Annotated[Session, Depends(get_session)]) -> ValidationOut:
    run = session.get(ValidationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "검증 기록을 찾을 수 없습니다.")
    return _validation_out(run)
