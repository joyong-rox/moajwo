"""열 매핑 작업 화면 API: 작업 화면 매핑 조회, 엑셀형 미리보기, 여러 파일 공통 검토, 선택한 열 일괄 확정.

- 원본 파일은 읽기만 한다. 미리보기는 파서가 정리한 값(머리글 결합·병합 칸 채움·병합 복제 열 제거 후)이다.
- 사용자 입력에서 온 표시 문자열(파일·시트·열 이름, 셀, 샘플, 근거, 제목·주석에서 읽은 단위)은 응답을 만들 때
  개인정보 형식을 가린다. 시트는 순번 식별자 sheet_id(s0, s1 …), 열은 column_id로만 가리킨다 — 가린 표시 이름으로
  대상을 찾지 않으므로 서버 안의 확정값 연결(원래 이름)은 그대로다.
- 공통 검토는 사용자가 이미 실행해 저장된 분석 결과(mapping_analyses)와 현재 확정값만 모은다 — 화면을 열었다고
  다시 분석하거나 모델(임베딩·LLM)을 부르지 않는다.
- 일괄 확정: 파싱·대상 확인은 쓰기 트랜잭션 밖에서 하고, 쓰기 잠금 안에서는 권한·현재 확정값 재확인과 저장만 한다.
  확인한 때와 확정값이 달라졌거나 하나라도 적용할 수 없으면 아무것도 저장하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep
from app.db import get_session
from app.decisions import DecisionInput, load_decisions, save_decisions
from app.files import column_id, get_file_for, load_parsed
from app.mapping import TOTAL_WORDS, get_engine, split_column_name
from app.models import MappingAnalysis, SourceFile
from app.name_limits import (
    COLUMN_MAX,
    SHEET_MAX,
    NameNotStorable,
    column_place,
    hint,
    name_problem,
    sheet_column_problem,
    sheet_place,
)
from app.pii import column_letter, mask_cell, mask_rrn_number, mask_text
from app.promotion import record_approval
from app.quantity import QUANTITY_FIELDS
from app.timefmt import utc_to_kst
from app.validation import _COLUMN_STOCK_WORDS, _FLOW_WORDS, _UNIT_DECL

router = APIRouter(tags=["mapping-workspace"])

PREVIEW_LIMIT_MAX = 200
COMMON_FILES_MAX = 20
BATCH_ITEMS_MAX = 500
SAMPLES_SHOWN = 5
PREVIEW_BASIS = (
    "파싱 후 정리된 값입니다 (머리글 여러 줄을 합친 열 이름, 병합 칸은 채운 값, 머리글 병합으로 생긴 복제 열은 제외). "
    "원본 파일의 셀 서식·수식·숨김 행은 보이지 않으며, 개인정보 형식은 가려서 보여 줍니다."
)


def sheet_id(index: int) -> str:
    """파일 안에서 시트를 가리키는 식별자 (시트 순번). 화면에는 가린 시트 이름을 보내므로 요청은 이 값으로 받는다."""
    return f"s{index}"


# ---------------------------------------------------------------- 미리보기용 보기 (마스킹한 파싱 결과, 작은 캐시)
#
# 미리보기 쪽 넘김·작업 화면 매핑·공통 검토·일괄 확정 확인은 열 이름과 정리된 행만 필요하다. 큰 파일(10만 행 CSV)을
# 요청마다 다시 파싱하면 쪽마다 10초 가까이 걸리므로, 파일 id·내용 해시(sha256)를 키로 최근 파일을 메모리에 둔다.
# 원본 파일은 바뀌지 않으므로(읽기 전용, 해시 고정) 오래된 값이 나오지 않는다. 보관 값은 가린 값이고 권한은 꺼내기 전에
# 따로 확인한다 (get_file_for). 매핑·확정은 보관하지 않아 바뀌어도 다시 만들 필요가 없다. 다른 기능의 파싱은 그대로다.
# 상한은 '보관한 값의 추정 크기 합계'와 파일 수다 — 작은 파일은 여러 개(홈의 상태 확인은 10개씩), 큰 파일은 적게
# (P-01: 2개만 두던 때는 홈에서 다른 화면에 갔다 오면 8개를 다시 읽었다). 칸 수만 세면 긴 글자가 빠지므로 칸마다
# sys.getsizeof로 센다 (같은 객체를 여러 번 셀 수 있어 실제보다 크게 잡힌다). 이 예산은 보관분만이다 — 파일을 처음
# 읽는 동안의 메모리는 app.parsing의 읽기 한도·동시 읽기 수가 따로 막는다 (docs/MEMORY.md, 환경별 값).
VIEW_CACHE_FILES = int(os.environ.get("MOAJWO_VIEW_CACHE_FILES") or 20)  # 보관하는 파일 수 상한
# 보관 추정 크기 합계 상한 (MiB, 2^20 바이트). 이보다 큰 파일 하나는 보관하지 않는다. 0이면 보관하지 않는다
VIEW_CACHE_MAX_BYTES = int(os.environ.get("MOAJWO_VIEW_CACHE_MIB") or 128) * 2**20


@dataclass
class SheetView:
    name: str  # 원래 시트 이름 — 서버 안에서만 쓴다 (응답에는 가려서)
    columns: list[str]  # 원래 열 이름 — 서버 안에서 확정값과 연결할 때만 쓴다 (응답에는 가려서)
    source_columns: list[int]
    header_row: int | None
    header_row_end: int | None
    header_cells: list[list[Any]]  # 가림
    rows: list[tuple[int, str, list[Any]]]  # (원본 행 번호, 행 종류, 가린 셀)
    data_rows: int
    title_texts: list[str]
    note_texts: list[str]
    tab: int = 0  # 원본 통합 문서의 시트 순번 (0부터, 건너뛴 설명 시트 포함 — 'N번째 시트'로 알릴 때)
    samples: list[list[Any]] = field(default_factory=list)  # 열별 값 예시 (파서가 가린 값)
    # 가림으로 글자가 바뀐 칸만: rows의 순번 → {열 번호: 원문 구분 표지}. 가린 표시가 같아도 원문이 다른 값을
    # 서버 안에서 구별하는 데만 쓴다 (분석의 같은 대상·그룹 비교). 원문·원문 해시는 두지 않는다 — raw_mark 참고
    marks: dict[int, dict[int, str]] = field(default_factory=dict)


@dataclass
class FileView:
    sheets: list[SheetView]


# 원문 구분 표지의 키: 프로세스가 시작할 때 만들고 저장·출력하지 않는다. 같은 프로세스 안에서만 같은 원문 → 같은 표지라,
# 표지는 한 요청 안의 비교에만 쓰고 응답·저장값·확인 기준(fingerprint)에는 넣지 않는다.
_MARK_KEY = secrets.token_bytes(32)


def raw_mark(value: Any) -> str:
    """원문을 구별하는 표지 (HMAC — 키가 없으면 원문을 되짚을 수 없음). 공백·전각 차이는 같은 값으로 본다
    (분석의 기존 비교 규칙 compact와 같음)."""
    text = "".join(unicodedata.normalize("NFKC", str(value)).split())
    return hmac.new(_MARK_KEY, text.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


_VIEWS: OrderedDict[tuple[int, str], FileView] = OrderedDict()
_VIEW_BYTES: dict[tuple[int, str], int] = {}  # 보관한 파일의 추정 크기 (합계 상한 계산용)
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
    size = 0
    for s in parsed.sheets:
        # 주민등록번호가 탐지된 시트는 13자리 숫자 형태도 가린다 (금액 전체를 가리지 않도록 그 시트에만)
        mask = mask_rrn_number if s.pii.counts.get("rrn") else mask_cell
        rows = []
        marks: dict[int, dict[int, str]] = {}
        for i, r in enumerate(s.body):
            shown = [mask(c) for c in r["cells"]]
            changed = {j: raw_mark(c) for j, (c, m) in enumerate(zip(r["cells"], shown, strict=True)) if m != c}
            if changed:
                marks[i] = changed
            rows.append((r["row"], r["kind"], shown))
        size += _rows_bytes(rows)
        sheets.append(
            SheetView(
                name=s.name,
                tab=s.tab,
                columns=list(s.columns),
                source_columns=list(s.source_columns or range(len(s.columns))),
                header_row=s.header_row,
                header_row_end=s.header_row_end,
                header_cells=[[mask(c) for c in row] for row in s.header_cells],
                rows=rows,
                data_rows=s.data_rows,
                title_texts=list(s.title_texts),
                note_texts=list(s.note_texts),
                samples=[[mask(c) for c in col[:SAMPLES_SHOWN]] for col in s.samples],
                marks=marks,
            )
        )
    view = FileView(sheets=sheets)
    if size <= VIEW_CACHE_MAX_BYTES and VIEW_CACHE_FILES > 0:
        with _VIEWS_LOCK:
            _VIEWS[key] = view
            _VIEW_BYTES[key] = size
            # 오래된 것부터 뺀다 — 방금 넣은 것은 남는다 (그 하나는 위에서 상한 안임을 확인)
            while len(_VIEWS) > VIEW_CACHE_FILES or sum(_VIEW_BYTES.values()) > VIEW_CACHE_MAX_BYTES:
                old, _ = _VIEWS.popitem(last=False)
                _VIEW_BYTES.pop(old, None)
    return view


def _rows_bytes(rows: list[tuple[int, str, list[Any]]]) -> int:
    """보관할 행의 추정 크기: 행 튜플·칸 목록·칸 값 (글자 길이가 반영된다). 열 이름·예시 칸은 작아 넣지 않는다."""
    total = 0
    for row in rows:
        cells = row[2]
        total += sys.getsizeof(row) + sys.getsizeof(cells) + sum(sys.getsizeof(c) for c in cells)
    return total


def _sheet(view: FileView, sid: str | None) -> tuple[int, SheetView] | None:
    m = re.fullmatch(r"s(\d+)", sid or "")
    if m is None or int(m.group(1)) >= len(view.sheets):
        return None
    i = int(m.group(1))
    return i, view.sheets[i]


def _labels() -> dict[str, str]:
    return {f.key: f.label for f in get_engine().fields}


def _can_change(user: CurrentUser, record: SourceFile) -> bool:
    return record.uploaded_by == user.id or (record.uploaded_by is None and user.is_manager)


# ---------------------------------------------------------------- 작업 화면 매핑 (저장된 분석 + 현재 확정값)


class WorkspaceColumn(BaseModel):
    column: str  # 가린 표시 이름
    column_id: str
    samples: list[Any]
    status: str  # confirmed / auto / review / unresolved / unanalyzed(분석 기록 없음)
    method: str
    field_key: str | None
    field_label: str | None
    score: float | None
    qualifier: str | None
    reason: str | None
    candidates: list[dict[str, Any]]


class WorkspaceSheet(BaseModel):
    sheet_id: str
    name: str  # 가린 표시 이름
    columns: list[WorkspaceColumn]


class WorkspaceMappingOut(BaseModel):
    file_id: int
    original_name: str  # 가린 표시 이름
    analyzed: bool  # 이 사용자의 저장된 분석이 있는지 (없으면 확정값만 보이고 나머지는 '미분석')
    analyzed_at: str | None
    embedding: str | None
    llm: str | None
    stale: bool  # 분석 뒤 파서 결과(시트·열 구성)가 달라졌으면 다시 분석 필요
    can_change: bool  # 이 사용자가 확정을 바꿀 수 있는 파일인지 (남의 파일은 조회 전용)
    sheets: list[WorkspaceSheet]


def _workspace(db: Session, user: CurrentUser, record: SourceFile, view: FileView) -> dict[str, Any]:
    """저장된 분석(있으면)에 지금의 확정값을 덧입힌다. 분석이 없는 열은 'unanalyzed'. 아무것도 저장하지 않는다."""
    row = db.scalar(
        select(MappingAnalysis).where(
            MappingAnalysis.source_file_id == record.id, MappingAnalysis.created_by == user.id
        )
    )
    stored = json.loads(row.result) if row is not None else None
    stored_sheets = stored["sheets"] if stored else []
    decisions = load_decisions(db, record.id)
    labels = _labels()
    stale = stored is not None and len(stored_sheets) != len(view.sheets)
    sheets: list[dict[str, Any]] = []
    for i, current in enumerate(view.sheets):
        masked_names = [mask_text(c) for c in current.columns]
        analysis = stored_sheets[i] if i < len(stored_sheets) else None
        # 분석과 지금의 열 구성(수·가린 이름)이 같을 때만 분석 결과를 쓴다 (시트 순번으로 맞춤)
        usable = analysis is not None and [c["column"] for c in analysis["columns"]] == masked_names
        if stored is not None and not usable:
            stale = True
        chosen = decisions.get(current.name, {})
        columns: list[dict[str, Any]] = []
        for j, raw in enumerate(current.columns):
            col = dict(analysis["columns"][j]) if usable else {
                "column": masked_names[j], "column_id": column_id(j),
                "samples": current.samples[j] if j < len(current.samples) else [],
                "status": "unanalyzed", "method": "none", "field_key": None, "field_label": None, "score": None,
                "qualifier": None, "reason": None, "candidates": [],
            }  # fmt: skip
            if raw in chosen:
                key = chosen[raw]
                col.update(
                    status="confirmed",
                    method="user",
                    field_key=key,
                    field_label=labels.get(key, key) if key else None,
                    score=None,
                    reason="사용자가 확정" if key else "사용자가 '해당 없음'으로 확정",
                )
            col["reason"] = mask_text(col["reason"]) if col.get("reason") else col.get("reason")
            columns.append(col)
        sheets.append({"sheet_id": sheet_id(i), "name": mask_text(current.name), "columns": columns})
    return {
        "file_id": record.id,
        "original_name": mask_text(record.original_name),
        "analyzed": stored is not None,
        "analyzed_at": utc_to_kst(row.created_at).isoformat() if row is not None else None,
        "embedding": stored.get("embedding") if stored else None,
        "llm": stored.get("llm") if stored else None,
        "stale": stale,
        "can_change": _can_change(user, record),
        "sheets": sheets,
    }


@router.get("/files/{file_id}/mapping/latest", response_model=WorkspaceMappingOut)
def latest_mapping(
    file_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> WorkspaceMappingOut:
    """작업 화면용 매핑: 이 사용자가 마지막으로 실행한 분석 + 지금의 확정값.

    분석 기록이 없어도 확정값은 보여 주고 나머지 열은 '미분석'으로 둔다 (다시 분석·저장하지 않는다).
    """
    record = get_file_for(db, user, file_id)
    return WorkspaceMappingOut(**_workspace(db, user, record, file_view(db, record)))


# ---------------------------------------------------------------- 엑셀형 미리보기 (파싱 후 값, 범위 제한)


class PreviewColumn(BaseModel):
    column_id: str
    name: str  # 가린 열 이름
    letter: str  # 원본 시트의 열 위치 (A, B, …)


class PreviewRow(BaseModel):
    row: int  # 원본 시트의 행 번호 (1부터)
    kind: str  # data / empty / subtotal / note
    cells: list[Any]


class SheetRef(BaseModel):
    sheet_id: str
    name: str  # 가린 표시 이름


class PreviewOut(BaseModel):
    file_id: int
    original_name: str  # 가린 표시 이름
    sheet_id: str
    sheet: str  # 가린 표시 이름
    sheets: list[SheetRef]
    basis: str
    header_row: int | None
    header_row_end: int | None
    header_rows: list[list[Any]]  # 머리글 행 (여러 줄이면 위→아래, 가림)
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
    sheet_id_param: Annotated[str | None, Query(alias="sheet_id", max_length=8)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=PREVIEW_LIMIT_MAX)] = 50,
) -> PreviewOut:
    record = get_file_for(db, user, file_id)  # 남의 파일은 404 (최고 관리자는 조회만)
    view = file_view(db, record)
    if not view.sheets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "읽을 수 있는 시트가 없습니다.")
    found = _sheet(view, sheet_id_param) if sheet_id_param else (0, view.sheets[0])
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "시트를 찾을 수 없습니다.")
    index, target = found
    sources = target.source_columns
    return PreviewOut(
        file_id=record.id,
        original_name=mask_text(record.original_name),
        sheet_id=sheet_id(index),
        sheet=mask_text(target.name),
        sheets=[SheetRef(sheet_id=sheet_id(i), name=mask_text(s.name)) for i, s in enumerate(view.sheets)],
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
    file_name: str  # 가린 표시 이름
    sheet_id: str
    sheet: str  # 가린 표시 이름
    column_id: str
    column: str
    samples: list[Any]
    status: str
    method: str
    field_key: str | None
    field_label: str | None
    qualifier: str | None
    reason: str | None  # 제안 근거 (가림)
    confirmed: bool
    value_kind: str
    quantity: bool  # 수량으로 비교하는 열 (숫자 값이거나 수량형 표준 항목) — 단위·흐름/시점을 비교한다
    unit: str | None  # 제목·주석의 '단위: …' (하나일 때만, 가림)
    unit_state: str  # one / none(표기 없음) / ambiguous(여러 개)
    unit_note: str | None  # 여러 개일 때 적힌 단위들 (가림)
    basis: str | None  # flow(흐름 표기) / stock(시점 표기) / conflict(둘 다) / None(표기 없음)
    can_change: bool


class Group(BaseModel):
    group_id: str
    category: str  # common / divergent / only / unresolved
    name: str
    files: int
    field_key: str | None
    field_label: str | None
    reasons: list[str]  # 공통으로 보지 않은 이유
    evidence: list[str]  # 실제로 확인한 근거 (확인하지 못한 것은 그렇다고 적음)
    members: list[Member]


class CommonOut(BaseModel):
    files: list[dict[str, Any]]  # file_id, name(가림), analyzed_at, stale, can_change
    missing: list[dict[str, Any]]  # 분석 기록이 없는 파일 (자동으로 분석하지 않음)
    groups: list[Group]


_MASKED = re.compile(r"\*{3,}")
_DATE = re.compile(r"^\d{4}[-./년]\s?\d{1,2}([-./월]\s?\d{1,2}일?)?$|^\d{4}년?$")
GENERIC_NAMES = frozenset({*TOTAL_WORDS, "구분", "비고", "기타", "항목", "내용", "값", "수", "건수"})
# 수량으로 비교하는 표준 항목은 app.quantity.QUANTITY_FIELDS (확정 수량 열의 계산용 값과 같은 기준)


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


def declared_units(sheet: SheetView) -> tuple[str, str | None, str | None]:
    """제목·주석의 '단위: …' 선언을 모두 읽는다 → (상태, 단위, 여러 개일 때 적힌 단위들).

    선언 하나 안의 '명, %'처럼 여러 단위이거나, 선언끼리 단위가 다르면 'ambiguous' (첫 값을 고르지 않음).
    각 단위는 선언의 첫 낱말만 쓴다 ('대 문의 …' → '대'). 표시 문자열은 가린다. 적혀 있지 않으면 'none' (추측하지 않음).
    """
    found: list[str] = []
    for text in [*sheet.title_texts, *sheet.note_texts]:
        for m in _UNIT_DECL.finditer(text):
            for part in re.split(r"[,，/]", m.group(1)):
                word = part.strip().split()
                if word:
                    found.append(mask_text(word[0]))
    distinct = list(dict.fromkeys(found))
    if not distinct:
        return "none", None, None
    if len(distinct) == 1:
        return "one", distinct[0], None
    return "ambiguous", None, " / ".join(distinct)


def _basis(column: str, sheet: SheetView) -> str | None:
    """흐름(월별 신규·실적 등)·시점(등록대수·보유·누계 등) 표기. 둘 다 있으면 'conflict'.

    제목의 '현황'만으로는 시점으로 정하지 않는다.
    """
    text = " ".join([column, *sheet.title_texts])
    flow = any(w in text for w in _FLOW_WORDS)
    stock = any(w in text for w in _COLUMN_STOCK_WORDS)
    if flow and stock:
        return "conflict"
    return "flow" if flow else ("stock" if stock else None)


BASIS_LABEL = {"flow": "흐름(월별 실적)", "stock": "시점(보유량)", "conflict": "흐름·시점 표기가 함께 있음"}


@router.post("/mapping/common", response_model=CommonOut)
def common_review(body: CommonIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> CommonOut:
    """선택한 파일들의 저장된 분석을 모아 같은 의미일 수 있는 열을 묶는다 (다시 분석·모델 호출 없음).

    같은 이름이라는 이유만으로 공통으로 보지 않는다: 표준 항목·구분어·값 형태가 같고, 수량 열은 단위와
    흐름/시점 표기가 충돌하지 않아야 '공통 매핑 후보'다. 충돌·불명확·일부 누락이나 '합계'·'구분'처럼 대상이 드러나지
    않는 이름은 '판단이 다른 항목'으로 둔다. 확인하지 못한 근거는 evidence에 그대로 적는다.
    """
    ids = list(dict.fromkeys(body.file_ids))
    records = [get_file_for(db, user, fid) for fid in ids]  # 하나라도 볼 수 없으면 404 (부분 응답 없음)
    files_out: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    members: list[Member] = []
    for record in records:
        view = file_view(db, record)
        data = _workspace(db, user, record, view)
        if not data["analyzed"]:
            missing.append({"file_id": record.id, "name": mask_text(record.original_name)})
            continue
        files_out.append({"file_id": record.id, "name": data["original_name"], "analyzed_at": data["analyzed_at"],
                          "stale": data["stale"], "can_change": data["can_change"]})  # fmt: skip
        if data["stale"]:
            continue  # 다시 분석이 필요한 파일은 묶지 않는다 (files의 stale로 알림)
        for i, sheet in enumerate(data["sheets"]):
            current = view.sheets[i]
            unit_state, unit, unit_note = declared_units(current)
            for j, col in enumerate(sheet["columns"]):
                kind = value_kind(col["samples"])
                quantity = kind == "number" or col["field_key"] in QUANTITY_FIELDS
                members.append(
                    Member(
                        file_id=record.id,
                        file_name=data["original_name"],
                        sheet_id=sheet["sheet_id"],
                        sheet=sheet["name"],
                        column_id=col["column_id"],
                        column=col["column"],
                        samples=col["samples"],
                        status=col["status"],
                        method=col["method"],
                        field_key=col["field_key"],
                        field_label=col["field_label"],
                        qualifier=col["qualifier"],
                        reason=col["reason"],
                        confirmed=col["status"] == "confirmed",
                        value_kind=kind,
                        quantity=quantity,
                        unit=unit if quantity else None,
                        unit_state=unit_state if quantity else "none",
                        unit_note=unit_note if quantity else None,
                        basis=_basis(mask_text(current.columns[j]), current) if quantity else None,
                        can_change=data["can_change"],
                    )
                )
    return CommonOut(files=files_out, missing=missing, groups=_group(members))


def _compare_quantity(items: list[Member], reasons: list[str], evidence: list[str]) -> None:
    """수량 열의 단위·흐름/시점 근거. 충돌·불명확·일부 누락은 이유로, 모두 없으면 '확인하지 못함'으로 적는다."""
    quantity = [m for m in items if m.quantity]
    if not quantity:
        return
    ambiguous = [m for m in quantity if m.unit_state == "ambiguous"]
    units = {m.unit for m in quantity if m.unit_state == "one"}
    unknown = [m for m in quantity if m.unit_state == "none"]
    if ambiguous:
        notes = sorted({m.unit_note or "" for m in ambiguous})
        reasons.append("단위 표기가 여러 개라 판단할 수 없음: " + "; ".join(notes))
    elif len(units) > 1:
        reasons.append("단위가 다름: " + " / ".join(sorted(u or "" for u in units)))
    elif units and unknown:
        reasons.append(f"일부 파일만 단위 표기가 있음 ({next(iter(units))} / 표기 없음)")
    elif units:
        evidence.append(f"단위: {next(iter(units))} (모든 파일의 표기 같음)")
    else:
        evidence.append("단위: 어느 파일에도 표기가 없어 확인하지 못함")
    bases = [m.basis for m in quantity]
    if "conflict" in bases:
        reasons.append("한 표에 흐름(신규·실적 등)과 시점(등록대수·보유 등) 표기가 함께 있어 판단할 수 없음")
    elif {"flow", "stock"} <= set(bases):
        reasons.append("월별 실적(흐름)과 시점 보유량이 섞여 있음")
    elif any(b is None for b in bases) and any(b is not None for b in bases):
        reasons.append("일부 파일에만 흐름/시점 표기가 있음")
    elif bases and bases[0] is not None:
        evidence.append(f"흐름/시점: {BASIS_LABEL[bases[0]]} (모든 파일 같음)")
    else:
        evidence.append("흐름/시점: 표기가 없어 확인하지 못함")


def _group(members: list[Member]) -> list[Group]:
    buckets: dict[str, list[Member]] = {}
    for m in members:
        # 개인정보 형식이 가려진 이름은 이름으로 묶지 않는다 (가려진 모양이 같아도 다른 열)
        key = f"pii:{m.file_id}:{m.sheet_id}:{m.column_id}" if _MASKED.search(m.column) else _norm(m.column)
        buckets.setdefault(key, []).append(m)
    order = {"common": 0, "divergent": 1, "unresolved": 2, "only": 3}
    groups: list[Group] = []
    for key, items in buckets.items():
        files = len({m.file_id for m in items})
        fields = {m.field_key for m in items}
        reasons: list[str] = []
        evidence: list[str] = []
        if key.startswith("pii:"):
            reasons.append("열 이름에 개인정보 형식이 있어 다른 열과 묶지 않음")
        if all(m.field_key is None and not m.confirmed for m in items):
            category = "unresolved"
            reasons.append("표준 항목 제안이 없음")
        elif files < 2:
            category = "only"
        else:
            evidence.append("열 이름: 같음 (공백·대소문자 무시)")
            if len(fields) > 1:
                shown = sorted({m.field_label or ("해당 없음" if m.confirmed else "미해결") for m in items})
                reasons.append(f"파일별 매핑이 다름: {' / '.join(shown)}")
            else:
                evidence.append(f"표준 항목: {items[0].field_label or '해당 없음'} (모든 열 같음)")
            if len({m.qualifier for m in items}) > 1:
                reasons.append("구분어가 다름: " + " / ".join(sorted({m.qualifier or "없음" for m in items})))
            else:
                evidence.append(f"구분어: {items[0].qualifier or '없음'} (모든 열 같음)")
            kinds = {m.value_kind for m in items} - {"empty"}
            if len(kinds) > 1:
                reasons.append("값 형태가 다름: " + " / ".join(VALUE_KIND_LABEL[k] for k in sorted(kinds)))
            elif kinds:
                evidence.append(f"값 형태: {VALUE_KIND_LABEL[next(iter(kinds))]} (모든 열 같음)")
            _compare_quantity(items, reasons, evidence)
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
                reasons=[mask_text(r) for r in reasons],
                evidence=[mask_text(e) for e in evidence] if category == "common" else [],
                members=items,
            )
        )
    groups.sort(key=lambda g: (order[g.category], -g.files, g.name))
    for i, g in enumerate(groups):
        g.group_id = f"g{i + 1}"
    return groups


# ---------------------------------------------------------------- 선택 일괄 확정 (확인 → 한 트랜잭션 적용)


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int
    sheet_id: str = Field(max_length=8)
    column_id: str = Field(max_length=16)
    field_key: str | None  # None = '해당 없음'


class BatchPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchItem] = Field(min_length=1, max_length=BATCH_ITEMS_MAX)


class PlanItem(BaseModel):
    file_id: int
    file_name: str  # 가림
    sheet_id: str
    sheet: str  # 가림
    column_id: str
    column: str  # 가림
    current_status: str  # 작업 화면의 상태 (confirmed / auto / review / unresolved / unanalyzed)
    suggestion_reason: str | None  # 제안 근거 (가림)
    current_confirmed: bool
    current_field_key: str | None
    current_label: str | None
    new_field_key: str | None
    new_label: str | None
    action: str  # apply(새 확정) / overwrite(기존 확정 변경 — 명시적 선택 필요) / unchanged(이미 같음) / excluded
    reason: str | None


class BatchPreviewOut(BaseModel):
    selected: int
    applicable: int  # apply 수 (overwrite는 따로 — 선택한 것만 적용)
    overwrite: int
    unchanged: int
    excluded: int
    items: list[PlanItem]


@dataclass
class _Target:
    """요청한 대상 1개를 쓰기 트랜잭션 밖에서 확인한 결과 (파일 내용은 바뀌지 않으므로 잠금 밖에서 만들어도 된다)."""

    item: BatchItem
    record: SourceFile | None
    sheet_name: str | None  # 원래 시트 이름 (서버 안에서만)
    raw: str | None  # 원래 열 이름 (서버 안에서만)
    file_name: str
    sheet_label: str
    column_label: str
    problem: str | None  # 열·시트·표준 항목이 없음 등 (권한은 따로)
    where: str | None = None  # 이름을 저장할 수 없는 대상의 위치 (파일 번호·시트 순번·열 위치 — 이름 없이)


def _resolve(db: Session, user: CurrentUser, items: list[BatchItem], own: bool) -> list[_Target]:
    """대상 해석: 파일 권한(own이면 변경 권한), 시트·열·표준 항목. 파싱이 필요할 수 있어 쓰기 트랜잭션 밖에서 부른다."""
    keys = [(i.file_id, i.sheet_id, i.column_id) for i in items]
    if len(set(keys)) != len(keys):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "같은 열이 두 번 들어 있습니다.")
    labels = _labels()
    cache: dict[int, tuple[SourceFile, FileView]] = {}
    out: list[_Target] = []
    for item in items:
        if item.file_id not in cache:
            record = get_file_for(db, user, item.file_id, own=own)  # 볼 수 없음 404 · own이면 남의 파일 403
            cache[item.file_id] = (record, file_view(db, record))
        record, view = cache[item.file_id]
        found = _sheet(view, item.sheet_id)
        sheet = found[1] if found else None
        index = int(item.column_id[1:]) if re.fullmatch(r"c\d+", item.column_id) else -1
        raw = sheet.columns[index] if sheet is not None and 0 <= index < len(sheet.columns) else None
        problem = where = None
        if raw is None:
            problem = "파일에 없는 시트·열"
        elif (bad := sheet_column_problem(sheet.name, raw)) is not None:  # 저장 규칙 — 쓰기 전에 두 DB에서 같게
            problem = f"{bad.text} — 확정할 수 없습니다. {hint([bad])}"
            where = f"파일 #{record.id} · {sheet_place(sheet)} · {column_place(sheet, index)}"
        elif item.field_key is not None and item.field_key not in labels:
            problem = "없는 표준 항목"
        # 위치 표시: 저장할 수 없는 이름(길이·NUL)은 원문(가린 이름) 대신 순번·원본 열 위치로 (되돌려 보내지 않음)
        sheet_label = item.sheet_id
        if sheet is not None:
            sheet_ok = name_problem("시트 이름", sheet.name, SHEET_MAX) is None
            sheet_label = mask_text(sheet.name) if sheet_ok else sheet_place(sheet)
        column_label = item.column_id
        if raw is not None:
            column_ok = name_problem("열 이름", raw, COLUMN_MAX) is None
            column_label = mask_text(raw) if column_ok else column_place(sheet, index)
        out.append(
            _Target(
                item=item,
                record=record,
                sheet_name=sheet.name if sheet else None,
                raw=raw,
                file_name=mask_text(record.original_name),
                sheet_label=sheet_label,
                column_label=column_label,
                problem=problem,
                where=where,
            )
        )
    return out


def _state(db: Session, targets: list[_Target]) -> dict[int, dict[str, dict[str, str | None]]]:
    """대상 파일들의 지금 확정값 (짧은 DB 읽기 — 쓰기 잠금 안에서 다시 부른다)."""
    return {fid: load_decisions(db, fid) for fid in {t.item.file_id for t in targets}}


def _plan_entry(t: _Target, decisions: dict[str, dict[str, str | None]], labels: dict[str, str],
                user: CurrentUser) -> PlanItem:  # fmt: skip
    chosen = decisions.get(t.sheet_name or "", {})
    confirmed = t.raw is not None and t.raw in chosen
    current = chosen.get(t.raw) if confirmed and t.raw is not None else None
    entry = PlanItem(
        file_id=t.item.file_id,
        file_name=t.file_name,
        sheet_id=t.item.sheet_id,
        sheet=t.sheet_label,
        column_id=t.item.column_id,
        column=t.column_label,
        current_status="confirmed" if confirmed else "unknown",
        suggestion_reason=None,
        current_confirmed=confirmed,
        current_field_key=current,
        current_label=labels.get(current) if current else ("해당 없음" if confirmed else None),
        new_field_key=t.item.field_key,
        new_label=labels.get(t.item.field_key) if t.item.field_key else "해당 없음",
        action="apply",
        reason=None,
    )
    if t.problem:
        entry.action, entry.reason = "excluded", t.problem
    elif t.record is not None and not _can_change(user, t.record):
        entry.action, entry.reason = "excluded", "다른 사용자의 파일 (조회 전용)"
    elif confirmed and current == t.item.field_key:
        entry.action, entry.reason = "unchanged", "이미 같은 값으로 확정됨"
    elif confirmed:
        entry.action, entry.reason = (
            "overwrite",
            f"기존 사용자 확정({entry.current_label})을 바꿈 — 명시적으로 선택해야 적용",
        )
    return entry


@router.post("/mapping/batch/preview", response_model=BatchPreviewOut)
def batch_preview(
    body: BatchPreviewIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> BatchPreviewOut:
    """일괄 확정 전 확인: 대상별 상태·제안 근거·기존 매핑·바꿀 매핑·적용 여부와 제외 사유. 아무것도 저장하지 않는다."""
    targets = _resolve(db, user, list(body.items), own=False)
    decisions = _state(db, targets)
    labels = _labels()
    plan = [_plan_entry(t, decisions[t.item.file_id], labels, user) for t in targets]
    # 상태·제안 근거: 작업 화면과 같은 값 (저장된 분석 + 확정값)
    workspaces: dict[int, dict[str, Any]] = {}
    for t, entry in zip(targets, plan, strict=True):
        if t.record is None:
            continue
        if t.record.id not in workspaces:
            workspaces[t.record.id] = _workspace(db, user, t.record, file_view(db, t.record))
        ws = next((s for s in workspaces[t.record.id]["sheets"] if s["sheet_id"] == t.item.sheet_id), None)
        col = next((c for c in ws["columns"] if c["column_id"] == t.item.column_id), None) if ws else None
        if col is not None:
            entry.current_status = col["status"]
            entry.suggestion_reason = col["reason"]
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


def _where(t: _Target) -> str:
    """문제 목록의 대상 표시: 이름을 저장할 수 없는 대상은 위치만, 그 밖은 화면과 같은 (가린) 파일·시트·열."""
    return t.where or f"{t.file_name} / {t.sheet_label} / {t.column_label}"


def _conflict(problems: list[str]) -> HTTPException:
    detail = {"message": "적용하지 않았습니다 (일부만 반영하지 않음).", "problems": [mask_text(p) for p in problems]}
    return HTTPException(status.HTTP_409_CONFLICT, detail)


@router.post("/mapping/batch/apply", response_model=ApplyOut)
def batch_apply(body: ApplyIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ApplyOut:
    """선택한 열을 한 트랜잭션으로 확정한다.

    1) 쓰기 트랜잭션 밖: 변경 권한(남의 파일 403)·시트·열·표준 항목 확인 (필요하면 파싱). 문제가 있으면 바로 거부.
    2) 쓰기 잠금 안: 권한과 지금 확정값을 다시 읽어 확인 화면에서 본 값과 비교 → 저장 → 커밋. 확인 뒤 다른 요청이
       확정값을 바꿨거나 덮어쓰기를 고르지 않았으면 아무것도 저장하지 않는다.
    """
    items: list[ApplyItem] = list(body.items)
    targets = _resolve(db, user, list(items), own=True)  # 파싱은 여기 (잠금 밖)
    problems = [f"{_where(t)}: {t.problem}" for t in targets if t.problem]
    if problems:
        raise _conflict(problems)
    db.rollback()  # 읽기만 한 트랜잭션을 닫고, 아래에서 쓰기 잠금부터 잡는다
    engine = get_engine()
    labels = _labels()
    file_ids = sorted({t.item.file_id for t in targets})
    try:
        # 쓰기 잠금 (SQLite: 이 트랜잭션이 끝날 때까지 다른 쓰기는 대기) — 이 안에서는 짧은 확인과 저장만 한다
        db.execute(
            update(SourceFile)
            .where(SourceFile.id.in_(file_ids))
            .values(id=SourceFile.id)
            .execution_options(synchronize_session=False)
        )
        for fid in file_ids:
            get_file_for(db, user, fid, own=True)  # 권한 다시 확인
        decisions = _state(db, targets)
        plan = [_plan_entry(t, decisions[t.item.file_id], labels, user) for t in targets]
        for item, t, entry in zip(items, targets, plan, strict=True):
            where = _where(t)
            if entry.current_confirmed != item.expected_confirmed or entry.current_field_key != item.expected_field_key:
                if entry.action != "unchanged":  # 같은 값으로 이미 확정된 경우(중복 요청)는 충돌로 보지 않음
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
            raise _conflict(problems)
        applied = learned = 0
        unchanged = sum(1 for e in plan if e.action == "unchanged")
        by_file: dict[int, list[DecisionInput]] = {}
        for t, entry in zip(targets, plan, strict=True):
            if entry.action in ("apply", "overwrite"):
                by_file.setdefault(t.item.file_id, []).append(
                    DecisionInput(t.sheet_name or "", t.raw or "", t.item.field_key)
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
    except NameNotStorable as exc:  # 위 대상 확인이 먼저 거절하므로 닿지 않는다 — 닿아도 500이 아니라 같은 409로
        db.rollback()
        raise _conflict([str(exc)]) from exc
    except Exception:
        db.rollback()
        raise
    return ApplyOut(applied=applied, unchanged=unchanged, learned=learned, files=len(by_file))
