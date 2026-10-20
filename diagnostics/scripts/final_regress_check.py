"""최종 main 종합 회귀 — 격리 진단 스택에서 API와 실제 브라우저로 업무 흐름·특수 경로·접근 통제를 확인한다 (합성 자료·전용 계정만).

- 검사마다 목록 ID(REGRESSION-20261020.md)·기대·실제를 남긴다. 미리 정한 기대값과 비교하고, 확인할 수 없으면 통과로 세지 않는다(ok=None).
- 모델(Ollama) 호출은 진단 스택에서 막혀 있다 — 매핑 분석은 use_llm=false로 부르고, 모델이 필요한 확인은 하지 않는다.
- 진단 DB에 쓰는 것은 A2(세션 만료) 한 곳뿐이다: 이 검사에서 새로 로그인한 user3 세션 한 줄의 만료 시각만 과거로 바꾼다.
- 접근 통제는 합성 계정의 정상 API 요청만 쓴다 (비밀번호 대입·부하·운영 요청 없음).
사용법: python final_regress_check.py <출력 폴더> <라벨>   환경: ONLY_PARTS=api,browser (비우면 전부)
"""

import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import openpyxl
from playwright.sync_api import Page, sync_playwright

from diag_common import ASSET_JS, BACKEND, URL, USERS, XLSX, Api, creds, db, run_meta, xlsx
from diag_scope import browser_proxy, route_guard

OUT, LABEL = Path(sys.argv[1]), sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
RES: list[dict] = []
BLOCKED: list[str] = []
ERRORS: list[dict] = []
TITLE = "주민등록 인구 현황 2026년 9월 (단위: 명)"
TAG = "회귀7_"  # 이번에 만든 합성 자료 이름 앞머리
XLS_FIXTURE = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "ids.xls"
CTX: dict = {}  # API 단계에서 만든 id (화면·접근 통제 단계가 씀)


def check(cid: str, what: str, ok, evidence="") -> None:
    RES.append({"id": cid, "what": what, "ok": ok, "evidence": str(evidence)[:600]})
    mark = {True: "통과", False: "실패", None: "확인 불가"}[ok]
    print(f"   {cid} {mark} | {what} | {str(evidence)[:200]}", flush=True)


def must(res, what: str, ok=(200, 201)):
    s, b, _ = res
    if s not in ok:
        raise RuntimeError(f"{what}: {s} {str(b)[:300]}")
    return b


def upload(api: Api, name: str, content: bytes, ctype: str = XLSX) -> dict:
    return must(api.upload(name, content, ctype), f"업로드 {name}")[0]


def cols_of(api: Api, fid: int, sheet_id: str = "s0") -> dict[str, str]:
    pv = must(api.call("GET", f"/files/{fid}/preview?sheet_id={sheet_id}"), f"미리보기 {fid}")
    return {x["name"]: x["column_id"] for x in pv["columns"]}


def confirm(api: Api, fid: int, mapping: dict[str, str | None], sheet_id: str = "s0"):
    cols = cols_of(api, fid, sheet_id)
    items = [{"sheet_id": sheet_id, "column_id": cols[n], "field_key": k} for n, k in mapping.items()]
    return api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": items})


def outputs() -> list[str]:
    p = subprocess.run(["docker", "exec", BACKEND, "sh", "-c", "ls /app/data/outputs"], capture_output=True, text=True, encoding="utf-8")
    return sorted(p.stdout.split())


def counts() -> dict:
    r = db("select (select count(*) from runs), (select count(*) from review_sessions), (select count(*) from analysis_runs), (select count(*) from source_files)")[0]
    return {"runs": r[0], "reviews": r[1], "analysis": r[2], "files": r[3], "outputs": len(outputs())}


def stored_sha(fid: int) -> tuple[str, str]:
    path, sha = db(f"select stored_path, sha256 from source_files where id = {int(fid)}")[0]
    full = path if path.startswith("/") else f"/app/data/{path}"  # 저장 경로는 데이터 폴더 기준 상대 경로
    p = subprocess.run(["docker", "exec", BACKEND, "sha256sum", full], capture_output=True, text=True, encoding="utf-8")
    return p.stdout.split()[0] if p.returncode == 0 else f"(읽기 실패 {p.stderr[:80]})", sha


def sheets_of(content: bytes) -> dict[str, list[list]]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    return {ws.title: [[(c.value, c.data_type) for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def review(api: Api, fids: list[int], period: str = "2026-09"):
    s, rv, _ = api.call("POST", "/reviews", {"file_ids": fids, "expected_period": period})
    if s not in (200, 201) and "개인정보" in str(rv):
        for f in fids:
            api.call("POST", f"/files/{f}/pii/acknowledge", {})
        s, rv, _ = api.call("POST", "/reviews", {"file_ids": fids, "expected_period": period})
    return s, rv


# ---------------------------------------------------------------- API: 업로드·매핑·검토·통합·분석·묶기·가림


def part_upload(u: Api) -> None:
    csv = "﻿기관코드,시도,인구수,증감,비율\n00123,서울특별시,1000,-12,3.75\n01234567890123456789,부산광역시,,5,0.5\n".encode()
    up = upload(u, f"{TAG}선행0.csv", csv, "text/csv")
    cells = [r["cells"] for r in must(u.call("GET", f"/files/{up['id']}/preview?sheet_id=s0"), "미리보기")["rows"]]
    check("U1", "CSV(UTF-8 BOM) 업로드: 인코딩·열·한글", up["encoding"] == "utf-8-sig" and up["sheets"][0]["columns"] == ["기관코드", "시도", "인구수", "증감", "비율"]
          and cells[0][1] == "서울특별시", (up["encoding"], up["sheets"][0]["columns"]))  # fmt: skip
    check("U4", "선행 0 `00123`·20자리 식별자는 글자 그대로", cells[0][0] == "00123" and cells[1][0] == "01234567890123456789", [c[0] for c in cells])
    check("U5", "빈칸은 빈칸(null, 0 아님), -12·3.75·0.5는 숫자", cells[1][2] is None and cells[0][3] == -12 and cells[0][4] == 3.75 and cells[1][4] == 0.5,
          [c[2:] for c in cells])  # fmt: skip
    cp = upload(u, f"{TAG}cp949.csv", "시도,인구수\n대전광역시,77\n".encode("cp949"), "text/csv")
    c2 = must(u.call("GET", f"/files/{cp['id']}/preview?sheet_id=s0"), "미리보기")["rows"][0]["cells"]
    check("U1", "CSV(CP949) 업로드: 한글 그대로", c2 == ["대전광역시", 77] and cp["encoding"] not in ("utf-8", "utf-8-sig"), (cp["encoding"], c2))
    multi = upload(u, f"{TAG}여러시트.xlsx", xlsx({"가시트": [[TITLE], ["시도", "인구수"], ["서울특별시", 10]], "나시트": [[TITLE], ["시도", "인구수"], ["부산광역시", 20]]}))
    check("U2", "XLSX 여러 시트: 시트 2개 각각 읽힘", multi["sheet_count"] == 2 and [s["name"] for s in multi["sheets"]] == ["가시트", "나시트"]
          and all(s["data_rows"] == 1 for s in multi["sheets"]), [(s["name"], s["data_rows"]) for s in multi["sheets"]])  # fmt: skip
    CTX["multi"] = multi["id"]
    xls = upload(u, f"{TAG}고정파일.xls", XLS_FIXTURE.read_bytes(), "application/vnd.ms-excel")
    check("U3", "XLS(시험 고정 파일 ids.xls) 업로드: 오류 없이 읽힘", xls["error"] is None and xls["sheet_count"] >= 1 and xls["data_rows"] > 0,
          (xls["sheet_count"], xls["data_rows"], xls["error"]))  # fmt: skip
    CTX["csv"] = up["id"]


def part_mapping(u: Api) -> None:
    f = upload(u, f"{TAG}매핑.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "인구수", "주민수", "메모"], ["서울특별시", 100, 100, "가"], ["부산광역시", 200, 200, "나"]]}))
    fid = f["id"]
    mp = must(u.call("POST", f"/files/{fid}/mapping?use_llm=false"), "매핑 분석")
    col = {c["column"]: c for c in mp["sheets"][0]["columns"]}
    check("M1", "자동 제안(사전·유사도, 모델 호출 없음): 시도→sido, 인구수→population", mp["llm"] == "skipped" and col["시도"]["field_key"] == "sido"
          and col["인구수"]["field_key"] == "population", {k: (v["field_key"], v["status"], v["method"]) for k, v in col.items()} | {"llm": mp["llm"]})  # fmt: skip
    must(confirm(u, fid, {"시도": "sido", "인구수": "population", "메모": None}), "확정")
    latest = must(u.call("GET", f"/files/{fid}/mapping/latest"), "최신 매핑")
    lc = {c["column"]: c for c in latest["sheets"][0]["columns"]}
    check("M2", "수동 확정 → 조회 값 일치", lc["시도"]["field_key"] == "sido" and lc["인구수"]["field_key"] == "population" and lc["시도"]["status"] == "confirmed",
          {k: (v["field_key"], v["status"]) for k, v in lc.items()})  # fmt: skip
    check("M3", "'해당 없음' 확정 → 확정 상태, 항목 없음", lc["메모"]["status"] == "confirmed" and lc["메모"]["field_key"] is None, (lc["메모"]["field_key"], lc["메모"]["status"]))
    must(u.call("POST", f"/files/{fid}/mapping?use_llm=false"), "다시 분석")
    again = {c["column"]: c for c in must(u.call("GET", f"/files/{fid}/mapping/latest"), "최신 매핑")["sheets"][0]["columns"]}
    check("M5", "다시 분석해도 확정 유지", all(again[k]["status"] == "confirmed" and again[k]["field_key"] == lc[k]["field_key"] for k in ("시도", "인구수", "메모")),
          {k: (v["field_key"], v["status"]) for k, v in again.items()})  # fmt: skip
    # M4: 일괄 확정 — 미리보기 뒤 한 파일이 바뀌면 적용은 전부 거절
    a = upload(u, f"{TAG}일괄A.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "주민수"], ["서울특별시", 1]]}))["id"]
    b = upload(u, f"{TAG}일괄B.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "주민수"], ["부산광역시", 2]]}))["id"]
    ca, cb = cols_of(u, a)["주민수"], cols_of(u, b)["주민수"]
    items = [{"file_id": a, "sheet_id": "s0", "column_id": ca, "field_key": "population"}, {"file_id": b, "sheet_id": "s0", "column_id": cb, "field_key": "population"}]
    pv = must(u.call("POST", "/mapping/batch/preview", {"items": items}), "일괄 미리보기")
    seen = {(x["file_id"], x["column_id"]): x for x in pv["items"]}
    must(confirm(u, a, {"주민수": "households"}), "중간 변경")  # 미리보기와 적용 사이에 A가 바뀜
    apply_items = [it | {"expected_confirmed": seen[(it["file_id"], it["column_id"])]["current_confirmed"],
                         "expected_field_key": seen[(it["file_id"], it["column_id"])]["current_field_key"]} for it in items]  # fmt: skip
    s, body, _ = u.call("POST", "/mapping/batch/apply", {"items": apply_items})
    lb = {c["column_id"]: c for c in must(u.call("GET", f"/files/{b}/mapping/latest"), "B 매핑")["sheets"][0]["columns"]}.get(cb, {})
    check("M4", "일괄 확정: 미리보기 뒤 A가 바뀌면 적용 409, B도 반영 안 됨(일부만 반영 없음)", s == 409 and not (lb.get("status") == "confirmed" and lb.get("field_key") == "population"),
          (s, str(body)[:160], lb.get("status"), lb.get("field_key")))  # fmt: skip


def part_review(u: Api) -> None:
    def up(name, rows):
        return upload(u, name, xlsx({"Sheet1": rows}))["id"]

    before = counts()
    bad = up(f"{TAG}합계오류.xlsx", [[TITLE], ["시도", "인구수"], ["전국", 999], ["서울특별시", 100], ["부산광역시", 200]])
    must(confirm(u, bad, {"시도": "sido", "인구수": "population"}), "확정")
    s, rv = review(u, [bad])
    sc, _, _ = u.call("POST", f"/reviews/{rv['id']}/complete")
    si, _, _ = u.call("POST", f"/reviews/{rv['id']}/integrate", {})
    check("V1", "합계 오류 → 차단 항목, 완료·통합 거절", s == 201 and rv["counts"]["block"] >= 1 and not rv["can_complete"] and sc == 409 and si == 409,
          (rv["counts"], [i["kind"] for i in rv["items"] if i["category"] == "block"], sc, si))  # fmt: skip
    aug = up(f"{TAG}8월자료.xlsx", [["주민등록 인구 현황 2026년 8월 (단위: 명)"], ["시도", "인구수"], ["서울특별시", 100], ["부산광역시", 200]])
    must(confirm(u, aug, {"시도": "sido", "인구수": "population"}), "확정")
    s, rv2 = review(u, [aug], "2026-09")
    kinds = [(i["category"], i["kind"]) for i in rv2["items"] if i["category"] in ("block", "pending")]
    check("V2", "기간 충돌(제목 2026년 8월, 기대 2026-09) → 차단 또는 확인 항목", s == 201 and any("period" in k for _, k in kinds) and not rv2["can_complete"], kinds)
    # V3·V4·S1·I1~I3: 확인 항목 승인 → 다시 검사 필요 → 다시 검사 → 완료 → 통합
    ok = up(f"{TAG}정상.xlsx", [[TITLE], ["시도", "인구수"], ["서울특별시", 100], ["강원특별자치", 200]])  # 오타 지역명 → 지역 연결 확인 항목
    must(confirm(u, ok, {"시도": "sido", "인구수": "population"}), "확정")
    sha_before = stored_sha(ok)
    s, r1 = review(u, [ok])
    pend = [i for i in r1["items"] if i["category"] == "pending" and i["can_change"] and i["decision"] is None]
    target = next((i for i in pend if (i.get("payload") or {}).get("candidates")), None)
    if target is None:
        check("V3", "확인 항목 승인 → 다시 검사 → 완료 가능", None, f"후보가 있는 확인 항목이 없음: {[(i['category'], i['kind']) for i in r1['items']]}")
        return
    c0 = target["payload"]["candidates"][0]
    choice = c0.get("key") or c0.get("code")  # 표준 항목 key 또는 7자리 지역·기관 코드
    sd, _, _ = u.call("POST", f"/reviews/{r1['id']}/items/{target['id']}/decision", {"decision": "approved", "choice": choice})
    after = must(u.call("GET", f"/reviews/{r1['id']}"), "검토")
    sr, r2, _ = u.call("POST", f"/reviews/{r1['id']}/recheck", {})
    carried = [i for i in r2.get("items", []) if i["kind"] == target["kind"]] if isinstance(r2, dict) else []
    check("V3", "확인 항목(지역 연결) 승인 → 다시 검사 → 새 검토에서 승인·선택 유지, 남은 확인 0, 완료 가능", sd == 200 and sr == 201 and r2["counts"]["pending_open"] == 0
          and r2["can_complete"] and any(i["decision"] == "approved" and i.get("choice") == choice for i in carried), {"kind": target["kind"], "choice": choice, "needs_recheck_after_approve": after["needs_recheck"], "recheck": sr,
                                   "new_counts": r2.get("counts"), "carried": [(i["category"], i["decision"], i.get("choice")) for i in carried]})  # fmt: skip
    old = must(u.call("GET", f"/reviews/{r1['id']}"), "이전 검토")
    so, _, _ = u.call("POST", f"/reviews/{r1['id']}/complete")
    check("V4", "다시 검사 → 새 검토, 이전 검토는 최신 검토를 가리키고 완료되지 않음", sr == 201 and r2["parent_id"] == r1["id"] and old["latest_id"] == r2["id"] and so != 200,
          (sr, r2.get("parent_id"), old["child_id"], old["latest_id"], so))  # fmt: skip
    # S1: 검사 뒤 확정 변경 → 완료 거절 (되돌리면 검사 입력이 같아져 다시 가능)
    must(confirm(u, ok, {"인구수": "households"}), "검사 뒤 확정 변경")
    stale = must(u.call("GET", f"/reviews/{r2['id']}"), "검토")
    s1, b1, _ = u.call("POST", f"/reviews/{r2['id']}/complete")
    check("S1", "검사 뒤 확정을 바꾸면 다시 검사 필요, 완료 409", stale["needs_recheck"] and s1 == 409, (stale["recheck_reasons"], s1, str(b1)[:120]))
    must(confirm(u, ok, {"인구수": "population"}), "확정 되돌림")
    pre_int = counts()
    si, _, _ = u.call("POST", f"/reviews/{r2['id']}/integrate", {})
    mid = counts()
    check("I2", "완료 전 검토로 통합 → 409, 실행 기록·결과 파일 그대로", si == 409 and mid["runs"] == pre_int["runs"] and mid["outputs"] == pre_int["outputs"], (si, pre_int, mid))
    sd2, done, _ = u.call("POST", f"/reviews/{r2['id']}/complete")
    sg, run, _ = u.call("POST", f"/reviews/{r2['id']}/integrate", {})
    if sg != 201:
        check("I1", "정상 통합", False, (sd2, sg, str(run)[:200]))
        return
    rinfo = must(u.call("GET", f"/runs/{run['id']}"), "실행 기록")
    s, content, h = u.call("GET", f"/runs/{run['id']}/download", raw=True)
    sh = sheets_of(content) if s == 200 else {}
    check("I1", "정상 통합 → 실행 기록(완료)·내려받기 시트 3개", rinfo["status"] == "done" and list(sh) == ["통합 데이터", "변경 내역", "처리 요약"],
          (rinfo["status"], list(sh), (h.get("content-disposition") or h.get("Content-Disposition") or "")[:80]))  # fmt: skip
    data = sh.get("통합 데이터", [])
    nums = [r for r in data if any(v == 100 for v, _ in r)]
    check("R1", "통합 결과: 숫자는 숫자 셀(n), 지역명은 글자 셀(s)", bool(nums) and any(t == "n" for v, t in nums[0] if v == 100) and any(t == "s" for v, t in nums[0] if v == "서울특별시"),
          nums[:1])  # fmt: skip
    sha_after = stored_sha(ok)
    check("I3", "통합 뒤 원본 파일 해시 = 업로드 때 기록한 해시 (원문 보존)", sha_before[0] == sha_before[1] == sha_after[0] == sha_after[1], (sha_before[0][:16], sha_after[0][:16]))
    CTX.update({"review": r2["id"], "old_review": r1["id"], "run": run["id"], "ok_file": ok})
    # X2: 차단된 완료·통합이 실행 기록·결과 파일을 만들지 않음 (V1의 합계 오류 검토 포함)
    end = counts()
    check("X2", "차단된 완료·통합 요청 뒤 실행 기록·결과 파일은 정상 통합 1건만 늘어남", end["runs"] == before["runs"] + 1 and end["outputs"] == before["outputs"] + 1, (before, end))


def part_analysis(u: Api) -> None:
    f = upload(u, f"{TAG}분석.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "인구수"], ["서울특별시", 100], ["부산광역시", 200], ["대구광역시", 300], ["인천광역시", 400]]}))["id"]
    must(confirm(u, f, {"시도": "sido", "인구수": "population"}), "확정")
    body = {"field_key": "population", "targets": [{"file_id": f}], "group_by": ["sido"],
            "bins": {"on": "sum", "ranges": [{"label": "작은", "max": 250}, {"label": "큰", "min": 250, "min_inclusive": False}]}}  # fmt: skip
    pv = must(u.call("POST", "/analysis/preview", body), "분석 확인")
    run = must(u.call("POST", "/analysis/run", body | {"expected_fingerprint": pv["fingerprint"]}), "분석 실행")
    res = must(u.call("GET", f"/analysis/runs/{run['id']}"), "분석 결과")
    groups = must(u.call("GET", f"/analysis/runs/{run['id']}/groups?offset=0&limit=50"), "그룹")["items"]
    got = {g["keys"][0]: (g["sum"], g["mean"], g["min"], g["max"], g["count"], g.get("bin")) for g in groups}
    want = {"서울특별시": (100, 100, 100, 100, 1, "작은"), "부산광역시": (200, 200, 200, 200, 1, "작은"), "대구광역시": (300, 300, 300, 300, 1, "큰"), "인천광역시": (400, 400, 400, 400, 1, "큰")}
    check("AN1", "그룹별 합계·평균·최소·최대·개수·구간 = 기대값", got == want, got)
    s, content, _ = u.call("GET", f"/analysis/runs/{run['id']}/download", raw=True)
    sh = sheets_of(content)
    rows = sh.get("결과", [])
    cell = {r[0][0]: r for r in rows if r and r[0][0] in want}
    check("R1", "분석 Excel: 시트 4개 순서, 결과 값은 숫자 셀, API와 같은 값", list(sh) == ["결과", "조건", "제외", "근거 행"] and len(cell) == 4
          and all(any(v == want[k][0] and t == "n" for v, t in r) for k, r in cell.items()), (list(sh), {k: [v for v, _ in r][:6] for k, r in cell.items()}))  # fmt: skip
    created = res.get("created_at", "")
    check("R1", "분석 생성 시각은 KST(+09:00)", created.endswith("+09:00"), created)
    total = must(u.call("POST", "/analysis/preview", {"field_key": "population", "targets": [{"file_id": f}], "aggregates": ["sum", "mean", "min", "max", "count"]}), "전체 확인")
    run2 = must(u.call("POST", "/analysis/run", {"field_key": "population", "targets": [{"file_id": f}], "expected_fingerprint": total["fingerprint"]}), "전체 실행")
    g2 = must(u.call("GET", f"/analysis/runs/{run2['id']}/groups?offset=0&limit=5"), "전체")["items"]
    check("AN1", "그룹 없이 전체: 합계 1000·평균 250·최소 100·최대 400·개수 4", [(g["sum"], g["mean"], g["min"], g["max"], g["count"]) for g in g2] == [(1000, 250, 100, 400, 4)],
          [(g["sum"], g["mean"], g["min"], g["max"], g["count"]) for g in g2])  # fmt: skip
    dup_bytes = xlsx({"Sheet1": [[TITLE], ["시도", "인구수"], ["광주광역시", 5], ["울산광역시", 6]]})
    d1, d2 = (upload(u, f"{TAG}중복{i}.xlsx", dup_bytes)["id"] for i in (1, 2))
    for d in (d1, d2):
        must(confirm(u, d, {"시도": "sido", "인구수": "population"}), "확정")
    dbody = {"field_key": "population", "targets": [{"file_id": d1}, {"file_id": d2}]}
    s, dp, _ = u.call("POST", "/analysis/preview", dbody)
    probs = json.dumps(dp.get("problems") if isinstance(dp, dict) else dp, ensure_ascii=False)
    sr, rb, _ = u.call("POST", "/analysis/run", dbody | {"expected_fingerprint": dp.get("fingerprint", "x" * 16)}) if isinstance(dp, dict) else (None, None, None)
    check("AN2", "같은 내용 파일 둘 → 문제 안내(같은 내용·중복), 실행 거절", ("같은" in probs or "중복" in probs) and sr not in (200, 201), (s, probs[:200], sr, str(rb)[:160]))
    CTX["analysis"] = run["id"]


def part_pii(u: Api) -> None:
    f = upload(u, f"{TAG}개인정보.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "시군구", "인구수", "연락처"], ["서울특별시", "가팀 kim1@example.com", 10, "010-1234-5678"],
                                                     ["부산광역시", "가팀 kim2@example.com", 20, "010-9876-5432"]]}))  # fmt: skip
    fid = f["id"]
    rows = must(u.call("GET", f"/files/{fid}/preview?sheet_id=s0"), "미리보기")["rows"]
    flat = json.dumps(rows, ensure_ascii=False)
    check("P1", "미리보기: 이메일·전화 가림, 원문 없음", "kim1@example.com" not in flat and "010-1234-5678" not in flat and "k***@example.com" in flat and "010-****-5678" in flat,
          [r["cells"] for r in rows])  # fmt: skip
    u.call("POST", f"/files/{fid}/pii/acknowledge", {})
    s = confirm(u, fid, {"시도": "sido", "시군구": "sigungu", "인구수": "population", "연락처": None})[0]
    s, rv = review(u, [fid])
    text = json.dumps(rv, ensure_ascii=False)
    check("P1", "검토 응답: 원문 이메일·전화 없음", s == 201 and "kim1@example.com" not in text and "010-1234-5678" not in text, (s, rv.get("counts") if isinstance(rv, dict) else rv))
    body = {"field_key": "population", "targets": [{"file_id": fid}], "group_by": ["sigungu"]}
    s, pv, _ = u.call("POST", "/analysis/preview", body)
    if s != 200:
        check("P3", "가린 뒤 같은 이름이 된 그룹 둘 → 합쳐지지 않음", None, f"분석 확인 거절 {s} {str(pv)[:200]} — backend 시험으로만 확인")
        return
    run = must(u.call("POST", "/analysis/run", body | {"expected_fingerprint": pv["fingerprint"]}), "분석 실행")
    groups = must(u.call("GET", f"/analysis/runs/{run['id']}/groups?offset=0&limit=50"), "그룹")["items"]
    labels = [g["keys"][0] for g in groups]
    check("P3", "가린 뒤 같은 이름이 된 그룹 둘 → 2행으로 남음(합계 10·20)", len(groups) == 2 and sorted(g["sum"] for g in groups) == [10, 20], labels)
    s, content, _ = u.call("GET", f"/analysis/runs/{run['id']}/download", raw=True)
    allcells = json.dumps(sheets_of(content), ensure_ascii=False, default=str)
    past = json.dumps(must(u.call("GET", f"/analysis/runs/{run['id']}"), "과거 결과"), ensure_ascii=False)
    check("P2", "분석 화면 값·Excel·과거 결과 조회: 원문 이메일 없음, 가린 값만", all("kim1@example.com" not in t and "kim2@example.com" not in t for t in (allcells, past, json.dumps(labels)))
          and all("k***@example.com" in x for x in labels), labels)  # fmt: skip


def part_bundle(u: Api) -> None:
    one = upload(u, f"{TAG}묶기단일.xlsx", xlsx({"단일": [[TITLE], ["시도", "인구수"], ["세종특별자치시", 3]]}))["id"]
    multi = CTX["multi"]
    for fid, sid in ((one, "s0"), (multi, "s0"), (multi, "s1")):
        must(confirm(u, fid, {"시도": "sido", "인구수": "population"}, sid), "확정")
    order = [{"file_id": multi, "sheet_id": "s1"}, {"file_id": one, "sheet_id": "s0"}, {"file_id": multi, "sheet_id": "s0"}]
    body = {"file_ids": [one, multi], "sheets": order}
    plan = must(u.call("POST", "/bundle/plan", body), "묶기 구성")
    run = must(u.call("POST", "/bundle/run", body | {"expected_basis": plan["basis"]}), "묶기 만들기")
    rid = run.get("id") or run.get("run_id")
    s, content, _ = u.call("GET", f"/runs/{rid}/download", raw=True)
    names = list(sheets_of(content)) if s == 200 else []
    check("B2", "여러 시트 중 고른 시트를 고른 순서로 (나시트 → 단일 → 가시트)", len(names) == 3 and "나시트" in names[0] and "단일" in names[1] and "가시트" in names[2], names)
    rev = {"file_ids": [one, multi], "sheets": list(reversed(order))}
    plan2 = must(u.call("POST", "/bundle/plan", rev), "순서 바꾼 구성")
    run2 = must(u.call("POST", "/bundle/run", rev | {"expected_basis": plan2["basis"]}), "순서 바꿔 만들기")
    s, c2, _ = u.call("GET", f"/runs/{run2.get('id') or run2.get('run_id')}/download", raw=True)
    names2 = list(sheets_of(c2))
    check("B2", "순서를 바꾸면 결과 시트 순서도 바뀜", names2 == list(reversed(names)), names2)
    rm = {"file_ids": [one], "sheets": [{"file_id": one, "sheet_id": "s0"}]}
    plan3 = must(u.call("POST", "/bundle/plan", rm), "파일 뺀 구성")
    check("B2", "파일을 빼면 그 파일 시트가 구성에서 빠짐", [s.get("file_id") for s in plan3["sheets"]] == [one], [(s.get("file_id"), s.get("sheet_id")) for s in plan3["sheets"]])
    # B6·X2: 요약을 켠 구성을 확인한 뒤 확정이 바뀌거나 요청 순서가 다르면 만들기 거절, 결과 파일·실행 기록 그대로
    sm = body | {"summary": {"items": [{"field_key": "population", "aggregates": ["sum"]}]}}
    plan4 = must(u.call("POST", "/bundle/plan", sm), "요약 켠 구성")
    before = counts()
    must(confirm(u, one, {"인구수": None}), "확정 변경")
    s, b, _ = u.call("POST", "/bundle/run", sm | {"expected_basis": plan4["basis"]})
    must(confirm(u, one, {"인구수": "population"}), "확정 되돌림")
    plan5 = must(u.call("POST", "/bundle/plan", sm), "다시 확인")
    s2, b2, _ = u.call("POST", "/bundle/run", sm | {"sheets": list(reversed(order)), "expected_basis": plan5["basis"]})
    after = counts()
    check("B6", "요약 켠 구성 확인 뒤 확정 변경·순서 다른 요청 → 만들기 409, 결과 파일·실행 기록 그대로", s == 409 and s2 == 409 and after == before,
          (s, str(b)[:120], s2, before, after))  # fmt: skip
    check("X2", "차단된 묶기 요청은 실행 기록·결과 파일을 만들지 않음", s == 409 and s2 == 409 and after == before, (before, after))
    # X3: 같은 이름 파일 둘
    same = [upload(u, f"{TAG}같은이름.xlsx", xlsx({"Sheet1": [[TITLE], ["시도", "인구수"], [r, n]]}))["id"] for r, n in (("제주특별자치도", 1), ("강원특별자치도", 2))]
    listed = [x for x in must(u.call("GET", "/files"), "파일 목록") if x.get("original_name") == f"{TAG}같은이름.xlsx"]
    for fid in same:
        must(confirm(u, fid, {"시도": "sido", "인구수": "population"}), "확정")
    sb = {"file_ids": same}
    p = must(u.call("POST", "/bundle/plan", sb), "같은 이름 구성")
    r = must(u.call("POST", "/bundle/run", sb | {"expected_basis": p["basis"]}), "같은 이름 묶기")
    s, c3, _ = u.call("GET", f"/runs/{r.get('id') or r.get('run_id')}/download", raw=True)
    sh3 = sheets_of(c3)
    vals = sorted(v for rows in sh3.values() for row in rows for v, _ in row if v in ("제주특별자치도", "강원특별자치도"))
    check("X3", "같은 이름 파일 둘 → 따로 저장·목록 2건, 묶기 시트 이름 겹치지 않고 두 내용 모두", len(set(same)) == 2 and len(listed) >= 2 and len(sh3) == 2
          and len(set(sh3)) == 2 and vals == ["강원특별자치도", "제주특별자치도"], (same, len(listed), list(sh3)))  # fmt: skip
    CTX["bundle_files"] = [one, multi]


def part_access(u1: Api, u2: Api, owner: Api) -> None:
    fid, rid, run, ana = CTX.get("ok_file"), CTX.get("review"), CTX.get("run"), CTX.get("analysis")
    if None in (fid, rid, run, ana):
        check("SEC1", "다른 사용자 자료 접근 거절", None, f"앞 단계 id가 없음 {(fid, rid, run, ana)} — 시험 성립 안 함")
        return
    probes = [("GET", f"/files/{fid}/preview?sheet_id=s0", None), ("GET", f"/files/{fid}/mapping/latest", None), ("GET", f"/reviews/{rid}", None),
              ("GET", f"/runs/{run}", None), ("GET", f"/runs/{run}/download", None), ("GET", f"/analysis/runs/{ana}", None),
              ("GET", f"/analysis/runs/{ana}/download", None), ("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": "c0", "field_key": None}]}),
              ("POST", f"/reviews/{rid}/recheck", {}), ("POST", "/bundle/plan", {"file_ids": [fid]}), ("POST", "/analysis/preview", {"field_key": "population", "targets": [{"file_id": fid}]})]  # fmt: skip
    got = {f"{m} {p.split('?')[0]}": u1.call(m, p, b)[0] for m, p, b in probes}
    check("SEC1", "user1이 user2의 파일·검토·실행·분석을 조회·변경 → 모두 거절(403·404·422), 200 없음", all(v in (403, 404, 409, 422) for v in got.values()), got)
    mine = {k: u2.call(m, p, b)[0] for (m, p, b), k in zip(probes[:7], list(got)[:7], strict=True)}
    check("SEC1", "같은 요청을 소유자(user2)가 하면 200 (시험 성립 확인)", all(v == 200 for v in mine.values()), mine)
    rv = must(owner.call("GET", f"/reviews/{rid}"), "관리자 조회")
    item = rv["items"][0]["id"] if rv["items"] else None
    od = owner.call("POST", f"/reviews/{rid}/items/{item}/decision", {"decision": "rejected", "choice": None})[0] if item else None
    op = owner.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": "c0", "field_key": None}]})[0]
    check("SEC2", "최고 관리자: 다른 사용자 검토는 조회만(can_edit false), 결정·확정 변경 거절", rv["can_edit"] is False and od in (403, 404, 409) and op in (403, 404, 409),
          {"can_edit": rv["can_edit"], "decision": od, "decisions": op})  # fmt: skip
    adm = {f"{m} {p}": u1.call(m, p, b)[0] for m, p, b in (("GET", "/users", None), ("GET", "/settings", None), ("PUT", "/settings", {}), ("GET", "/admin/promotions", None),
                                                          ("GET", "/admin/audit", None), ("GET", "/admin/overview", None))}  # fmt: skip
    check("SEC2", "일반 사용자의 관리 API → 403", all(v == 403 for v in adm.values()), adm)
    anon = Api()
    an = {p: anon.call("GET", p)[0] for p in ("/files", f"/reviews/{rid}", f"/runs/{run}/download", f"/analysis/runs/{ana}", "/auth/me")}
    check("SEC3", "익명 요청 → 보호 자원 401", all(v == 401 for v in an.values()), an)


def part_session_api() -> None:
    u3 = Api().login("user3")
    token = next(c.value for c in u3.jar)
    th = hashlib.sha256(token.encode("ascii")).hexdigest()
    ok_before = u3.call("GET", "/auth/me")[0]
    past = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
    code = (f"import sqlite3; c = sqlite3.connect('/app/data/moajwo.db'); n = c.execute('update auth_sessions set expires_at = ? where token_hash = ?', "
            f"({past!r}, {th!r})).rowcount; c.commit(); print(n)")  # fmt: skip
    p = subprocess.run(["docker", "exec", BACKEND, "python", "-c", code], capture_output=True, text=True, encoding="utf-8")
    after = u3.call("GET", "/auth/me")[0]
    an = u3.call("GET", "/files")[0]
    check("A2", "세션 만료(이 세션 한 줄만 만료 시각을 과거로) → /auth/me·보호 자원 401", ok_before == 200 and p.stdout.strip() == "1" and after == 401 and an == 401,
          (ok_before, p.stdout.strip(), after, an))  # fmt: skip
    check("SEC3", "만료 세션 → 보호 자원 401", after == 401 and an == 401, (after, an))
    CTX["expired_cookie"] = token


# ---------------------------------------------------------------- 화면


def login(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


def holder(held: list):
    def handle(route, *_):
        held.append(route)

    return handle


def ui_session(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user1")
    pg.reload()
    pg.wait_for_selector(".shell", timeout=60000)
    kept = pg.evaluate("fetch('/api/auth/me').then(r => r.status)")  # page.request는 범위 제한 프록시를 거치므로 페이지 안에서 확인
    links = pg.locator(".side-link").all_inner_texts()
    name = cookie_name()
    cookie = next(c["value"] for c in cx.cookies() if c["name"] == name)
    pg.get_by_role("button", name="로그아웃").click()
    pg.wait_for_selector(".login-card", timeout=30000)
    after = pg.evaluate("fetch('/api/auth/me').then(r => r.status)")
    check("A1", "로그인 → 새로고침 뒤 유지 → 로그아웃 → 로그인 화면, /auth/me 401", kept == 200 and after == 401 and pg.locator(".login-card").count() == 1, (kept, after))
    old = Api()
    old.jar.set_cookie(_cookie(name, cookie))
    reuse = old.call("GET", "/auth/me")[0]
    check("A3", "로그아웃한 쿠키를 다시 써도 401", reuse == 401, reuse)
    cx.close()
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "owner")
    olinks = pg.locator(".side-link").all_inner_texts()
    check("A4", "역할별 메뉴: 일반 사용자는 사용자 관리·설정 없음, 최고 관리자는 있음",
          not any(x.strip().startswith(("사용자", "설정")) for x in links) and any("설정" in x for x in olinks), {"user1": [x.strip() for x in links], "owner": [x.strip() for x in olinks]})  # fmt: skip
    cx.close()
    # 만료 세션 쿠키로 화면 열기 → 로그인 화면
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    name = cookie_name()
    cx.add_cookies([{"name": name, "value": CTX["expired_cookie"], "url": URL}])
    pg = cx.new_page()
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card, .shell", timeout=30000)
    check("A2", "만료 세션 쿠키로 화면을 열면 로그인 화면", pg.locator(".login-card").count() == 1, pg.locator(".shell").count())
    cx.close()


def cookie_name() -> str:
    a = Api().login("user3")
    return next(c.name for c in a.jar)


def _cookie(name: str, value: str):
    import http.cookiejar

    host = URL.split("//")[1].split(":")[0]
    return http.cookiejar.Cookie(0, name, value, None, False, host, False, False, "/", True, False, None, False, None, None, {})


def ui_home_selection(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user1")
    box = pg.locator("input[type=checkbox][aria-label$='검증 대상']").first
    if not box.is_checked():
        box.focus()
        pg.keyboard.press("Space")
    pg.wait_for_timeout(500)
    picked = pg.locator(".wk-validation input[type=checkbox][aria-label$='검증 대상']:checked").count()
    pg.evaluate("history.pushState(null, '', '/?files=999999'); window.dispatchEvent(new Event('moajwo:navigate'))")
    pg.wait_for_timeout(800)
    note = pg.locator("text=하나도 고르지 않았습니다").first
    msg = note.inner_text() if note.count() else ""
    left = pg.locator(".wk-validation input[type=checkbox][aria-label$='검증 대상']:checked").count()
    check("X1", "검증 대상을 고른 뒤 `/?files=없는번호` → 이전 대상 비움·안내", picked >= 1 and left == 0 and "이전에 고른 검증 대상도 비웠습니다" in msg, (picked, left, msg[:120]))
    cx.close()


def ui_analysis_late(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user1")
    pg.goto(URL + "/analysis")
    pg.wait_for_load_state("networkidle")
    pg.get_by_label("분석할 항목").select_option("population")
    boxes = pg.locator(".an-files input[type=checkbox]")
    boxes.first.wait_for(timeout=30000)
    if boxes.count() < 2:
        check("AN4", "늦은 대상 확인 응답이 바뀐 조건을 덮지 않음", None, f"분석할 파일 {boxes.count()}개 — 조건을 바꿀 두 번째 파일 없음")
        cx.close()
        return
    boxes.nth(0).check()
    held: list = []
    pg.route("**/api/analysis/preview", holder(held))
    pg.get_by_role("button", name="대상 확인 (계산 안 함)").click()
    pg.wait_for_timeout(500)
    boxes.nth(1).check()  # 응답을 기다리는 동안 파일을 하나 더 고름 (조건 변경)
    pg.wait_for_timeout(300)
    for r in list(held):
        r.continue_()
    pg.wait_for_timeout(1500)
    warn = pg.locator("p.mapping-warn", has_text="이전 조건의 확인 결과를 쓰지 않았습니다")
    used = pg.locator("[aria-label$=' 사용']").count()
    check("AN4", "대상 확인 응답이 늦게 오는 동안 파일을 더 고르면 늦은 응답을 쓰지 않음(안내·대상 표 없음)", len(held) == 1 and warn.count() == 1 and used == 0, (len(held), warn.count(), used))
    pg.unroute("**/api/analysis/preview")
    cx.close()


def ui_bundle(b) -> None:
    u1 = Api().login("user1")
    ids = []
    for name, title in ((f"{TAG}묶기입력A.xlsx", "인구 현황"), (f"{TAG}묶기입력B.xlsx", "주민등록 인구 현황 2026년 9월 (단위: 천명)")):
        fid = upload(u1, name, xlsx({"Sheet1": [[title], ["시도", "인구수"], ["세종특별자치시", 3], ["제주특별자치도", 4]]}))["id"]
        must(confirm(u1, fid, {"시도": "sido", "인구수": "population"}), "확정")
        ids.append(fid)
    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    plans: list[str] = []
    pg.on("request", lambda r: plans.append(r.url) if r.url.endswith("/api/bundle/plan") else None)
    login(pg, "user1")
    pg.goto(f"{URL}/bundle?files={ids[0]},{ids[1]}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    sw = pg.locator("label:has(input.peer.sr-only[type=checkbox]) input[type=checkbox]").first
    if not sw.is_checked():
        sw.focus()
        pg.keyboard.press("Space")
        pg.wait_for_timeout(800)
    add = pg.get_by_role("button", name="항목 추가", exact=True)
    if add.count():
        add.click()
        pg.wait_for_timeout(1500)
    field = pg.locator("input.bd-input[aria-label$='기간']").first
    if not field.count():
        check("B4", "입력 도중 재확인이 나가도 초점·입력값 유지", None, "기간 입력칸이 나타나지 않음")
    else:
        field.focus()
        field.evaluate("e => { window.__bdField = e }")
        n0, typed, kept = len(plans), "", []
        for ch in "2026-09":
            if ch == "-":
                pg.keyboard.press("Minus")
            else:
                pg.keyboard.press(ch)
            typed += ch
            pg.wait_for_timeout(450)  # 재확인 대기(250ms)보다 길게 — 글자마다 구성 확인이 다시 나가고 응답까지 옴
            kept.append(pg.evaluate("(t) => document.activeElement === window.__bdField && window.__bdField.value === t", typed))
        check("B4", "기간 입력 중 글자마다 재확인이 나가도 초점·입력값 유지", all(kept) and len(plans) - n0 >= 3, {"rechecks": len(plans) - n0, "kept": kept})
    # B5: 늦게 온 확인 응답이 최신 구성을 덮지 않음 — 순서 변경(A)의 확인 응답만 붙잡고, 그동안 두 번째 파일을 빼서(B) B의 응답을 먼저 받은 뒤 A를 늦게 돌려줌
    #     (확인 중에는 위로·아래로가 비활성이고, 항목 없는 요약 켜기는 요청을 바꾸지 않아 두 번째 변경은 '이 파일 빼기'로 만든다). 앞 시험이 저장한 구성이 없도록 새 문맥에서
    cx.close()
    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user1")
    pg.goto(f"{URL}/bundle?files={ids[0]},{ids[1]}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1000)
    make = pg.locator(".bundle button.mapping-run")
    why = pg.locator(".bundle .wk-why")
    first = pg.locator("ol.bd-sheets li").first.locator("strong, .bd-sheet-name").first.inner_text().strip()
    held: list = []

    def first_only(route, *_):
        if held:
            route.continue_()
        else:
            held.append(route)

    pg.route("**/api/bundle/plan", first_only)
    pg.locator(f"button[aria-label='{first} 아래로']").click()  # A: 첫 시트를 아래로 (이 응답만 붙잡음)
    pg.wait_for_timeout(600)
    pg.locator("ul.bd-files li").nth(1).get_by_role("button", name="이 파일 빼기").click()  # B: 두 번째 파일 빼기 (이 응답은 그대로 받음)
    pg.wait_for_timeout(1500)
    state_b = {"button": make.inner_text(), "why": why.inner_text() if why.count() else "", "enabled": make.is_enabled(), "stale": pg.locator(".bd-stale").count()}
    for r in list(held):
        try:
            r.continue_()
        except Exception:  # noqa: BLE001 — 브라우저가 이미 취소한 요청
            pass
    pg.wait_for_timeout(1500)
    after = {"button": make.inner_text(), "why": why.inner_text() if why.count() else "", "enabled": make.is_enabled(), "stale": pg.locator(".bd-stale").count()}
    pg.unroute("**/api/bundle/plan")
    check("B5", "늦게 온 확인 응답(A: 2개 파일 순서 변경)이 최신 구성(B: 1개 파일)의 결과를 덮지 않음", len(held) == 1 and "시트 1개" in state_b["button"]
          and state_b["enabled"] and not state_b["stale"] and after == state_b, {"held": len(held), "B": state_b, "A 늦게 온 뒤": after})  # fmt: skip
    cx.close()


def ui_kst(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", timezone_id="UTC")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user2")
    rid = CTX.get("review")
    raw = must(Api().login("user2").call("GET", f"/reviews/{rid}"), "검토")["created_at"]
    want = (datetime.fromisoformat(raw).replace(tzinfo=UTC) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S") if "+" not in raw else raw
    pg.goto(URL + "/reviews")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    shown = pg.locator("td[data-label='만든 때']").all_inner_texts()
    check("R1", "검토 목록 '만든 때'는 브라우저 시간대(UTC)와 상관없이 KST로 표시", want in shown, {"api": raw, "want": want, "shown": shown[:3]})
    cx.close()


def main() -> None:
    only = set(filter(None, os.environ.get("ONLY_PARTS", "").split(",")))
    meta = {}
    if not only or "api" in only:
        u1, u2, owner = Api().login("user1"), Api().login("user2"), Api().login("owner")
        for name, fn in (("upload", part_upload), ("mapping", part_mapping), ("review", part_review), ("analysis", part_analysis), ("pii", part_pii),
                         ("bundle", part_bundle)):  # fmt: skip
            try:
                print(f"== {name}", flush=True)
                fn(u2)
            except Exception as e:  # noqa: BLE001 — 측정 오류로 기록 (통과로 세지 않음)
                ERRORS.append({"part": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                print("오류", name, type(e).__name__, str(e)[:300], flush=True)
        for name, fn in (("access", lambda: part_access(u1, u2, owner)), ("session", part_session_api)):
            try:
                print(f"== {name}", flush=True)
                fn()
            except Exception as e:  # noqa: BLE001
                ERRORS.append({"part": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                print("오류", name, type(e).__name__, str(e)[:300], flush=True)
        (OUT / f"final-regress-{LABEL}-ctx.json").write_text(json.dumps(CTX, ensure_ascii=False), encoding="utf-8")  # 화면만 다시 돌릴 때 씀 (만료된 합성 세션 토큰 포함 — Git 밖)
    elif (OUT / f"final-regress-{LABEL}-ctx.json").exists():
        CTX.update(json.loads((OUT / f"final-regress-{LABEL}-ctx.json").read_text(encoding="utf-8")))
    if not only or "browser" in only:
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
            cx = b.new_context()
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            pg.goto(URL + "/")
            meta = run_meta("final_regress_check.py", LABEL, pg.evaluate(ASSET_JS)) | {"browser": f"msedge {b.version}"}
            cx.close()
            for name, fn in (("session", ui_session), ("home", ui_home_selection), ("analysis_late", ui_analysis_late), ("bundle", ui_bundle), ("kst", ui_kst)):
                try:
                    print(f"== 화면 {name}", flush=True)
                    fn(b)
                except Exception as e:  # noqa: BLE001
                    ERRORS.append({"part": f"ui_{name}", "error": f"{type(e).__name__}: {str(e)[:300]}"})
                    print("오류", name, type(e).__name__, str(e)[:300], flush=True)
            b.close()
    out = OUT / f"final-regress-{LABEL}.json"
    out.write_text(json.dumps({"checks": RES, "error": ERRORS, "blocked_out_of_scope": BLOCKED, "meta": meta, "ctx": {k: v for k, v in CTX.items() if k != "expired_cookie"}}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"RESULT 통과 {sum(r['ok'] is True for r in RES)} · 실패 {sum(r['ok'] is False for r in RES)} · 확인 불가 {sum(r['ok'] is None for r in RES)} · 오류 {len(ERRORS)}")


if __name__ == "__main__":
    main()
