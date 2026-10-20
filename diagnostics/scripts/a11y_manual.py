"""접근성 수동 확인 보조 — 실제 브라우저(Edge)에서 키보드만으로 조작하고 초점·재배치·확대·간격·대상 크기·상태 메시지를 기록한다.

판정은 기록과 캡처를 사람이(이번에는 검토자가 캡처·기록을 보고) 내린다. 스크린리더 낭독 확인은 이 스크립트로 대신하지 않는다.
사용법: python a11y_manual.py <출력 폴더> [검사 이름,...]   (검사: keyboard, dialog, analysis, bundle, upload, reflow, zoom, authforms, spacing, target, status, login)
"""

import io
import json
import re
import sys
from pathlib import Path

import openpyxl
from playwright.sync_api import Page, sync_playwright

from diag_common import EV, URL, USERS, creds
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else set()
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
ZOOMEXT = Path(__file__).resolve().parents[1] / "tools" / "zoomext"
REC: dict[str, list] = {}

FOCUS_JS = """() => {
  const e = document.activeElement
  if (!e || e === document.body || e === document.documentElement) return null
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e)
  const name = (e.getAttribute('aria-label') || e.labels?.[0]?.innerText || e.innerText || e.value || e.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 50)
  const pts = [[r.left + 3, r.top + 3], [r.right - 3, r.top + 3], [r.left + 3, r.bottom - 3], [r.right - 3, r.bottom - 3], [r.left + r.width / 2, r.top + r.height / 2]]
  let covered = 0
  for (const [x, y] of pts) {
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) { covered++; continue }
    const h = document.elementFromPoint(x, y)
    if (!h || !(e === h || e.contains(h) || h.contains(e))) covered++
  }
  return {tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), type: e.getAttribute('type'), name,
          outline: cs.outlineStyle === 'none' ? 'none' : cs.outlineStyle + ' ' + cs.outlineWidth, shadow: cs.boxShadow === 'none' ? 'none' : 'yes',
          rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], covered, inView: r.bottom > 0 && r.top < innerHeight,
          inDialog: !!e.closest('[role=dialog],[role=alertdialog]')}
}"""

OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth
  const page = document.scrollingElement.scrollWidth - vw
  const out = []
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect()
    if (!r.width || r.right <= vw + 1) continue
    let p = e.parentElement, inScroller = false
    while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { inScroller = true; break } p = p.parentElement }
    if (!inScroller) out.push((e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : e.tagName.toLowerCase()) + ' ' + Math.round(r.right))
  }
  const scrollers = [...document.querySelectorAll('*')].filter(e => { const o = getComputedStyle(e).overflowX; return (o === 'auto' || o === 'scroll') && e.scrollWidth > e.clientWidth + 1 })
    .map(e => ({cls: (e.className || e.tagName).toString().split(' ')[0], focusable: e.tabIndex >= 0 || !!e.querySelector('a,button,input,select,textarea,[tabindex]'), table: !!e.querySelector('table')}))
  return {pageOverflow: page, outside: [...new Set(out)].slice(0, 15), scrollers}
}"""

TARGET_JS = """() => {
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=tab],[role=checkbox],[role=radio],[role=switch],summary,[tabindex]:not([tabindex="-1"])'
  // 스크롤·잘림 영역(overflow가 visible이 아닌 조상) 밖으로 가려 보이지 않는 대상은 세지 않는다 (7차: 긴 파일 표의 가려진 행이 표 밖 요소와 겹쳐 잡히던 측정 오류)
  const clipped = e => { const r = e.getBoundingClientRect(); for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const s = getComputedStyle(p);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') { const q = p.getBoundingClientRect(); if (r.bottom <= q.top || r.top >= q.bottom || r.right <= q.left || r.left >= q.right) return true } } return false }
  const els = [...document.querySelectorAll(sel)].filter(e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width && r.height && cs.visibility !== 'hidden' && !e.closest('[hidden]') && !clipped(e) })
  const rects = els.map(e => e.getBoundingClientRect())
  const small = []
  els.forEach((e, i) => {
    const r = rects[i]
    if (r.width >= 24 && r.height >= 24) return
    const inline = e.tagName === 'A' && getComputedStyle(e).display === 'inline' && e.parentElement && e.parentElement.innerText.trim().length > e.innerText.trim().length + 5
    // 간격 예외 근사: 대상 중심의 지름 24px 원이 다른 대상과 겹치지 않으면 예외
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2
    // 감싸는 요소(스크롤 영역 등)·안에 든 요소는 '다른 대상'으로 세지 않는다
    const near = rects.some((o, j) => j !== i && !els[j].contains(e) && !e.contains(els[j]) && Math.max(o.left - cx, 0, cx - o.right) ** 2 + Math.max(o.top - cy, 0, cy - o.bottom) ** 2 < 12 * 12)
    small.push({tag: e.tagName.toLowerCase(), role: e.getAttribute('role'), name: (e.getAttribute('aria-label') || e.innerText || e.value || '').trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), inline, spacingOk: !near})
  })
  return {total: els.length, small}
}"""

LIVE_JS = """() => [...document.querySelectorAll('[role=status],[role=alert],[aria-live],[role=log],[role=progressbar]')].map(e => ({role: e.getAttribute('role'), live: e.getAttribute('aria-live'), text: e.innerText.trim().slice(0, 80), visible: !!(e.offsetWidth || e.offsetHeight)}))"""


def rec(group: str, item: dict) -> None:
    REC.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:260], flush=True)


BLOCKED: list[str] = []


def guard_ctx(cx) -> None:
    """첫 요청이 진단 주소 밖이면 막는다 (리다이렉트 이후 요청은 launch의 browser_proxy가 막음)."""
    cx.route("**/*", route_guard(URL, BLOCKED))


def settle(pg: Page) -> None:
    pg.wait_for_load_state("networkidle", timeout=60000)
    pg.wait_for_function("() => !document.body.innerText.includes('불러오는 중')", timeout=60000)
    pg.wait_for_timeout(400)


def focus(pg: Page) -> dict | None:
    return pg.evaluate(FOCUS_JS)


def tab_walk(pg: Page, n: int, label: str, shift: bool = False) -> list:
    seq = []
    for _ in range(n):
        pg.keyboard.press("Shift+Tab" if shift else "Tab")
        f = focus(pg)
        seq.append(f)
    rec("tab-order", {"where": label, "stops": [f"{(x or {}).get('tag')}:{(x or {}).get('name')}" for x in seq],
                      "no_visible_focus": [x["name"] for x in seq if x and x["outline"] == "none" and x["shadow"] == "none"],
                      "obscured": [x["name"] for x in seq if x and x["covered"] >= 3 and x["inView"]]})  # fmt: skip
    return seq


def kb_login(pg: Page, who: str) -> None:
    """마우스 없이 로그인: Tab으로 아이디 칸까지 가서 글자 입력 → Tab → 비밀번호 → Enter."""
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    stops = []
    for _ in range(8):
        pg.keyboard.press("Tab")
        f = focus(pg)
        stops.append(f)
        if f and f["tag"] == "input" and f["type"] in ("text", None) and "아이디" in (f["name"] or ""):
            break
    pg.keyboard.type(USERS[who])
    pg.keyboard.press("Tab")
    f2 = focus(pg)
    pg.keyboard.type(creds()[who])
    pg.keyboard.press("Enter")
    pg.wait_for_selector(".shell", timeout=60000)
    rec("login", {"who": who, "tabs_to_username": len(stops), "first_stops": [f"{(x or {}).get('tag')}:{(x or {}).get('name')}" for x in stops],
                  "password_field": f2 and f2.get("type"), "landed": pg.evaluate("location.pathname"), "focus_after": focus(pg)})  # fmt: skip


def press_until(pg: Page, pred, n: int = 60, key: str = "Tab") -> dict | None:
    for _ in range(n):
        pg.keyboard.press(key)
        f = focus(pg)
        if f and pred(f):
            return f
    return None


def check_keyboard_nav(pg: Page) -> None:
    kb_login(pg, "user1")
    tab_walk(pg, 25, "홈(로그인 직후)")
    f = press_until(pg, lambda f: f["name"] == "검토 목록")
    pg.keyboard.press("Enter")
    settle(pg)
    rec("nav", {"menu_reached": bool(f), "path": pg.evaluate("location.pathname"), "focus_after_nav": focus(pg),
                "title": pg.title()})  # fmt: skip
    # 검토 열기 → 상세 → Esc/닫기 후 초점
    f = press_until(pg, lambda f: "검토" in (f["name"] or "") and f["tag"] in ("a", "button") and "#" in (f["name"] or ""), 40)
    rec("review-open", {"found": f})
    if f:
        pg.keyboard.press("Enter")
        settle(pg)
        rec("review-open", {"after": pg.evaluate("location.pathname + location.search"), "focus": focus(pg)})
        pg.screenshot(path=str(OUT / "kb-review-detail.png"))
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(600)
        rec("review-open", {"after_escape": pg.evaluate("location.pathname + location.search"), "focus": focus(pg)})
    # 열 매핑 탭: Tab으로 '데이터 미리보기' 탭 → Enter
    pg.goto(URL + f"/mapping?file={IDS['user1']['multi']}")
    settle(pg)
    t = press_until(pg, lambda f: f["role"] == "tab" and "미리보기" in f["name"], 80)
    if t:
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(600)
        sel = pg.evaluate("[...document.querySelectorAll('[role=tab]')].map(e => e.innerText.trim() + ':' + e.getAttribute('aria-selected'))")
        pg.keyboard.press("ArrowLeft")
        arrow = focus(pg)
        rec("tabs", {"reached": t["name"], "selected_after_enter": sel, "focus_after_arrowleft": arrow and arrow["name"]})
    else:
        rec("tabs", {"reached": None})
    tab_walk(pg, 40, "열 매핑(여러 시트)")


def check_dialog(pg: Page) -> None:
    kb_login(pg, "user1")
    pg.goto(URL + "/automations")
    settle(pg)
    f = press_until(pg, lambda f: "월간 인구 취합" in (f["name"] or ""), 60)
    if f:
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(800)
    trig = press_until(pg, lambda f: f["name"] == "자동화 삭제", 60)
    rec("dialog", {"trigger": trig})
    if not trig:
        return
    pg.keyboard.press("Enter")
    pg.get_by_role("alertdialog").wait_for(timeout=15000)
    first = focus(pg)
    cycle = [focus(pg) for _ in range(0)]
    for _ in range(5):
        pg.keyboard.press("Tab")
        cycle.append(focus(pg))
    shift = []
    for _ in range(3):
        pg.keyboard.press("Shift+Tab")
        shift.append(focus(pg))
    dlg = pg.evaluate("(() => { const d = document.querySelector('[role=alertdialog]'); return {labelledby: d.getAttribute('aria-labelledby'), describedby: d.getAttribute('aria-describedby'), modal: d.getAttribute('aria-modal'), name: d.getAttribute('aria-label') || (document.getElementById(d.getAttribute('aria-labelledby') || '')?.innerText) } })()")
    pg.screenshot(path=str(OUT / "kb-dialog-open.png"))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(600)
    after = focus(pg)
    rec("dialog", {"first_focus": first and first["name"], "tab_cycle": [x and f"{x['name']}|dlg={x['inDialog']}" for x in cycle],
                   "shift_tab": [x and f"{x['name']}|dlg={x['inDialog']}" for x in shift], "dialog_attrs": dlg,
                   "closed": pg.get_by_role("alertdialog").count() == 0, "focus_after_escape": after and after["name"]})  # fmt: skip
    # 다시 열고 '취소'를 Enter로 → 초점 복귀
    pg.keyboard.press("Enter") if after and after["name"] == "자동화 삭제" else None
    if pg.get_by_role("alertdialog").count():
        pg.keyboard.press("Enter")  # 처음 초점 = 취소
        pg.wait_for_timeout(600)
        rec("dialog", {"cancel_by_enter_closed": pg.get_by_role("alertdialog").count() == 0, "focus_after_cancel": (focus(pg) or {}).get("name"),
                       "automation_still_exists": "월간 인구 취합" in pg.inner_text("main")})  # fmt: skip


def check_analysis(pg: Page) -> None:
    kb_login(pg, "user1")
    pg.goto(URL + "/analysis")
    settle(pg)
    s = press_until(pg, lambda f: f["tag"] == "summary", 80)
    if s:
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(400)
    link = press_until(pg, lambda f: f["tag"] == "a" and f"#{IDS['user1_analysis_many']} " in ((f["name"] or "") + " "), 20)
    rec("analysis", {"summary": s and s["name"], "link": link and link["name"]})
    if not link:
        return
    pg.keyboard.press("Enter")
    pg.locator("table.an-result tbody tr").first.wait_for(timeout=60000)
    settle(pg)
    rec("analysis", {"opened": pg.evaluate("location.search"), "focus_after_open": focus(pg)})
    nxt = press_until(pg, lambda f: f["name"].startswith("다음 50개"), 200)
    first_before = pg.locator("table.an-result tbody tr").first.inner_text()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(1200)
    rec("analysis", {"next_found": bool(nxt), "pager": pg.locator(".mw-pager").first.inner_text(), "focus_after_next": focus(pg),
                     "rows_changed": pg.locator("table.an-result tbody tr").first.inner_text() != first_before})  # fmt: skip
    # 표 영역 스크롤 가능 여부(키보드로 초점이 가는지)
    sc = pg.evaluate("[...document.querySelectorAll('.upload-table-wrap')].map(e => ({scrollable: e.scrollWidth > e.clientWidth, tabindex: e.tabIndex}))")
    rec("analysis", {"table_wrappers": sc})
    pg.keyboard.press("Home")
    dl = press_until(pg, lambda f: f["tag"] == "a" and "Excel" in f["name"], 200, "Shift+Tab")
    if dl:
        with pg.expect_download(timeout=60000) as d:
            pg.keyboard.press("Enter")
        rec("analysis", {"download_by_enter": d.value.suggested_filename})


def check_bundle(pg: Page) -> None:
    kb_login(pg, "user1")
    pg.goto(URL + f"/bundle?files={IDS['user1']['multi']},{IDS['user1']['basic']}")
    settle(pg)
    pg.screenshot(path=str(OUT / "kb-bundle-start.png"), full_page=True)
    seq = tab_walk(pg, 60, "파일별 묶기(여러 시트 파일 포함)")
    names = [x["name"] for x in seq if x]
    rec("bundle", {"has_sheet_checkboxes": any("현황" in n for n in names), "has_move_buttons": [n for n in names if n.endswith(("위로", "아래로"))][:6]})
    # 시트 고르기: 체크 상자 Space
    pg.keyboard.press("Home")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    picked = []
    for _ in range(80):
        pg.keyboard.press("Tab")
        f = focus(pg)
        if f and f["tag"] == "input" and f["type"] == "checkbox" and "현황" in f["name"]:
            pg.keyboard.press("Space")
            picked.append(f["name"])
        if len(picked) >= 2:
            break
    pg.wait_for_timeout(800)
    order_before = pg.locator("ol.bd-sheets li").all_inner_texts() if pg.locator("ol.bd-sheets").count() else []
    down = press_until(pg, lambda f: f["name"].endswith("아래로") and f["tag"] == "button", 120)
    if down:
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(500)
    order_after = pg.locator("ol.bd-sheets li").all_inner_texts() if pg.locator("ol.bd-sheets").count() else []
    rec("bundle", {"picked": picked, "move_button": down and down["name"], "focus_after_move": (focus(pg) or {}).get("name"),
                   "order_before": [o.split("\n")[0] for o in order_before], "order_after": [o.split("\n")[0] for o in order_after]})  # fmt: skip
    pg.screenshot(path=str(OUT / "kb-bundle-after-move.png"), full_page=True)
    # 요약 켬/끔 (라디오·스위치) — Space/화살표
    sw = press_until(pg, lambda f: f["type"] in ("radio", "checkbox") and "요약" in f["name"], 120)
    if sw:
        st0 = pg.evaluate("document.activeElement.checked")
        pg.keyboard.press("Space")
        pg.wait_for_timeout(500)
        rec("bundle", {"summary_toggle": sw["name"], "checked_before": st0, "checked_after": pg.evaluate("document.activeElement.checked")})
    plan = press_until(pg, lambda f: f["tag"] == "button" and ("구성" in f["name"] or "확인" in f["name"]), 120)
    rec("bundle", {"plan_button": plan and plan["name"]})
    pg.screenshot(path=str(OUT / "kb-bundle-end.png"), full_page=True)


def check_upload(pg: Page) -> None:
    kb_login(pg, "user1")
    f = press_until(pg, lambda f: (f["role"] == "button" or f["tag"] == "button") and "파일" in f["name"], 60)  # 영역(role=button) 또는 실제 버튼
    rec("upload", {"dropzone": f})
    if not f:
        return
    wb = openpyxl.Workbook()
    wb.active.append(["주민등록 인구 현황 2026년 9월 (단위: 명)"])
    wb.active.append(["시도", "인구수"])
    wb.active.append(["세종특별자치시", 123])
    buf = io.BytesIO()
    wb.save(buf)
    live_before = pg.evaluate(LIVE_JS)
    with pg.expect_file_chooser(timeout=15000) as fc:
        pg.keyboard.press("Enter")
    fc.value.set_files(files=[{"name": "키보드_업로드_합성.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": buf.getvalue()}])
    seen = []
    for _ in range(20):
        pg.wait_for_timeout(300)
        seen.append(pg.evaluate(LIVE_JS))
    settle(pg)
    rec("upload", {"live_before": live_before, "live_during": seen[2] if len(seen) > 2 else None, "live_after": pg.evaluate(LIVE_JS), "focus_after": focus(pg)})
    pg.screenshot(path=str(OUT / "kb-upload-after.png"), full_page=True)


REFLOW_PAGES = [("login", None, "/"), ("home", "user1", "/"), ("mapping", "user1", f"/mapping?file={IDS['user1']['long']}"),
                ("reviews", "user1", "/reviews"), ("review-detail", "user1", f"/reviews?review={IDS['user1_review']}"),
                ("runs", "user1", "/runs"), ("bundle", "user1", f"/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}"),
                ("analysis-detail", "user1", f"/analysis?id={IDS['user1_analysis_many']}"), ("account", "user1", "/account"),
                ("admin-users", "owner", "/moajwoAdmin/users"), ("settings", "owner", "/settings"), ("dictionary", "owner", "/dictionary")]  # fmt: skip


def login_fast(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)


def check_reflow(b) -> None:
    """320 CSS px (= 1280 px에서 400% 확대와 같은 폭)에서 페이지 가로 넘침과 넘치는 요소 (표 같은 2차원 영역의 내부 스크롤은 따로)."""
    ctxs = {}
    for name, who, path in REFLOW_PAGES:
        key = who or "anon"
        if key not in ctxs:
            cx = b.new_context(viewport={"width": 320, "height": 640}, locale="ko-KR")
            guard_ctx(cx)
            pg = cx.new_page()
            if who:
                login_fast(pg, who)
            ctxs[key] = (cx, pg)
        pg = ctxs[key][1]
        pg.goto(URL + path)
        settle(pg)
        o = pg.evaluate(OVERFLOW_JS)
        pg.screenshot(path=str(OUT / f"reflow320-{name}.png"), full_page=True)
        rec("reflow320", {"page": name, **o})
    for cx, _ in ctxs.values():
        cx.close()


def check_zoom(p) -> None:
    """실제 브라우저 확대 200% (1280×800 창) — 확장의 chrome.tabs.setZoom."""
    import tempfile

    cx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="msedge", headless=True, viewport={"width": 1280, "height": 800}, proxy=browser_proxy(URL),
                                              args=[f"--disable-extensions-except={ZOOMEXT}", f"--load-extension={ZOOMEXT}"])  # fmt: skip
    guard_ctx(cx)
    pg = cx.pages[0] if cx.pages else cx.new_page()
    pg.goto(URL + "/")
    sw = cx.service_workers[0] if cx.service_workers else cx.wait_for_event("serviceworker", timeout=15000)
    login_fast(pg, "user1")
    for name, _who, path in [x for x in REFLOW_PAGES if x[1] in (None, "user1")]:
        pg.goto(URL + path)
        sw.evaluate("chrome.tabs.query({}).then(ts => Promise.all(ts.map(t => chrome.tabs.setZoom(t.id, 2.0))))")
        pg.wait_for_timeout(800)
        settle(pg)
        o = pg.evaluate(OVERFLOW_JS)
        css = pg.evaluate("[innerWidth, devicePixelRatio]")
        cdp = cx.new_cdp_session(pg)
        m = cdp.send("Page.getLayoutMetrics")
        cs = m["contentSize"]
        import base64

        shot = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                   "clip": {"x": 0, "y": 0, "width": cs["width"], "height": min(cs["height"], 6000), "scale": 1}})  # fmt: skip
        (OUT / f"zoom200-{name}.png").write_bytes(base64.b64decode(shot["data"]))
        rec("zoom200", {"page": name, "css_width_dpr": css, **o})
    cx.close()


SETUP_STATUS = '{"setup_required": true, "user": null}'


def open_auth_form(pg: Page, mode: str) -> None:
    """회원가입 또는 최초 설정 화면을 띄운다. 최초 설정은 상태 응답만 브라우저에서 바꾼다 (서버에 쓰지 않음)."""
    pg.unroute("**/api/auth/status")
    if mode == "setup":
        pg.route("**/api/auth/status", lambda r: r.fulfill(status=200, content_type="application/json", body=SETUP_STATUS))
    pg.goto(URL + "/")
    if mode == "signup":
        pg.get_by_role("button", name=re.compile("회원가입")).first.click()
    pg.locator(".login-help").first.wait_for(timeout=15000)
    settle(pg)


def check_authforms(p) -> None:
    """도움말이 붙은 회원가입·최초 설정 화면: 320 CSS px 재배치(확대 100%)와 실제 브라우저 확대 200% (1280×800 창, check_zoom과 같은 확장)."""
    import base64
    import tempfile

    cx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="msedge", headless=True, viewport={"width": 1280, "height": 800}, proxy=browser_proxy(URL),
                                              args=[f"--disable-extensions-except={ZOOMEXT}", f"--load-extension={ZOOMEXT}"])  # fmt: skip
    guard_ctx(cx)
    pg = cx.pages[0] if cx.pages else cx.new_page()
    pg.goto(URL + "/")
    sw = cx.service_workers[0] if cx.service_workers else cx.wait_for_event("serviceworker", timeout=15000)
    set_zoom = "z => chrome.tabs.query({}).then(ts => Promise.all(ts.map(t => chrome.tabs.setZoom(t.id, z))))"
    for mode in ("signup", "setup"):
        sw.evaluate(set_zoom, 1.0)
        pg.set_viewport_size({"width": 320, "height": 640})
        open_auth_form(pg, mode)
        o = pg.evaluate(OVERFLOW_JS)
        pg.screenshot(path=str(OUT / f"reflow320-{mode}.png"), full_page=True)
        rec("auth-reflow320", {"page": mode, "css_width_dpr": pg.evaluate("[innerWidth, devicePixelRatio]"), "help": pg.locator(".login-help").count(), **o})
        pg.set_viewport_size({"width": 1280, "height": 800})
        open_auth_form(pg, mode)
        sw.evaluate(set_zoom, 2.0)
        pg.wait_for_timeout(800)
        settle(pg)
        o = pg.evaluate(OVERFLOW_JS)
        cdp = cx.new_cdp_session(pg)
        cs = cdp.send("Page.getLayoutMetrics")["contentSize"]
        shot = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                   "clip": {"x": 0, "y": 0, "width": cs["width"], "height": min(cs["height"], 6000), "scale": 1}})  # fmt: skip
        (OUT / f"zoom200-{mode}.png").write_bytes(base64.b64decode(shot["data"]))
        rec("auth-zoom200", {"page": mode, "css_width_dpr": pg.evaluate("[innerWidth, devicePixelRatio]"), "help": pg.locator(".login-help").count(), **o})
    cx.close()


SPACING_CSS = "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }"
CLIP_JS = """() => [...document.querySelectorAll('body *')].filter(e => { const cs = getComputedStyle(e); if (!e.innerText || e.children.length > 3) return false;
  const hid = (cs.overflow + cs.overflowX + cs.overflowY).includes('hidden') || cs.textOverflow === 'ellipsis' || cs.webkitLineClamp !== 'none'
  return hid && (e.scrollHeight > e.clientHeight + 2 || e.scrollWidth > e.clientWidth + 2) && e.offsetWidth > 0 })
  .map(e => (e.className || e.tagName).toString().split(' ')[0] + ': ' + e.innerText.trim().slice(0, 30)).slice(0, 20)"""


def check_spacing(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    guard_ctx(cx)
    pg = cx.new_page()
    login_fast(pg, "user1")
    for name, _who, path in [x for x in REFLOW_PAGES if x[1] == "user1"]:
        pg.goto(URL + path)
        settle(pg)
        before = pg.evaluate(CLIP_JS)
        pg.add_style_tag(content=SPACING_CSS)
        pg.wait_for_timeout(400)
        after = pg.evaluate(CLIP_JS)
        pg.screenshot(path=str(OUT / f"spacing-{name}.png"), full_page=True)
        rec("spacing", {"page": name, "clipped_before": before, "clipped_after": [x for x in after if x not in before]})
    cx.close()


def check_target(b) -> None:
    for vp, size in (("desktop", {"width": 1280, "height": 800}), ("mobile", {"width": 390, "height": 844})):
        cx = b.new_context(viewport=size, locale="ko-KR")
        guard_ctx(cx)
        pg = cx.new_page()
        login_fast(pg, "user1")
        for name, _who, path in [x for x in REFLOW_PAGES if x[1] == "user1"]:
            pg.goto(URL + path)
            settle(pg)
            t = pg.evaluate(TARGET_JS)
            fails = [x for x in t["small"] if not x["inline"] and not x["spacingOk"]]
            rec("target", {"vp": vp, "page": name, "interactive": t["total"], "under24": len(t["small"]), "under24_no_exception": fails[:12]})
        cx.close()


def check_status(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    guard_ctx(cx)
    pg = cx.new_page()
    login_fast(pg, "user1")
    # 조회 실패 메시지가 보조 기술에 전달되는지 (500 주입)
    pg.route("**/api/reviews?*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"진단용 서버 오류 주입"}'))
    pg.goto(URL + "/reviews")
    pg.wait_for_timeout(2500)
    rec("status", {"state": "검토 목록 조회 실패", "live": pg.evaluate(LIVE_JS), "error_text_present": "오류" in pg.inner_text("main") or "실패" in pg.inner_text("main")})
    pg.unroute_all(behavior="ignoreErrors")
    guard_ctx(cx)
    # 불러오는 중
    held = []
    pg.route("**/api/analysis/runs/*", lambda r: held.append(r))
    pg.goto(URL + f"/analysis?id={IDS['user1_analysis']}")
    pg.wait_for_timeout(1200)
    rec("status", {"state": "분석 결과 불러오는 중", "live": pg.evaluate(LIVE_JS), "busy": pg.evaluate("[...document.querySelectorAll('[aria-busy=true]')].length")})
    for r in held:
        r.continue_()
    pg.unroute_all(behavior="ignoreErrors")
    guard_ctx(cx)
    # 분석 대상 확인 결과·오류 (잘못된 조건)
    pg.goto(URL + "/analysis")
    settle(pg)
    rec("status", {"state": "새 분석 화면", "live": pg.evaluate(LIVE_JS)})
    # 색만으로 상태를 구분하는지: 상태 표시 요소의 글자
    pg.goto(URL + f"/reviews?review={IDS['user1_review']}")
    settle(pg)
    badges = pg.evaluate("[...document.querySelectorAll('[class*=status],[class*=badge],[class*=tag],[class*=is-]')].filter(e => e.offsetWidth).map(e => ({cls: e.className.toString().slice(0, 50), text: e.innerText.trim().slice(0, 30)})).filter(x => !x.text).slice(0, 15)")
    rec("status", {"state": "검토 상세 — 글자 없는 색 표시 후보", "no_text_colored": badges})
    cx.close()


def check_login(b) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    guard_ctx(cx)
    pg = cx.new_page()
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    attrs = pg.evaluate("[...document.querySelectorAll('.login-card input')].map(e => ({type: e.type, name: e.name, autocomplete: e.autocomplete, label: e.labels?.[0]?.innerText.trim(), required: e.required}))")
    # 붙여넣기 허용: 붙여넣기 이벤트를 막는지 (preventDefault)
    pasted = pg.evaluate("""() => { const i = document.querySelector('.login-card input[type=password]'); const dt = new DataTransfer(); dt.setData('text/plain', 'x');
        const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}); return i.dispatchEvent(ev) }""")
    pg.get_by_role("button", name=re.compile("회원가입")).first.click()
    pg.wait_for_timeout(500)
    signup = pg.evaluate("[...document.querySelectorAll('.login-card input')].map(e => ({type: e.type, autocomplete: e.autocomplete, label: e.labels?.[0]?.innerText.trim(), describedby: e.getAttribute('aria-describedby')}))")
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_timeout(800)
    invalid = pg.evaluate("[...document.querySelectorAll('.login-card input')].map(e => ({label: e.labels?.[0]?.innerText.trim(), valid: e.validity.valid, msg: e.validationMessage, ariaInvalid: e.getAttribute('aria-invalid')}))")
    rec("login-assist", {"login_inputs": attrs, "paste_not_blocked": pasted, "signup_inputs": signup, "empty_submit": invalid})
    cx.close()


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))

        def fresh():
            cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", accept_downloads=True)
            guard_ctx(cx)
            return cx, cx.new_page()

        for name, fn in (("keyboard", check_keyboard_nav), ("dialog", check_dialog), ("analysis", check_analysis),
                         ("bundle", check_bundle), ("upload", check_upload)):  # fmt: skip
            if ONLY and name not in ONLY:
                continue
            cx, pg = fresh()
            try:
                fn(pg)
            except Exception as e:  # noqa: BLE001 — 검사 실패로 기록
                rec("error", {"check": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                pg.screenshot(path=str(OUT / f"error-{name}.png"), full_page=True)
            cx.close()
        for name, fn, arg in (("reflow", check_reflow, b), ("zoom", check_zoom, p), ("authforms", check_authforms, p), ("spacing", check_spacing, b),
                              ("target", check_target, b), ("status", check_status, b), ("login", check_login, b)):  # fmt: skip
            if ONLY and name not in ONLY:
                continue
            try:
                fn(arg)
            except Exception as e:  # noqa: BLE001
                rec("error", {"check": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
        b.close()
    path = OUT / "manual-records.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    old.update(REC)  # 그룹 단위로 통째로 바꾼다 — 검사마다 그룹 이름을 따로 둘 것 (같은 이름을 쓰면 다른 검사의 기록이 지워짐)
    path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
