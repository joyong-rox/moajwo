"""U-06 후속 확인: 업로드·분석 중에 '파일 선택'을 누르면 열리지 않는 이유가 보이고 들리는지 (격리 서버, 새 계정, 합성 자료, 측정만 저장).

업로드 요청(POST /api/files)을 잡아 두어 '업로드 중' 상태를 일정하게 만든 뒤 적는다:
브라우저의 파일 선택 요청(file chooser)이 생기는지, 버튼 아래 이유 문구, 버튼 설명(aria-describedby), 상태 알림,
axe 위반(WCAG 2.x A·AA 태그), 390px 폭 가로 넘침. 요청을 보낸 뒤에는 이유 문구가 사라지고 다시 열리는지도 본다.
실제 OS 대화상자는 열지 않는다. 이전에 보고된 '선택 창이 안 열림'(U-06)을 재현하거나 해결했다는 판정이 아니다.
사용법: python upload_busy_check.py <출력 폴더> <라벨> <axe.min.js>
"""

import json
import secrets
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

from diag_common import ASSET_JS, URL, Api, run_meta, xlsx
from diag_scope import browser_proxy, route_guard

OUT, LABEL, AXE = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
REC: dict[str, list] = {}
BLOCKED: list[str] = []

STATE_JS = r"""() => {
  const text = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : null)
  const sec = document.querySelector('section.upload')
  const btn = sec.querySelector('.wk-drop-button')
  const ids = (btn.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
  return {
    busy: !!sec.querySelector('.upload-drop.is-busy'),
    aria_disabled: btn.getAttribute('aria-disabled'),
    why: [...sec.querySelectorAll('.wk-why')].map(text),
    described_by: ids.map((id) => text(document.getElementById(id))),
    status: text(sec.querySelector('[role=status]')),
    focused: document.activeElement === btn,
  }
}"""


def rec(group: str, item: dict) -> None:
    REC.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:400], flush=True)


def main() -> None:
    owner = Api().login("owner")
    name, password = f"busy-{LABEL}-{secrets.token_hex(2)}", "Ub-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": name, "display_name": f"업로드 중 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    src = OUT / "업로드중_합성.xlsx"
    src.write_bytes(xlsx({"시트1": [["시도", "인구"], ["서울특별시", 100], ["부산광역시", 200]]}))

    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = br.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(name)
        pg.get_by_label("비밀번호", exact=True).fill(password)
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell", timeout=60000)
        pg.wait_for_load_state("networkidle")
        asset = pg.evaluate(ASSET_JS)
        btn = pg.locator("section.upload .wk-drop-button")

        def chooser(case: str, act, choose: Path | None = None) -> None:  # noqa: ANN001
            t = time.perf_counter()
            try:
                with pg.expect_file_chooser(timeout=2500) as fc:
                    act()
                if choose is not None:
                    fc.value.set_files(str(choose))
                opened = True
            except PwTimeout:
                opened = False
            rec("chooser", {"case": case, "opened": opened, "ms": round((time.perf_counter() - t) * 1000)})

        def state(case: str) -> None:
            pg.wait_for_timeout(300)
            rec("state", {"case": case, **pg.evaluate(STATE_JS)})
            pg.screenshot(path=str(OUT / f"{LABEL}-{case}.png"), full_page=True)

        def axe(case: str) -> None:
            pg.add_script_tag(path=AXE)
            res = pg.evaluate("() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}})")
            found = []
            for v in res["violations"]:
                for n in v["nodes"]:
                    why = (n["any"] or n["all"] or [{"message": ""}])[0]["message"]
                    found.append({"id": v["id"], "target": n["target"], "html": n["html"][:160], "why": why})
            rec("axe", {"case": case, "violations": found})

        held: list = []

        def hold(route) -> None:  # noqa: ANN001 — 업로드 요청만 잡아 둔다 (나머지는 그대로)
            if route.request.method == "POST":
                held.append(route)
            else:
                route.continue_()

        state("0-idle")
        pg.route("**/api/files", hold)
        chooser("고르기 → 업로드 시작", lambda: btn.click(), choose=src)
        pg.wait_for_selector("section.upload .upload-drop.is-busy", timeout=30000)
        state("1-busy")
        btn.focus()
        chooser("업로드 중 버튼 클릭", lambda: btn.click(force=True))
        state("2-busy-after-click")
        chooser("업로드 중 키보드 Enter", lambda: pg.keyboard.press("Enter"))
        state("3-busy-after-enter")
        axe("busy-1280")
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(400)
        rec("mobile", {"case": "busy-390", "overflow_px": pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")})
        state("4-busy-390")
        pg.set_viewport_size({"width": 1280, "height": 900})
        for _ in range(100):  # 잡아 둔 요청이 있을 때까지
            if held:
                break
            pg.wait_for_timeout(100)
        held.pop().continue_()
        pg.unroute("**/api/files", hold)
        pg.wait_for_selector("section.upload .upload-drop:not(.is-busy)", timeout=120000)
        state("5-done")
        axe("done-1280")
        chooser("업로드 끝난 뒤 클릭", lambda: btn.click())
        REC["meta"] = [run_meta("upload_busy_check.py", LABEL, asset)]
        br.close()
    REC["blocked_out_of_scope"] = BLOCKED
    src.unlink()
    (OUT / f"upload-busy-{LABEL}.json").write_text(json.dumps(REC, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
