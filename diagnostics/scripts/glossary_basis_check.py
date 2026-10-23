"""C-04 확인: 검토를 끝낸 뒤 기관·지역 용어사전이 바뀌면, 그 검토 결과로 통합되는지·결과의 기관명이 바뀌는지
(격리 서버, 새 계정, 합성 자료, 측정만 저장 — 판정하지 않음).

사례 (기관명·집행액 표, 기관명은 기관명으로 확정·집행액은 해당 없음):
- alias_added: '모아청'(용어사전에 없음)을 검토에서 '연결하지 않음'으로 거부하고 완료 → 관리자가 별칭 '모아청' → 모아시험청 추가
- entry_removed: '모아시험청'(용어사전과 일치)을 필수 기관으로 검사·완료 → 관리자가 그 항목 삭제
- unrelated: '모아시험청'으로 검사·완료 → 관리자가 이 자료에 없는 이름의 별칭 추가
변경 뒤 검토 조회(다시 검사 필요·이유)와 통합(상태·안내, 만들어지면 결과의 기관명·코드)을 적는다.
사용법: python glossary_basis_check.py <출력 폴더> <라벨> <새 계정 이름> <코드 앞자리 3자리(예: 991)>
"""

import io
import json
import secrets
import sys
from pathlib import Path

import openpyxl

from diag_common import Api

OUT, LABEL, NAME, PREFIX = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
OUT.mkdir(parents=True, exist_ok=True)
HEAD = ["기관명", "집행액"]
MAP = {"기관명": "org_name", "집행액": None}


def csv_bytes(rows: list[list[str]]) -> bytes:
    return "\r\n".join(",".join(r) for r in [HEAD, *rows]).encode("utf-8")


def main() -> None:
    owner = Api().login("owner")
    password = "Gb-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": NAME, "display_name": f"용어사전 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    u = Api()
    if u.call("POST", "/auth/login", {"username": NAME, "password": password})[0] != 200:
        raise RuntimeError("새 계정 로그인 실패")
    entry, alias, other = "모아시험청", "모아청", "다른시험청"
    code, other_code = f"{PREFIX}0001", f"{PREFIX}0002"
    text = f"코드,정식명칭,유형,시도코드,시군구코드,별칭\n{code},{entry},중앙행정기관,,,\n{other_code},{other},중앙행정기관,,,\n"
    boundary = secrets.token_hex(8)  # 용어사전 가져오기는 multipart 이름 'file' (Api.upload는 /files용 'files')
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="g.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    s, b, _ = owner.call("POST", "/glossary/import", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if s != 200 or not b.get("applied"):
        raise RuntimeError(f"용어사전 가져오기 실패: {s} {b}")

    def review(case: str, rows: list[list[str]], **params) -> dict:
        s, up, _ = u.upload(f"용어_{case}.csv", csv_bytes(rows), "text/csv")
        fid = up[0]["id"]
        cols = u.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]["columns"]
        decisions = [{"sheet_id": "s0", "column_id": c["column_id"], "field_key": MAP.get(c["name"])} for c in cols]
        if u.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": decisions})[0] != 200:
            raise RuntimeError(f"확정 실패 {case}")
        s, r, _ = u.call("POST", "/reviews", {"file_ids": [fid], "expected_period": "2026-09", **params})
        if s != 201:
            raise RuntimeError(f"검토 생성 실패 {case}: {s} {r}")
        return r

    def finish(r: dict, reject: str | None = None) -> dict:
        for i in r["items"]:
            if i["category"] == "pending" and i["kind"] == "entity" and i["before"] == reject:
                u.call("POST", f"/reviews/{r['id']}/items/{i['id']}/decision", {"decision": "rejected"})
        s, done, _ = u.call("POST", f"/reviews/{r['id']}/complete")
        return {"complete": s, "summary": done.get("summary") if isinstance(done, dict) else str(done)[:200],
                "items": sorted({(i["category"], i["kind"], i.get("before")) for i in r["items"]})}  # fmt: skip

    def after(r: dict) -> dict:
        got = u.call("GET", f"/reviews/{r['id']}")[1]
        s, run, _ = u.call("POST", f"/reviews/{r['id']}/integrate", {"include_unmapped": True})
        out = {"needs_recheck": got.get("needs_recheck"), "recheck_reasons": got.get("recheck_reasons"),
               "summary": got.get("summary"), "integrate": s}  # fmt: skip
        if s == 201:
            book = u.call("GET", f"/runs/{run['id']}/download", raw=True)[1]
            ws = openpyxl.load_workbook(io.BytesIO(book)).worksheets[0]
            rows = list(ws.iter_rows(values_only=True))
            head = list(rows[0])
            out["output"] = [{k: row[head.index(k)] for k in ("기관명", "기관명 코드") if k in head} for row in rows[1:]]
        else:
            out["detail"] = run.get("detail") if isinstance(run, dict) else str(run)[:300]
        return out

    rec: dict[str, dict] = {"glossary": {"entry": [code, entry], "other": [other_code, other]}}
    # 1) 별칭 추가
    r1 = review("alias_added", [[alias, "100"], [entry, "200"]])
    rec["alias_added"] = {"before": finish(r1, reject=alias)}
    s, a, _ = owner.call("POST", "/glossary/aliases", {"alias": alias, "target_code": code})
    rec["alias_added"]["glossary_change"] = {"alias": [alias, code], "status": s}
    rec["alias_added"]["after"] = after(r1)
    # 2) 항목 삭제 (별칭 사례 뒤 — 같은 항목)
    r2 = review("entry_removed", [[entry, "100"]], required_entities=[entry])
    rec["entry_removed"] = {"before": finish(r2)}
    # 3) 관련 없는 변경 (항목 삭제 전에 확인)
    r3 = review("unrelated", [[entry, "300"]])
    rec["unrelated"] = {"before": finish(r3)}
    s, a, _ = owner.call("POST", "/glossary/aliases", {"alias": "다른청", "target_code": other_code})
    rec["unrelated"]["glossary_change"] = {"alias": ["다른청", other_code], "status": s}
    rec["unrelated"]["after"] = after(r3)
    entries = owner.call("GET", f"/glossary/entries?q={code}")[1]
    entry_id = next(e["id"] for e in entries if e["code"] == code)
    s, _, _ = owner.call("DELETE", f"/glossary/entries/{entry_id}")
    rec["entry_removed"]["glossary_change"] = {"deleted": code, "status": s}
    rec["entry_removed"]["after"] = after(r2)
    for k, v in rec.items():
        print(k, json.dumps(v, ensure_ascii=False, default=str)[:900], flush=True)
    (OUT / f"glossary-basis-{LABEL}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
