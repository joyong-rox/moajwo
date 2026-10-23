"""D-01·D-02 확인: 시연 5개 파일로 합계 산식 경고·항목 정의 표시·중복 계산 안내·조직코드 구분을 실제 경로에서 잰다
(격리 서버, 새 계정, 측정만 저장 — 판정하지 않음).

1) 새 계정으로 5개 파일을 올리고 열 매핑 분석(모델 호출 없음) 제안을 그대로 확정한다 (제안을 받아들인 사용자).
2) 통합 전 검사(검토)를 만들고 '합계 산식 확인 필요' 경고를 API와 검토 화면에서 읽는다 (통합은 경고만).
3) 데이터 통계 분석 화면: '발신건수합계'를 고르면 보이는 항목 정의, 대상 확인에서 통일부 시트의 '확인 필요'(산식 불일치)와
   실행 버튼 상태 → 통일부 '사용'을 끄고 다시 확인 → 실행 → 결과의 '자료 제외' (2026-10-23 정책: 통계는 막음).
4) 묶기 통계 요약: '발신건수합계'와 '발신Mail건수'를 함께 고른 계획(합계 항목은 막힘)·거절된 실행 → 통일부 시트를
   합계 항목에서만 뺀 실행의 요약 시트 '주의'·자료 줄.
5) 조직코드를 확정한 상태의 '발신Mail건수' 분석 (반복 확인 없이 합계), 6) 조직코드를 '해당 없음'으로 바꾼 통일부 시트의 반복 안내.
사용법: python mail_formula_check.py <출력 폴더> <라벨> <새 계정 이름> <입력 파일 폴더>
"""

import io
import json
import secrets
import sys
from pathlib import Path

import openpyxl
from playwright.sync_api import sync_playwright

from diag_common import ASSET_JS, URL, XLSX, Api, run_meta
from diag_scope import browser_proxy, route_guard

OUT, LABEL, NAME, SRC = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
FILES = [f"{m} 사용량.xlsx" for m in ("행안부", "기획예산처", "재정경제부", "외교부", "통일부")]
OUT.mkdir(parents=True, exist_ok=True)
REC: dict[str, list] = {}
BLOCKED: list[str] = []


def rec(group: str, item: dict) -> None:
    REC.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:500], flush=True)


def text(loc) -> str:  # noqa: ANN001 — Playwright Locator
    return " ".join(loc.inner_text().split())


def main() -> None:
    owner = Api().login("owner")
    password = "Mf-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": NAME, "display_name": f"산식 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    (OUT / f"creds-{LABEL}.json").write_text(json.dumps({NAME: password}), encoding="utf-8")
    u = Api()
    if u.call("POST", "/auth/login", {"username": NAME, "password": password})[0] != 200:
        raise RuntimeError("새 계정 로그인 실패")
    fids, columns = [], {}
    for name in FILES:
        s, body, _ = u.upload(name, (SRC / name).read_bytes(), XLSX)
        fid = body[0]["id"]
        cols = u.call("POST", f"/files/{fid}/mapping?use_llm=false")[1]["sheets"][0]["columns"]
        items = [{"sheet_id": "s0", "column_id": c["column_id"], "field_key": c["field_key"]} for c in cols]
        s, body, _ = u.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": items})
        if s != 200:
            raise RuntimeError(f"확정 실패 {name}: {s} {body}")
        fids.append(fid)
        columns[fid] = {c["column"]: c["column_id"] for c in cols}
        rec("setup", {"file": fid, "name": name, "confirmed": len(items), "unmapped": [c["column"] for c in cols if not c["field_key"]]})
    unif = fids[-1]

    # 2) 통합 전 검사 (API)
    s, review, _ = u.call("POST", "/reviews", {"file_ids": fids})
    found = [i for i in review.get("items", []) if i["kind"] == "formula_mismatch"]
    rec("review-api", {"status": s, "id": review.get("id"), "formula_items": [(i["file_name"], i["column"], i["reason"]) for i in found],
                       "kinds": sorted({i["kind"] for i in review.get("items", [])})})  # fmt: skip
    links = [{"file_id": f, "sheet_id": "s0", "unit": "건"} for f in fids]

    # 4) 묶기 통계 요약: 합계와 구성 항목을 함께 고름 (API)
    items = [{"field_key": k, "aggregates": ["sum"], "links": links} for k in ("mail_sent_total_count", "mail_sent_count")]
    body = {"file_ids": fids, "summary": {"items": items}}
    s, plan, _ = u.call("POST", "/bundle/plan", body)
    rec("bundle-plan", {"status": s, "warnings": plan.get("warnings"), "item_problems": [i.get("problems") for i in plan["summary"]["items"]],
                        "link_formula": [[(lk["output_sheet"], (lk.get("formula") or {}).get("state")) for lk in i["links"]] for i in plan["summary"]["items"]],
                        "problems": plan.get("problems")})  # fmt: skip
    s, refused, _ = u.call("POST", "/bundle/run", {**body, "expected_basis": plan["basis"]})
    rec("bundle-run-refused", {"status": s, "detail": refused.get("detail") if isinstance(refused, dict) else None})
    # 통일부 시트를 합계 항목에서만 뺀다 (파일별 시트에는 그대로 들어감) — 뺀 이유를 적는다
    items[0]["links"] = [{**lk, "exclude": True, "exclude_reason": "산식이 다른 합계"} if lk["file_id"] == unif else lk for lk in links]
    s, plan, _ = u.call("POST", "/bundle/plan", body)
    rec("bundle-plan-left-out", {"status": s, "problems": plan.get("problems"), "ready": [i.get("ready") for i in plan["summary"]["items"]]})
    if s == 200 and not plan.get("problems"):
        s, run, _ = u.call("POST", "/bundle/run", {**body, "expected_basis": plan["basis"]})
        content = u.call("GET", f"/runs/{run['id']}/download", raw=True)[1]
        wb = openpyxl.load_workbook(io.BytesIO(content))
        rows = [list(r) for r in wb["통계 요약"].iter_rows(values_only=True)]
        rec("bundle-excel", {"run": run["id"], "notes": [r[1] for r in rows if r[0] == "주의"],
                             "totals": [r[:2] for r in rows if r[0] == "전체"],
                             "links": [r[:10] for r in rows if len(r) > 9 and r[7] in ("포함", "뺌 (사용자)", "뺌")]})  # fmt: skip

    # 5) 조직코드를 확정한 상태: 발신Mail건수 전체 (반복 확인 없이)
    req = {"field_key": "mail_sent_count", "targets": links}
    s, pv, _ = u.call("POST", "/analysis/preview", req)
    rec("org-mapped-preview", {"status": s, "targets": [(t["file_id"], t["status"], t["reasons"]) for t in pv["targets"]], "problems": pv["problems"]})
    if s == 200 and not pv["problems"] and all(t["status"] == "ok" for t in pv["targets"]):
        s, run, _ = u.call("POST", "/analysis/run", req | {"expected_fingerprint": pv["fingerprint"]})
        g = u.call("GET", f"/analysis/runs/{run['id']}/groups?limit=10")[1]["items"]
        rec("org-mapped-run", {"run": run["id"], "groups": [(x["sum"], x["count"], x["min"], x["max"]) for x in g]})

    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = br.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(NAME)
        pg.get_by_label("비밀번호", exact=True).fill(password)
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell", timeout=60000)
        asset = pg.evaluate(ASSET_JS)
        # 2) 검토 화면의 산식 경고
        if review.get("id"):
            pg.goto(f"{URL}/reviews?review={review['id']}")
            pg.locator("details.review-warnings > summary").first.wait_for(timeout=60000)
            pg.locator("details.review-warnings > summary").first.click()  # 검증 경고는 접힌 목록 (기존 표시 방식)
            item = pg.locator("details.review-warnings li", has_text="합계 산식 확인 필요").first
            item.wait_for(timeout=30000)
            rec("review-screen", {"label_count": pg.get_by_text("합계 산식 확인 필요").count(), "item": text(item)[:400]})
            pg.screenshot(path=str(OUT / f"{LABEL}-1-review.png"), full_page=True)
        # 3) 데이터 통계 분석 화면: 항목 정의와 대상 확인의 산식 주의
        pg.goto(URL + "/analysis")
        pg.get_by_label("분석할 항목").select_option("mail_sent_total_count")
        help_text = pg.locator("#an-field-help")
        help_text.wait_for(timeout=30000)
        for fid in fids:
            pg.locator("label.an-file", has_text=f"#{fid} ").locator("input").check()
        pg.get_by_role("button", name="대상 확인 (계산 안 함)").click()  # 단위는 확인 뒤 표에 적는다
        pg.locator("fieldset.an-step legend", has_text="2. 대상").wait_for(timeout=60000)
        for box in pg.locator("table.an-targets input[aria-label$=' 단위 확인']").all():
            box.fill("건")
        pg.get_by_role("button", name="다시 확인 (계산 안 함)").click()
        pg.wait_for_timeout(2500)
        warns = [text(x) for x in pg.locator("fieldset.an-step p.mapping-warn").all()]
        run_button = pg.get_by_role("button", name="통계 분석 실행")
        rows = [text(r)[:300] for r in pg.locator("table.an-targets tbody tr").all()]
        rec("analysis-screen", {"field_help": text(help_text), "warnings": warns, "targets": rows,
                                "run_disabled": run_button.is_disabled()})  # fmt: skip
        pg.screenshot(path=str(OUT / f"{LABEL}-2-analysis.png"), full_page=True)
        # 통일부 시트의 '사용'을 끄고 다시 확인 → 실행 (명시적으로 뺀 사실이 결과에 남는지)
        pg.locator(f"table.an-targets input[aria-label^='#{unif} '][aria-label$=' 사용']").uncheck()
        pg.get_by_role("button", name="다시 확인 (계산 안 함)").click()
        pg.wait_for_timeout(2500)
        rows = [text(r)[:300] for r in pg.locator("table.an-targets tbody tr").all()]
        rec("analysis-left-out", {"targets": rows, "run_disabled": run_button.is_disabled()})
        if not run_button.is_disabled():
            run_button.click()
            pg.wait_for_url("**/analysis?id=*", timeout=60000)
            pg.get_by_role("heading", name="제외", exact=False).first.wait_for(timeout=60000)
            left = [text(x) for x in pg.locator("li", has_text="자료 제외").all()]
            rec("analysis-result", {"url": pg.evaluate("location.pathname + location.search"), "excluded": left})
            pg.screenshot(path=str(OUT / f"{LABEL}-3-analysis-result.png"), full_page=True)
        REC["meta"] = [run_meta("mail_formula_check.py", LABEL, asset)]
        br.close()

    # 6) 시연처럼 통일부 조직코드를 '해당 없음'으로 바꾸면: 반복 안내가 그 열을 짚는다
    s, body, _ = u.call("PUT", f"/files/{unif}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": columns[unif]["조직코드"], "field_key": None}]})
    s, pv, _ = u.call("POST", "/analysis/preview", {"field_key": "mail_sent_count", "targets": [{"file_id": unif, "sheet_id": "s0", "unit": "건"}]})
    rec("org-unmapped-preview", {"status": s, "target": [(t["status"], t["reasons"]) for t in pv["targets"]]})
    REC["blocked_out_of_scope"] = BLOCKED
    (OUT / f"mail-formula-{LABEL}.json").write_text(json.dumps(REC, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
