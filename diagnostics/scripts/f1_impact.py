"""F-1 영향 조사 (읽기 전용) — 저장된 데이터 분석·묶기 통계 요약 결과 가운데, 가린 뒤 표시가 같아진 서로 다른 원문이
계산에 끼었을 수 있는 결과를 찾는다. 자동으로 고치거나 다시 계산하지 않는다.

판정 (결과마다, 그 결과가 쓴 시트 기준):
- 영향 없음 (가린 칸 없음): 가림으로 바뀐 칸·열 이름이 없다 → 가림이 계산에 끼어들 수 없다.
- 영향 없음 (충돌 없음): 바뀐 칸은 있지만 표시가 같은 서로 다른 원문이 없다.
- 영향 가능: 구분 열(지역·기관·조직·부서·차종·용도·연료)에 표시가 같은 서로 다른 원문이 있거나, 계산한 열 이름이
  가린 뒤 같아진다 → 수정판에서 같은 조건으로 다시 실행해 비교할 후보.
- 확인 불가: 원본 파일을 읽지 못함 (사유 기록).
구분 열은 결과에 저장된 확정(가린 이름)과 지금의 확정(원래 이름)을 함께 본다. 구분 열이 아닌 열의 충돌은 따로 센다.

읽기 전용: DB는 SQLite mode=ro로 열고, 원본은 파서로 읽기만 한다. 출력에는 원문·원문 해시 없이 번호·개수·판정만 남긴다.
앱의 파서·가림 함수(app.parsing·app.pii)를 쓰므로 backend 폴더(컨테이너는 /app)에서 실행한다. 데이터 위치는 MOAJWO_DATA_DIR.
  로컬 사본:     cd backend && PYTHONPATH=. MOAJWO_DATA_DIR=<사본 폴더> python ../diagnostics/scripts/f1_impact.py [--json <파일>]
  실행 중 컨테이너: docker exec -i -w /app <backend 컨테이너> python - < diagnostics/scripts/f1_impact.py
"""

import json
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from typing import Any

from app.config import DATA_DIR
from app.parsing import parse_file
from app.pii import mask_cell, mask_rrn_number, mask_text

# 분석이 같은 대상·그룹을 가리는 데 쓰는 항목 (analysis.IDENTITY_FIELDS — 판마다 다를 수 있어 여기 고정해 둔다)
IDENTITY = frozenset(
    {
        "sido",
        "sigungu",
        "region",
        "region_code",
        "org_name",
        "org_code",
        "organization_code",
        "organization_name",
        "department",
        "vehicle_type",
        "vehicle_use",
        "fuel_type",
    }
)


def _norm(value: Any) -> str:
    """분석의 비교 규칙과 같게 공백·전각 차이는 같은 값으로 본다."""
    return "".join(unicodedata.normalize("NFKC", str(value)).split())


class Files:
    """결과가 쓴 원본 파일을 한 번씩만 읽는다 (읽기만 함)."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.parsed: dict[int, Any] = {}

    def get(self, file_id: int) -> Any:
        if file_id not in self.parsed:
            row = self.con.execute("SELECT stored_path, original_name FROM source_files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                self.parsed[file_id] = "DB에 파일 기록 없음"
            else:
                try:
                    self.parsed[file_id] = parse_file(DATA_DIR / row[0], row[1])
                except Exception as exc:  # noqa: BLE001 — 조사는 계속하고 사유만 남긴다
                    self.parsed[file_id] = f"읽지 못함: {type(exc).__name__}"
        return self.parsed[file_id]

    def current_identity(self, file_id: int, sheet_name: str) -> set[str]:
        rows = self.con.execute(
            "SELECT column_name, field_key FROM mapping_decisions WHERE source_file_id = ? AND sheet_name = ?",
            (file_id, sheet_name),
        )
        return {c for c, k in rows if k in IDENTITY}


def sheet_facts(files: Files, file_id: int, sheet_id: str, stored_identity: set[str]) -> dict[str, Any]:
    res = files.get(file_id)
    out: dict[str, Any] = {"file_id": file_id, "sheet_id": sheet_id}
    if isinstance(res, str):
        return out | {"error": res}
    index = int(sheet_id[1:])
    if index >= len(res.sheets):
        return out | {"error": "시트 구성이 결과 때와 다름"}
    s = res.sheets[index]
    mask = mask_rrn_number if s.pii.counts.get("rrn") else mask_cell
    current = files.current_identity(file_id, s.name)
    identity = {j for j, c in enumerate(s.columns) if c in current or mask_text(c) in stored_identity}
    changed = 0
    shown: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in s.body:
        for j, cell in enumerate(r["cells"]):
            if cell is None or cell == "":
                continue
            m = mask(cell)
            changed += m != cell
            shown[j][str(m)].add(_norm(cell))
    clashes = {j: sum(len(raws) > 1 for raws in by.values()) for j, by in shown.items()}
    return out | {
        "changed_cells": changed,
        "changed_column_names": sum(mask_text(c) != c for c in s.columns),
        "identity_columns": sorted(identity),
        "identity_collisions": {f"c{j}": n for j, n in clashes.items() if n and j in identity},
        "other_collisions": {f"c{j}": n for j, n in clashes.items() if n and j not in identity},
    }


def name_collision(files: Files, targets: list[tuple[int, str, str]]) -> bool:
    """계산한 열들의 원래 이름이 서로 다른데 가린 이름이 같은가 (열 이름 구분이 가린 뒤 같아지는 경우의 상위 조건)."""
    by: dict[str, set[str]] = defaultdict(set)
    for file_id, sheet_id, col_id in targets:
        res = files.get(file_id)
        if isinstance(res, str) or not col_id:
            continue
        sheet = res.sheets[int(sheet_id[1:])] if int(sheet_id[1:]) < len(res.sheets) else None
        if sheet is None or int(col_id[1:]) >= len(sheet.columns):
            continue
        name = sheet.columns[int(col_id[1:])]
        by[mask_text(name)].add(_norm(name))
    return any(len(raws) > 1 for raws in by.values())


def verdict(sheets: list[dict[str, Any]], names_clash: bool) -> str:
    if any("error" in s for s in sheets):
        return "확인 불가"
    if names_clash or any(s["identity_collisions"] for s in sheets):
        return "영향 가능"
    if not any(s["changed_cells"] or s["changed_column_names"] for s in sheets):
        return "영향 없음 (가린 칸 없음)"
    return "영향 없음 (충돌 없음)"


def main() -> None:
    con = sqlite3.connect(f"file:{(DATA_DIR / 'moajwo.db').as_posix()}?mode=ro", uri=True)
    files = Files(con)
    report: dict[str, Any] = {"revision": con.execute("SELECT version_num FROM alembic_version").fetchone()[0]}
    analyses = []
    for rid, by, created, inputs in con.execute("SELECT id, created_by, created_at, inputs FROM analysis_runs ORDER BY id").fetchall():
        used = [t for t in json.loads(inputs) if t.get("status") == "ok" and t.get("sheet_id")]
        stored = {c for t in used for c, k in t.get("decisions") or [] if k in IDENTITY}
        sheets = [sheet_facts(files, t["file_id"], t["sheet_id"], stored) for t in used]
        clash = name_collision(files, [(t["file_id"], t["sheet_id"], t.get("column_id") or "") for t in used])
        analyses.append({"id": rid, "user": by, "created_at": created, "verdict": verdict(sheets, clash),
                         "column_name_collision": clash, "sheets": sheets})  # fmt: skip
    bundles = []
    for rid, uid, started, summary in con.execute("SELECT id, user_id, started_at, summary FROM runs ORDER BY id").fetchall():
        s = json.loads(summary or "{}")
        if s.get("mode") != "sheets" or not s.get("stats_summary"):
            continue
        sheets = [sheet_facts(files, o["file_id"], o["sheet_id"], set()) for o in s.get("sheets", []) if o.get("sheet_id")]
        bundles.append({"id": rid, "user": uid, "started_at": started, "verdict": verdict(sheets, False),
                        "items": [i.get("field_key") for i in s.get("summary_items", [])], "sheets": sheets})  # fmt: skip
    report |= {"analysis_runs": analyses, "bundle_summary_runs": bundles}
    count: dict[str, int] = defaultdict(int)
    for r in analyses + bundles:
        count[r["verdict"]] += 1
    report["counts"] = dict(count)
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(f"리비전 {report['revision']} · 분석 결과 {len(analyses)}건 · 통계 요약 묶기 {len(bundles)}건 · 판정 {dict(count)}")
    for r in analyses + bundles:
        kind = "분석" if r in analyses else "묶기"
        print(f"  {kind} #{r['id']} (사용자 {r['user']}): {r['verdict']}")


if __name__ == "__main__":
    main()
