"""S-01 조사 (읽기 전용) — 저장된 자료에 지금의 개인정보 형식 규칙(app.pii: 이메일·휴대전화·주민등록번호)에 걸리는 값이
어디에 얼마나 남아 있는지. 자동으로 고치거나 다시 탐지하지 않는다.

조사 범위 (각각 따로 센다):
1. 업로드 탐지 결과: 저장된 탐지 건수(source_files.pii_summary)와, 지금 규칙으로 원본을 다시 읽은 건수 비교
   (업로드 때와 같은 범위 — 설명 시트를 뺀 시트). 포함 확인 여부를 함께 적는다.
2. 설명 시트: 업로드 탐지가 보지 않는 시트(이름이 '설명·안내·주석' 등)의 개인정보 형식 칸 수
3. 학습·사전: 학습 별칭, 용어사전 별칭(출처별), 전사 표준, 승격 후보, 매핑 확정·승인의 열·시트 이름
4. 기록·결과 DB: 검토 항목, 변경 기록, 실행 요약, 분석 결과, 검증 결과, 자동화 규칙, 계정 이력·사용자
5. 결과 파일(outputs): 통합·묶기 결과 엑셀의 개인정보 형식 칸 수 (정책상 포함을 확인한 원본 값이 들어갈 수 있음)

출력: 번호·종류·건수·판정 근거만. 원문과 원문 해시는 남기지 않는다. 숫자 13자리처럼 형식만 같은 값도 세므로 '형식 일치'다.
DB는 SQLite mode=ro로 열고 원본·결과 파일은 읽기만 한다. 로그는 이 자료(백업)에 없어 범위 밖이다.
사용법 (backend 폴더에서): PYTHONPATH=. python ../diagnostics/scripts/s01_residual_survey.py <데이터 폴더> <출력 json>
"""

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl
from app import parsing
from app.pii import PiiReport, find_in_text, scan_grid

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
KEEP_IDS = 20  # 표마다 적는 번호 수 (전체 건수는 따로)

TEXT_COLUMNS = {
    "learned_aliases": ["alias"],
    "glossary_aliases": ["alias", "note"],
    "standard_terms": ["term"],
    "promotion_candidates": ["column_name", "review_note"],
    "mapping_decisions": ["sheet_name", "column_name"],
    "mapping_approvals": ["column_name"],
    "mapping_analyses": ["result"],
    "review_items": ["file_name", "before", "after", "reason", "payload"],
    "review_sessions": ["params"],
    "run_changes": ["file_name", "before", "after", "reason"],
    "runs": ["summary"],
    "analysis_runs": ["summary", "conditions", "result"],
    "validation_runs": ["params", "result"],
    "automations": ["name", "column_rules", "entity_rules"],
    "account_events": ["actor_name", "target_name", "reason"],
    "users": ["username", "display_name", "status_reason"],
    "source_files": ["original_name"],
}


def kinds(text: object) -> Counter:
    return Counter(kind for kind, _ in find_in_text(str(text))) if text is not None else Counter()


def survey_tables(con: sqlite3.Connection) -> dict:
    out = {}
    for table, cols in TEXT_COLUMNS.items():
        have = {r[1] for r in con.execute(f"pragma table_info({table})")}
        extra = ", source" if table == "glossary_aliases" else ""
        for col in cols:
            if col not in have:
                continue
            per_kind: Counter = Counter()
            ids: list[int] = []
            by_source: Counter = Counter()
            rows = 0
            for row in con.execute(f"select id, {col}{extra} from {table}"):
                rows += 1
                k = kinds(row[1])
                if k:
                    per_kind.update(k)
                    if len(ids) < KEEP_IDS:
                        ids.append(row[0])
                    if extra:
                        by_source[row[2]] += 1
            if per_kind:
                out[f"{table}.{col}"] = {"rows": rows, "matches": dict(per_kind), "first_ids": ids,
                                         **({"by_source": dict(by_source)} if extra else {})}  # fmt: skip
    return out


def read_all(path: Path, name: str) -> tuple[dict, dict]:
    ext = path.suffix.lower()
    if ext == ".xlsx":
        return parsing._read_xlsx(path)
    if ext == ".xls":
        return parsing._read_xls(path)
    sheets, _ = parsing._read_csv(path, Path(name).stem)
    return sheets, {}


def survey_uploads(con: sqlite3.Connection) -> dict:
    files = []
    for fid, name, stored, summary, ack in con.execute(
        "select id, original_name, stored_path, pii_summary, pii_acknowledged_at from source_files order by id"
    ):
        rec = {"file": fid, "ext": Path(name).suffix.lower(), "acknowledged": ack is not None}
        stored_counts = (json.loads(summary) or {}).get("counts", {}) if summary else None
        rec["stored"] = {k: v for k, v in (stored_counts or {}).items() if v} if stored_counts is not None else None
        try:
            sheets, filled = read_all(DATA / stored, name)
        except Exception as exc:  # noqa: BLE001 — 읽지 못한 파일은 사유만 적는다
            rec["error"] = type(exc).__name__
            files.append(rec)
            continue
        names = list(sheets)
        skipped = [n for n in names if parsing._INFO_SHEET.search(n)]
        if len(skipped) == len(names):
            skipped = []
        now = PiiReport()
        for n in names:
            if n not in skipped:
                now.merge(parsing.analyze_sheet(n, sheets[n], filled.get(n)).pii)
        info = PiiReport()
        for n in skipped:
            scan_grid(n, sheets[n], info)
        rec["now"] = {k: v for k, v in now.counts.items() if v}
        rec["info_sheets"] = len(skipped)
        rec["info_sheet_matches"] = {k: v for k, v in info.counts.items() if v}
        files.append(rec)
        print("file", fid, json.dumps(rec, ensure_ascii=False), flush=True)
    newly = [f["file"] for f in files if f.get("now") and f.get("now") != (f.get("stored") or {})]
    return {
        "files": files,
        "summary": {
            "files": len(files),
            "unreadable": [f["file"] for f in files if "error" in f],
            "stored_detected": [f["file"] for f in files if f.get("stored")],
            "now_detected": [f["file"] for f in files if f.get("now")],
            "differs_from_stored": newly,
            "info_sheet_matches": [f["file"] for f in files if f.get("info_sheet_matches")],
            "acknowledged": [f["file"] for f in files if f["acknowledged"]],
        },
    }


def survey_outputs(con: sqlite3.Connection) -> dict:
    out = {}
    runs = {Path(p).name: rid for rid, p in con.execute("select id, output_path from runs") if p}
    for path in sorted((DATA / "outputs").glob("*.xlsx")):
        per_kind: Counter = Counter()
        sheets_hit: dict[str, int] = defaultdict(int)
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for cell in row:
                        if isinstance(cell, str):
                            k = kinds(cell)
                            if k:
                                per_kind.update(k)
                                sheets_hit[ws.title] += 1
        finally:
            wb.close()
        if per_kind:
            out[f"run {runs.get(path.name, '?')}"] = {"matches": dict(per_kind), "sheets": dict(sheets_hit)}
    return {"output_files": len(list((DATA / "outputs").glob("*.xlsx"))), "with_matches": out}


def main() -> None:
    con = sqlite3.connect(f"file:{(DATA / 'moajwo.db').as_posix()}?mode=ro", uri=True)
    result = {
        "revision": con.execute("select version_num from alembic_version").fetchone()[0],
        "tables": survey_tables(con),
        "uploads": survey_uploads(con),
        "outputs": survey_outputs(con),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"tables": list(result["tables"]), "uploads": result["uploads"]["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
