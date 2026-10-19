"""Tailgrids 스위치(숨긴 체크박스 + 트랙)의 꺼짐·켜짐·키보드 초점 상태 색과 대비. 사용법: python toggle_check.py <출력 폴더>"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from diag_common import EV, URL, USERS, creds

OUT = Path(sys.argv[1])
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
JS = """() => { const i = document.querySelector('label input.peer.sr-only[type=checkbox]'); const t = i.nextElementSibling; const k = t.nextElementSibling;
  const box = getComputedStyle(t); const card = (() => { let p = i.closest('label').parentElement; while (p) { const c = getComputedStyle(p).backgroundColor; if (!c.includes('0)') && c !== 'transparent') return c; p = p.parentElement } return 'rgb(255,255,255)' })()
  return {checked: i.checked, focused: document.activeElement === i, track: box.backgroundColor, ring: box.boxShadow, knob: getComputedStyle(k).backgroundColor, around: card} }"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS["user1"])
    pg.get_by_label("비밀번호", exact=True).fill(creds()["user1"])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell")
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(600)
    states = {"꺼짐": pg.evaluate(JS)}
    lab = pg.locator("label", has_text="통계 요약 시트 추가")
    for _ in range(60):
        pg.keyboard.press("Tab")
        if pg.evaluate("document.activeElement.matches('input.peer.sr-only')"):
            break
    states["꺼짐+초점"] = pg.evaluate(JS)
    lab.screenshot(path=str(OUT / "toggle-off-focus.png"))
    pg.keyboard.press("Space")
    pg.wait_for_timeout(300)
    states["켜짐+초점"] = pg.evaluate(JS)
    lab.screenshot(path=str(OUT / "toggle-on-focus.png"))
    b.close()
(OUT / "toggle.json").write_text(json.dumps(states, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(states, ensure_ascii=False, indent=1))
