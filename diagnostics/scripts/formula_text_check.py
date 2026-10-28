"""S-03 확인: 원본의 글자 칸이 '='·'+'·'-'·'@'로 시작할 때, 결과 Excel(통합표·변경 내역·파일별 시트 묶기·통계 분석)에서
수식이 아니라 글자로 남는지 (격리 서버, 새 계정, 합성 자료, 측정만 저장 — 판정하지 않음).

링크 수식의 주소는 아무것도 듣지 않는 127.0.0.1:9 — 결과 파일을 열지 않고 openpyxl로 칸 형식만 본다.
사용법: python formula_text_check.py <출력 폴더> <라벨> <새 계정 이름>
"""

import io
import json
import secrets
import sys
from pathlib import Path

import openpyxl

from diag_common import Api

OUT, LABEL, NAME = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
TEXTS = ["=1+1", '=HYPERLINK("http://127.0.0.1:9/","x")', "+1+1", "-1+1", "@SUM(1,1)"]
CSV = "기관명,집행액,비고\r\n" + "\r\n".join(f'행안부,{1000 + i},"{t.replace(chr(34), chr(34) * 2)}"' for i, t in enumerate(TEXTS))


def cells(content: bytes) -> dict[str, list]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    found = {}
    for ws in wb.worksheets:
        hits = [(c.coordinate, c.data_type, str(c.value)[:40]) for r in ws.iter_rows() for c in r
                if isinstance(c.value, str) and c.value[:1] in "=+-@" or c.data_type == "f"]  # fmt: skip
        if hits:
            found[ws.title] = hits
    return found


def main() -> None:
    owner = Api().login("owner")
    password = "Ft-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": NAME, "display_name": f"수식 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    u = Api()
    if u.call("POST", "/auth/login", {"username": NAME, "password": password})[0] != 200:
        raise RuntimeError("새 계정 로그인 실패")
    s, body, _ = u.upload("수식글자.csv", CSV.encode("utf-8-sig"), "text/csv")
    fid = body[0]["id"]
    preview = u.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]
    rec: dict = {"file": fid, "preview_values": [r for r in preview.get("rows", [])][:5]}
    # 통합표: 검토 → 확인 항목 처리 → 완료 → 통합 (표준 항목이 없는 열도 원래 이름으로 넣는다)
    s, review, _ = u.call("POST", "/reviews", {"file_ids": [fid]})
    for item in [i for i in review["items"] if i["category"] == "pending"]:
        cands = item["payload"].get("candidates") or []
        body = {"decision": "approved", "choice": cands[0].get("code") or cands[0].get("key")} if cands else {"decision": "rejected"}
        u.call("POST", f"/reviews/{review['id']}/items/{item['id']}/decision", body)
    s, done, _ = u.call("POST", f"/reviews/{review['id']}/complete")
    rec["review_complete"] = s
    s, run, _ = u.call("POST", f"/reviews/{review['id']}/integrate", {"include_unmapped": True})
    rec["integrate"] = s
    if s == 201:
        content = u.call("GET", f"/runs/{run['id']}/download", raw=True)[1]
        rec["integration_cells"] = cells(content)
    # 파일별 시트로 묶기
    s, plan, _ = u.call("POST", "/bundle/plan", {"file_ids": [fid]})
    s, run, _ = u.call("POST", "/bundle/run", {"file_ids": [fid], "expected_basis": plan["basis"]})
    rec["bundle"] = s
    if s == 201:
        rec["bundle_cells"] = cells(u.call("GET", f"/runs/{run['id']}/download", raw=True)[1])
    print(json.dumps(rec, ensure_ascii=False, default=str)[:3000])
    (OUT / f"formula-text-{LABEL}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
