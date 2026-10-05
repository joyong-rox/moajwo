import logging
from dataclasses import asdict
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
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
