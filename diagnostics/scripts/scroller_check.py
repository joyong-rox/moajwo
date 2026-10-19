"""표 스크롤 영역에 키보드 초점이 가고 화살표로 가로 스크롤되는지 (axe scrollable-region-focusable 확인용). 사용법: python scroller_check.py"""
import json

from playwright.sync_api import sync_playwright

from diag_common import EV, URL, USERS, creds

IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    for w in (320, 390):
        pg = b.new_page(viewport={"width": w, "height": 800})
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(USERS["user1"])
        pg.get_by_label("비밀번호", exact=True).fill(creds()["user1"])
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell")
        pg.goto(f"{URL}/analysis?id={IDS['user1_analysis']}")
        pg.locator("table.an-result tbody tr").first.wait_for()
        pg.wait_for_timeout(800)
        info = pg.evaluate("[...document.querySelectorAll('.upload-table-wrap')].map(e => ({sw: e.scrollWidth, cw: e.clientWidth, ti: e.tabIndex}))")
        hits = []
        for _ in range(80):
            pg.keyboard.press("Tab")
            d = pg.evaluate("(() => { const e = document.activeElement; return e && e.classList.contains('upload-table-wrap') ? {sl: e.scrollLeft, sw: e.scrollWidth, cw: e.clientWidth} : null })()")
            if d:
                pg.keyboard.press("ArrowRight")
                pg.keyboard.press("ArrowRight")
                pg.wait_for_timeout(200)
                d["after_arrow"] = pg.evaluate("document.activeElement.scrollLeft")
                hits.append(d)
        print(w, "표 영역", info, "Tab으로 초점 간 스크롤 영역", hits[:4], "Edge", b.version)
        pg.close()
    b.close()
