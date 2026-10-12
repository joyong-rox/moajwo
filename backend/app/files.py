import json
import logging
from dataclasses import asdict
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, ensure_own, ensure_view
from app.config import DATA_DIR
from app.db import get_session
from app.decisions import DecisionInput, load_decisions, load_learned, save_decisions
from app.mapping import get_engine
from app.models import SourceFile
from app.parsing import SUPPORTED_EXTENSIONS, FileResult, ParseError, parse_file
from app.pii import mask_text
from app.promotion import load_standard, record_approval
from app.storage import MAX_UPLOAD_BYTES, UploadTooLarge, safe_original_name, store_original

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
mapping_router = APIRouter(prefix="/mapping", tags=["mapping"])

MAX_FILES_PER_REQUEST = 20


class SheetOut(BaseModel):
    name: str
    header_row: int | None
    columns: list[str]
    data_rows: int
    empty_rows: int
    subtotal_rows: int
    note_rows: int
    missing_cells: int
    header_row_end: int | None
    preview: list[dict[str, Any]]


class UploadResultOut(BaseModel):
    original_name: str
    id: int | None = None
    size_bytes: int | None = None
    encoding: str | None = None
    sheet_count: int = 0
    data_rows: int = 0
    sheets: list[SheetOut] = []
    pii: dict[str, Any] | None = None  # 개인정보 탐지 요약 (마스킹 값과 위치만)
    skipped_sheets: list[str] = []  # 통계표 설명 시트(메타정보·설명·안내)는 읽지 않음
    error: str | None = None


@router.post("", response_model=list[UploadResultOut])
def upload_files(
    files: Annotated[list[UploadFile], File()],
    session: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
) -> list[UploadResultOut]:
    if len(files) > MAX_FILES_PER_REQUEST:
        detail = f"한 번에 최대 {MAX_FILES_PER_REQUEST}개까지 올릴 수 있습니다."
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)
    return [_handle_one(upload, session, user.id) for upload in files]


def _handle_one(upload: UploadFile, session: Session, user_id: int) -> UploadResultOut:
    name = safe_original_name(upload.filename)
    ext = PurePath(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return UploadResultOut(original_name=name, error="xlsx, xls, csv 파일만 올릴 수 있습니다.")

    try:
        stored = store_original(upload.file, ext)
    except UploadTooLarge:
        limit_mb = MAX_UPLOAD_BYTES // 1024 // 1024
        return UploadResultOut(original_name=name, error=f"파일이 너무 큽니다 (최대 {limit_mb}MB).")

    record = SourceFile(
        original_name=name,
        stored_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        uploaded_by=user_id,
    )
    session.add(record)
    session.commit()

    result = UploadResultOut(original_name=name, id=record.id, size_bytes=stored.size_bytes)
    try:
        parsed = parse_file(stored.path, name)
    except ParseError as exc:
        logger.warning("parse failed: id=%s ext=%s reason=%s", record.id, ext, exc)
        result.error = str(exc)
        return result

    report = parsed.pii
    summary = {"counts": report.counts, "items": [asdict(i) for i in report.items]}
    record.pii_summary = json.dumps(summary, ensure_ascii=False)
    record.data_rows = parsed.data_rows
    session.commit()
    if report.found:
        # 원문은 기록하지 않는다 (건수만)
        logger.info("pii patterns found: id=%s counts=%s", record.id, report.counts)
        result.pii = {**summary, "acknowledged": False}

    result.encoding = parsed.encoding
    result.skipped_sheets = parsed.skipped_sheets
    result.sheet_count = len(parsed.sheets)
    result.data_rows = parsed.data_rows
    # body(전체 행)는 크므로 응답 모델에 필요한 필드만 읽는다.
    # 열 이름에 든 개인정보는 화면으로 보내기 전에 가린다 (미리보기 셀은 파서가 이미 마스킹)
    result.sheets = [
        SheetOut.model_validate(s, from_attributes=True).model_copy(
            update={"columns": [mask_text(c) for c in s.columns]}
        )
        for s in parsed.sheets
    ]
    return result


class CandidateOut(BaseModel):
    key: str
    label: str
    score: float
    fuzzy: float
    embedding: float | None


def column_id(index: int) -> str:
    """시트 안에서 열을 가리키는 식별자. 화면에는 마스킹한 이름을 보내므로 확정 요청은 이 값으로 받는다."""
    return f"c{index}"


class ColumnMappingOut(BaseModel):
    column: str  # 화면 표시용 (열 이름의 개인정보는 마스킹)
    column_id: str  # 확정 요청에 쓰는 식별자 (원래 이름은 서버만 안다)
    samples: list[Any]
    status: str
    method: str
    field_key: str | None
    field_label: str | None
    score: float | None
    qualifier: str | None
    reason: str | None
    candidates: list[CandidateOut]


class SheetMappingOut(BaseModel):
    name: str
    columns: list[ColumnMappingOut]


class FileMappingOut(BaseModel):
    file_id: int
    original_name: str
    embedding: str
    llm: str
    sheets: list[SheetMappingOut]


@router.post("/{file_id}/mapping", response_model=FileMappingOut)
def map_file_columns(
    file_id: int, session: Annotated[Session, Depends(get_session)], user: CurrentUserDep, use_llm: bool = True
) -> FileMappingOut:
    """저장된 원본(읽기 전용)을 다시 파싱해 시트별 열 매핑 후보를 만든다.

    사용자가 확정한 열은 확정값을 그대로 쓰고, 학습된 별칭은 1단계 정확 일치에 포함한다.
    """
    get_file_for(session, user, file_id)
    record, parsed = load_parsed(session, file_id)
    engine = get_engine()
    learned = load_learned(session, user.id)
    standard = load_standard(session)
    decisions = load_decisions(session, file_id)
    embedding_status, llm_status = "skipped", "skipped"
    sheets: list[SheetMappingOut] = []
    for sheet in parsed.sheets:
        result = engine.map_columns(
            sheet.columns,
            sheet.samples,
            use_llm=use_llm,
            learned=learned,
            decisions=decisions.get(sheet.name),
            standard=standard,
        )
        # 여러 시트 중 하나라도 실패하면 실패로, 하나라도 성공하면 성공으로 보고한다.
        embedding_status = _merge_status(embedding_status, result.embedding)
        llm_status = _merge_status(llm_status, result.llm)
        sheets.append(
            SheetMappingOut(
                name=sheet.name,
                columns=[
                    {
                        **asdict(c),
                        "column": mask_text(c.column),
                        "column_id": column_id(i),
                        "reason": mask_text(c.reason) if c.reason else c.reason,
                    }
                    for i, c in enumerate(result.columns)
                ],
            )
        )
    return FileMappingOut(
        file_id=record.id,
        original_name=record.original_name,
        embedding=embedding_status,
        llm=llm_status,
        sheets=sheets,
    )


def _merge_status(current: str, new: str) -> str:
    if "unavailable" in (current, new):
        return "unavailable"
    if "ok" in (current, new):
        return "ok"
    return "skipped"


def get_file_for(session: Session, user: CurrentUser, file_id: int, own: bool = False) -> SourceFile:
    """요청한 사용자가 볼 수 있는(own=True면 바꿀 수 있는) 파일. 남의 파일은 없는 것처럼 404."""
    record = session.get(SourceFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "파일을 찾을 수 없습니다.")
    (ensure_own if own else ensure_view)(user, record.uploaded_by, "파일")
    return record


def load_parsed(session: Session, file_id: int) -> tuple[SourceFile, FileResult]:
    record = session.get(SourceFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "파일을 찾을 수 없습니다.")
    path = (DATA_DIR / record.stored_path).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 파일이 없습니다.")
    try:
        return record, parse_file(path, record.original_name)
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


class DecisionIn(BaseModel):
    sheet: str
    column_id: str | None = None  # 매핑 응답의 column_id (권장)
    column: str | None = None  # 예전 방식: 원래 열 이름 (호환용, 개인정보가 없는 이름일 때)
    field_key: str | None  # None = 해당 없음


class DecisionsIn(BaseModel):
    decisions: list[DecisionIn]


class DecisionsOut(BaseModel):
    saved: int
    learned: list[str]
    not_learned: list[dict[str, str]]


@router.put("/{file_id}/mapping/decisions", response_model=DecisionsOut)
def save_mapping_decisions(
    file_id: int, body: DecisionsIn, session: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> DecisionsOut:
    """사용자가 확인·수정한 매핑을 확정 저장하고, 열 이름을 이 사용자의 별칭으로 학습한다.
    승인한 매핑은 전사 표준 사전 승격 집계에도 기록한다."""
    get_file_for(session, user, file_id, own=True)
    _, parsed = load_parsed(session, file_id)
    engine = get_engine()
    columns = {s.name: s.columns for s in parsed.sheets}
    valid_keys = {f.key for f in engine.fields}
    inputs: list[DecisionInput] = []
    for d in body.decisions:
        names = columns.get(d.sheet, [])
        ids = {column_id(i): name for i, name in enumerate(names)}
        raw = ids.get(d.column_id) if d.column_id is not None else (d.column if d.column in names else None)
        if raw is None:
            shown = d.column_id if d.column_id is not None else mask_text(d.column or "")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"없는 열입니다: {d.sheet} / {shown}")
        if d.field_key is not None and d.field_key not in valid_keys:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"없는 표준 항목입니다: {d.field_key}")
        inputs.append(DecisionInput(d.sheet, raw, d.field_key))
    result = save_decisions(session, engine, file_id, inputs, user_id=user.id)
    for d in inputs:
        if d.field_key is not None:
            record_approval(session, engine, user.id, d.column, d.field_key)
    session.commit()
    return DecisionsOut(
        saved=result.saved,
        learned=[mask_text(n) for n in result.learned],
        not_learned=[{**n, "column": mask_text(n.get("column", ""))} for n in result.not_learned],
    )


class FieldOut(BaseModel):
    key: str
    label: str
    description: str


@mapping_router.get("/fields", response_model=list[FieldOut])
def list_fields() -> list[FieldOut]:
    return [FieldOut(key=f.key, label=f.label, description=f.description) for f in get_engine().fields]
