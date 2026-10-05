"""용어사전 업로드(CSV·xlsx·xls) 해석·검사·반영.

양식 열: 구분(기관/지역), 코드, 정식명칭, 유형, 상위코드, 시작일, 종료일, 이전코드, 별칭, 메모
- 오류가 한 행이라도 있으면 아무것도 반영하지 않는다 (사전이 일부만 바뀌는 것을 막음).
- 지역 코드는 행정동코드 시도 2자리 / 시군구 5자리만 받는다. 행정동 코드표에 없거나 이름이 다르면 경고만 한다.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin_codes
from app.glossary import normalize_name
from app.models import GlossaryAlias, GlossaryEntry
from app.parsing import Cell, ParseError, is_missing, parse_file

COLUMNS = ["구분", "코드", "정식명칭", "유형", "상위코드", "시작일", "종료일", "이전코드", "별칭", "메모"]
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "구분": ("구분", "종류", "kind"),
    "코드": ("코드", "code"),
    "정식명칭": ("정식명칭", "명칭", "이름", "기관명", "지역명", "name"),
    "유형": ("유형", "type"),
    "상위코드": ("상위코드", "parentcode", "parent"),
    "시작일": ("시작일", "유효시작일", "생성일", "validfrom"),
    "종료일": ("종료일", "유효종료일", "validto"),
    "이전코드": ("이전코드", "이전기관코드", "previouscodes"),
    "별칭": ("별칭", "aliases", "alias"),
    "메모": ("메모", "비고", "note"),
}
KIND_VALUES = {"기관": "org", "org": "org", "지역": "region", "region": "region", "시도": "region", "시군구": "region"}
_DATE = re.compile(r"^(\d{4})[-./]?\s*(\d{1,2})[-./]?\s*(\d{1,2})$")
_ALIAS_SPLIT = re.compile(r"[;,/\n]+")
MAX_ROWS = 20000


@dataclass
class ImportRow:
    row: int
    kind: str
    code: str
    name: str
    type_label: str | None
    parent_code: str | None
    valid_from: date | None
    valid_to: date | None
    previous_codes: list[str]
    aliases: list[str]
    note: str | None


@dataclass
class ImportReport:
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    aliases_added: int = 0
    applied: bool = False
    errors: list[dict[str, object]] = field(default_factory=list)
    warnings: list[dict[str, object]] = field(default_factory=list)


def _text(cell: Cell) -> str | None:
    if cell is None or (isinstance(cell, str) and is_missing(cell)):
        return None
    text = str(cell).strip()
    return text or None


def _date(cell: Cell) -> date | None:
    text = _text(cell)
    if text is None:
        return None
    m = _DATE.match(text.split(" ")[0])
    if not m:
        raise ValueError(f"날짜 형식이 아닙니다: {text} (예: 2026-01-01)")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _column_index(columns: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for i, name in enumerate(columns):
        key = normalize_name(name).replace("_", "")
        for field_name, names in HEADER_ALIASES.items():
            if field_name not in found and key in names:
                found[field_name] = i
    return found


def read_rows(path: Path, original_name: str, report: ImportReport) -> list[ImportRow]:
    try:
        parsed = parse_file(path, original_name)
    except ParseError as exc:
        report.errors.append({"row": None, "message": str(exc)})
        return []
    sheet = next((s for s in parsed.sheets if {"코드", "정식명칭"} <= _column_index(s.columns).keys()), None)
    if sheet is None:
        report.errors.append(
            {
                "row": None,
                "message": "'코드'와 '정식명칭' 열이 있는 시트를 찾지 못했습니다. 양식을 내려받아 사용하세요.",
            }
        )
        return []
    index = _column_index(sheet.columns)

    def get(cells: list[Cell], key: str) -> Cell:
        return cells[index[key]] if key in index else None

    rows: list[ImportRow] = []
    for body in sheet.body:
        if body["kind"] in ("empty", "note"):
            continue
        cells, row_no = body["cells"], body["row"]
        report.rows += 1
        if report.rows > MAX_ROWS:
            report.errors.append({"row": row_no, "message": f"한 번에 {MAX_ROWS}행까지 올릴 수 있습니다."})
            break
        try:
            rows.append(_to_row(row_no, cells, get))
        except ValueError as exc:
            report.errors.append({"row": row_no, "message": str(exc)})
    return rows


def _to_row(row_no: int, cells: list[Cell], get) -> ImportRow:  # noqa: ANN001
    kind_text = _text(get(cells, "구분")) or ""
    kind = KIND_VALUES.get(normalize_name(kind_text))
    code = _text(get(cells, "코드"))
    name = _text(get(cells, "정식명칭"))
    if kind is None:
        raise ValueError(f"구분은 '기관' 또는 '지역'이어야 합니다: {kind_text or '(비어 있음)'}")
    if not code or not name:
        raise ValueError("코드와 정식명칭은 비울 수 없습니다.")
    if len(code) > 32 or not re.fullmatch(r"[0-9A-Za-z\-]+", code):
        raise ValueError(f"코드는 영문·숫자 32자 이내여야 합니다: {code}")
    if kind == "region" and (not code.isdigit() or len(code) not in (2, 5)):
        raise ValueError(f"지역 코드는 행정동코드 시도 2자리 또는 시군구 5자리여야 합니다: {code}")
    valid_from, valid_to = _date(get(cells, "시작일")), _date(get(cells, "종료일"))
    if valid_from and valid_to and valid_from > valid_to:
        raise ValueError("시작일이 종료일보다 늦습니다.")
    parent = _text(get(cells, "상위코드"))
    if kind == "region" and len(code) == 5 and parent is None:
        parent = code[:2]  # 시군구의 상위 시도는 앞 2자리
    aliases = [a.strip() for a in _ALIAS_SPLIT.split(_text(get(cells, "별칭")) or "") if a.strip()]
    previous = [p.strip() for p in re.split(r"[,;\s]+", _text(get(cells, "이전코드")) or "") if p.strip()]
    return ImportRow(
        row_no,
        kind,
        code,
        name,
        _text(get(cells, "유형")) or ({2: "시도", 5: "시군구"}.get(len(code)) if kind == "region" else None),
        parent,
        valid_from,
        valid_to,
        previous,
        aliases,
        _text(get(cells, "메모")),
    )


def _check_region(row: ImportRow, report: ImportReport) -> None:
    table = admin_codes.load_table()
    area = table.lookup(row.code)
    if area is None:
        report.warnings.append(
            {"row": row.row, "message": f"행정동 코드표({table.effective_date})에 없는 지역 코드: {row.code}"}
        )
    elif normalize_name(area.full_name) != normalize_name(row.name):
        report.warnings.append(
            {
                "row": row.row,
                "message": f"행정동 코드표의 이름과 다릅니다: {row.code} = {area.full_name} (업로드: {row.name})",
            }
        )


def check_rows(rows: list[ImportRow], report: ImportReport) -> None:
    """파일 안 중복과 지역 코드를 검사한다. 형식 오류가 있어도 끝까지 검사해 한 번에 모두 보여준다."""
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.kind, row.code)
        if key in seen:
            report.errors.append({"row": row.row, "message": f"같은 코드가 {seen[key]}행에도 있습니다: {row.code}"})
        seen[key] = row.row
        if row.kind == "region":
            _check_region(row, report)


def apply_rows(session: Session, rows: list[ImportRow], report: ImportReport) -> None:
    for row in rows:
        entry = session.scalar(
            select(GlossaryEntry).where(GlossaryEntry.kind == row.kind, GlossaryEntry.code == row.code)
        )
        if entry is None:
            entry = GlossaryEntry(kind=row.kind, code=row.code)
            session.add(entry)
            report.inserted += 1
        else:
            report.updated += 1
        entry.name = row.name
        entry.normalized = normalize_name(row.name)
        entry.type_label = row.type_label
        entry.parent_code = row.parent_code
        entry.valid_from = row.valid_from
        entry.valid_to = row.valid_to
        entry.previous_codes = ",".join(row.previous_codes) or None
        entry.note = row.note
        entry.source = "upload"
        for alias in row.aliases:
            normalized = normalize_name(alias)
            exists = session.scalar(
                select(GlossaryAlias.id).where(
                    GlossaryAlias.normalized == normalized,
                    GlossaryAlias.kind == row.kind,
                    GlossaryAlias.target_code == row.code,
                )
            )
            if exists is None:
                session.add(
                    GlossaryAlias(
                        alias=alias, normalized=normalized, kind=row.kind, target_code=row.code, source="upload"
                    )
                )
                session.flush()
                report.aliases_added += 1
        session.flush()
    session.commit()
    report.applied = True


def import_file(session: Session, path: Path, original_name: str) -> ImportReport:
    report = ImportReport()
    rows = read_rows(path, original_name, report)
    if not report.errors and not rows:
        report.errors.append({"row": None, "message": "등록할 행이 없습니다."})
    check_rows(rows, report)
    report.errors.sort(key=lambda e: (e["row"] is not None, e["row"] or 0))
    if not report.errors:  # 하나라도 오류가 있으면 반영하지 않는다
        apply_rows(session, rows, report)
    if report.errors:
        session.rollback()
    return report


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else ""


TEMPLATE_ROWS = [
    ["기관", "1741000", "행정안전부", "중앙행정기관", "", "2017-07-26", "", "", "행안부", "행정기관코드 예시"],
    ["기관", "1051000", "기획재정부", "중앙행정기관", "", "2008-02-29", "2025-12-31", "", "기재부", "폐지 예시"],
    ["지역", "11500", "서울특별시 강서구", "시군구", "11", "", "", "", "", "행정동코드 시군구 5자리"],
]


def template_csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return "\ufeff" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않도록 BOM


def export_csv(entries: list[GlossaryEntry], aliases: dict[tuple[str, str], list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for e in entries:
        writer.writerow(
            [
                "기관" if e.kind == "org" else "지역",
                e.code,
                e.name,
                e.type_label or "",
                e.parent_code or "",
                _date_text(e.valid_from),
                _date_text(e.valid_to),
                e.previous_codes or "",
                "; ".join(aliases.get((e.kind, e.code), [])),
                e.note or "",
            ]
        )
    return "﻿" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않도록 BOM
