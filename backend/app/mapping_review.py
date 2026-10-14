"""열 매핑 작업 화면 API: 저장된 분석 조회, 엑셀형 미리보기, 여러 파일 공통 검토, 선택한 열 일괄 확정.

- 원본 파일은 읽기만 한다. 미리보기는 파서가 정리한 값(머리글 결합·병합 칸 채움·병합 복제 열 제거 후)이며, 개인정보는
  응답을 만들 때 가린다 (열 이름·머리글·셀·샘플·근거). 열은 column_id로만 가리킨다 (마스킹한 이름으로 찾지 않는다).
- 공통 검토는 사용자가 이미 실행해 저장된 분석 결과(mapping_analyses)와 현재 확정값만 모은다 — 화면을 열었다고
  다시 분석하거나 모델(임베딩·LLM)을 부르지 않는다.
- 일괄 확정은 서버가 파일 소유권·현재 확정값·표준 항목을 다시 확인하고, 모든 대상을 한 트랜잭션으로 저장한다.
  확인한 때와 확정값이 달라졌거나 하나라도 적용할 수 없으면 아무것도 저장하지 않는다.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep
from app.db import get_session
from app.decisions import DecisionInput, load_decisions, save_decisions
from app.files import FileMappingOut, column_id, get_file_for, load_parsed
from app.mapping import TOTAL_WORDS, get_engine, split_column_name
from app.models import MappingAnalysis, SourceFile
from app.pii import column_letter, mask_cell, mask_rrn_number, mask_text
from app.promotion import record_approval
from app.timefmt import utc_to_kst
from app.validation import _COLUMN_STOCK_WORDS, _FLOW_WORDS, _UNIT_DECL

router = APIRouter(tags=["mapping-workspace"])

PREVIEW_LIMIT_MAX = 200
COMMON_FILES_MAX = 20
BATCH_ITEMS_MAX = 500
PREVIEW_BASIS = (
    "파싱 후 정리된 값입니다 (머리글 여러 줄을 합친 열 이름, 병합 칸은 채운 값, 머리글 병합으로 생긴 복제 열은 제외). "
    "원본 파일의 셀 서식·수식·숨김 행은 보이지 않으며, 개인정보 형식은 가려서 보여 줍니다."
)


# ---------------------------------------------------------------- 저장된 분석


# ---------------------------------------------------------------- 미리보기용 보기 (마스킹한 파싱 결과, 작은 캐시)
#
# 미리보기 쪽 넘김·저장된 분석 조회·공통 검토·일괄 확정 확인은 열 이름과 정리된 행만 필요하다. 큰 파일(10만 행 CSV)을
# 요청마다 다시 파싱하면 쪽마다 10초 가까이 걸리므로, 파일 id·내용 해시(sha256)를 키로 최근 몇 개 파일만 메모리에 둔다.
# 원본 파일은 바뀌지 않으므로(읽기 전용, 해시 고정) 오래된 값이 나오지 않는다. 다른 기능의 파싱은 그대로다.

VIEW_CACHE_FILES = 2
VIEW_CACHE_MAX_CELLS = 3_000_000  # 이보다 큰 파일은 보관하지 않는다 (메모리 보호)


@dataclass
class SheetView:
    name: str
    columns: list[str]  # 원래 열 이름 — 서버 안에서 확정값과 연결할 때만 쓴다 (응답에는 마스킹해서)
    source_columns: list[int]
    header_row: int | None
    header_row_end: int | None
    header_cells: list[list[Any]]  # 마스킹
    rows: list[tuple[int, str, list[Any]]]  # (원본 행 번호, 행 종류, 마스킹한 셀)
    data_rows: int
    title_texts: list[str]
    note_texts: list[str]


@dataclass
class FileView:
    sheets: list[SheetView]


_VIEWS: OrderedDict[tuple[int, str], FileView] = OrderedDict()
_VIEWS_LOCK = threading.Lock()
_BUILDING: dict[tuple[int, str], threading.Lock] = {}  # 같은 파일을 동시에 두 번 파싱하지 않게 (먼저 온 요청을 기다림)


def file_view(db: Session, record: SourceFile) -> FileView:
    key = (record.id, record.sha256)
    with _VIEWS_LOCK:
        if key in _VIEWS:
            _VIEWS.move_to_end(key)
            return _VIEWS[key]
        building = _BUILDING.setdefault(key, threading.Lock())
    with building:
        with _VIEWS_LOCK:
            if key in _VIEWS:  # 기다리는 동안 먼저 온 요청이 만들어 둠
                _VIEWS.move_to_end(key)
                return _VIEWS[key]
        try:
            return _build_view(db, record, key)
        finally:
            with _VIEWS_LOCK:
                _BUILDING.pop(key, None)


def _build_view(db: Session, record: SourceFile, key: tuple[int, str]) -> FileView:
    _, parsed = load_parsed(db, record.id)
    sheets: list[SheetView] = []
    cells = 0
    for s in parsed.sheets:
        # 주민등록번호가 탐지된 시트는 13자리 숫자 형태도 가린다 (금액 전체를 가리지 않도록 그 시트에만)
        mask = mask_rrn_number if s.pii.counts.get("rrn") else mask_cell
        rows = [(r["row"], r["kind"], [mask(c) for c in r["cells"]]) for r in s.body]
        cells += len(rows) * max(1, len(s.columns))
        sheets.append(
            SheetView(
                name=s.name,
                columns=list(s.columns),
                source_columns=list(s.source_columns or range(len(s.columns))),
                header_row=s.header_row,
                header_row_end=s.header_row_end,
                header_cells=[[mask(c) for c in row] for row in s.header_cells],
                rows=rows,
                data_rows=s.data_rows,
                title_texts=list(s.title_texts),
                note_texts=list(s.note_texts),
            )
        )
    view = FileView(sheets=sheets)
    if cells <= VIEW_CACHE_MAX_CELLS:
        with _VIEWS_LOCK:
            _VIEWS[key] = view
            while len(_VIEWS) > VIEW_CACHE_FILES:
                _VIEWS.popitem(last=False)
    return view


def _labels() -> dict[str, str]:
    return {f.key: f.label for f in get_engine().fields}


def _merged_analysis(db: Session, user: CurrentUser, record: SourceFile, parsed: FileView) -> dict[str, Any] | None:
    """저장된 분석에 지금의 확정값을 덧입힌다. 파서 결과와 열 수·이름이 다르면 stale로 표시한다."""
    row = db.scalar(
        select(MappingAnalysis).where(
            MappingAnalysis.source_file_id == record.id, MappingAnalysis.created_by == user.id
        )
    )
    if row is None:
        return None
    data = json.loads(row.result)
    decisions = load_decisions(db, record.id)
    labels = _labels()
    by_name = {s.name: s for s in parsed.sheets}
    stale = False
    for sheet in data["sheets"]:
        current = by_name.get(sheet["name"])
        if current is None or len(current.columns) != len(sheet["columns"]):
            stale = True
            continue
        if [mask_text(c) for c in current.columns] != [c["column"] for c in sheet["columns"]]:
            stale = True
        chosen = decisions.get(sheet["name"], {})
        for i, col in enumerate(sheet["columns"]):
            raw = current.columns[i] if i < len(current.columns) else None
            if raw is not None and raw in chosen:
                key = chosen[raw]
                col.update(
                    status="confirmed",
                    method="user",
                    field_key=key,
                    field_label=labels.get(key, key) if key else None,
                    score=None,
                    reason="사용자가 확정" if key else "사용자가 '해당 없음'으로 확정",
                )
    data["analyzed_at"] = utc_to_kst(row.created_at).isoformat()
    data["stale"] = stale or {s["name"] for s in data["sheets"]} != set(by_name)
    return data


class StoredMappingOut(FileMappingOut):
    analyzed_at: str
    stale: bool  # 분석 뒤 파서 결과(열 수·이름)가 달라졌으면 다시 분석 필요
    can_change: bool  # 이 사용자가 확정을 바꿀 수 있는 파일인지 (남의 파일은 조회 전용)


def _can_change(user: CurrentUser, record: SourceFile) -> bool:
    return record.uploaded_by == user.id or (record.uploaded_by is None and user.is_manager)


@router.get("/files/{file_id}/mapping/latest", response_model=StoredMappingOut)
def latest_mapping(
    file_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> StoredMappingOut:
    """이 사용자가 마지막으로 실행한 분석 + 지금의 확정값. 분석 기록이 없으면 404 (다시 분석하지 않는다)."""
    record = get_file_for(db, user, file_id)
    data = _merged_analysis(db, user, record, file_view(db, record))
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "저장된 분석이 없습니다. '열 매핑 분석'을 먼저 실행하세요.")
    return StoredMappingOut(**data, can_change=_can_change(user, record))


# ---------------------------------------------------------------- 엑셀형 미리보기 (파싱 후 값, 범위 제한)


class PreviewColumn(BaseModel):
    column_id: str
    name: str  # 마스킹한 열 이름
    letter: str  # 원본 시트의 열 위치 (A, B, …)


class PreviewRow(BaseModel):
    row: int  # 원본 시트의 행 번호 (1부터)
    kind: str  # data / empty / subtotal / note
    cells: list[Any]


class PreviewOut(BaseModel):
    file_id: int
    original_name: str
    sheet: str
    sheets: list[str]
    basis: str
    header_row: int | None
    header_row_end: int | None
    header_rows: list[list[Any]]  # 머리글 행 (여러 줄이면 위→아래, 마스킹)
    columns: list[PreviewColumn]
    rows: list[PreviewRow]
    offset: int
    limit: int
    total_rows: int  # 머리글 아래 전체 행 수 (빈 행·소계·주석 포함)
    data_rows: int
    dropped_columns: int  # 머리글 병합으로 생긴 복제 열 (미리보기에서 제외된 열 수)


@router.get("/files/{file_id}/preview", response_model=PreviewOut)
def preview(
    file_id: int,
    db: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
    sheet: Annotated[str | None, Query(max_length=255)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=PREVIEW_LIMIT_MAX)] = 50,
) -> PreviewOut:
    record = get_file_for(db, user, file_id)  # 남의 파일은 404 (최고 관리자는 조회만)
    view = file_view(db, record)
    if not view.sheets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "읽을 수 있는 시트가 없습니다.")
    target = next((s for s in view.sheets if s.name == sheet), None) if sheet else view.sheets[0]
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "시트를 찾을 수 없습니다.")
    sources = target.source_columns
    return PreviewOut(
        file_id=record.id,
        original_name=record.original_name,
        sheet=target.name,
        sheets=[s.name for s in view.sheets],
        basis=PREVIEW_BASIS,
        header_row=target.header_row,
        header_row_end=target.header_row_end,
        header_rows=target.header_cells,
        columns=[
            PreviewColumn(column_id=column_id(i), name=mask_text(name), letter=column_letter(sources[i]))
            for i, name in enumerate(target.columns)
        ],
        rows=[PreviewRow(row=r, kind=k, cells=c) for r, k, c in target.rows[offset : offset + limit]],
        offset=offset,
        limit=limit,
        total_rows=len(target.rows),
        data_rows=target.data_rows,
        dropped_columns=max(0, (max(sources) + 1 if sources else 0) - len(sources)),
    )


# ---------------------------------------------------------------- 여러 파일 공통 검토


class CommonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ids: list[int] = Field(min_length=1, max_length=COMMON_FILES_MAX)


class Member(BaseModel):
    file_id: int
    file_name: str
    sheet: str
    column_id: str
    column: str
    samples: list[Any]
    status: str
    method: str
    field_key: str | None
    field_label: str | None
    qualifier: str | None
    confirmed: bool
    value_kind: str
    unit: str | None
    basis: str | None  # 흐름(월별 실적) / 시점(보유량) 표기
    can_change: bool


class Group(BaseModel):
    group_id: str
    category: str  # common / divergent / only / unresolved
    name: str
    files: int
    field_key: str | None
    field_label: str | None
    reasons: list[str]
    members: list[Member]


class CommonOut(BaseModel):
    files: list[dict[str, Any]]  # file_id, name, analyzed_at, stale, can_change
    missing: list[dict[str, Any]]  # 분석 기록이 없는 파일 (자동으로 분석하지 않음)
    groups: list[Group]


_MASKED = re.compile(r"\*{3,}")
_DATE = re.compile(r"^\d{4}[-./년]\s?\d{1,2}([-./월]\s?\d{1,2}일?)?$|^\d{4}년?$")
GENERIC_NAMES = frozenset({*TOTAL_WORDS, "구분", "비고", "기타", "항목", "내용", "값", "수", "건수"})


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).lower()


def value_kind(samples: list[Any]) -> str:
    kinds = set()
    for v in samples:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            kinds.add("text")
        elif isinstance(v, int | float):
            kinds.add("number")
        elif isinstance(v, str) and _DATE.match(v.strip()):
            kinds.add("date")
        elif isinstance(v, str) and re.fullmatch(r"[0-9A-Za-z-]+", v.strip()) and any(ch.isdigit() for ch in v):
            kinds.add("code")
        else:
            kinds.add("text")
    if not kinds:
        return "empty"
    return kinds.pop() if len(kinds) == 1 else "mixed"


VALUE_KIND_LABEL = {
    "number": "숫자",
    "code": "코드·번호",
    "date": "날짜·기간",
    "text": "글자",
    "mixed": "섞임",
    "empty": "값 없음",
}


def declared_unit(sheet: SheetView) -> str | None:
    """제목·주석에 '단위: …'로 적힌 단위 (적힌 글자 그대로, 하나일 때만). 적혀 있지 않으면 None (추측하지 않음)."""
    for text in [*sheet.title_texts, *sheet.note_texts]:
        m = _UNIT_DECL.search(text)
        if m:
            units = [re.sub(r"\s+", "", u) for u in re.split(r"[,，/]", m.group(1)) if u.strip()]
            if len(units) == 1:
                return units[0]
    return None


def _basis(column: str, sheet: SheetView) -> str | None:
    text = " ".join([column, *sheet.title_texts])
    flow = any(w in text for w in _FLOW_WORDS)
    stock = any(w in text for w in _COLUMN_STOCK_WORDS) or any(
        w in t for t in sheet.title_texts for w in ("현황", "말일")
    )
    if flow and not stock:
        return "흐름"
    if stock and not flow:
        return "시점"
    return None


@router.post("/mapping/common", response_model=CommonOut)
def common_review(body: CommonIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> CommonOut:
    """선택한 파일들의 저장된 분석을 모아 같은 의미일 수 있는 열을 묶는다 (다시 분석·모델 호출 없음).

    같은 이름이라는 이유만으로 공통으로 보지 않는다: 표준 항목·구분어·값 형태·단위·흐름/시점 표기가 모두 같아야
    '공통 매핑 후보'이고, 하나라도 다르거나 이름이 '합계'·'구분'처럼 대상이 드러나지 않으면 '판단이 다른 항목'으로 둔다.
    """
    ids = list(dict.fromkeys(body.file_ids))
    records = [get_file_for(db, user, fid) for fid in ids]  # 하나라도 볼 수 없으면 404 (부분 응답 없음)
    files_out: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    members: list[Member] = []
    for record in records:
        parsed = file_view(db, record)
        data = _merged_analysis(db, user, record, parsed)
        if data is None:
            missing.append({"file_id": record.id, "name": record.original_name})
            continue
        can_change = _can_change(user, record)
        files_out.append({"file_id": record.id, "name": record.original_name, "analyzed_at": data["analyzed_at"],
                          "stale": data["stale"], "can_change": can_change})  # fmt: skip
        sheets = {s.name: s for s in parsed.sheets}
        for sheet in data["sheets"]:
            parsed_sheet = sheets.get(sheet["name"])
            if parsed_sheet is None or data["stale"]:
                continue  # 다시 분석이 필요한 파일은 묶지 않는다 (files의 stale로 알림)
            unit = declared_unit(parsed_sheet)
            for i, col in enumerate(sheet["columns"]):
                raw = parsed_sheet.columns[i]
                members.append(
                    Member(
                        file_id=record.id,
                        file_name=record.original_name,
                        sheet=sheet["name"],
                        column_id=col["column_id"],
                        column=col["column"],
                        samples=col["samples"],
                        status=col["status"],
                        method=col["method"],
                        field_key=col["field_key"],
                        field_label=col["field_label"],
                        qualifier=col["qualifier"],
                        confirmed=col["status"] == "confirmed",
                        value_kind=value_kind(col["samples"]),
                        unit=unit,
                        basis=_basis(mask_text(raw), parsed_sheet),
                        can_change=can_change,
                    )
                )
    return CommonOut(files=files_out, missing=missing, groups=_group(members))


def _group(members: list[Member]) -> list[Group]:
    buckets: dict[str, list[Member]] = {}
    for m in members:
        # 개인정보 형식이 가려진 이름은 이름으로 묶지 않는다 (가려진 모양이 같아도 다른 열)
        key = f"pii:{m.file_id}:{m.sheet}:{m.column_id}" if _MASKED.search(m.column) else _norm(m.column)
        buckets.setdefault(key, []).append(m)
    order = {"common": 0, "divergent": 1, "unresolved": 2, "only": 3}
    groups: list[Group] = []
    for key, items in buckets.items():
        files = len({m.file_id for m in items})
        fields = {m.field_key for m in items}
        reasons: list[str] = []
        if key.startswith("pii:"):
            reasons.append("열 이름에 개인정보 형식이 있어 다른 열과 묶지 않음")
        if all(m.field_key is None and not m.confirmed for m in items):
            category = "unresolved"
            reasons.append("표준 항목 제안이 없음")
        elif files < 2:
            category = "only"
        else:
            if len(fields) > 1:
                shown = sorted({m.field_label or ("해당 없음" if m.confirmed else "미해결") for m in items})
                reasons.append(f"파일별 매핑이 다름: {' / '.join(shown)}")
            if len({m.qualifier for m in items}) > 1:
                reasons.append("구분어가 다름: " + " / ".join(sorted({m.qualifier or "없음" for m in items})))
            kinds = {m.value_kind for m in items} - {"empty"}
            if len(kinds) > 1:
                reasons.append("값 형태가 다름: " + " / ".join(VALUE_KIND_LABEL[k] for k in sorted(kinds)))
            # 단위·흐름/시점은 숫자 열에만 의미가 있다 (시트 단위를 글자 열에 적용하지 않음)
            numeric = [m for m in items if m.value_kind == "number"]
            units = {m.unit for m in numeric if m.unit}
            if len(units) > 1:
                reasons.append("단위가 다름: " + " / ".join(sorted(units)))
            elif units and any(m.unit is None for m in numeric):
                reasons.append("일부 파일은 단위를 알 수 없음")
            bases = {m.basis for m in numeric if m.basis}
            if len(bases) > 1:
                reasons.append("월별 실적(흐름)과 시점 보유량이 섞여 있을 수 있음")
            primary = split_column_name(items[0].column).primary
            if primary in GENERIC_NAMES or _norm(items[0].column) in GENERIC_NAMES:
                reasons.append("이름만으로는 같은 대상·단위인지 알 수 없음 (값과 파일 맥락 확인 필요)")
            category = "divergent" if reasons else "common"
        shared = next(iter(fields)) if len(fields) == 1 else None
        groups.append(
            Group(
                group_id="",
                category=category,
                name=items[0].column,
                files=files,
                field_key=shared if category == "common" else None,
                field_label=next((m.field_label for m in items if m.field_key == shared), None) if shared else None,
                reasons=reasons,
                members=items,
            )
        )
    groups.sort(key=lambda g: (order[g.category], -g.files, g.name))
    for i, g in enumerate(groups):
        g.group_id = f"g{i + 1}"
    return groups


# ---------------------------------------------------------------- 선택 일괄 확정 (미리보기 → 한 트랜잭션 적용)


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int
    sheet: str = Field(max_length=255)
    column_id: str = Field(max_length=16)
    field_key: str | None  # None = '해당 없음'


class BatchPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchItem] = Field(min_length=1, max_length=BATCH_ITEMS_MAX)


class PlanItem(BaseModel):
    file_id: int
    file_name: str
    sheet: str
    column_id: str
    column: str
    current_confirmed: bool
    current_field_key: str | None
    current_label: str | None
    new_field_key: str | None
    new_label: str | None
    action: str  # apply(새 확정) / overwrite(기존 확정 변경 — 명시적 선택 필요) / unchanged(이미 같음) / excluded
    reason: str | None


class BatchPreviewOut(BaseModel):
    selected: int
    applicable: int  # apply + overwrite(선택 시)
    overwrite: int
    unchanged: int
    excluded: int
    items: list[PlanItem]


def _item_key(i: BatchItem) -> tuple[int, str, str]:
    return (i.file_id, i.sheet, i.column_id)


def _plan(db: Session, user: CurrentUser, items: list[BatchItem]) -> tuple[list[PlanItem], dict[tuple, str]]:
    """대상별 현재 확정값과 적용 가능 여부. 두 번째 값은 (파일, 시트, 열 id) → 원래 열 이름 (적용 가능한 것만)."""
    if len({_item_key(i) for i in items}) != len(items):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "같은 열이 두 번 들어 있습니다.")
    labels = _labels()
    parsed_cache: dict[int, tuple[SourceFile, FileView, dict]] = {}
    plan: list[PlanItem] = []
    raws: dict[tuple, str] = {}
    for item in items:
        if item.file_id not in parsed_cache:
            record = get_file_for(db, user, item.file_id)  # 볼 수 없는 파일 → 404 (요청 전체 거부)
            parsed_cache[item.file_id] = (record, file_view(db, record), load_decisions(db, item.file_id))
        record, parsed, decisions = parsed_cache[item.file_id]
        sheet = next((s for s in parsed.sheets if s.name == item.sheet), None)
        index = int(item.column_id[1:]) if re.fullmatch(r"c\d+", item.column_id) else -1
        raw = sheet.columns[index] if sheet is not None and 0 <= index < len(sheet.columns) else None
        chosen = decisions.get(item.sheet, {})
        confirmed = raw is not None and raw in chosen
        current = chosen.get(raw) if confirmed else None
        entry = PlanItem(
            file_id=record.id,
            file_name=record.original_name,
            sheet=item.sheet,
            column_id=item.column_id,
            column=mask_text(raw) if raw is not None else item.column_id,
            current_confirmed=confirmed,
            current_field_key=current,
            current_label=labels.get(current) if current else ("해당 없음" if confirmed else None),
            new_field_key=item.field_key,
            new_label=labels.get(item.field_key) if item.field_key else "해당 없음",
            action="apply",
            reason=None,
        )
        if raw is None:
            entry.action, entry.reason = "excluded", "파일에 없는 시트·열"
        elif not _can_change(user, record):
            entry.action, entry.reason = "excluded", "다른 사용자의 파일 (조회 전용)"
        elif item.field_key is not None and item.field_key not in labels:
            entry.action, entry.reason = "excluded", "없는 표준 항목"
        elif confirmed and current == item.field_key:
            entry.action, entry.reason = "unchanged", "이미 같은 값으로 확정됨"
        elif confirmed:
            entry.action, entry.reason = (
                "overwrite",
                f"기존 사용자 확정({entry.current_label})을 바꿈 — 명시적으로 선택해야 적용",
            )
        if entry.action in ("apply", "overwrite"):
            raws[_item_key(item)] = raw  # type: ignore[assignment]
        plan.append(entry)
    return plan, raws


@router.post("/mapping/batch/preview", response_model=BatchPreviewOut)
def batch_preview(
    body: BatchPreviewIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> BatchPreviewOut:
    """일괄 확정 전 확인: 대상별 기존 매핑·바꿀 매핑·적용 여부와 제외 사유. 아무것도 저장하지 않는다."""
    plan, _ = _plan(db, user, body.items)
    count = lambda a: sum(1 for p in plan if p.action == a)  # noqa: E731
    return BatchPreviewOut(
        selected=len(plan),
        applicable=count("apply"),
        overwrite=count("overwrite"),
        unchanged=count("unchanged"),
        excluded=count("excluded"),
        items=plan,
    )


class ApplyItem(BatchItem):
    expected_confirmed: bool  # 확인 화면에서 본 현재 상태 (확정 여부)
    expected_field_key: str | None  # 확인 화면에서 본 현재 확정값
    overwrite: bool = False  # 기존 사용자 확정을 바꾸는 것을 명시적으로 선택했는지


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApplyItem] = Field(min_length=1, max_length=BATCH_ITEMS_MAX)


class ApplyOut(BaseModel):
    applied: int
    unchanged: int
    learned: int
    files: int


@router.post("/mapping/batch/apply", response_model=ApplyOut)
def batch_apply(body: ApplyIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ApplyOut:
    """선택한 열을 한 트랜잭션으로 확정한다.

    하나라도 적용할 수 없거나 확인 뒤 확정값이 바뀌었으면 전부 저장하지 않는다.
    """
    file_ids = sorted({i.file_id for i in body.items})
    for fid in file_ids:
        get_file_for(db, user, fid, own=True)  # 남의 파일·없는 파일이 섞이면 403/404 — 아무것도 저장하지 않음
    engine = get_engine()
    try:
        # 쓰기 잠금을 먼저 잡는다 (SQLite: 이 트랜잭션이 끝날 때까지 다른 쓰기는 대기)
        # → 아래 확인과 저장 사이에 다른 요청이 확정값을 바꿀 수 없다
        db.execute(
            update(SourceFile)
            .where(SourceFile.id.in_(file_ids))
            .values(id=SourceFile.id)
            .execution_options(synchronize_session=False)
        )
        plan, raws = _plan(db, user, list(body.items))
        problems: list[str] = []
        for item, entry in zip(body.items, plan, strict=True):
            where = f"{entry.file_name} / {entry.sheet} / {entry.column}"
            if entry.current_confirmed != item.expected_confirmed or entry.current_field_key != item.expected_field_key:
                if not (entry.action == "unchanged"):  # 같은 값으로 이미 확정된 경우(중복 요청)는 충돌로 보지 않음
                    problems.append(
                        f"{where}: 확인한 뒤 확정값이 바뀌었습니다 (지금: {entry.current_label or '미확정'})"
                    )
                    continue
            if entry.action == "excluded":
                problems.append(f"{where}: {entry.reason}")
            elif entry.action == "overwrite" and not item.overwrite:
                problems.append(f"{where}: 기존 확정을 바꾸려면 덮어쓰기를 선택해야 합니다")
        if problems:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "적용하지 않았습니다 (일부만 반영하지 않음).",
                                                           "problems": problems})  # fmt: skip
        applied = learned = 0
        unchanged = sum(1 for e in plan if e.action == "unchanged")
        by_file: dict[int, list[DecisionInput]] = {}
        for item, entry in zip(body.items, plan, strict=True):
            if entry.action in ("apply", "overwrite"):
                by_file.setdefault(item.file_id, []).append(
                    DecisionInput(item.sheet, raws[_item_key(item)], item.field_key)
                )
        for fid, inputs in by_file.items():
            result = save_decisions(db, engine, fid, inputs, user_id=user.id, commit=False)
            applied += result.saved
            learned += len(result.learned)
            for d in inputs:
                if d.field_key is not None:
                    record_approval(db, engine, user.id, d.column, d.field_key)  # 같은 사용자는 몇 번이어도 1명
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    return ApplyOut(applied=applied, unchanged=unchanged, learned=learned, files=len(by_file))
