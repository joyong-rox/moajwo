"""xlsx / xls / csv 파일을 시트별 격자로 읽고 헤더·행 종류를 판별한다.

원본 파일은 읽기만 한다. 이 모듈은 어떤 파일도 쓰지 않는다.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import chardet
import openpyxl
import xlrd

SUPPORTED_EXTENSIONS = (".xlsx", ".xls", ".csv")

# 헤더는 1~3행 안에서만 찾는다.
HEADER_SEARCH_ROWS = 3
PREVIEW_ROWS = 20
CHARDET_MIN_CONFIDENCE = 0.5
# chardet 판별 실패/저신뢰 시 시도 순서 (EUC-KR 우선, CP949는 EUC-KR 확장)
CSV_FALLBACK_ENCODINGS = ("euc-kr", "cp949", "utf-8")

RowKind = Literal["data", "empty", "subtotal", "note"]
Cell = str | int | float | bool | None


class ParseError(Exception):
    pass


@dataclass
class SheetResult:
    name: str
    header_row: int | None  # 1-based, 헤더를 못 찾으면 None
    columns: list[str]
    data_rows: int
    empty_rows: int
    subtotal_rows: int
    note_rows: int = 0  # 자료/주/※ 등 주석 행
    missing_cells: int = 0  # '-', 'x', '…' 등 결측 표기 셀 (데이터 행 기준)
    header_row_end: int | None = None  # 2단 헤더면 아래 헤더 행 (1-based), 아니면 header_row와 같음
    preview: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FileResult:
    sheets: list[SheetResult]
    encoding: str | None = None  # csv만

    @property
    def data_rows(self) -> int:
        return sum(s.data_rows for s in self.sheets)


# ---------------------------------------------------------------- 셀 정규화

_PLAIN_NUMBER = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$")
# 천 단위 구분이 올바른 경우만 숫자로 본다 ("010 1234 5678" 같은 전화번호 오변환 방지)
_GROUPED_NUMBER = re.compile(r"^[+-]?\d{1,3}(?:([, ])\d{3})(?:\1\d{3})*(?:\.\d+)?$")


def to_number(text: str) -> int | float | None:
    """'1,234', '1 234', ' 56.7 ' 같은 숫자 문자열을 숫자로. 숫자가 아니면 None."""
    s = text.strip().replace(" ", " ")
    if not s:
        return None
    if _PLAIN_NUMBER.match(s):
        pass
    elif _GROUPED_NUMBER.match(s):
        s = s.replace(",", "").replace(" ", "")
    else:
        return None
    value = float(s)
    return int(value) if "." not in s and value.is_integer() else value


def normalize_cell(value: Any) -> Cell:
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds").removesuffix(" 00:00:00")
    if isinstance(value, date | time):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    number = to_number(text)
    return number if number is not None else text


# ---------------------------------------------------------------- 파일 읽기


def _read_xlsx(path: Path) -> dict[str, list[list[Any]]]:
    # read_only 모드에서는 병합 셀 정보를 얻을 수 없어 일반 모드로 연다 (파일은 읽기만 함).
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        sheets: dict[str, list[list[Any]]] = {}
        for ws in wb.worksheets:
            grid = [list(row) for row in ws.iter_rows(values_only=True)]
            for rng in ws.merged_cells.ranges:
                top_left = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row - 1 < len(grid) else None
                _fill_range(grid, rng.min_row - 1, rng.max_row, rng.min_col - 1, rng.max_col, top_left)
            sheets[ws.title] = grid
        return sheets
    finally:
        wb.close()


def _read_xls(path: Path) -> dict[str, list[list[Any]]]:
    book = xlrd.open_workbook(path, formatting_info=True, on_demand=True)
    try:
        sheets: dict[str, list[list[Any]]] = {}
        for sh in book.sheets():
            grid = [[_xls_value(book, sh.cell(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
            for rlo, rhi, clo, chi in sh.merged_cells:
                if rlo < len(grid):
                    _fill_range(grid, rlo, rhi, clo, chi, grid[rlo][clo] if clo < len(grid[rlo]) else None)
            sheets[sh.name] = grid
        return sheets
    finally:
        book.release_resources()


def _xls_value(book: xlrd.book.Book, cell: xlrd.sheet.Cell) -> Any:
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
        except (ValueError, xlrd.xldate.XLDateError):
            return cell.value
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return None
    return cell.value


def _fill_range(grid: list[list[Any]], r0: int, r1: int, c0: int, c1: int, value: Any) -> None:
    """병합 범위 [r0, r1) x [c0, c1)를 상단 왼쪽 값으로 채운다 (ffill)."""
    for r in range(r0, min(r1, len(grid))):
        row = grid[r]
        if len(row) < c1:
            row.extend([None] * (c1 - len(row)))
        for c in range(c0, c1):
            row[c] = value


def decode_csv(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    guess = chardet.detect(raw)
    encoding = guess.get("encoding")
    if encoding and (guess.get("confidence") or 0) >= CHARDET_MIN_CONFIDENCE:
        try:
            return raw.decode(encoding), encoding.lower()
        except (UnicodeDecodeError, LookupError):
            pass
    for candidate in CSV_FALLBACK_ENCODINGS:
        try:
            return raw.decode(candidate), candidate
        except UnicodeDecodeError:
            continue
    raise ParseError("CSV 인코딩을 판별할 수 없습니다.")


def _read_csv(path: Path, sheet_name: str) -> tuple[dict[str, list[list[Any]]], str]:
    text, encoding = decode_csv(path.read_bytes())
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [list(r) for r in csv.reader(io.StringIO(text, newline=""), dialect)]
    return {sheet_name: rows}, encoding


# ---------------------------------------------------------------- 분석

_SUBTOTAL_ANYWHERE = re.compile(r"(소계|합계|총계|누계|subtotal|grandtotal)", re.IGNORECASE)
# '전국'은 시도·시군구 통계에서 전체 합계 행이다.
_SUBTOTAL_EXACT = re.compile(r"^(계|합|총|전국|total|sum)$", re.IGNORECASE)
_SUBTOTAL_MAX_LEN = 20  # 긴 문장 속 '합계' 단어는 소계 행으로 보지 않는다

# 정부 통계 하단 주석: "자료: …", "출처 : …", "주: 1) …", "주1) …", "※ …", "* …", "(단위: 명)"
_NOTE_PREFIX = re.compile(
    r"^(?:(?:자료|출처|주|비고|참고|note|notes|source)\s*\d*\s*[:：)]|[※*＊†‡]|\(?\s*단위\s*[:：])",
    re.IGNORECASE,
)

# 통계표 결측/비공개 표기. 원본 표기는 그대로 두고 표시만 한다 (0과 구분).
MISSING_MARKERS = frozenset({"-", "－", "–", "—", "x", "X", "…", "...", "·", "ㆍ", "..", "na", "n/a", "NA", "N/A"})

# 2단 헤더 아래 줄에 올 수 있는 숫자는 연도뿐이다 (그 외 숫자가 있으면 데이터 행으로 본다).
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def is_missing(cell: Cell) -> bool:
    return isinstance(cell, str) and cell in MISSING_MARKERS


def _is_subtotal(row: list[Cell]) -> bool:
    for cell in row:
        if not isinstance(cell, str):
            continue
        compact = re.sub(r"\s+", "", cell)
        if len(compact) > _SUBTOTAL_MAX_LEN:
            continue
        if _SUBTOTAL_EXACT.match(compact) or _SUBTOTAL_ANYWHERE.search(compact):
            return True
    return False


def _is_note(row: list[Cell]) -> bool:
    """값이 한 종류뿐(병합 ffill 포함)이고 주석 접두어로 시작하는 행."""
    values = {c for c in row if c is not None}
    if len(values) != 1:
        return False
    (value,) = values
    return isinstance(value, str) and bool(_NOTE_PREFIX.match(value.strip()))


def classify_row(row: list[Cell]) -> RowKind:
    if all(c is None for c in row):
        return "empty"
    if _is_note(row):
        return "note"
    if _is_subtotal(row):
        return "subtotal"
    return "data"


def _header_texts(row: list[Cell]) -> set[str]:
    return {c for c in row if isinstance(c, str) and not is_missing(c) and not _is_note([c])}


def detect_header_row(rows: list[list[Cell]]) -> int | None:
    """1~3행 중 '서로 다른 텍스트 셀'이 가장 많은 행을 헤더로 본다 (0-based).

    병합된 제목·단위 행은 ffill 후 같은 값이 반복되므로 고유 값이 1개뿐이라 자연스럽게 밀린다.
    동점이면 위쪽 행을 고른다. 텍스트 셀이 하나도 없으면 None.
    """
    best, best_score = None, 0
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        score = len(_header_texts(row))
        if score > best_score:
            best, best_score = i, score
    return best


def _runs(row: list[Cell]) -> list[tuple[int, int]]:
    """같은 값이 가로로 2칸 이상 이어진 구간 [start, end). 가로 병합 셀의 ffill 흔적이다."""
    runs, i = [], 0
    while i < len(row):
        j = i + 1
        while j < len(row) and row[i] is not None and row[j] == row[i]:
            j += 1
        if j - i >= 2:
            runs.append((i, j))
        i = j
    return runs


def _is_header_like(cell: Cell) -> bool:
    if cell is None:
        return True
    if isinstance(cell, str):
        return not is_missing(cell)
    return isinstance(cell, int) and not isinstance(cell, bool) and _YEAR_MIN <= cell <= _YEAR_MAX


def _is_two_level(top: list[Cell], sub: list[Cell]) -> bool:
    """top이 가로 병합된 그룹 헤더이고 sub가 그 아래를 나눠 쓰는 하위 헤더인지.

    - top에 가로 반복 구간이 있고, 전체 폭을 덮는 한 덩어리(제목 행)는 아니어야 한다.
    - 각 구간 아래 sub 셀은 모두 채워져 있고 서로 달라야 한다.
    - sub의 모든 셀은 텍스트 또는 연도여야 한다 (금액 등 숫자가 있으면 데이터 행).
    """
    runs = _runs(top)
    if not runs or len({c for c in top if c is not None}) < 2:
        return False
    # 하위 헤더에는 '계'·'합계' 열이 흔하므로 소계 여부는 보지 않는다 (실제 소계 행은 숫자가 있어 걸러진다).
    if not all(_is_header_like(c) for c in sub) or _is_note(sub):
        return False
    for start, end in runs:
        cells = sub[start:end]
        if any(c is None for c in cells) or len(set(cells)) != len(cells):
            return False
    return True


def detect_header_block(rows: list[list[Cell]]) -> tuple[int, int] | None:
    """헤더 구간 (top, bottom) 0-based. 1단이면 top == bottom."""
    h = detect_header_row(rows)
    if h is None:
        return None
    if h > 0 and _is_two_level(rows[h - 1], rows[h]):
        return h - 1, h
    if h + 1 < len(rows) and _is_two_level(rows[h], rows[h + 1]):
        return h, h + 1
    return h, h


def _trim(grid: list[list[Cell]]) -> list[list[Cell]]:
    """뒤쪽 빈 행과 오른쪽 빈 열을 잘라내고 모든 행 길이를 맞춘다."""
    while grid and all(c is None for c in grid[-1]):
        grid.pop()
    width = max((max((i + 1 for i, c in enumerate(r) if c is not None), default=0) for r in grid), default=0)
    return [(r + [None] * width)[:width] for r in grid]


def _column_names(header_rows: list[list[Cell]], width: int) -> list[str]:
    """헤더 행(1~2줄)을 위→아래로 합쳐 열 이름을 만든다. 예: '인구' + '2025' → '인구_2025'."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for i in range(width):
        parts: list[str] = []
        for row in header_rows:
            value = row[i]
            if value is not None and (not parts or str(value) != parts[-1]):
                parts.append(str(value))
        name = "_".join(parts) or f"열{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        names.append(name)
    return names


def analyze_sheet(name: str, raw: list[list[Any]]) -> SheetResult:
    grid = _trim([[normalize_cell(v) for v in row] for row in raw])
    width = len(grid[0]) if grid else 0
    block = detect_header_block(grid)
    header_rows = grid[block[0] : block[1] + 1] if block else []
    body_start = block[1] + 1 if block else 0

    counts = {"data": 0, "empty": 0, "subtotal": 0, "note": 0}
    missing_total = 0
    preview: list[dict[str, Any]] = []
    for offset, row in enumerate(grid[body_start:]):
        kind = classify_row(row)
        counts[kind] += 1
        missing = [j for j, c in enumerate(row) if is_missing(c)] if kind == "data" else []
        missing_total += len(missing)
        if len(preview) < PREVIEW_ROWS:
            preview.append({"row": body_start + offset + 1, "kind": kind, "cells": row, "missing": missing})

    return SheetResult(
        name=name,
        header_row=block[0] + 1 if block else None,
        header_row_end=block[1] + 1 if block else None,
        columns=_column_names(header_rows, width),
        data_rows=counts["data"],
        empty_rows=counts["empty"],
        subtotal_rows=counts["subtotal"],
        note_rows=counts["note"],
        missing_cells=missing_total,
        preview=preview,
    )


def parse_file(path: Path, original_name: str | None = None) -> FileResult:
    """original_name: 사용자가 올린 파일명. CSV는 시트가 없어 이 이름(확장자 제외)을 시트명으로 쓴다."""
    ext = path.suffix.lower()
    encoding = None
    try:
        if ext == ".xlsx":
            sheets = _read_xlsx(path)
        elif ext == ".xls":
            sheets = _read_xls(path)
        elif ext == ".csv":
            sheets, encoding = _read_csv(path, Path(original_name or path.name).stem)
        else:
            raise ParseError(f"지원하지 않는 형식입니다: {ext}")
    except ParseError:
        raise
    except Exception as exc:  # 손상/암호화 파일 등 라이브러리별 예외를 사용자 메시지로 통일
        raise ParseError(f"파일을 읽을 수 없습니다 ({type(exc).__name__}).") from exc
    return FileResult(sheets=[analyze_sheet(n, g) for n, g in sheets.items()], encoding=encoding)
