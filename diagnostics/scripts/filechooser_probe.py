"""'파일 선택' 창이 가끔 열리지 않는 원인 조사 (4차·7차 a11y_fix_check ①단계 시간 초과) — 측정만 저장.

a11y_fix_check.py와 같은 순서를 반복한다: 클릭으로 열고 빈 선택으로 닫기 → Space로 열고 닫기 → Enter로 열기.
단계마다 버튼의 keydown·click, 숨긴 input[type=file]의 click 수(페이지 안 기록)와 Playwright의 filechooser 이벤트 여부를 남겨,
앱 처리기가 불리지 않았는지(앱) / 불렸는데 창 이벤트가 없는지(브라우저·도구)를 나눈다. 업로드는 하지 않는다(선택은 모두 빈 목록).
사용법: python filechooser_probe.py <출력 폴더> <라벨> [반복 수, 기본 8]   환경: PROBE_FRESH=1이면 반복마다 새 문맥
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from diag_common import URL, USERS, creds
from diag_scope import browser_proxy, route_guard

OUT, LABEL = Path(sys.argv[1]), sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 8
OUT.mkdir(parents=True, exist_ok=True)
BLOCKED: list[str] = []
SEL = ".wk-home .upload-drop .wk-drop-button"
SPY_JS = """() => { if (window.__fcSpy) return; const s = window.__fcSpy = {btn_keydown: 0, btn_click: 0, input_click: 0, input_cancel: 0, input_change: 0};
  document.addEventListener('keydown', e => { if (e.target.closest && e.target.closest('.wk-drop-button')) s.btn_keydown++ }, true);
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('.wk-drop-button')) s.btn_click++;
    if (e.target.matches && e.target.matches('input[type=file]')) s.input_click++ }, true);
  document.addEventListener('cancel', e => { if (e.target.matches && e.target.matches('input[type=file]')) s.input_cancel++ }, true);
  document.addEventListener('change', e => { if (e.target.matches && e.target.matches('input[type=file]')) s.input_change++ }, true) }"""


def login(pg: Page) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS["user1"])
    pg.get_by_label("비밀번호", exact=True).fill(creds()["user1"])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


def step(pg: Page, how: str, timeout: int) -> dict:
    spy0 = pg.evaluate("() => ({...window.__fcSpy})")
    drop = pg.locator(SEL)
    opened, err = False, None
    try:
        with pg.expect_file_chooser(timeout=timeout) as fc:
            if how == "click":
                drop.click()
            else:
                drop.focus()
                pg.keyboard.press("Space" if how == "space" else "Enter")
        fc.value.set_files(files=[])
        opened = True
    except Exception as e:  # noqa: BLE001 — 열리지 않음을 기록
        err = type(e).__name__
    pg.wait_for_timeout(300)
    spy1 = pg.evaluate("() => ({...window.__fcSpy})")
    focus = pg.evaluate("() => { const e = document.activeElement; return e ? (e.className || e.tagName).toString().slice(0, 40) : null }")
    busy = pg.locator(".upload-drop.is-busy").count()
    return {"how": how, "opened": opened, "error": err, "delta": {k: spy1[k] - spy0[k] for k in spy1}, "focus_after": focus, "busy": busy}


def main() -> None:
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = pg = None
        for i in range(N):
            if cx is None or os.environ.get("PROBE_FRESH"):
                if cx:
                    cx.close()
                cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
                cx.route("**/*", route_guard(URL, BLOCKED))
                pg = cx.new_page()
                login(pg)
            pg.evaluate(SPY_JS)
            seq = [step(pg, "click", 8000), step(pg, "space", 8000), step(pg, "enter", 8000)]
            rows.append({"round": i + 1, "steps": seq})
            print(i + 1, [(s["how"], s["opened"], s["delta"]) for s in seq], flush=True)
        b.close()
    fails = [(r["round"], s) for r in rows for s in r["steps"] if not s["opened"]]
    res = {"label": LABEL, "rounds": N, "fresh_context": bool(os.environ.get("PROBE_FRESH")), "rows": rows, "not_opened": fails, "blocked_out_of_scope": BLOCKED,
           "browser": "msedge"}  # fmt: skip
    (OUT / f"filechooser-probe-{LABEL}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"RESULT 반복 {N} · 열리지 않음 {len(fails)}: {[(r, s['how'], s['delta']) for r, s in fails]}")


if __name__ == "__main__":
    main()
