"""U-05 확인: 대표 화면을 7개 폭(320·360·390·430·768·1366·1920)에서 열어 가로 넘침과 axe(WCAG 2.x A·AA 태그) 위반을 잰다.
새 계정·합성 자료. 판정은 하지 않고 측정만 적는다. 사용법: python u05_responsive.py <출력 폴더> <axe.min.js>"""

import json
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from diag_common import URL, XLSX, Api, xlsx
from diag_scope import browser_proxy, route_guard

OUT, AXE = Path(sys.argv[1]), sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
WIDTHS = [320, 360, 390, 430, 768, 1366, 1920]
owner = Api().login("owner")
name, pw = "u05-" + secrets.token_hex(3), "Rs-" + secrets.token_urlsafe(14) + "7z"
assert owner.call("POST", "/users", {"username": name, "display_name": "반응형 확인", "status": "active", "password": pw})[0] == 201
u = Api()
assert u.call("POST", "/auth/login", {"username": name, "password": pw})[0] == 200
rows = [["2026년 9월 시도별 인구 (단위: 명)"], ["시도", "인구"], ["서울특별시", 100], ["부산광역시", 200]]
fid = u.upload("반응형_합성.xlsx", xlsx({"현황": rows}), XLSX)[1][0]["id"]
cols = u.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]["columns"]
keys = {"시도": "sido", "인구": "population"}
u.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": c["column_id"], "field_key": keys.get(c["name"])} for c in cols]})
review = u.call("POST", "/reviews", {"file_ids": [fid], "expected_period": "2026-09"})[1]
req = {"field_key": "population", "targets": [{"file_id": fid}]}
pv = u.call("POST", "/analysis/preview", req)[1]
aid = u.call("POST", "/analysis/run", {**req, "expected_fingerprint": pv["fingerprint"]})[1]["id"]
PAGES = {
    "홈(파일 고름)": f"/?files={fid}",
    "열 매핑 작업": f"/mapping?file={fid}",
    "검토": f"/reviews?review={review['id']}",
    "데이터 분석 결과": f"/analysis?id={aid}",
    "묶기": f"/bundle?files={fid}",
    "실행 기록": "/runs",
    "자동화": "/automations",
    "내 계정": "/account",
}
rec: list[dict] = []
blocked: list[str] = []
with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
    for w in WIDTHS:
        cx = br.new_context(viewport={"width": w, "height": 900}, locale="ko-KR")
        cx.route("**/*", route_guard(URL, blocked))
        pg = cx.new_page()
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(name)
        pg.get_by_label("비밀번호", exact=True).fill(pw)
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell", timeout=60000)
        for label, path in PAGES.items():
            pg.goto(URL + path)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(500)
            sw, cw = pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            pg.add_script_tag(path=AXE)
            res = pg.evaluate("() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}})")
            item = {"width": w, "page": label, "overflow_px": sw - cw,
                    "violations": [[v["id"], v["impact"], len(v["nodes"])] for v in res["violations"]],
                    "incomplete": sorted({v["id"] for v in res["incomplete"]})}  # fmt: skip
            rec.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
            if w in (320, 1366) and label in ("홈(파일 고름)", "검토"):
                pg.screenshot(path=str(OUT / f"u05-{w}-{label}.png"), full_page=False)
        cx.close()
    br.close()
(OUT / "u05-responsive.json").write_text(json.dumps({"pages": rec, "blocked": blocked}, ensure_ascii=False, indent=1), encoding="utf-8")
