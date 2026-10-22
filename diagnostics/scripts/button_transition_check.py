"""버튼이 비활성 → 활성으로 바뀌는 동안의 글자 대비 (전환 중 검사) — 측정만 저장하고 판정은 a11y_regress.py(--suite transition)가 한다.

a11y_scan.py는 CSS 전환이 끝난 뒤 재는 '정지 상태' 검사다. 이 스크립트는 그 대기 때문에 전환 중 결함이 가려지지 않도록,
버튼이 실제로 비활성으로 그려진 뒤 다시 활성으로 바뀌는 과정을 프레임마다 기록한다.
- 기록: 시각(표시용 반올림 ms `t`, 판정·재계산용 원시값 `t_raw`), disabled, hover·초점·focus-visible, 글자 크기·굵기, 글자색·배경색·투명도, 뒤 배경,
  계산된 대비(반올림 전 원시값), 첫 활성 프레임의 인덱스. 프레임은 모두 저장한다 (집계는 transition_summary.py).
- 기준: 일반 크기 글자 4.5:1, 큰 글자(24px 이상, 또는 18.66px 이상 굵게) 3:1. 비활성 프레임은 예외라 판정에 넣지 않는다.
- 활성 전환을 관측하지 못한 실행(비활성이 한 번도 그려지지 않았거나 다시 활성이 되지 않음)은 통과로 세지 않는다.
로그인: 시험용 응답(로그인 요청을 브라우저에서 0.5초 붙잡았다가 서버와 같은 모양의 401을 돌려줌 — 서버에 보내지 않아 계정 잠금과 무관)과
실제 서버 응답(존재하지 않는 아이디 — 실패 횟수가 쌓이는 계정이 없음)을 나눠 기록한다. 틀린 비밀번호를 서버로 반복 제출하지 않는다.
그 밖의 버튼: 실제 화면이다. 묶기 '엑셀 만들기'는 요약 스위치를 켰다 바로 꺼, 확인 전 구성(비활성) → 이미 확인된 구성(곧바로 활성)으로 돌아오는
경로를 잰다 — 활성화가 확인 응답을 기다리지 않는다. 그 사이 확인 결과가 바뀌지 않게 구성 확인 요청을 0.6초 붙잡는다 (붙잡은 수를 기록).
7차: 확인 응답을 기다린 뒤 활성이 되는 경로(bundle_wait)도 잰다 — 시트 순서를 바꾸고 확인 요청을 0.6초 붙잡았다가 돌려준다.
사용법: python button_transition_check.py <출력 폴더> <라벨>   환경: ONLY_CHECKS=login,review,bundle (비우면 전부), DIAG_FE_IMAGE(출처 기록)
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from transition_summary import summarize

from diag_common import ASSET_JS, EV, URL, USERS, creds, run_meta
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
LABEL = sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
RES: dict[str, list] = {"transition": []}
BLOCKED: list[str] = []
META: dict = {}
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
LOGIN_FAIL = '{"detail": "아이디 또는 비밀번호가 올바르지 않습니다."}'  # 서버(auth_api.login)의 401 본문과 같은 모양

SAMPLER_JS = """sels => { window.__bt = new Promise(resolve => {
  const t0 = performance.now(), out = {}
  const back = el => { for (let p = el.parentElement; p; p = p.parentElement) { const c = getComputedStyle(p).backgroundColor; if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) return c } return 'rgb(255, 255, 255)' }
  for (const s of sels) out[s] = {frames: [], saw_disabled: false, enabled_at: null, enabled_index: null}
  const tick = () => {
    const now = performance.now() - t0
    let done = true
    for (const s of sels) {
      const b = document.querySelector(s), o = out[s]
      if (b) { const cs = getComputedStyle(b)
        o.frames.push({t: Math.round(now), t_raw: now, disabled: b.disabled, hover: b.matches(':hover'), focused: document.activeElement === b, focus_visible: b.matches(':focus-visible'),
          font_size: cs.fontSize, font_weight: cs.fontWeight, color: cs.color, bg: cs.backgroundColor, opacity: cs.opacity, around: back(b), text: b.textContent.trim().slice(0, 30)})
        if (b.disabled) o.saw_disabled = true
        if (o.saw_disabled && !b.disabled && o.enabled_at === null) { o.enabled_at = now; o.enabled_index = o.frames.length - 1 } }
      if (!(o.enabled_at !== null && now - o.enabled_at > 600)) done = false
    }
    if (done || now > 8000) return resolve(out)
    requestAnimationFrame(tick)
  }
  tick()  // 설치하는 순간 첫 프레임을 바로 찍는다 (곧바로 이어지는 키 입력보다 먼저 — 비활성 상태를 놓치지 않게)
}) }"""


def rec(item: dict) -> None:
    RES["transition"].append(item)
    print(f"{item['run']} · {item['button'][:22]} · 관측 {item['observed']} · 활성 최저 {item['min_enabled_ratio'] and round(item['min_enabled_ratio'], 3)} "
          f"(기준 {item['need']}) · 미달 프레임 {item['enabled_below_need']} · 정지 {item['final_ratio'] and round(item['final_ratio'], 3)}", flush=True)  # fmt: skip


def holder(held: list):
    """요청을 붙잡아 목록에 넣는 경로 처리기 (Playwright가 넘기는 두 번째 인자 request는 쓰지 않는다)."""

    def handle(route, *_):
        held.append(route)

    return handle


def sample(pg: Page, sels: list[str], action) -> dict:
    pg.evaluate(SAMPLER_JS, sels)
    action()
    return pg.evaluate("() => window.__bt")


def check_login(b) -> None:
    btn = ".login-card button[type=submit]"
    for vp, size in VIEWPORTS.items():
        for method in ("mouse_stay", "mouse_away", "keyboard"):
            cx = b.new_context(viewport=size, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            pg.goto(URL + "/")
            pg.wait_for_selector(".login-card")
            held: list = []
            pg.route("**/api/auth/login", holder(held))  # 서버로 보내지 않고 붙잡는다
            pg.get_by_label("아이디").fill(USERS["user1"])
            pg.get_by_label("비밀번호", exact=True).fill("시험용-응답-서버로-보내지-않음")

            def act(method=method, pg=pg, held=held):
                if method == "keyboard":
                    pg.mouse.move(2, 2)
                    pg.get_by_label("비밀번호", exact=True).press("Enter")
                else:
                    pg.locator(btn).click()
                    if method == "mouse_away":
                        pg.mouse.move(2, 2)
                pg.wait_for_timeout(500)  # 비활성(로그인 중)이 그려지도록 0.5초 뒤 응답
                for r in list(held):
                    r.fulfill(status=401, content_type="application/json", body=LOGIN_FAIL)
                held.clear()

            rec(summarize(f"login.ctl.{vp}.{method}", "로그인 (.mapping-run)", "시험용 응답", vp, method, sample(pg, [btn], act)[btn]))
            cx.close()
        for i in (1, 2):  # 실제 서버 응답 — 없는 아이디 (실패 횟수가 쌓이는 계정이 없음)
            cx = b.new_context(viewport=size, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            pg.goto(URL + "/")
            pg.wait_for_selector(".login-card")
            pg.get_by_label("아이디").fill("diag-no-such-user")
            pg.get_by_label("비밀번호", exact=True).fill("없는-아이디-확인용")
            rec(summarize(f"login.real.{vp}.{i}", "로그인 (.mapping-run)", "실제 서버 응답", vp, "mouse_stay", sample(pg, [btn], lambda pg=pg: pg.locator(btn).click())[btn]))
            cx.close()


def login(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


def check_review(b) -> None:
    """홈 '사전 검토 열기'(.wk-btn-primary)·'검증 실행'(.wk-btn-outline): 검증 대상이 0개면 비활성, 하나 고르면 활성 (서버 요청 없음)."""
    open_btn = ".wk-val-side button.wk-btn-primary"
    run_btn = ".wk-val-side button.wk-btn-outline"
    for vp, size in VIEWPORTS.items():
        for method in ("hover", "away"):
            cx = b.new_context(viewport=size, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            login(pg, "user1")
            pg.wait_for_timeout(800)
            # 이미 고른 검증 대상·전월 자료를 모두 풀어 두 버튼이 비활성인 상태에서 시작한다
            for _ in range(60):
                checked = pg.locator(".wk-validation input[type=checkbox]:checked")
                if not checked.count():
                    break
                checked.first.focus()
                pg.keyboard.press("Space")
                pg.wait_for_timeout(150)
            box = pg.locator("input[type=checkbox][aria-label$='검증 대상']").first
            pg.wait_for_timeout(400)
            if not pg.locator(open_btn).is_disabled():
                raise RuntimeError("시작 전 '사전 검토 열기'가 비활성이 아님 — 선택을 다 풀지 못함")
            pg.locator(open_btn).scroll_into_view_if_needed()
            if method == "hover":
                bb = pg.locator(open_btn).bounding_box()
                pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)  # 비활성 버튼 위에 마우스를 둔 채
            else:
                pg.mouse.move(2, 2)
            box.focus()
            data = sample(pg, [open_btn, run_btn], lambda pg=pg: pg.keyboard.press("Space"))
            rec(summarize(f"review.{vp}.{method}", "사전 검토 열기 (.wk-btn-primary)", "실제 화면 (서버 요청 없음)", vp, method, data[open_btn]))
            rec(summarize(f"verify.{vp}.{method}", "검증 실행 (.wk-btn-outline)", "실제 화면 (서버 요청 없음)", vp, method, data[run_btn]))
            pg.keyboard.press("Space")  # 원래대로 (고른 것 해제)
            cx.close()


def check_bundle(b) -> None:
    """묶기 '엑셀 만들기'(.mapping-run): 요약을 켜면 확인 전 구성이라 비활성, 바로 끄면 이미 확인된 구성으로 돌아와 곧바로 활성.
    '확인 응답 뒤 활성' 경로는 아니다 (같은 .mapping-run의 '서버 응답 뒤 활성'은 로그인에서 잰다).
    묶기 '위로'(.mapping-confirm): 첫 시트를 '아래로' 옮기면 그 시트의 '위로'가 활성."""
    make = ".bundle button.mapping-run"
    for vp, size in VIEWPORTS.items():
        for method in ("hover", "away"):
            cx = b.new_context(viewport=size, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            login(pg, "user1")
            pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
            pg.wait_for_load_state("networkidle")
            pg.wait_for_function(f"() => {{ const b = document.querySelector({json.dumps(make)}); return b && !b.disabled }}", timeout=60000)
            held: list = []
            pg.route("**/api/bundle/plan", holder(held))
            pg.locator(make).scroll_into_view_if_needed()
            if method == "hover":
                bb = pg.locator(make).bounding_box()
                pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            else:
                pg.mouse.move(2, 2)
            sw = pg.locator("label:has(input.peer.sr-only[type=checkbox]) input[type=checkbox]").first

            count: list[int] = []

            def act(pg=pg, sw=sw, held=held, count=count):
                sw.focus()
                pg.keyboard.press("Space")  # 요약 켬 → 끔 (최종 구성은 이미 확인된 것과 같음)
                pg.keyboard.press("Space")
                pg.wait_for_timeout(600)  # 그동안 확인 결과가 바뀌지 않게 확인 요청을 붙잡아 둔 시간
                count.append(len(held))
                for r in list(held):
                    r.continue_()
                held.clear()

            data = sample(pg, [make], act)
            pg.unroute("**/api/bundle/plan")
            item = summarize(f"bundle_make.{vp}.{method}", "묶기 엑셀 만들기 (.mapping-run)", "실제 화면 (켰다 끈 구성 → 확인된 구성, 응답을 기다리지 않음)", vp, method, data[make])
            rec(item | {"held_plan_requests": count[0] if count else None})
            cx.close()
    # 묶기 '엑셀 만들기' — 확인 응답을 기다린 뒤 활성: 시트 순서를 바꾸면(키보드) 새 구성의 확인 요청이 나가고, 응답이 올 때까지 비활성
    for vp, size in VIEWPORTS.items():
        for method in ("hover", "away"):
            cx = b.new_context(viewport=size, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            login(pg, "user1")
            pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
            pg.wait_for_load_state("networkidle")
            pg.wait_for_function(f"() => {{ const b = document.querySelector({json.dumps(make)}); return b && !b.disabled }}", timeout=60000)
            first = pg.locator("ol.bd-sheets li").first.locator("strong, .bd-sheet-name").first.inner_text().strip()
            held: list = []
            pg.route("**/api/bundle/plan", holder(held))
            pg.locator(make).scroll_into_view_if_needed()
            if method == "hover":
                bb = pg.locator(make).bounding_box()
                pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)  # 마우스는 엑셀 만들기 위에 둔 채
            else:
                pg.mouse.move(2, 2)
            pg.locator(f"button[aria-label='{first} 아래로']").focus()
            count: list[int] = []

            def act(pg=pg, held=held, count=count):
                pg.keyboard.press("Enter")  # 구성 변경 → 확인 요청 (250ms 뒤)
                pg.wait_for_timeout(600)  # 확인 응답을 붙잡아 둔 시간 — 그동안 비활성
                count.append(len(held))
                for r in list(held):
                    r.continue_()
                held.clear()

            data = sample(pg, [make], act)
            pg.unroute("**/api/bundle/plan")
            item = summarize(f"bundle_wait.{vp}.{method}", "묶기 엑셀 만들기 (.mapping-run)", "실제 서버 응답 (순서 변경 → 확인 응답 0.6초 붙잡은 뒤 활성)", vp, method, data[make])
            rec(item | {"held_plan_requests": count[0] if count else None})
            cx.close()
    cx = b.new_context(viewport=VIEWPORTS["desktop"], locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg, "user1")
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1000)
    first = pg.locator("ol.bd-sheets li").first.locator("strong, .bd-sheet-name").first.inner_text().strip()
    up, down = f"button[aria-label='{first} 위로']", f"button[aria-label='{first} 아래로']"
    pg.mouse.move(2, 2)
    pg.locator(down).focus()
    data = sample(pg, [up], lambda: pg.keyboard.press("Enter"))
    rec(summarize("bundle_up.desktop.keyboard", "묶기 위로 (.mapping-confirm)", "실제 화면 (서버 요청 없음)", "desktop", "keyboard", data[up]))
    pg.locator(up).focus()
    pg.keyboard.press("Enter")  # 원래 순서로
    cx.close()


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = b.new_context()
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        META.update(run_meta("button_transition_check.py", LABEL, pg.evaluate(ASSET_JS)) | {"browser": f"msedge {b.version}"})
        cx.close()
        for name, fn in (("login", check_login), ("review", check_review), ("bundle", check_bundle)):
            if os.environ.get("ONLY_CHECKS") and name not in os.environ["ONLY_CHECKS"].split(","):
                continue
            try:
                fn(b)
            except Exception as e:  # noqa: BLE001 — 측정 오류로 기록 (통과로 세지 않음)
                RES.setdefault("error", []).append({"check": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                print("오류", name, type(e).__name__, str(e)[:200], flush=True)
        b.close()
    RES["blocked_out_of_scope"] = BLOCKED
    RES["meta"] = META
    (OUT / f"transition-{LABEL}.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
