"""D-01~D-07 재현·확인 (진단 격리 스택, 합성 자료) — 수정 전·후 이미지에 같은 조건으로 돌려 비교한다.

판정 근거는 브라우저(Edge)의 계산된 접근성 이름·설명(CDP), 상태 영역의 글자 변화(MutationObserver),
계산된 색과 대비(contrast_calc), DOM 속성이다. 스크린리더 낭독은 확인하지 않는다 (코드·접근성 속성 확인).
진단 자료에 쓰는 동작: 합성 파일 업로드, 사용자2 매핑을 잠시 바꿨다 되돌림, 서버가 거절하는 가입·설정 요청(계정 안 생김).
사용법: python a11y_fix_check.py <출력 폴더> <라벨(before|after)>   환경: ONLY_CHECKS=upload,analysis,contrast,signup,names,stale
"""

import io
import json
import os
import re
import sys
from pathlib import Path

import openpyxl
from contrast_calc import oklab_to_srgb, over, ratio
from playwright.sync_api import Page, sync_playwright

from diag_common import EV, URL, USERS, Api, creds
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
LABEL = sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
RES: dict[str, list] = {}
BLOCKED: list[str] = []
WHITE = (255, 255, 255)

LIVE_JS = """() => {
  window.__live = []
  const find = n => (n && n.nodeType === 3 ? n.parentElement : n)
  const tag = el => { const sec = el.closest('section'); const h = sec && sec.querySelector('h2'); return (h ? h.innerText.trim().slice(0, 20) : '') + ' ' + (el.className || el.tagName).toString().split(' ')[0] }
  const push = (el, how) => window.__live.push({t: Math.round(performance.now()), role: el.getAttribute('role'), live: el.getAttribute('aria-live'), where: tag(el), how, text: el.innerText.trim().slice(0, 140)})
  new MutationObserver(ms => { for (const m of ms) {
    for (const a of m.addedNodes) { if (a.nodeType === 1 && a.matches && a.matches('[role=alert],[role=status],[aria-live]')) push(a, '새 영역') }
    const el = find(m.target) && find(m.target).closest('[role=status],[role=alert],[aria-live]')
    if (el) push(el, '내용 변경')
  } }).observe(document.body, {subtree: true, childList: true, characterData: true})
}"""


def rec(group: str, item: dict) -> None:
    RES.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:300], flush=True)


def xlsx_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


VALID = xlsx_bytes([["주민등록 인구 현황 2026년 9월 (단위: 명)"], ["시도", "인구수"], ["세종특별자치시", 11]])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def login(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


def ax(pg: Page, selector: str) -> dict:
    """브라우저가 계산한 접근성 이름·설명·역할 (CDP Accessibility)."""
    cdp = pg.context.new_cdp_session(pg)
    root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
    if not node:
        return {"found": False}
    n = cdp.send("Accessibility.getPartialAXTree", {"nodeId": node, "fetchRelatives": False})["nodes"][0]
    return {"found": True, "role": n.get("role", {}).get("value"), "name": n.get("name", {}).get("value"), "description": n.get("description", {}).get("value")}


def live_log(pg: Page) -> list:
    rows = pg.evaluate("window.__live || []")
    out, last = [], {}
    for r in rows:  # 같은 영역의 같은 글자가 이어진 기록은 한 번으로 (DOM 변경 횟수가 아니라 글자 변화)
        key = (r["where"], r["role"], r["live"])
        if last.get(key) == r["text"] and r["how"] == "내용 변경":
            continue
        last[key] = r["text"]
        out.append(r)
    pg.evaluate("window.__live = []")
    return out


def wait_upload_done(pg: Page) -> None:
    pg.wait_for_timeout(300)
    pg.wait_for_function("() => !document.querySelector('.upload-drop.is-busy')", timeout=120000)
    pg.wait_for_timeout(600)


def choose(pg: Page, files: list[dict], how: str = "enter") -> None:
    drop = pg.locator(".wk-home .upload-drop")
    drop.focus()
    with pg.expect_file_chooser(timeout=15000) as fc:
        pg.keyboard.press("Enter" if how == "enter" else "Space")
    fc.value.set_files(files=files)


# ---------------------------------------------------------------- D-01·D-02 업로드


def check_upload(pg: Page) -> None:
    login(pg, "user1")
    drop = pg.locator(".wk-home .upload-drop")
    rec("D-01", {"what": "업로드 조작 요소의 계산된 이름·설명", **ax(pg, ".wk-home .upload-drop"),
                 "visible_text": drop.inner_text().replace("\n", " / ")[:160]})  # fmt: skip
    rec("D-01", {"what": "역할 button 이름 '파일 선택'으로 찾기", "found": pg.get_by_role("button", name="파일 선택", exact=False).count()})
    # 클릭·Space도 파일 선택을 여는지 (선택은 취소)
    for how in ("click", "space"):
        try:
            with pg.expect_file_chooser(timeout=8000) as fc:
                drop.click() if how == "click" else (drop.focus(), pg.keyboard.press("Space"))
            fc.value.set_files(files=[])
            rec("D-01", {"what": f"{how}로 파일 선택 열림", "ok": True})
        except Exception as e:  # noqa: BLE001
            rec("D-01", {"what": f"{how}로 파일 선택 열림", "ok": False, "error": type(e).__name__})
    status_before = pg.evaluate("[...document.querySelectorAll('.wk-upload [role=status]')].map(e => e.className.toString())")
    rec("D-02", {"what": "업로드 영역의 상태 영역이 메시지 전에 있음", "status_regions_in_upload": status_before})
    pg.evaluate(LIVE_JS)
    focus0 = pg.evaluate("document.activeElement && document.activeElement.className.toString()")
    # 1) 성공 1개 (Enter)
    choose(pg, [{"name": "진단_상태_성공1.xlsx", "mimeType": XLSX, "buffer": VALID}], "enter")
    wait_upload_done(pg)
    rec("D-02", {"what": "① 성공 1개 (Enter로 선택)", "live": live_log(pg), "focus_kept": pg.evaluate("document.activeElement && document.activeElement.className.toString()") == focus0})
    # 2) 같은 결과 반복 (Space)
    choose(pg, [{"name": "진단_상태_성공2.xlsx", "mimeType": XLSX, "buffer": VALID}], "space")
    wait_upload_done(pg)
    rec("D-02", {"what": "② 같은 성공 1개 반복 (Space로 선택)", "live": live_log(pg)})
    # 3) 일부 실패: 정상 1 + 손상된 xlsx 1
    choose(pg, [{"name": "진단_상태_정상.xlsx", "mimeType": XLSX, "buffer": VALID}, {"name": "진단_상태_손상.xlsx", "mimeType": XLSX, "buffer": b"not a zip file"}])
    wait_upload_done(pg)
    rec("D-02", {"what": "③ 일부 실패 (정상 1 + 손상 1)", "live": live_log(pg),
                 "alerts_visible": pg.evaluate("[...document.querySelectorAll('.wk-upload [role=alert]')].filter(e => e.offsetWidth).map(e => e.innerText.trim().slice(0, 80))")})  # fmt: skip
    # 4) 다시 그려도 반복 안내 없음 (검색 칸 입력으로 다시 그리기)
    search = pg.locator(".wk-home input[type=search]").first
    if search.count():
        search.fill("진단")
        pg.wait_for_timeout(800)
        search.fill("")
        pg.wait_for_timeout(800)
    rec("D-02", {"what": "④ 다시 그리기만 했을 때 (검색 칸 입력)", "live": [x for x in live_log(pg) if "wk-upload" in x["where"] or "파일 업로드" in x["where"]]})
    # 5) 요청 전체 실패 (요청을 브라우저에서 끊음 — 서버에 가지 않음)
    pg.route("**/api/files", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
    choose(pg, [{"name": "진단_상태_전체실패.xlsx", "mimeType": XLSX, "buffer": VALID}])
    wait_upload_done(pg)
    pg.unroute("**/api/files")
    rec("D-02", {"what": "⑤ 요청 전체 실패 (요청 차단)", "live": live_log(pg)})
    # 6) 지원하지 않는 형식만
    choose(pg, [{"name": "진단_상태_문서.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 diag"}])
    pg.wait_for_timeout(1500)
    rec("D-02", {"what": "⑥ 지원하지 않는 형식만", "live": live_log(pg)})
    # 7) 끌어놓기도 동작하는지 (합성 DataTransfer)
    pg.evaluate("""async (b64) => { const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); const dt = new DataTransfer();
        dt.items.add(new File([bin], '진단_상태_끌어놓기.xlsx', {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
        const el = document.querySelector('.wk-home .upload-drop'); el.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})) }""",
                __import__("base64").b64encode(VALID).decode())  # fmt: skip
    wait_upload_done(pg)
    rec("D-01", {"what": "끌어놓기 업로드 동작", "live": live_log(pg)})
    pg.screenshot(path=str(OUT / f"{LABEL}-upload.png"), full_page=True)


# ---------------------------------------------------------------- D-02 분석 불러오기


def check_analysis_loading(pg: Page) -> None:
    login(pg, "user1")
    held: list = []
    pg.goto(URL + "/analysis")
    pg.wait_for_load_state("networkidle")
    pg.evaluate(LIVE_JS)
    pg.route("**/api/analysis/sources*", lambda r: held.append(r))
    sel = pg.get_by_label("분석할 항목")
    sel.focus()
    options = sel.locator("option").all_inner_texts()
    sel.select_option(index=1)
    pg.wait_for_timeout(800)
    loading = pg.evaluate("[...document.querySelectorAll('.an [role=status], [role=status]')].filter(e => e.closest('.an, main')).map(e => e.innerText.trim()).filter(Boolean)")
    for r in held:
        r.continue_()
    held.clear()
    pg.unroute("**/api/analysis/sources*")
    pg.wait_for_timeout(1000)
    rec("D-02", {"what": "분석 자료 목록 불러오는 중 → 완료", "options": options[:3], "while_loading_status": loading, "live": live_log(pg)})
    # 결과 불러오기: 같은 요소가 유지되는지 표시를 붙여 확인
    pg.route(f"**/api/analysis/runs/{IDS['user1_analysis']}", lambda r: held.append(r))
    pg.goto(f"{URL}/analysis?id={IDS['user1_analysis']}")
    pg.wait_for_timeout(1200)
    pg.evaluate(LIVE_JS)
    marked = pg.evaluate("""() => { const s = [...document.querySelectorAll('[role=status]')].find(e => /불러오는 중/.test(e.innerText)); if (s) s.__diag = 1;
        const p = [...document.querySelectorAll('main p')].find(e => /불러오는 중/.test(e.innerText)); return {status_text: s ? s.innerText.trim() : null, plain_text: p ? p.innerText.trim() : null, plain_role: p ? p.getAttribute('role') : null} }""")
    for r in held:
        r.continue_()
    pg.unroute(f"**/api/analysis/runs/{IDS['user1_analysis']}")
    pg.locator("table.an-result tbody tr").first.wait_for(timeout=60000)
    pg.wait_for_timeout(600)
    after = pg.evaluate("() => { const s = [...document.querySelectorAll('[role=status]')].find(e => e.__diag === 1); return s ? {same_node: true, text: s.innerText.trim(), cls: s.className} : {same_node: false} }")
    rec("D-02", {"what": "분석 결과 불러오는 중 → 완료 (같은 상태 요소 유지)", "while_loading": marked, "after": after, "live": live_log(pg)})


# ---------------------------------------------------------------- D-03·D-04 대비


def parse_color(s: str):
    m = re.match(r"rgba?\(([^)]+)\)", s.strip())
    if m:
        p = [float(x) for x in re.split(r"[ ,/]+", m.group(1)) if x]
        return tuple(p[:3]), (p[3] if len(p) > 3 else 1.0)
    m = re.match(r"oklab\(([^)]+)\)", s.strip())
    if m:
        p = [float(x) for x in re.split(r"[ /]+", m.group(1).strip()) if x]
        return oklab_to_srgb(*p[:3]), (p[3] if len(p) > 3 else 1.0)
    return None, None


BG_JS = """el => { let p = el.parentElement; while (p) { const c = getComputedStyle(p).backgroundColor; if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) return c; p = p.parentElement } return 'rgb(255, 255, 255)' }"""


def border_case(pg: Page, page: str, selector: str) -> None:
    loc = pg.locator(selector).first
    if not loc.count() or not loc.is_visible():
        rec("D-03", {"page": page, "selector": selector, "result": "화면에 없음 (측정 안 함)"})
        return
    color = loc.evaluate("e => getComputedStyle(e).borderTopColor")
    width = loc.evaluate("e => getComputedStyle(e).borderTopWidth")
    own_bg = loc.evaluate("e => getComputedStyle(e).backgroundColor")
    around = loc.evaluate(BG_JS)
    fg, a = parse_color(color)
    bg, _ = parse_color(around)
    ib, ia = parse_color(own_bg)
    r_out = ratio(over(fg, a, bg), bg)
    r_in = ratio(over(fg, a, over(ib, ia, bg)), over(ib, ia, bg))
    rec("D-03", {"page": page, "selector": selector, "border": f"{width} {color}", "outside_bg": around, "inside_bg": own_bg,
                 "ratio_vs_outside": r_out, "ratio_vs_inside": r_in, "pass_3_to_1": min(r_out, r_in) >= 3.0})  # fmt: skip


def check_contrast(pg: Page) -> None:
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    border_case(pg, "로그인", ".login-card input[name=username]")
    login(pg, "user1")
    border_case(pg, "홈", ".wk-home input[type=search]")
    pg.goto(URL + "/account")
    pg.wait_for_load_state("networkidle")
    border_case(pg, "내 계정", ".admin-form input[type=password]")
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(600)
    track = "label:has(input.peer.sr-only[type=checkbox]) input.peer.sr-only[type=checkbox] + *"
    knob = "label:has(input.peer.sr-only[type=checkbox]) input.peer.sr-only[type=checkbox] + * + *"
    cb = pg.locator("label:has(input.peer.sr-only[type=checkbox]) input.peer.sr-only[type=checkbox]").first

    def toggle_state(name: str) -> None:
        t = pg.locator(track).first
        tc, ta = parse_color(t.evaluate("e => getComputedStyle(e).backgroundColor"))
        kc, ka = parse_color(pg.locator(knob).first.evaluate("e => getComputedStyle(e).backgroundColor"))
        around, _ = parse_color(t.evaluate(BG_JS))
        shadow = t.evaluate("e => getComputedStyle(e).boxShadow")
        rings = [(parse_color(c), w) for c, w in re.findall(r"((?:rgba?|oklab)\([^)]*\))\s+0px 0px 0px (\d+(?:\.\d+)?)px", shadow)]
        rings = [((c, a), float(w)) for (c, a), w in rings if c is not None and a > 0]
        track_on_bg = over(tc, ta, around)
        item = {"state": name, "checked": cb.evaluate("e => e.checked"), "disabled": cb.evaluate("e => e.disabled"), "focused": cb.evaluate("e => document.activeElement === e"),
                "track": f"rgb{tuple(round(x) for x in tc)} a={ta}", "knob": f"rgb{tuple(round(x) for x in kc)}", "around": f"rgb{tuple(round(x) for x in around)}",
                "track_vs_around": ratio(track_on_bg, around), "knob_vs_track": ratio(over(kc, ka, track_on_bg), track_on_bg)}  # fmt: skip
        if rings:
            # 넓은 그림자가 바깥 (offset 흰 간격이 안쪽). 링 색은 알파를 주변과 합성
            outer = max(rings, key=lambda x: x[1])
            (rc, ra), rw = outer
            ring_on = over(rc, ra, around)
            item |= {"focus_ring": f"rgb{tuple(round(x) for x in rc)} a={ra} spread {rw}px", "ring_vs_around": ratio(ring_on, around),
                     "ring_layers": [f"rgb{tuple(round(x) for x in c)} a={a} {w}px" for (c, a), w in rings]}  # fmt: skip
        rec("D-04", item)

    toggle_state("꺼짐")
    for _ in range(80):
        pg.keyboard.press("Tab")
        if cb.evaluate("e => document.activeElement === e"):
            break
    toggle_state("꺼짐 + 키보드 초점")
    pg.locator("label:has(input.peer.sr-only[type=checkbox])").first.screenshot(path=str(OUT / f"{LABEL}-toggle-off-focus.png"))
    pg.keyboard.press("Space")
    pg.wait_for_timeout(400)
    toggle_state("켜짐 + 키보드 초점")
    pg.locator("label:has(input.peer.sr-only[type=checkbox])").first.screenshot(path=str(OUT / f"{LABEL}-toggle-on-focus.png"))
    border_case(pg, "묶기(요약 켬)", ".bd-choice select")
    border_case(pg, "묶기(요약 켬)", ".bd-input")
    pg.keyboard.press("Space")
    pg.wait_for_timeout(300)
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    toggle_state("꺼짐 (초점 없음)")
    # 비활성 표시만 보기 위해 DOM에서 잠시 disabled를 켠다 (시험 전용, 되돌림)
    cb.evaluate("e => { e.disabled = true }")
    toggle_state("비활성 (시험용 DOM 변경)")
    cb.evaluate("e => { e.disabled = false }")


# ---------------------------------------------------------------- D-05 가입·최초 설정


def check_signup(pg: Page) -> None:
    def fields() -> list:
        return pg.evaluate("""() => [...document.querySelectorAll('.login-card input')].map(e => ({label: e.labels?.[0]?.innerText.split('\\n')[0].trim(),
            autocomplete: e.autocomplete, minLength: e.minLength, maxLength: e.maxLength, placeholder: e.placeholder,
            describedby: (e.getAttribute('aria-describedby') || '').split(' ').filter(Boolean).map(id => document.getElementById(id)?.innerText.trim() || '(없는 id)')}))""")

    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    rec("D-05", {"mode": "로그인 (그대로여야 함)", "fields": fields()})
    pg.get_by_role("button", name=re.compile("회원가입")).first.click()
    pg.wait_for_timeout(500)
    rec("D-05", {"mode": "회원가입", "fields": fields(), "ax_username": ax(pg, ".login-card input[name=username]"), "ax_password": ax(pg, ".login-card input[name=password]")})
    pg.locator(".login-card input[name=username]").fill("diag-x")
    pg.locator(".login-card input[name=password]").fill("12345678")
    still = pg.evaluate("[...document.querySelectorAll('.login-help')].map(e => e.offsetWidth > 0)")
    rec("D-05", {"mode": "회원가입 — 입력 뒤에도 도움말 보임", "visible": still})
    pg.get_by_label("표시 이름").fill("진단 가입 시험")
    pg.get_by_label("비밀번호 확인").fill("87654321")
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_timeout(800)
    rec("D-05", {"mode": "회원가입 — 확인란 불일치", "alert": pg.locator(".login-card [role=alert]").all_inner_texts()})
    pg.get_by_label("아이디").fill("AB")  # 서버 규칙 위반 → 서버가 거절 (계정 안 생김)
    pg.get_by_label("비밀번호 확인").fill("12345678")
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_timeout(1200)
    rec("D-05", {"mode": "회원가입 — 서버 거절(아이디 규칙)", "alert": pg.locator(".login-card [role=alert]").all_inner_texts()})
    # 최초 설정 화면: 상태 응답만 바꿔 화면을 띄운다 (설정 요청은 서버가 409로 거절 — 이미 설정됨)
    pg.route("**/api/auth/status", lambda r: r.fulfill(status=200, content_type="application/json", body='{"setup_required": true, "user": null}'))
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    pg.wait_for_timeout(500)
    rec("D-05", {"mode": "최초 설정 (상태 응답 주입)", "title": pg.locator(".login-card h2").inner_text(), "fields": fields()})
    pg.locator(".login-card input[name=username]").fill("diag-setup")
    pg.get_by_label("이름", exact=True).fill("진단 설정")
    pg.locator(".login-card input[name=password]").fill("12345678")
    pg.get_by_label("비밀번호 확인").fill("12345678")
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_timeout(1200)
    rec("D-05", {"mode": "최초 설정 — 서버 거절 표시", "alert": pg.locator(".login-card [role=alert]").all_inner_texts()})
    pg.unroute("**/api/auth/status")


# ---------------------------------------------------------------- D-06 계정·점수, D-07 라벨 구조


def snapshot_text(pg: Page, selector: str) -> str | None:
    loc = pg.locator(selector).first
    return loc.aria_snapshot() if loc.count() else None


def check_names(pg: Page) -> None:
    login(pg, "user1")
    rec("D-06", {"what": "계정 표시", "aria_label_attr": pg.locator(".side-account").first.get_attribute("aria-label"),
                 "accessible_text": snapshot_text(pg, ".side-account"), "visible": pg.locator(".side-account").first.inner_text().replace("\n", " ")})  # fmt: skip
    SRONLY = """sel => [...document.querySelectorAll(sel)].map(e => { const c = getComputedStyle(e), r = e.getBoundingClientRect();
        return {text: e.textContent, position: c.position, size: [Math.round(r.width), Math.round(r.height)], clip: c.clip, overflow: c.overflow} })"""
    rec("D-06", {"what": "계정 표시의 보이지 않는 글자가 화면에서 잘려 있는지", "sr_only": pg.evaluate(SRONLY, ".side-account .sr-only")})
    pg.locator(".side-account").first.screenshot(path=str(OUT / f"{LABEL}-side-account.png"))
    pg.goto(f"{URL}/reviews?review={IDS['user1_review']}")
    pg.locator(".review-score-value").first.wait_for(timeout=60000)
    rec("D-06", {"what": "점수의 보이지 않는 글자가 화면에서 잘려 있는지", "sr_only": pg.evaluate(SRONLY, ".review-score-value .sr-only")})
    pg.locator(".review-score").first.screenshot(path=str(OUT / f"{LABEL}-review-score.png"))
    rec("D-06", {"what": "검토 점수 (완료된 검토)", "aria_label_attr": pg.locator(".review-score-value").first.get_attribute("aria-label"),
                 "accessible_text": snapshot_text(pg, ".review-score-value"), "visible": pg.locator(".review-score-value").first.inner_text().replace("\n", " ")})  # fmt: skip
    # D-07: 취합 방식 라디오 (홈) — 라벨 누르면 그 입력만 선택, 화살표 키로 이동
    pg.goto(URL + "/")
    pg.wait_for_load_state("networkidle")
    cards = pg.locator(".wk-mode-card")
    if cards.count() >= 2:
        before = pg.evaluate("[...document.querySelectorAll('input[name=wk-result-mode]')].map(e => e.value + ':' + e.checked)")
        cards.nth(1).locator(".wk-mode-text strong").click()
        after_click = pg.evaluate("[...document.querySelectorAll('input[name=wk-result-mode]')].map(e => e.value + ':' + e.checked)")
        pg.locator("input[name=wk-result-mode]:checked").focus()
        pg.keyboard.press("ArrowUp")
        after_key = pg.evaluate("[...document.querySelectorAll('input[name=wk-result-mode]')].map(e => e.value + ':' + e.checked)")
        focus_style = pg.evaluate("""() => { const i = document.querySelector('input[name=wk-result-mode]:focus'); if (!i) return null; const c = i.nextElementSibling;
            return {tag: c.tagName, display: getComputedStyle(c).display, size: [c.offsetWidth, c.offsetHeight], shadow: getComputedStyle(c).boxShadow} }""")
        struct = pg.evaluate("""() => [...document.querySelectorAll('.wk-mode-card')].map(l => ({inner: [...l.querySelectorAll(':scope > *')].map(e => e.tagName).join(','),
            wrap: l.querySelector('input').parentElement.tagName, labelled: l.querySelector('input').labels.length}))""")
        rec("D-07", {"what": "취합 방식 라디오", "before": before, "after_label_click": after_click, "after_arrow_up": after_key, "focus_indicator": focus_style, "structure": struct})
        pg.locator(".wk-mode-card").first.locator(".wk-mode-text strong").click()  # 원래 선택으로
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(500)
    lab = pg.locator("label:has(input.peer.sr-only[type=checkbox])").first
    info = lab.evaluate("""l => ({htmlFor: l.htmlFor, inputId: l.querySelector('input').id, children: [...l.querySelectorAll('*')].filter(e => e.parentElement === l || e.parentElement.parentElement === l).map(e => e.tagName + '.' + getComputedStyle(e).display).join(' '),
        trackSize: [l.querySelector('input').nextElementSibling.offsetWidth, l.querySelector('input').nextElementSibling.offsetHeight]})""")
    c0 = lab.locator("input").evaluate("e => e.checked")
    lab.locator("span", has_text="통계 요약 시트 추가").click()
    c1 = lab.locator("input").evaluate("e => e.checked")
    lab.locator("input").focus()
    pg.keyboard.press("Space")
    c2 = lab.locator("input").evaluate("e => e.checked")
    rec("D-07", {"what": "통계 요약 스위치", **info, "checked_initial": c0, "after_label_text_click": c1, "after_space": c2, "ax": ax(pg, "label:has(input.peer.sr-only[type=checkbox]) input[type=checkbox]")})


def check_stale_and_readonly(pg: Page) -> None:
    """'지난 검사 점수'(사용자2 매핑을 잠시 바꿔 만듦, 되돌림)와 다른 사용자 검토 조회 전용 표시."""
    u2 = Api().login("user2")
    fid = IDS["user2"]["basic"]
    s, pv, _ = u2.call("GET", f"/files/{fid}/preview?sheet_id=s0")
    cols = {x["name"]: x["column_id"] for x in pv["columns"]}
    s1, _, _ = u2.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sigungu"}]})
    try:
        login(pg, "user2")
        pg.goto(f"{URL}/reviews?review={IDS['user2_review']}")
        pg.locator(".review-score-value").first.wait_for(timeout=60000)
        rec("D-06", {"what": "검토 점수 (다시 검사 전 — 지난 검사 점수)", "mapping_change": s1, "aria_label_attr": pg.locator(".review-score-value").first.get_attribute("aria-label"),
                     "accessible_text": snapshot_text(pg, ".review-score-value"), "visible": pg.locator(".review-score-value").first.inner_text().replace("\n", " ")})  # fmt: skip
    finally:
        s2, _, _ = u2.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sido"}]})
        rec("D-06", {"what": "사용자2 매핑 되돌림", "status": s2})
    ctx2 = pg.context.browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    ctx2.route("**/*", route_guard(URL, BLOCKED))
    p2 = ctx2.new_page()
    login(p2, "owner")
    p2.goto(f"{URL}/reviews?review={IDS['user1_review']}")
    p2.locator(".review-score-value").first.wait_for(timeout=60000)
    rec("D-06", {"what": "다른 사용자 검토 (최고 관리자 조회 전용)", "accessible_text": snapshot_text(p2, ".review-score-value"),
                 "readonly_note": p2.locator(".review-automation").first.inner_text() if p2.locator(".review-automation").count() else None})  # fmt: skip
    ctx2.close()


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for name, fn in (("upload", check_upload), ("analysis", check_analysis_loading), ("contrast", check_contrast), ("signup", check_signup),
                         ("names", check_names), ("stale", check_stale_and_readonly)):  # fmt: skip
            if os.environ.get("ONLY_CHECKS") and name not in os.environ["ONLY_CHECKS"].split(","):
                continue
            cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", accept_downloads=True)
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            try:
                fn(pg)
            except Exception as e:  # noqa: BLE001 — 확인 실패로 기록 (통과로 세지 않음)
                rec("error", {"check": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                pg.screenshot(path=str(OUT / f"{LABEL}-error-{name}.png"), full_page=True)
            cx.close()
        b.close()
    RES["blocked_out_of_scope"] = BLOCKED
    (OUT / f"fix-check-{LABEL}.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
