"""S-03 확인: 자원별 조회·변경 권한을 계정 종류마다 정상 API 요청으로 잰다
(격리 서버, 이 실행이 만든 새 계정·합성 자료만, 측정만 저장 — 판정은 보고서에서 정책과 대조).

계정: A(자료를 만든 일반 사용자), B(다른 일반 사용자), O(최고 관리자, 진단 시드), 익명, A의 로그아웃한 세션, 승인 대기 P.
A의 자원: 파일·매핑 확정·검토(완료)·통합 실행·데이터 통계 분석·자동화·묶기 실행.
변경 요청 전후로 DB 건수(읽기 전용)를 비교해 거절된 요청이 아무것도 바꾸지 않았는지 본다. 세션 값·비밀번호는 기록하지 않는다.
사용법: python authz_matrix.py <출력 폴더> <라벨>
"""

import json
import secrets
import sys
from pathlib import Path

import diag_common
from diag_common import XLSX, Api, xlsx

OUT, LABEL = Path(sys.argv[1]), sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
TABLES = ["source_files", "mapping_decisions", "review_sessions", "review_items", "runs", "run_changes", "analysis_runs",
          "automations", "auth_sessions", "users"]  # fmt: skip


def counts() -> dict[str, int]:
    return {t: diag_common.db(f"select count(*) from {t}")[0][0] for t in TABLES}


def account(owner: Api, name: str, status: str = "active") -> tuple[Api, str]:
    password = "Az-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": name, "display_name": name, "status": status, "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패 {name}: {s} {b}")
    api = Api()
    return api, password


def main() -> None:
    owner = Api().login("owner")
    a, pa = account(owner, f"authz-a-{LABEL}")
    b, pb = account(owner, f"authz-b-{LABEL}")
    p, pp = account(owner, f"authz-p-{LABEL}", "pending")
    rec: dict = {"setup": {}, "pending_login": None, "reads": {}, "writes": {}}
    assert a.call("POST", "/auth/login", {"username": f"authz-a-{LABEL}", "password": pa})[0] == 200
    assert b.call("POST", "/auth/login", {"username": f"authz-b-{LABEL}", "password": pb})[0] == 200
    s, body, _ = p.call("POST", "/auth/login", {"username": f"authz-p-{LABEL}", "password": pp})
    rec["pending_login"] = {"status": s, "detail": body.get("detail") if isinstance(body, dict) else None}

    # A의 자원 (합성 자료)
    rows = [["2026년 9월 시도별 인구 (단위: 명)"], ["시도", "인구"], ["서울특별시", 100], ["부산광역시", 200]]
    s, up, _ = a.upload(f"권한_{LABEL}.xlsx", xlsx({"현황": rows}), XLSX)
    fid = up[0]["id"]
    cols = a.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]["columns"]
    keys = {"시도": "sido", "인구": "population"}
    decisions = {"decisions": [{"sheet_id": "s0", "column_id": c["column_id"], "field_key": keys.get(c["name"])} for c in cols]}
    assert a.call("PUT", f"/files/{fid}/mapping/decisions", decisions)[0] == 200
    s, review, _ = a.call("POST", "/reviews", {"file_ids": [fid], "expected_period": "2026-09"})
    rid = review["id"]
    for item in review["items"]:
        if item["category"] == "pending":
            choice = (item.get("payload") or {}).get("candidates", [{}])[0].get("code") if item["kind"] == "entity" else None
            a.call("POST", f"/reviews/{rid}/items/{item['id']}/decision", {"decision": "approved" if choice else "rejected", "choice": choice})
    assert a.call("POST", f"/reviews/{rid}/complete")[0] == 200, "검토 완료 실패"
    s, run, _ = a.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
    run_id = run["id"]
    req = {"field_key": "population", "targets": [{"file_id": fid}]}
    pv = a.call("POST", "/analysis/preview", req)[1]
    s, an, _ = a.call("POST", "/analysis/run", {**req, "expected_fingerprint": pv["fingerprint"]})
    aid = an["id"]
    s, auto, _ = a.call("POST", f"/runs/{run_id}/automation", {"name": f"권한확인-{LABEL}"})
    auto_id = auto["id"]
    plan_req = {"file_ids": [fid]}
    s, plan, _ = a.call("POST", "/bundle/plan", plan_req)
    rec["setup"] = {"file": fid, "review": rid, "run": run_id, "analysis": aid, "automation": auto_id, "bundle_plan": s}

    # 로그아웃한 세션: A로 한 번 더 로그인해 그 세션 값을 쥔 뒤 로그아웃 — 값은 쓰기만 하고 기록하지 않는다
    a2 = Api()
    assert a2.call("POST", "/auth/login", {"username": f"authz-a-{LABEL}", "password": pa})[0] == 200
    token = next(c.value for c in a2.jar if c.name == "moajwo_session")
    assert a2.call("POST", "/auth/logout")[0] == 204
    stale = Api()
    stale_headers = {"Cookie": f"moajwo_session={token}"}

    actors = {"A": (a, None), "B": (b, None), "O": (owner, None), "anon": (Api(), None), "A_logged_out": (stale, stale_headers)}
    reads = [
        ("GET", f"/files/{fid}/preview"), ("GET", f"/files/{fid}/mapping/latest"), ("GET", f"/reviews/{rid}"),
        ("GET", f"/runs/{run_id}"), ("GET", f"/runs/{run_id}/changes"), ("GET", f"/runs/{run_id}/download"),
        ("GET", f"/analysis/runs/{aid}"), ("GET", f"/analysis/runs/{aid}/download"), ("GET", f"/automations/{auto_id}"),
    ]  # fmt: skip
    lists = {"/files": fid, "/runs": run_id, "/analysis/runs": aid, "/automations": auto_id, "/reviews": rid}
    for who, (api, headers) in actors.items():
        out = {}
        for method, path in reads:
            s, body, _ = api.call(method, path, headers=headers, raw=True)
            out[path] = s
        for path, own_id in lists.items():
            s, body, _ = api.call("GET", path, headers=headers)
            items = body.get("items", body) if isinstance(body, dict) else body
            ids = [x.get("id") for x in items] if isinstance(items, list) else None
            out[f"{path} (목록에 A의 것)"] = (s, None if ids is None else own_id in ids)
        rec["reads"][who] = out
        print("read", who, json.dumps(out, ensure_ascii=False)[:600], flush=True)

    writes = [
        ("PUT", f"/files/{fid}/mapping/decisions", decisions),
        ("POST", f"/files/{fid}/mapping?use_llm=false", None),
        ("PATCH", f"/reviews/{rid}", {"remember_format": True}),
        ("POST", f"/reviews/{rid}/recheck", {"replace": {}}),
        ("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True}),
        ("POST", "/reviews", {"file_ids": [fid], "expected_period": "2026-09"}),
        ("POST", "/analysis/preview", req),
        ("POST", f"/runs/{run_id}/automation", {"name": f"남이저장-{LABEL}"}),
        ("POST", f"/automations/{auto_id}/run", {"file_ids": [fid]}),
        ("POST", "/bundle/plan", plan_req),
        ("PUT", f"/runs/{run_id}", {}),
        ("DELETE", f"/runs/{run_id}", None),
        ("DELETE", f"/automations/{auto_id}", None),
    ]
    for who in ("anon", "A_logged_out", "B", "O"):  # A는 자기 자원이라 바꾸는 요청을 보내지 않는다. O는 마지막 (삭제 포함)
        api, headers = actors[who]
        out = {}
        for method, path, body in writes:
            before = counts()
            s, resp, _ = api.call(method, path, body, headers=headers)
            after = counts()
            changed = {t: [before[t], after[t]] for t in TABLES if before[t] != after[t]}
            detail = resp.get("detail") if isinstance(resp, dict) else None
            out[f"{method} {path}"] = {"status": s, "db_changed": changed, "detail": str(detail)[:120] if detail else None}
        rec["writes"][who] = out
        print("write", who, json.dumps({k: (v["status"], v["db_changed"]) for k, v in out.items()}, ensure_ascii=False)[:900], flush=True)
    (OUT / f"authz-{LABEL}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
