"""통합 결과 생성: 검토가 끝난 세션으로 새 결과 엑셀(3개 시트)을 만들고 바뀐 값을 run_changes에 모두 기록한다.

- 원본 파일은 읽기만 한다. 결과는 항상 data/outputs/에 새 이름으로 만든다 (같은 이름이 있으면 실패).
- 통합 데이터의 모든 셀을 '파일에 적힌 값'과 비교해, 달라진 셀은 빠짐없이 run_changes에 기록하고 배경색을 칠한다.
  (색칠한 셀과 셀 단위 기록이 1:1인지 저장 전에 검사한다. 어긋나면 결과를 남기지 않고 실패로 기록한다.)
- 원본 열 이름 → 표준 항목 이름 매핑도 열 이름 변경으로 기록한다 (통합 행 1 = 헤더).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.decisions import load_decisions, load_learned, load_org_codes
from app.files import load_parsed
from app.glossary import EntityRef, Resolution, entity_ref, normalize_name
from app.glossary_api import get_glossary
from app.mapping import ColumnMapping, get_engine
from app.models import Automation, IntegrationRun, ReviewSession, RunChange
from app.parsing import Cell, is_missing
from app.promotion import load_standard
from app.quantity import confirmed_quantity_indexes, quantity_sheet
from app.review import mapping_reason, session_items, summarize
from app.validation import EntityResolver

logger = logging.getLogger(__name__)

OUTPUT_DIR = DATA_DIR / "outputs"
# 처리 시각은 한국 시간으로 적는다. 컨테이너는 UTC이고 slim 이미지에 tzdata가 없어
# 고정 오프셋을 쓴다 (KST는 일광절약시간 없음).
KST = timezone(timedelta(hours=9), "KST")
ENTITY_FIELDS = ("org_name", "sido", "sigungu", "region")
PROVENANCE = ["원본 파일", "원본 시트", "원본 행"]
SHEET_DATA, SHEET_CHANGES, SHEET_SUMMARY = "통합 데이터", "변경 내역", "처리 요약"
CHANGE_HEADER = [
    "번호",
    "원본 파일",
    "원본 시트",
    "원본 행",
    "통합 행",
    "열",
    "원본 열",
    "이전 값",
    "이후 값",
    "종류",
    "근거",
    "확신도",
]

KIND_LABEL = {
    "ffill": "병합 셀 채움",
    "number": "숫자 변환",
    "trim": "공백 정리",
    "entity": "이름 정규화",
    "column": "열 이름 매핑",
    "other": "기타 변환",
}
# 종류별 배경색 (결과 파일 '처리 요약' 시트에 범례를 함께 넣는다)
FILLS = {
    "ffill": "DDEBF7",
    "number": "FFF2CC",
    "trim": "EDEDED",
    "entity": "E2EFDA",
    "column": "FCE4D6",
    "other": "F8CBAD",
}


class IntegrationError(Exception):
    pass


@dataclass
class Change:
    source_file_id: int | None
    file_name: str
    sheet: str
    source_row: int | None
    output_row: int
    column: str
    source_column: str
    kind: str
    before: str | None
    after: str | None
    reason: str
    confidence: float
    output_col: int | None = None  # 통합 데이터 시트의 열 번호 (1-based, 셀 변경만)


@dataclass
class _Column:
    index: int  # 원본 시트의 열 번호 (0-based)
    source: str  # 원본 열 이름
    label: str  # 결과 열 이름
    field_key: str
    mapping: ColumnMapping


@dataclass
class _Built:
    labels: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    dropped_columns: list[dict[str, Any]] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)  # 원래 이름으로 넣은 '해당 없음' 열


# ---------------------------------------------------------------- 값 비교


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def same(a: Any, b: Any) -> bool:
    """파일에 적힌 값과 결과 값이 같은지 (3.0과 3은 같음, '1,000'과 1000은 다름)."""
    if a is None or b is None:  # CSV의 빈 문자열('')과 빈 칸은 같은 빈 값
        return a in (None, "") and b in (None, "")
    if _is_number(a) and _is_number(b):
        return float(a) == float(b)
    if isinstance(a, datetime | date) or isinstance(b, datetime | date):
        return a == b
    return type(a) is type(b) and a == b


def text(value: Any) -> str | None:
    """run_changes·변경 내역 시트에 적을 값 표현."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


# ---------------------------------------------------------------- 결과 데이터 만들기


def _columns(
    sheet_columns: list[str],
    mappings: list[ColumnMapping],
    include_unmapped: bool = False,
    standard_labels: frozenset[str] = frozenset(),
) -> tuple[list[_Column], list[dict[str, Any]]]:
    """include_unmapped: '해당 없음'으로 확정한 열(표준 항목이 없는 열)도 원래 열 이름으로 결과에 넣는다."""
    used: set[str] = set()
    columns, dropped = [], []
    for j, m in enumerate(mappings):
        if include_unmapped and not m.field_key and m.status == "confirmed":
            label = sheet_columns[j]
            if label in standard_labels or label in used:  # 표준 항목 열과 섞이지 않게 구분
                label = f"{label} (원본 열)"
            used.add(label)
            columns.append(_Column(j, sheet_columns[j], label, None, m))
            continue
        if not m.field_key or m.status not in ("confirmed", "auto"):
            reason = "'해당 없음'으로 확정" if m.status == "confirmed" else "확정되지 않은 매핑"
            dropped.append({"column": sheet_columns[j], "reason": reason})
            continue
        label = (m.field_label or m.field_key) + (f" ({m.qualifier})" if m.qualifier else "")
        if label in used:  # 같은 시트의 두 열이 같은 표준 항목이면 원본 열 이름으로 구분
            label = f"{label} [{sheet_columns[j]}]"
        used.add(label)
        columns.append(_Column(j, sheet_columns[j], label, m.field_key, m))
    return columns, dropped


class _EntityNormalizer:
    """기관·지역 열 값 → 용어사전 정식 이름·7자리 코드. 검토에서 사용자가 승인한 연결을 먼저 쓴다."""

    def __init__(self, resolver: EntityResolver, choices: dict[str, str], review_id: int) -> None:
        self.resolver = resolver
        self.choices = choices  # 이름 → 7자리 코드 (검토 승인)
        self.review_id = review_id

    def resolve(self, name: str, field_key: str, sido: str | None) -> tuple[EntityRef | None, str, float]:
        glossary = self.resolver.glossary
        if glossary is None:
            return None, "", 0.0
        code = self.choices.get(name)
        if code and code in glossary.entries:
            ref = entity_ref(glossary.entries[code])
            return ref, f"검토 #{self.review_id}에서 사용자가 '{name}' → '{ref.name}'({code}) 연결을 승인했습니다.", 1.0
        lookup = f"{sido} {name}" if field_key == "sigungu" and sido else name
        res: Resolution | None = self.resolver.resolve(lookup, field_key)
        if res is None or res.status != "resolved" or res.ref is None or res.note:
            return None, "", 0.0
        if res.via == "alias":
            return res.ref, f"용어사전 별칭 '{name}'에 따라 '{res.ref.name}'(으)로 바꿉니다.", 1.0
        if res.via == "short":
            return res.ref, f"'{name}'은(는) 지역 약칭이라 정식명칭 '{res.ref.name}'(으)로 바꿉니다.", 1.0
        return res.ref, "용어사전 정식명칭과 일치합니다.", 1.0


def _display_name(ref: EntityRef, field_key: str, has_sido: bool = True) -> str:
    """시군구 열에는 시도를 뺀 이름을 쓴다. 정식명칭은 '시도명 시군구명' 형식이므로 첫 공백 뒤를 쓴다.

    (자료의 시도 값이 '서울' 같은 약칭이어도 '서울특별시 강서구' → '강서구'가 되도록 접두어를 떼지 않는다)
    단, 같은 시트에 시도 열이 없으면 정식명칭 그대로 둔다
    ('중구'·'강서구'처럼 시도마다 같은 이름이 있어 줄이면 구분이 사라진다).
    """
    if field_key == "sigungu" and has_sido and " " in ref.name:
        return ref.name.split(" ", 1)[1]
    return ref.name


def build(db: Session, session: ReviewSession, include_unmapped: bool = False) -> _Built:
    if session.status != "ready":
        raise IntegrationError("검토가 끝난(검토 완료) 세션만 통합할 수 있습니다.")
    params = json.loads(session.params)
    excluded = set(json.loads(session.excluded_file_ids))
    file_ids = [f for f in json.loads(session.file_ids) if f not in excluded]
    if not file_ids:
        raise IntegrationError("통합할 파일이 없습니다 (모두 제외됨).")

    engine = get_engine()
    learned, standard = load_learned(db, session.created_by), load_standard(db)
    glossary = get_glossary(db)
    items = session_items(db, session)
    choices = {
        i.before: i.choice for i in items if i.kind == "entity" and i.decision == "approved" and i.choice and i.before
    }
    normalizer = _EntityNormalizer(
        EntityResolver(glossary, params.get("expected_period") or params.get("name_period")), choices, session.id
    )

    built = _Built(labels=list(PROVENANCE))
    standard_labels = frozenset(f.label for f in engine.fields)
    label_index: dict[str, int] = {}

    def column_at(label: str) -> int:
        if label not in label_index:
            built.labels.append(label)
            label_index[label] = len(built.labels) - 1
        return label_index[label]

    for fid in json.loads(session.file_ids):
        if fid in excluded:
            record, _ = load_parsed(db, fid)
            built.files.append({"id": fid, "name": record.original_name, "excluded": True, "sheets": []})
    org_codes = load_org_codes(db)
    for fid in file_ids:
        record, parsed = load_parsed(db, fid)
        decisions = load_decisions(db, fid)
        info: dict[str, Any] = {"id": fid, "name": record.original_name, "excluded": False, "sheets": []}
        for sheet in parsed.sheets:
            mappings = engine.map_columns(
                sheet.columns,
                sheet.samples,
                use_llm=False,
                learned=learned,
                decisions=decisions.get(sheet.name),
                standard=standard,
                org_codes=org_codes,
            ).columns
            sheet = quantity_sheet(sheet, confirmed_quantity_indexes(mappings))  # 확정한 수량 열은 계산용 값
            columns, dropped = _columns(sheet.columns, mappings, include_unmapped, standard_labels)
            for c in columns:
                if c.field_key is None and c.label not in built.unmapped_columns:
                    built.unmapped_columns.append(c.label)
            for d in dropped:
                built.dropped_columns.append({"file": record.original_name, "sheet": sheet.name, **d})
            for c in columns:
                column_at(c.label)
                if c.field_key in ENTITY_FIELDS:
                    column_at(f"{c.label} 코드")
                if normalize_name(c.source) != normalize_name(c.label):
                    reason, _ = mapping_reason(c.mapping)
                    built.changes.append(
                        Change(
                            fid,
                            record.original_name,
                            sheet.name,
                            sheet.header_row,
                            1,
                            c.label,
                            c.source,
                            "column",
                            c.source,
                            c.label,
                            f"열 이름 매핑: {reason}",
                            round(float(c.mapping.score if c.mapping.score is not None else 1.0), 4),
                        )
                    )
            sido_col = next((c for c in columns if c.field_key == "sido"), None)
            used_rows = 0
            for entry in sheet.body:
                if entry["kind"] != "data":
                    continue
                used_rows += 1
                out_row = len(built.rows) + 2  # 1행 = 헤더
                row: list[Any] = [None] * len(built.labels)
                row[0], row[1], row[2] = record.original_name, sheet.name, entry["row"]
                sido_value = entry["cells"][sido_col.index] if sido_col else None
                for c in columns:
                    original: Cell | Any = entry["original"][c.index]
                    value: Any = entry["cells"][c.index]
                    if isinstance(original, datetime | date):
                        value = original  # 날짜는 원본 형식 그대로 둔다 (변환하지 않음)
                    kind, reason, confidence = None, "", 1.0
                    if c.field_key in ENTITY_FIELDS and isinstance(value, str) and not is_missing(value):
                        ref, why, conf = normalizer.resolve(
                            value, c.field_key, sido_value if isinstance(sido_value, str) else None
                        )
                        if ref is not None:
                            row[label_index[f"{c.label} 코드"]] = ref.code
                            shown = _display_name(ref, c.field_key, sido_col is not None)
                            if shown != value:
                                value, kind, reason, confidence = shown, "entity", why, conf
                    row[label_index[c.label]] = value
                    if same(original, value):
                        continue
                    if kind is None:
                        kind, reason = _classify(original, value, c)
                    built.changes.append(
                        Change(
                            fid,
                            record.original_name,
                            sheet.name,
                            entry["row"],
                            out_row,
                            c.label,
                            c.source,
                            kind,
                            text(original),
                            text(value),
                            reason,
                            confidence,
                            output_col=label_index[c.label] + 1,
                        )
                    )
                built.rows.append(row)
            info["sheets"].append(
                {
                    "name": sheet.name,
                    "data_rows": sheet.data_rows,
                    "used_rows": used_rows,
                    "subtotal_rows": sheet.subtotal_rows,
                    "note_rows": sheet.note_rows,
                }
            )
        built.files.append(info)
    # 열이 나중에 늘어난 경우 앞 행들의 길이를 맞춘다
    for row in built.rows:
        row.extend([None] * (len(built.labels) - len(row)))
    if session.automation_id is not None:
        automation = db.get(Automation, session.automation_id)
        if automation is not None:
            _apply_output_format(built, json.loads(automation.output_format).get("columns", []))
    return built


def _apply_output_format(built: _Built, order: list[str]) -> None:
    """저장된 자동화의 열 순서대로 결과 열을 정렬한다 (출처 열은 맨 앞, 새 열은 뒤에 붙임)."""
    head = list(range(len(PROVENANCE)))
    rest = [i for i in range(len(PROVENANCE), len(built.labels))]
    rank = {label: n for n, label in enumerate(order)}
    rest.sort(key=lambda i: (rank.get(built.labels[i], len(rank)), i))
    new_order = head + rest
    position = {old: new for new, old in enumerate(new_order)}
    built.labels = [built.labels[i] for i in new_order]
    built.rows = [[row[i] for i in new_order] for row in built.rows]
    for ch in built.changes:
        if ch.output_col is not None:
            ch.output_col = position[ch.output_col - 1] + 1


def _classify(original: Any, value: Any, c: _Column) -> tuple[str, str]:
    if original is None and value is not None:
        return "ffill", f"'{c.source}' 열의 병합 셀(빈 칸)을 위쪽 값 '{text(value)}'(으)로 채웠습니다."
    if isinstance(original, str) and _is_number(value):
        return "number", f"숫자 문자열 '{original.strip()}'을(를) 숫자로 바꿨습니다 (천 단위 쉼표·공백 제거)."
    if isinstance(original, str) and isinstance(value, str) and original.strip() == value:
        return "trim", "앞뒤 공백을 지웠습니다."
    if isinstance(original, str) and not original.strip() and value is None:
        return "trim", "공백만 있던 칸을 빈 칸으로 정리했습니다."
    return "other", f"원본 값 '{text(original)}'을(를) '{text(value)}'(으)로 읽었습니다."


# ---------------------------------------------------------------- 엑셀 쓰기

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)


def _autosize(ws: Any, widths: dict[int, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(8, min(60, width + 2))


def write_workbook(path: Path, built: _Built, summary: dict[str, Any]) -> int:
    """3개 시트를 쓴다. 칠한 셀 수를 돌려준다 (셀 단위 변경 기록 수와 같아야 함)."""
    wb = Workbook()
    data = wb.active
    data.title = SHEET_DATA
    data.append(built.labels)
    for cell in data[1]:
        cell.font, cell.fill = BOLD, HEADER_FILL
    for row in built.rows:
        data.append(row)
    painted = 0
    widths: dict[int, int] = {i + 1: len(str(label)) * 2 for i, label in enumerate(built.labels)}
    for ch in built.changes:
        if ch.output_col is None:
            continue
        cell = data.cell(row=ch.output_row, column=ch.output_col)
        cell.fill = PatternFill("solid", fgColor=FILLS[ch.kind])
        comment = Comment(
            f"{KIND_LABEL[ch.kind]}\n이전 값: {ch.before if ch.before is not None else '(빈 칸)'}\n{ch.reason}",
            "Moajwo",
        )
        comment.width, comment.height = 320, 120
        cell.comment = comment
        painted += 1
    for row in built.rows:
        for i, value in enumerate(row):
            widths[i + 1] = max(widths.get(i + 1, 8), len(text(value) or ""))
    _autosize(data, widths)
    data.freeze_panes = "D2"
    data.auto_filter.ref = data.dimensions

    changes = wb.create_sheet(SHEET_CHANGES)
    changes.append(CHANGE_HEADER)
    for cell in changes[1]:
        cell.font, cell.fill = BOLD, HEADER_FILL
    for n, ch in enumerate(built.changes, start=1):
        changes.append(
            [
                n,
                ch.file_name,
                ch.sheet,
                ch.source_row if ch.source_row is not None else "",
                ch.output_row,
                ch.column,
                ch.source_column,
                ch.before if ch.before is not None else "(빈 칸)",
                ch.after if ch.after is not None else "(빈 칸)",
                KIND_LABEL[ch.kind],
                ch.reason,
                ch.confidence,
            ]
        )
        changes.cell(row=n + 1, column=10).fill = PatternFill("solid", fgColor=FILLS[ch.kind])
        changes.cell(row=n + 1, column=12).number_format = "0%"
    _autosize(changes, {1: 4, 2: 24, 3: 10, 4: 6, 5: 6, 6: 16, 7: 16, 8: 16, 9: 16, 10: 10, 11: 60, 12: 6})
    for row in changes.iter_rows(min_row=2, min_col=11, max_col=11):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    changes.freeze_panes = "A2"
    changes.auto_filter.ref = changes.dimensions

    _write_summary(wb.create_sheet(SHEET_SUMMARY), built, summary)
    wb.save(path)
    return painted


def _write_summary(ws: Any, built: _Built, summary: dict[str, Any]) -> None:
    def title(text_: str) -> None:
        ws.append([])
        ws.append([text_])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)

    ws.append(["처리 요약"])
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)
    for key, value in [
        ("처리 시각", summary["processed_at"]),
        ("통합 실행 번호", summary["run_id"]),
        ("검토 번호", summary["review_id"]),
        ("검증 번호", summary["validation_run_id"]),
        ("기준 기간", summary["expected_period"] or "(지정 안 함)"),
        ("통합 행 수", summary["row_count"]),
        ("변경 건수 (셀)", summary["cell_change_count"]),
        ("열 이름 매핑", summary["column_change_count"]),
        ("경고 건수", summary["warning_count"]),
        (
            "표준 항목이 없는 열",
            f"원래 이름으로 포함 ({len(built.unmapped_columns)}개)" if summary.get("include_unmapped") else "넣지 않음",
        ),
    ]:
        ws.append([key, value])

    title("입력 파일")
    ws.append(["파일", "시트", "데이터 행", "통합한 행", "소계·합계 행(제외)", "주석 행(제외)", "상태"])
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = BOLD, HEADER_FILL
    for f in built.files:
        if f["excluded"]:
            ws.append([f["name"], "", "", 0, "", "", "제외 (개인정보 확인 거부)"])
            continue
        for s in f["sheets"]:
            ws.append(
                [f["name"], s["name"], s["data_rows"], s["used_rows"], s["subtotal_rows"], s["note_rows"], "통합"]
            )

    title("변경 종류별 건수")
    for kind, count in summary["change_counts"].items():
        ws.append([KIND_LABEL[kind], count])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill("solid", fgColor=FILLS[kind])

    if built.dropped_columns:
        title("통합하지 않은 열")
        ws.append(["파일", "시트", "열", "이유"])
        for d in built.dropped_columns:
            ws.append([d["file"], d["sheet"], d["column"], d["reason"]])

    title("색 범례 (통합 데이터 시트)")
    for kind, label in KIND_LABEL.items():
        if kind == "column":
            continue
        ws.append([label, "셀 메모에 이전 값과 근거가 있습니다."])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill("solid", fgColor=FILLS[kind])
    ws.append(["코드 열", "용어사전에서 찾은 7자리 코드를 덧붙인 열입니다 (원본 값 변경 아님, 색 없음)."])
    _autosize(ws, {1: 22, 2: 30, 3: 10, 4: 10, 5: 16, 6: 12, 7: 24})


# ---------------------------------------------------------------- 실행


def _output_path(run_id: int, now: datetime) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"통합결과_{run_id:04d}_{now.strftime('%Y%m%d_%H%M%S')}.xlsx"


def pending_run(db: Session, session: ReviewSession) -> IntegrationRun | None:
    """자동화 실행으로 만든 '검토 대기' 실행 기록 (다시 검사한 세션이면 이전 세션의 기록)."""
    current: ReviewSession | None = session
    while current is not None:
        run = db.scalar(
            select(IntegrationRun).where(
                IntegrationRun.review_session_id == current.id, IntegrationRun.status == "review"
            )
        )
        if run is not None:
            return run
        current = db.get(ReviewSession, current.parent_id) if current.parent_id else None
    return None


def include_unmapped_default(db: Session, session: ReviewSession) -> bool:
    """자동화로 실행한 검토면 그 자동화에 저장한 선택, 아니면 넣지 않음(기존 동작)."""
    if session.automation_id is None:
        return False
    automation = db.get(Automation, session.automation_id)
    if automation is None:
        return False
    return bool(json.loads(automation.output_format).get("include_unmapped", False))


def integrate(db: Session, session: ReviewSession, include_unmapped: bool | None = None) -> IntegrationRun:
    """include_unmapped: None이면 자동화 설정(없으면 False)을 따른다."""
    started = datetime.now(KST)
    run = pending_run(db, session)
    if include_unmapped is None:
        include_unmapped = include_unmapped_default(db, session)
    built = build(db, session, include_unmapped)
    now = datetime.now(KST)
    items = session_items(db, session)
    warning_count = summarize(session, items).warnings
    file_ids = [f["id"] for f in built.files if not f["excluded"]]
    if run is None:
        run = IntegrationRun(
            trigger="automation" if session.automation_id else "manual",
            automation_id=session.automation_id,
            user_id=session.created_by,
            started_at=started,
        )
        db.add(run)
    run.review_session_id = session.id
    run.status = "running"
    run.file_ids = json.dumps(file_ids)
    run.input_file_count = len(json.loads(session.file_ids))
    run.row_count = len(built.rows)
    run.warning_count = warning_count
    run.summary = "{}"
    db.commit()

    cell_changes = [c for c in built.changes if c.output_col is not None]
    counts: dict[str, int] = {}
    for ch in built.changes:
        counts[ch.kind] = counts.get(ch.kind, 0) + 1
    summary = {
        "processed_at": now.strftime("%Y-%m-%d %H:%M:%S (KST)"),
        "run_id": run.id,
        "review_id": session.id,
        "validation_run_id": session.validation_run_id,
        "expected_period": json.loads(session.params).get("expected_period"),
        "row_count": len(built.rows),
        "cell_change_count": len(cell_changes),
        "column_change_count": counts.get("column", 0),
        "warning_count": warning_count,
        "change_counts": counts,
        "files": built.files,
        "dropped_columns": built.dropped_columns,
        "columns": built.labels,
        "include_unmapped": include_unmapped,
        "unmapped_columns": built.unmapped_columns,
    }
    target = _output_path(run.id, now)
    tmp_path: Path | None = None
    moved = False  # 이번 실행이 만든 결과 파일만 지운다 (원래 있던 파일은 절대 건드리지 않음)
    try:
        with tempfile.NamedTemporaryFile(dir=OUTPUT_DIR, suffix=".xlsx.tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        painted = write_workbook(tmp_path, built, summary)
        if painted != len(cell_changes):  # 색칠한 셀 = 셀 단위 변경 기록 (빠진 기록이 있으면 실패)
            raise IntegrationError(f"변경 기록 불일치: 칠한 셀 {painted}개, 기록 {len(cell_changes)}개")
        if target.exists():
            raise IntegrationError(f"같은 이름의 결과 파일이 이미 있습니다: {target.name}")
        os.replace(tmp_path, target)  # 같은 디렉터리 안 이동 (위에서 대상 없음 확인)
        tmp_path, moved = None, True
        os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        db.add_all(
            RunChange(run_id=run.id, **{k: v for k, v in asdict(ch).items() if k != "output_col"})
            for ch in built.changes
        )
        run.status = "done"
        run.output_path = target.relative_to(DATA_DIR).as_posix()
        run.output_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        run.change_count = len(built.changes)
        run.summary = json.dumps(summary, ensure_ascii=False, default=str)
        run.finished_at = datetime.now(KST)
        db.commit()
    except Exception as exc:
        db.rollback()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        if moved and target.exists():
            os.chmod(target, stat.S_IWUSR | stat.S_IRUSR)
            target.unlink(missing_ok=True)
        failed = db.get(IntegrationRun, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.error = str(exc)[:1000]
            failed.finished_at = datetime.now(KST)
            db.commit()
        logger.exception("integration run %s failed", run.id)
        raise IntegrationError(f"통합 결과를 만들지 못했습니다: {exc}") from exc
    logger.info("integration run=%s rows=%d changes=%d", run.id, run.row_count, run.change_count)
    return run
