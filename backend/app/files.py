import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, ensure_own, ensure_view
from app.config import DATA_DIR, MAX_FILE_CELLS, MAX_UPLOAD_MIB, MAX_XLSX_UNCOMPRESSED_MIB, PARSE_SLOTS
from app.db import get_session
from app.decisions import DecisionInput, load_decisions, load_learned, load_org_codes, save_decisions
from app.mapping import get_engine
from app.models import MappingAnalysis, SourceFile
from app.name_limits import (
    FILE_NAME_MAX,
    HAS_NUL,
    NUL,
    TOO_LONG,
    NameNotStorable,
    OptionalStoredText,
    StoredText,
    column_place,
    refusal,
    sheet_column_problem,
    sheet_place,
)
from app.parsing import MAX_CELL_CHARS, SUPPORTED_EXTENSIONS, FileResult, ParseBusy, ParseError, parse_file
from app.pii import mask_text, shown_pii_items
from app.promotion import load_standard, record_approval
from app.storage import UploadTooLarge, safe_original_name, store_original
from app.timefmt import utc_to_kst

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
mapping_router = APIRouter(prefix="/mapping", tags=["mapping"])

MAX_FILES_PER_REQUEST = 20
ORIGINAL_GONE = (
    "원본 파일이 서버에 없습니다 — 서버 재배포·재시작으로 지워졌을 수 있습니다. 같은 원본을 다시 올리면 새 파일로 "
    "이어서 쓸 수 있습니다 (계정·검토·실행 기록은 그대로)."
)


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
    original_name: str  # 가린 표시 이름 (원문은 보내지 않는다 — 식별은 id)
    id: int | None = None
    size_bytes: int | None = None
    encoding: str | None = None
    sheet_count: int = 0
    data_rows: int = 0
    sheets: list[SheetOut] = []
    pii: dict[str, Any] | None = None  # 개인정보 탐지 요약 (마스킹 값과 위치만)
    skipped_sheets: list[str] = []  # 통계표 설명 시트(메타정보·설명·안내)는 읽지 않음
    error: str | None = None


class LimitsOut(BaseModel):
    """파일 읽기 한도 (환경마다 다름 — docs/MEMORY.md). 화면이 안내 문구에 그대로 쓴다. 크기 단위는 MiB."""

    max_upload_mib: int
    max_xlsx_uncompressed_mib: int
    max_file_cells: int
    max_cell_chars: int
    parse_slots: int
    max_files_per_request: int


@router.get("/limits", response_model=LimitsOut)
def file_limits() -> LimitsOut:
    return LimitsOut(
        max_upload_mib=MAX_UPLOAD_MIB,
        max_xlsx_uncompressed_mib=MAX_XLSX_UNCOMPRESSED_MIB,
        max_file_cells=MAX_FILE_CELLS,
        max_cell_chars=MAX_CELL_CHARS,
        parse_slots=PARSE_SLOTS,
        max_files_per_request=MAX_FILES_PER_REQUEST,
    )


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
        return UploadResultOut(original_name=mask_text(name), error="xlsx, xls, csv 파일만 올릴 수 있습니다.")
    if NUL in name:  # 파일 이름은 그대로 저장한다 — NUL은 PostgreSQL이 받지 않으므로 두 DB에서 같게 저장 전에 거절
        error = f"파일 이름에 NUL(0x00) 문자가 있어 올리지 않았습니다 — 이름을 바꿔 다시 올리세요. [{HAS_NUL}]"
        return UploadResultOut(original_name=mask_text(name.replace(NUL, "")), error=error)
    if len(name) > FILE_NAME_MAX:  # 자르지 않는다 — 두 DB에서 같게 저장 전에 거절 (이름 원문은 문구에 넣지 않음)
        error = (
            f"파일 이름이 {len(name):,}자로 저장 한도 {FILE_NAME_MAX}자를 넘어 올리지 않았습니다 — 이름을 줄여 다시 "
            f"올리세요. [{TOO_LONG}]"
        )
        return UploadResultOut(original_name=mask_text(name), error=error)

    try:
        stored = store_original(upload.file, ext)
    except UploadTooLarge:
        return UploadResultOut(original_name=mask_text(name), error=f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_MIB}MiB).")

    record = SourceFile(
        original_name=name,
        stored_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        uploaded_by=user_id,
    )
    session.add(record)
    session.commit()

    result = UploadResultOut(original_name=mask_text(name), id=record.id, size_bytes=stored.size_bytes)
    try:
        parsed = parse_file(stored.path, name)
    except ParseBusy as exc:
        # 읽기 차례를 얻지 못함 — 입력 오류가 아니므로 '읽지 못한 파일'로 남기지 않고 기록·원본을 지운다
        # (다시 올리면 됨. 업로드 때 읽어야 개인정보 탐지 결과가 남으므로 읽지 않은 파일을 두지 않는다)
        session.delete(record)
        session.commit()
        stored.path.chmod(0o600)  # 원본은 읽기 전용으로 저장된다 (Windows는 읽기 전용 파일을 지우지 못함)
        stored.path.unlink(missing_ok=True)
        logger.warning("upload parse busy: ext=%s", ext)
        return UploadResultOut(original_name=mask_text(name), error=str(exc))
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
        result.pii = {"counts": summary["counts"], "items": shown_pii_items(summary["items"]), "acknowledged": False}

    result.encoding = parsed.encoding
    result.skipped_sheets = [mask_text(n) for n in parsed.skipped_sheets]
    result.sheet_count = len(parsed.sheets)
    result.data_rows = parsed.data_rows
    # body(전체 행)는 크므로 응답 모델에 필요한 필드만 읽는다.
    # 시트·열 이름에 든 개인정보는 화면으로 보내기 전에 가린다 (미리보기 셀은 파서가 이미 마스킹)
    result.sheets = [
        SheetOut.model_validate(s, from_attributes=True).model_copy(
            update={"name": mask_text(s.name), "columns": [mask_text(c) for c in s.columns]}
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
    name: str  # 화면 표시용 시트 이름 (개인정보 형식은 가림)
    sheet_id: str = ""  # 파일 안의 시트 순번 식별자 (s0, s1 …) — 작업 화면 API는 이 값으로 시트를 받는다
    columns: list[ColumnMappingOut]


class FileMappingOut(BaseModel):
    file_id: int
    original_name: str
    embedding: str
    llm: str
    sheets: list[SheetMappingOut]
    analyzed_at: str | None = None  # 이 분석을 저장한 시각 (KST)


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
    org_codes = load_org_codes(session)
    embedding_status, llm_status = "skipped", "skipped"
    sheets: list[SheetMappingOut] = []
    for sheet_index, sheet in enumerate(parsed.sheets):
        result = engine.map_columns(
            sheet.columns,
            sheet.samples,
            use_llm=use_llm,
            learned=learned,
            decisions=decisions.get(sheet.name),
            standard=standard,
            org_codes=org_codes,
        )
        # 여러 시트 중 하나라도 실패하면 실패로, 하나라도 성공하면 성공으로 보고한다.
        embedding_status = _merge_status(embedding_status, result.embedding)
        llm_status = _merge_status(llm_status, result.llm)
        sheets.append(
            SheetMappingOut(
                name=mask_text(sheet.name),
                sheet_id=f"s{sheet_index}",
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
    out = FileMappingOut(
        file_id=record.id,
        original_name=mask_text(record.original_name),
        embedding=embedding_status,
        llm=llm_status,
        sheets=sheets,
    )
    out.analyzed_at = _store_analysis(session, record.id, user.id, out)
    return out


def _store_analysis(session: Session, file_id: int, user_id: int, out: FileMappingOut) -> str:
    """분석 결과(이미 마스킹한 응답)를 파일·사용자마다 1건으로 저장한다 (다시 분석하면 바꿔 씀).
    여러 파일 공통 검토·작업 화면 다시 열기는 이 기록을 쓰므로 다시 분석하거나 모델을 부르지 않는다."""
    now = datetime.now(UTC)
    row = session.scalar(
        select(MappingAnalysis).where(MappingAnalysis.source_file_id == file_id, MappingAnalysis.created_by == user_id)
    )
    payload = out.model_dump_json(exclude={"analyzed_at"})
    if row is None:
        try:
            session.add(MappingAnalysis(source_file_id=file_id, created_by=user_id, created_at=now, result=payload))
            session.commit()
            return utc_to_kst(now).isoformat()
        except IntegrityError:
            # 같은 파일·사용자의 최초 분석이 동시에 저장됨: 먼저 저장한 행을 이번 결과로 바꿔 쓴다 (1건 유지)
            session.rollback()
            row = session.scalar(
                select(MappingAnalysis).where(
                    MappingAnalysis.source_file_id == file_id, MappingAnalysis.created_by == user_id
                )
            )
            if row is None:
                raise
    row.result, row.created_at = payload, now
    session.commit()
    return utc_to_kst(now).isoformat()


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
    if not path.is_relative_to(DATA_DIR.resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "원본 파일이 없습니다.")
    if not path.is_file():
        # 기록은 있는데 파일이 없다 — 외부 DB를 쓰는 서버(Render 등)는 재배포·재시작 때 파일이 지워질 수 있다.
        # 다른 파일로 대신 잇지 않고, 기록·계정은 지우지 않는다 (docs/EXTERNAL_DB.md '파일 보존')
        raise HTTPException(status.HTTP_410_GONE, ORIGINAL_GONE)
    try:
        return record, parse_file(path, record.original_name)
    except ParseBusy as exc:  # 입력 오류가 아니다 — 잠시 뒤 같은 요청을 다시 보내면 된다
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc), headers={"Retry-After": "10"}) from exc
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


class DecisionIn(BaseModel):
    # 요청 글자는 저장하지 않지만 '없는 열·항목' 안내에 되돌려 보내므로 NUL은 요청에서 막는다 (loc으로 알림)
    sheet: StoredText = ""  # 원래 시트 이름 (호환용). 화면·응답의 시트 이름은 개인정보를 가리므로 sheet_id를 권장
    sheet_id: OptionalStoredText = None  # 매핑 응답의 sheet_id (s0, s1 …)
    column_id: OptionalStoredText = None  # 매핑 응답의 column_id (권장)
    column: OptionalStoredText = None  # 예전 방식: 원래 열 이름 (호환용, 개인정보가 없는 이름일 때)
    field_key: OptionalStoredText  # None = 해당 없음


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
    by_id = {f"s{i}": s.name for i, s in enumerate(parsed.sheets)}
    valid_keys = {f.key for f in engine.fields}
    inputs: list[DecisionInput] = []
    for d in body.decisions:
        sheet = by_id.get(d.sheet_id, "") if d.sheet_id is not None else d.sheet
        names = columns.get(sheet, [])
        ids = {column_id(i): name for i, name in enumerate(names)}
        raw = ids.get(d.column_id) if d.column_id is not None else (d.column if d.column in names else None)
        if raw is None:
            shown = d.column_id if d.column_id is not None else mask_text(d.column or "")
            where = d.sheet_id if d.sheet_id is not None else mask_text(d.sheet)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"없는 열입니다: {where} / {shown}")
        if d.field_key is not None and d.field_key not in valid_keys:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"없는 표준 항목입니다: {d.field_key}")
        # 열 번호로 찾은 실제 이름까지 — 한도 초과·NUL은 쓰기 전에 두 DB에서 같게 거절 (app/name_limits.py)
        problem = sheet_column_problem(sheet, raw)
        if problem is not None:
            found = next(s for s in parsed.sheets if s.name == sheet)
            place = f"{sheet_place(found)} · {column_place(found, names.index(raw))}"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, refusal("확정하지 않았습니다", [(place, problem)])
            )
        inputs.append(DecisionInput(sheet, raw, d.field_key))
    try:
        result = save_decisions(session, engine, file_id, inputs, user_id=user.id)
    except NameNotStorable as exc:  # 위에서 먼저 거절하므로 닿지 않는다 — 닿아도 500이 아니라 같은 422로
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
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
    aliases: list[str] = []  # 표준 사전에 적힌 다른 이름 (사전에 있는 것만)
    modifier: bool = False  # 다른 항목의 구분으로 붙는 항목인지 (예: 관용·자가용)


@mapping_router.get("/fields", response_model=list[FieldOut])
def list_fields() -> list[FieldOut]:
    return [
        FieldOut(key=f.key, label=f.label, description=f.description, aliases=list(f.aliases), modifier=f.modifier)
        for f in get_engine().fields
    ]
