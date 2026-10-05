"""용어사전 API: 항목·별칭 관리, 일괄 업로드, 이름 해석."""

import tempfile
from datetime import date
from pathlib import Path, PurePath
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import admin_codes
from app.db import get_session
from app.glossary import AliasEntry, EntityRef, Entry, Glossary, normalize_name, period_end
from app.glossary_import import export_csv, import_file, template_csv
from app.models import GlossaryAlias, GlossaryEntry
from app.parsing import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/glossary", tags=["glossary"])

PERIOD_PATTERN = r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$"
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _entry(e: GlossaryEntry) -> Entry:
    previous = tuple(c for c in (e.previous_codes or "").split(",") if c)
    return Entry(
        e.kind,
        e.code,
        e.name,
        e.type_label or "",
        e.parent_code,
        e.valid_from,
        e.valid_to,
        previous,  # type: ignore[arg-type]
    )


def get_glossary(session: Session) -> Glossary:
    aliases: dict[str, list[AliasEntry]] = {}
    for a in session.scalars(select(GlossaryAlias)):
        aliases.setdefault(a.normalized, []).append(AliasEntry(a.kind, a.target_code, a.valid_from, a.valid_to))  # type: ignore[arg-type]
    return Glossary([_entry(e) for e in session.scalars(select(GlossaryEntry))], aliases)


# ---------------------------------------------------------------- 현황


class InfoOut(BaseModel):
    entries: int
    org_entries: int
    region_entries: int
    example_entries: int
    aliases: int
    region_code_table_date: str


@router.get("/info", response_model=InfoOut)
def info(session: Annotated[Session, Depends(get_session)]) -> InfoOut:
    by_kind = dict(session.execute(select(GlossaryEntry.kind, func.count()).group_by(GlossaryEntry.kind)).all())
    examples = session.scalar(select(func.count()).where(GlossaryEntry.source == "example")) or 0
    return InfoOut(
        entries=sum(by_kind.values()),
        org_entries=by_kind.get("org", 0),
        region_entries=by_kind.get("region", 0),
        example_entries=examples,
        aliases=session.scalar(select(func.count(GlossaryAlias.id))) or 0,
        region_code_table_date=admin_codes.load_table().effective_date,
    )


# ---------------------------------------------------------------- 항목


class EntryOut(BaseModel):
    id: int
    kind: str
    code: str
    name: str
    type: str | None
    parent_code: str | None
    valid_from: date | None
    valid_to: date | None
    previous_codes: str | None
    note: str | None
    source: str
    active: bool
    aliases: list[str]


def _entry_out(e: GlossaryEntry, aliases: list[str]) -> EntryOut:
    return EntryOut(
        id=e.id,
        kind=e.kind,
        code=e.code,
        name=e.name,
        type=e.type_label,
        parent_code=e.parent_code,
        valid_from=e.valid_from,
        valid_to=e.valid_to,
        previous_codes=e.previous_codes,
        note=e.note,
        source=e.source,
        active=e.valid_to is None or e.valid_to >= date.today(),
        aliases=aliases,
    )


def _aliases_by_target(session: Session) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for a in session.scalars(select(GlossaryAlias).order_by(GlossaryAlias.id)):
        out.setdefault((a.kind, a.target_code), []).append(a.alias)
    return out


@router.get("/entries", response_model=list[EntryOut])
def list_entries(
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    kind: Literal["all", "org", "region"] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EntryOut]:
    stmt = select(GlossaryEntry)
    if kind != "all":
        stmt = stmt.where(GlossaryEntry.kind == kind)
    if q and (norm := normalize_name(q)):
        like = "%" + norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        alias_codes = select(GlossaryAlias.target_code).where(GlossaryAlias.normalized.like(like, escape="\\"))
        stmt = stmt.where(
            or_(
                GlossaryEntry.normalized.like(like, escape="\\"),
                GlossaryEntry.code == q.strip(),
                GlossaryEntry.code.in_(alias_codes),
            )
        )
    stmt = stmt.order_by(
        GlossaryEntry.kind, GlossaryEntry.valid_to.is_not(None), func.length(GlossaryEntry.name)
    ).limit(limit)
    aliases = _aliases_by_target(session)
    return [_entry_out(e, aliases.get((e.kind, e.code), [])) for e in session.scalars(stmt)]


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: int, session: Annotated[Session, Depends(get_session)]) -> None:
    entry = session.get(GlossaryEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "항목을 찾을 수 없습니다.")
    session.execute(
        delete(GlossaryAlias).where(GlossaryAlias.kind == entry.kind, GlossaryAlias.target_code == entry.code)
    )
    session.delete(entry)
    session.commit()


class ImportOut(BaseModel):
    rows: int
    inserted: int
    updated: int
    aliases_added: int
    applied: bool
    errors: list[dict[str, object]]
    warnings: list[dict[str, object]]


@router.post("/import", response_model=ImportOut)
def import_glossary(
    file: Annotated[UploadFile, File()], session: Annotated[Session, Depends(get_session)]
) -> ImportOut:
    """CSV·xlsx·xls 양식으로 항목과 별칭을 일괄 등록·갱신한다. 오류가 있으면 아무것도 반영하지 않는다."""
    name = PurePath((file.filename or "").replace("\\", "/")).name or "glossary.csv"
    ext = PurePath(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "xlsx, xls, csv 파일만 올릴 수 있습니다.")
    with tempfile.TemporaryDirectory(prefix="moajwo-glossary-") as tmp:
        path = Path(tmp) / f"upload{ext}"
        size = 0
        with path.open("wb") as dst:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "용어사전 파일은 10MB까지 올릴 수 있습니다.")
                dst.write(chunk)
        report = import_file(session, path, name)
    return ImportOut(**report.__dict__)


@router.get("/template.csv")
def download_template() -> Response:
    return _csv_response(template_csv(), "용어사전_양식.csv")


@router.get("/export.csv")
def export_glossary(session: Annotated[Session, Depends(get_session)]) -> Response:
    entries = list(session.scalars(select(GlossaryEntry).order_by(GlossaryEntry.kind, GlossaryEntry.code)))
    return _csv_response(export_csv(entries, _aliases_by_target(session)), "용어사전.csv")


def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# ---------------------------------------------------------------- 해석


class RefOut(BaseModel):
    kind: str
    code: str
    name: str
    type: str
    valid_to: str | None


def ref_out(ref: EntityRef) -> RefOut:
    return RefOut(kind=ref.kind, code=ref.code, name=ref.name, type=ref.type_label, valid_to=ref.valid_to)


class ResolveOut(BaseModel):
    name: str
    status: str
    via: str
    ref: RefOut | None
    candidates: list[RefOut]
    note: str | None


@router.get("/resolve", response_model=ResolveOut)
def resolve(
    name: Annotated[str, Query(min_length=1, max_length=200)],
    session: Annotated[Session, Depends(get_session)],
    prefer: Literal["org", "region"] = "org",
    period: Annotated[str | None, Query(pattern=PERIOD_PATTERN)] = None,
) -> ResolveOut:
    r = get_glossary(session).resolve(name, period_end(period), prefer)
    return ResolveOut(
        name=r.name,
        status=r.status,
        via=r.via,
        ref=ref_out(r.ref) if r.ref else None,
        candidates=[ref_out(c) for c in r.candidates],
        note=r.note,
    )


# ---------------------------------------------------------------- 별칭


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=255)
    kind: Literal["org", "region"]
    target_code: str = Field(min_length=1, max_length=32)
    valid_from: date | None = None
    valid_to: date | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> "AliasIn":
        if not normalize_name(self.alias):
            raise ValueError("별칭이 비어 있습니다.")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("유효기간 시작일이 종료일보다 늦습니다.")
        return self


class AliasOut(BaseModel):
    id: int
    alias: str
    kind: str
    target_code: str
    target_name: str | None
    valid_from: date | None
    valid_to: date | None
    note: str | None
    source: str


def _target(session: Session, kind: str, code: str) -> GlossaryEntry | None:
    return session.scalar(select(GlossaryEntry).where(GlossaryEntry.kind == kind, GlossaryEntry.code == code))


def alias_out(session: Session, a: GlossaryAlias) -> AliasOut:
    target = _target(session, a.kind, a.target_code)
    return AliasOut(
        id=a.id,
        alias=a.alias,
        kind=a.kind,
        target_code=a.target_code,
        target_name=target.name if target else None,
        valid_from=a.valid_from,
        valid_to=a.valid_to,
        note=a.note,
        source=a.source,
    )


@router.get("/aliases", response_model=list[AliasOut])
def list_aliases(session: Annotated[Session, Depends(get_session)]) -> list[AliasOut]:
    return [alias_out(session, a) for a in session.scalars(select(GlossaryAlias).order_by(GlossaryAlias.id.desc()))]


def _overlaps(a_from: date | None, a_to: date | None, b_from: date | None, b_to: date | None) -> bool:
    return (a_from is None or b_to is None or a_from <= b_to) and (b_from is None or a_to is None or b_from <= a_to)


@router.post("/aliases", response_model=AliasOut, status_code=status.HTTP_201_CREATED)
def create_alias(body: AliasIn, session: Annotated[Session, Depends(get_session)]) -> AliasOut:
    if _target(session, body.kind, body.target_code) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"용어사전에 없는 항목입니다: {body.target_code}")
    normalized = normalize_name(body.alias)
    # 유니크 제약은 유효기간이 비어 있으면(NULL) 중복을 막지 못하므로, 기간이 겹치는 같은 별칭을 직접 확인한다.
    for other in session.scalars(
        select(GlossaryAlias).where(
            GlossaryAlias.normalized == normalized,
            GlossaryAlias.kind == body.kind,
            GlossaryAlias.target_code == body.target_code,
        )
    ):
        if _overlaps(other.valid_from, other.valid_to, body.valid_from, body.valid_to):
            raise HTTPException(status.HTTP_409_CONFLICT, "이미 같은 별칭이 등록되어 있습니다.")
    alias = GlossaryAlias(
        alias=body.alias.strip(),
        normalized=normalized,
        kind=body.kind,
        target_code=body.target_code,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        note=body.note,
        source="user",
    )
    session.add(alias)
    session.commit()
    return alias_out(session, alias)


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(alias_id: int, session: Annotated[Session, Depends(get_session)]) -> None:
    alias = session.get(GlossaryAlias, alias_id)
    if alias is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "별칭을 찾을 수 없습니다.")
    session.delete(alias)
    session.commit()
