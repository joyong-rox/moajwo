import logging
from dataclasses import asdict
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.db import get_session
from app.mapping import get_engine
from app.models import SourceFile
from app.parsing import SUPPORTED_EXTENSIONS, ParseError, parse_file
from app.storage import MAX_UPLOAD_BYTES, UploadTooLarge, safe_original_name, store_original

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

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
    error: str | None = None


@router.post("", response_model=list[UploadResultOut])
def upload_files(
    files: Annotated[list[UploadFile], File()],
    session: Annotated[Session, Depends(get_session)],
) -> list[UploadResultOut]:
    if len(files) > MAX_FILES_PER_REQUEST:
        detail = f"한 번에 최대 {MAX_FILES_PER_REQUEST}개까지 올릴 수 있습니다."
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)
    return [_handle_one(upload, session) for upload in files]


def _handle_one(upload: UploadFile, session: Session) -> UploadResultOut:
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
        original_name=name, stored_path=stored.relative_path, sha256=stored.sha256, size_bytes=stored.size_bytes
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

    result.encoding = parsed.encoding
    result.sheet_count = len(parsed.sheets)
    result.data_rows = parsed.data_rows
    result.sheets = [SheetOut(**asdict(s)) for s in parsed.sheets]
    return result


class CandidateOut(BaseModel):
    key: str
    label: str
    score: float
    fuzzy: float
    embedding: float | None


class ColumnMappingOut(BaseModel):
    column: str
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
    file_id: int, session: Annotated[Session, Depends(get_session)], use_llm: bool = True
) -> FileMappingOut:
    """저장된 원본(읽기 전용)을 다시 파싱해 시트별 열 매핑 후보를 만든다."""
    record = session.get(SourceFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "파일을 찾을 수 없습니다.")
    path = (DATA_DIR / record.stored_path).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 파일이 없습니다.")
    try:
        parsed = parse_file(path, record.original_name)
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    engine = get_engine()
    embedding_status, llm_status = "skipped", "skipped"
    sheets: list[SheetMappingOut] = []
    for sheet in parsed.sheets:
        result = engine.map_columns(sheet.columns, sheet.samples, use_llm=use_llm)
        # 여러 시트 중 하나라도 실패하면 실패로, 하나라도 성공하면 성공으로 보고한다.
        embedding_status = _merge_status(embedding_status, result.embedding)
        llm_status = _merge_status(llm_status, result.llm)
        sheets.append(SheetMappingOut(name=sheet.name, columns=[asdict(c) for c in result.columns]))
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
