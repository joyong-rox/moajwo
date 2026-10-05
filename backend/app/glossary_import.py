"""용어사전 업로드(CSV·xlsx·xls) 해석·검사·반영.

양식 열: 코드(7자리), 정식명칭, 유형, 시도코드(2자리), 시군구코드(3자리), 시작일, 종료일, 이전코드, 별칭, 메모
- 항목은 7자리 코드 하나로 식별한다. 같은 코드는 갱신, 새 코드는 추가.
- 오류가 한 행이라도 있으면 아무것도 반영하지 않는다 (사전이 일부만 바뀌는 것을 막음). 오류는 모두 한 번에 알려준다.
- 시도·시군구 코드 조합이 행정동 코드표에 없거나 이름이 다르면 경고만 한다.
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

COLUMNS = ["코드", "정식명칭", "유형", "시도코드", "시군구코드", "시작일", "종료일", "이전코드", "별칭", "메모"]
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "코드": ("코드", "기관코드", "행정기관코드", "code"),
    "정식명칭": ("정식명칭", "명칭", "이름", "기관명", "name"),
    "유형": ("유형", "type"),
    "시도코드": ("시도코드", "sidocode"),
    "시군구코드": ("시군구코드", "sigungucode"),
    "시작일": ("시작일", "유효시작일", "생성일", "validfrom"),
    "종료일": ("종료일", "유효종료일", "validto"),
    "이전코드": ("이전코드", "이전기관코드", "previouscodes"),
    "별칭": ("별칭", "aliases", "alias"),
    "메모": ("메모", "비고", "note"),
}
CODE_PATTERN = re.compile(r"^[0-9A-Z]{7}$")
_DATE = re.compile(r"^(\d{4})[-./]?\s*(\d{1,2})[-./]?\s*(\d{1,2})$")
_ALIAS_SPLIT = re.compile(r"[;,/\n]+")
MAX_ROWS = 20000

TEMPLATE_ROWS = [
    ["1741000", "행정안전부", "중앙행정기관", "", "", "2017-07-26", "", "", "행안부", "7자리 기관코드"],
    ["1051000", "기획재정부", "중앙행정기관", "", "", "2008-02-29", "2025-12-31", "", "기재부", "폐지 예시"],
    ["1053000", "재정경제부", "중앙행정기관", "", "", "2026-01-02", "", "1051000", "", "이전코드로 개편 전 항목 연결"],
    ["3150000", "서울특별시 강서구", "기초자치단체", "11", "500", "", "", "", "", "시도 2자리 + 시군구 3자리"],
]


@dataclass
class ImportRow:
    row: int
    code: str
    name: str
    type_label: str | None
    sido_code: str | None
    sigungu_code: str | None
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


def _digits(cell: Cell, width: int, label: str) -> str | None:
    """'11'·11 → '11', '500'·500 → '500'. 엑셀이 숫자로 바꿔 앞자리 0이 빠진 경우도 채운다."""
    text = _text(cell)
    if text is None:
        return None
    if not text.isdigit() or len(text) > width:
        raise ValueError(f"{label}는 {width}자리 숫자여야 합니다: {text}")
    return text.zfill(width)


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
        message = "'코드'와 '정식명칭' 열이 있는 시트를 찾지 못했습니다. 양식을 내려받아 사용하세요."
        report.errors.append({"row": None, "message": message})
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
    code = (_text(get(cells, "코드")) or "").upper()
    name = _text(get(cells, "정식명칭"))
    if not code or not name:
        raise ValueError("코드와 정식명칭은 비울 수 없습니다.")
    if not CODE_PATTERN.match(code):
        raise ValueError(f"코드는 7자리(숫자·영문 대문자)여야 합니다: {code}")
    sido = _digits(get(cells, "시도코드"), 2, "시도코드")
    sigungu = _digits(get(cells, "시군구코드"), 3, "시군구코드")
    if sigungu and not sido:
        raise ValueError("시군구코드가 있으면 시도코드도 있어야 합니다.")
    valid_from, valid_to = _date(get(cells, "시작일")), _date(get(cells, "종료일"))
    if valid_from and valid_to and valid_from > valid_to:
        raise ValueError("시작일이 종료일보다 늦습니다.")
    previous = [p.strip().upper() for p in re.split(r"[,;\s]+", _text(get(cells, "이전코드")) or "") if p.strip()]
    bad_prev = [p for p in previous if not CODE_PATTERN.match(p)]
    if bad_prev:
        raise ValueError(f"이전코드는 7자리여야 합니다: {', '.join(bad_prev)}")
    aliases = [a.strip() for a in _ALIAS_SPLIT.split(_text(get(cells, "별칭")) or "") if a.strip()]
    return ImportRow(
        row_no,
        code,
        name,
        _text(get(cells, "유형")),
        sido,
        sigungu,
        valid_from,
        valid_to,
        previous,
        aliases,
        _text(get(cells, "메모")),
    )


def _region_warnings(session: Session, rows: list[ImportRow], report: ImportReport, codes: set[str]) -> None:
    table = admin_codes.load_table()
    current: dict[str, int] = {}
    for row in rows:
        if row.sido_code is None:
            continue
        region = row.sido_code + (row.sigungu_code or "")
        area = table.lookup(region)
        if area is None:
            message = f"행정동 코드표({table.effective_date})에 없는 시도·시군구 코드: {region}"
            report.warnings.append({"row": row.row, "message": message})
        elif normalize_name(area.full_name) != normalize_name(row.name):
            message = f"행정동 코드표의 이름과 다릅니다: {region} = {area.full_name} (업로드: {row.name})"
            report.warnings.append({"row": row.row, "message": message})
        if row.valid_to is not None:
            continue
        # 현행 항목끼리 같은 시도·시군구 코드를 쓰면 지역코드 열 연결이 모호해진다
        if region in current:
            message = f"같은 시도·시군구 코드({region})가 {current[region]}행에도 있습니다."
            report.warnings.append({"row": row.row, "message": message})
        current[region] = row.row
        sigungu_match = (
            GlossaryEntry.sigungu_code.is_(None)
            if row.sigungu_code is None
            else GlossaryEntry.sigungu_code == row.sigungu_code
        )
        other = session.scalar(
            select(GlossaryEntry).where(
                GlossaryEntry.sido_code == row.sido_code,
                sigungu_match,
                GlossaryEntry.code.not_in(codes),
                GlossaryEntry.valid_to.is_(None),
            )
        )
        if other is not None:
            message = f"이미 등록된 {other.name}({other.code})도 시도·시군구 코드 {region}를 씁니다."
            report.warnings.append({"row": row.row, "message": message})


def check_rows(session: Session, rows: list[ImportRow], report: ImportReport) -> None:
    """파일 안 중복과 시도·시군구 코드를 검사한다. 형식 오류가 있어도 끝까지 검사해 한 번에 모두 보여준다."""
    seen: dict[str, int] = {}
    for row in rows:
        if row.code in seen:
            report.errors.append(
                {"row": row.row, "message": f"같은 코드가 {seen[row.code]}행에도 있습니다: {row.code}"}
            )
        seen[row.code] = row.row
    _region_warnings(session, rows, report, set(seen))


def apply_rows(session: Session, rows: list[ImportRow], report: ImportReport) -> None:
    for row in rows:
        entry = session.scalar(select(GlossaryEntry).where(GlossaryEntry.code == row.code))
        if entry is None:
            entry = GlossaryEntry(code=row.code)
            session.add(entry)
            report.inserted += 1
        else:
            report.updated += 1
        entry.name = row.name
        entry.normalized = normalize_name(row.name)
        entry.type_label = row.type_label
        entry.sido_code = row.sido_code
        entry.sigungu_code = row.sigungu_code
        entry.valid_from = row.valid_from
        entry.valid_to = row.valid_to
        entry.previous_codes = ",".join(row.previous_codes) or None
        entry.note = row.note
        entry.source = "upload"
        for alias in row.aliases:
            normalized = normalize_name(alias)
            exists = session.scalar(
                select(GlossaryAlias.id).where(
                    GlossaryAlias.normalized == normalized, GlossaryAlias.target_code == row.code
                )
            )
            if exists is None:
                session.add(GlossaryAlias(alias=alias, normalized=normalized, target_code=row.code, source="upload"))
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
    check_rows(session, rows, report)
    report.errors.sort(key=lambda e: (e["row"] is not None, e["row"] or 0))
    if not report.errors:  # 하나라도 오류가 있으면 반영하지 않는다
        apply_rows(session, rows, report)
    else:
        session.rollback()
    return report


def _csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return "﻿" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않도록 BOM


def template_csv() -> str:
    return _csv(TEMPLATE_ROWS)


def export_csv(entries: list[GlossaryEntry], aliases: dict[str, list[str]]) -> str:
    def text(value: date | None) -> str:
        return value.isoformat() if value else ""

    return _csv(
        [
            [
                e.code,
                e.name,
                e.type_label or "",
                e.sido_code or "",
                e.sigungu_code or "",
                text(e.valid_from),
                text(e.valid_to),
                e.previous_codes or "",
                "; ".join(aliases.get(e.code, [])),
                e.note or "",
            ]
            for e in entries
        ]
    )
