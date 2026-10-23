"""A-02 확인: 자동화를 1회 등록한 뒤 새 파일로 2회 실행하고, 기간·새 열·매핑 변경 때 필요한 확인이 생기는지 (격리 서버, 새 계정, 합성 자료).
사용법: python a02_automation.py <출력 json>
"""

import json
import secrets
import sys
from pathlib import Path

from diag_common import XLSX, Api, xlsx

OUT = Path(sys.argv[1])
owner = Api().login("owner")
name, pw = "a02-" + secrets.token_hex(3), "Au-" + secrets.token_urlsafe(14) + "7z"
assert owner.call("POST", "/users", {"username": name, "display_name": "자동화 확인", "status": "active", "password": pw})[0] == 201
u = Api()
assert u.call("POST", "/auth/login", {"username": name, "password": pw})[0] == 200
rec: dict = {}


def upload(fname: str, period: str, extra_col: bool = False) -> int:
    head = ["시도", "인구", "세대수"] + (["비고"] if extra_col else [])
    rows = [[f"{period[:4]}년 {int(period[5:])}월 시도별 인구 (단위: 명)"], head]
    for i, sido in enumerate(["서울특별시", "부산광역시", "대구광역시"]):
        rows.append([sido, 100 + i, 40 + i] + (["메모"] if extra_col else []))
    return u.upload(fname, xlsx({"현황": rows}), XLSX)[1][0]["id"]


def summary(review: dict) -> dict:
    return {"status": review.get("status"), "summary": review.get("summary"),
            "pending": sorted({(i["kind"], i.get("column")) for i in review["items"] if i["category"] == "pending"}),
            "blocks": [i["kind"] for i in review["items"] if i["category"] == "block"]}  # fmt: skip


# 최초 설정: 업로드 → 매핑 확정 → 검토 → 완료 → 통합 → 자동화 저장
f1 = upload("자동화_9월.xlsx", "2026-09")
cols = u.call("GET", f"/files/{f1}/preview?sheet_id=s0")[1]["columns"]
keys = {"시도": "sido", "인구": "population", "세대수": "households"}
u.call("PUT", f"/files/{f1}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": c["column_id"], "field_key": keys.get(c["name"])} for c in cols]})
s, review, _ = u.call("POST", "/reviews", {"file_ids": [f1], "expected_period": "2026-09"})
for i in review["items"]:
    if i["category"] == "pending":
        u.call("POST", f"/reviews/{review['id']}/items/{i['id']}/decision", {"decision": "approved"})
assert u.call("POST", f"/reviews/{review['id']}/complete")[0] == 200
s, run, _ = u.call("POST", f"/reviews/{review['id']}/integrate", {"include_unmapped": False})
s, auto, _ = u.call("POST", f"/runs/{run['id']}/automation", {"name": "월별 인구"})
rec["setup"] = {"review": summary(review), "run_status": run.get("status"), "automation": auto.get("id"),
                "saved": {k: auto.get(k) for k in ("column_rules", "entity_rules", "conditions", "output_columns") if k in auto}}  # fmt: skip

# 반복 1: 같은 형식의 10월 파일 (기간을 10월로)
f2 = upload("자동화_10월.xlsx", "2026-10")
s, r2, _ = u.call("POST", f"/automations/{auto['id']}/run", {"file_ids": [f2], "expected_period": "2026-10"})
rec["run1_same_format"] = {"status": s, "run_status": r2.get("run", {}).get("status"), "counts": r2.get("counts"), "summary": r2.get("summary")}

# 반복 2: 새 열('비고')이 생긴 11월 파일
f3 = upload("자동화_11월.xlsx", "2026-11", extra_col=True)
s, r3, _ = u.call("POST", f"/automations/{auto['id']}/run", {"file_ids": [f3], "expected_period": "2026-11"})
rec["run2_new_column"] = {"status": s, "run_status": r3.get("run", {}).get("status"), "counts": r3.get("counts"), "summary": r3.get("summary")}

# 기간이 다른 자료: 12월로 실행하면서 파일은 11월 (기간 불일치)
f4 = upload("자동화_기간불일치.xlsx", "2026-11")
s, r4, _ = u.call("POST", f"/automations/{auto['id']}/run", {"file_ids": [f4], "expected_period": "2026-12"})
rec["run3_period_mismatch"] = {"status": s, "run_status": r4.get("run", {}).get("status"), "counts": r4.get("counts"), "summary": r4.get("summary")}

# 검토 상태로 남은 실행(반복 2)에서 매핑을 바꾸면 다시 검사가 필요한지
if r3.get("review_id"):
    rid = r3["review_id"]
    before = u.call("GET", f"/reviews/{rid}")[1]
    cols3 = u.call("GET", f"/files/{f3}/preview?sheet_id=s0")[1]["columns"]
    pop = next(c for c in cols3 if c["name"] == "세대수")
    u.call("PUT", f"/files/{f3}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": pop["column_id"], "field_key": "population"}]})
    after = u.call("GET", f"/reviews/{rid}")[1]
    rec["mapping_changed_during_review"] = {"before": before.get("needs_recheck"), "after": after.get("needs_recheck"),
                                            "reasons": after.get("recheck_reasons")}  # fmt: skip
runs = u.call("GET", "/runs")[1]
rec["runs"] = [{"id": r["id"], "status": r["status"], "trigger": r.get("trigger"), "automation": r.get("automation_id")} for r in runs]
print(json.dumps(rec, ensure_ascii=False, indent=1))
OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
