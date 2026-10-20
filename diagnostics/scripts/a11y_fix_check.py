"""D-01~D-07 재현·확인 (진단 격리 스택, 합성 자료) — 수정 전·후 이미지에 같은 조건으로 돌려 비교한다.

판정 근거는 브라우저(Edge)의 계산된 접근성 이름·설명(CDP), 상태 영역의 글자 변화(MutationObserver),
계산된 색과 대비(contrast_calc), DOM 속성이다. 스크린리더 낭독은 확인하지 않는다 (코드·접근성 속성 확인).
진단 자료에 쓰는 동작: 합성 파일 업로드, 사용자2 매핑을 잠시 바꿨다 되돌림, 서버가 거절하는 가입·설정 요청(계정 안 생김).
사용법: python a11y_fix_check.py <출력 폴더> <라벨(before|after)>
환경: ONLY_CHECKS=upload,analysis,contrast,signup,names,stale,upload2,analysis2,dialog,inputs (비우면 전부), AXE_JS=axe.min.js 경로(있으면 A1 규칙도 실행)
이 스크립트는 측정값만 저장한다. 통과·실패 판정은 a11y_regress.py가 이 결과 JSON을 읽어 따로 한다.
시험에서만 하는 일: 업로드·분석 표 요청을 브라우저에서 잠시 붙잡아 '진행 중' 상태를 관찰, 결과 표 요청에 시험용 500 응답 (앱은 늦추지 않음).
진단 3차(2026-10-20) 추가 진단 자료: 최고 관리자 계정으로 오타 지역명 합성 파일 업로드·매핑·검증 ('연결 대상' 선택 칸을 띄우려는 것).
진단 4차(2026-10-20) 추가 진단 자료: 표준 사전 합성 항목 (user1·user2·최고 관리자가 합성 열을 확정 → 최고 관리자가 승격 승인, '빼기' 확인창을 열려는 것).
"""

import io
import json
import os
import re
import sys
from pathlib import Path

import openpyxl
from contrast_calc import oklab_to_srgb, over, ratio, shown
from playwright.sync_api import Page, sync_playwright

from diag_common import ASSET_JS, EV, URL, USERS, Api, creds, run_meta
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
  const tag = el => { const sec = el.closest('section'); const h = sec && sec.querySelector('h2'); return (h ? h.innerText.trim().slice(0, 20) : '') + ' ' + (el.className || (el.parentElement && el.parentElement.className) || el.tagName).toString().split(' ')[0] }
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


# 업로드 조작 요소: 수정 전은 영역 전체(div role=button), 수정 후는 영역 안의 '파일 선택' 버튼 — 같은 스크립트로 둘 다 잰다
CONTROL_JS = "() => document.querySelector('.wk-home .upload-drop[role=button]') ? '.wk-home .upload-drop' : '.wk-home .upload-drop .wk-drop-button'"


def control(pg: Page) -> str:
    return pg.evaluate(CONTROL_JS)


def choose(pg: Page, files: list[dict], how: str = "enter") -> None:
    drop = pg.locator(control(pg))
    drop.focus()
    with pg.expect_file_chooser(timeout=15000) as fc:
        pg.keyboard.press("Enter" if how == "enter" else "Space")
    fc.value.set_files(files=files)


# ---------------------------------------------------------------- D-01·D-02 업로드


def check_upload(pg: Page) -> None:
    login(pg, "user1")
    sel = control(pg)
    drop = pg.locator(sel)
    rec("D-01", {"what": "업로드 조작 요소의 계산된 이름·설명", "selector": sel, **ax(pg, sel), "visible_text": drop.inner_text().replace("\n", " / ")[:160],
                 "zone_text": pg.locator(".wk-home .upload-drop").inner_text().replace("\n", " / ")[:160]})  # fmt: skip
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
    r_out = ratio(over(fg, a, bg), bg)  # 원시 값 — 판정은 이 값으로, 보이는 값은 shown()
    r_in = ratio(over(fg, a, over(ib, ia, bg)), over(ib, ia, bg))
    rec("D-03", {"page": page, "selector": selector, "border": f"{width} {color}", "outside_bg": around, "inside_bg": own_bg,
                 "ratio_vs_outside": r_out, "ratio_vs_inside": r_in, "shown": [shown(r_out), shown(r_in)], "pass_3_to_1": min(r_out, r_in) >= 3.0})  # fmt: skip


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


# ---------------------------------------------------------------- PR #16 2차 보완 확인 (진단 3차, 2026-10-20): A1 판정 근거, 업로드 중·완료 뒤, 상태 안내 범위

AXE_JS = os.environ.get("AXE_JS")  # 있으면 axe 규칙을 그 자리에서 돌려 판정 근거를 남긴다 (없으면 '미실행'으로 기록)
STATUS_JS = """() => { const s = document.querySelector('.wk-upload p[role=status]'); return s ? {text: s.innerText.trim(), in_hidden: !!s.closest('[hidden]')} : null }"""
PICK_JS = "() => { const e = document.querySelector('.wk-pickcount'); return e ? e.innerText.trim() : null }"
FOCUS_JS = """() => { const e = document.activeElement; if (!e || e === document.body) return {name: '(본문)', in_dialog: false};
    return {name: (e.getAttribute('aria-label') || e.innerText || e.value || '').trim().slice(0, 30), tag: e.tagName, in_dialog: !!e.closest('[role=alertdialog]')} }"""


def axe_rule(pg: Page, rule: str, selector: str | None = None) -> dict:
    """axe 규칙 하나만 돌린 결과 (위반·판정 보류·통과 노드). a11y_scan.py와 같은 axe.min.js."""
    if not AXE_JS:
        return {"axe": "미실행 (AXE_JS 없음)"}
    if not pg.evaluate("() => !!window.axe"):
        pg.add_script_tag(path=AXE_JS)
    return pg.evaluate("""async ([rule, sel]) => { const r = await axe.run(sel ? document.querySelector(sel) : document, {runOnly: {type: 'rule', values: [rule]}});
        const pick = xs => xs.flatMap(v => v.nodes.map(n => ({target: n.target, html: n.html.slice(0, 200), summary: (n.failureSummary || '').slice(0, 240)})));
        return {rule, axe_version: axe.version, violations: pick(r.violations), incomplete: pick(r.incomplete), passes: pick(r.passes), inapplicable: r.inapplicable.length > 0} }""", [rule, selector])  # fmt: skip


def control_state(pg: Page, what: str) -> None:
    sel = control(pg)
    st = pg.evaluate("""s => { const e = document.querySelector(s); if (!e) return null; return {tag: e.tagName, role: e.getAttribute('role'), aria_label: e.getAttribute('aria-label'),
        aria_disabled: e.getAttribute('aria-disabled'), disabled: !!e.disabled, focused: document.activeElement === e, visible_text: e.innerText.trim().replace(/\\n+/g, ' / '),
        describedby: (e.getAttribute('aria-describedby') || '').split(' ').filter(Boolean).map(id => document.getElementById(id)?.innerText.trim() ?? '(없는 id)'),
        hidden_input_inside_button_role: !!(e.getAttribute('role') === 'button' && e.querySelector('input[type=file]'))} }""", sel)  # fmt: skip
    rec("A1", {"what": what, "selector": sel, **(st or {"found": False}), "ax": ax(pg, sel)})


def hold_upload(pg: Page, held: list) -> None:
    """업로드 요청(POST /api/files)을 브라우저에서 잠시 붙잡는다 — 시험에서 '업로드 중' 상태를 관찰하려는 것 (앱은 늦추지 않음)."""
    pg.route("**/api/files", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())


def release(pg: Page, held: list, pattern) -> None:
    for r in held:
        r.continue_()
    held.clear()
    pg.unroute(pattern)


def login_inplace(pg: Page, who: str) -> None:
    """새로 고치지 않고 로그인 화면에서 바로 로그인 (화면이 다시 마운트되는지 보려고)."""
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)


def check_upload_scope(pg: Page) -> None:
    login(pg, "user1")
    control_state(pg, "평상시")
    rec("A1", {"what": "axe label-content-name-mismatch — 홈 업로드 영역", **axe_rule(pg, "label-content-name-mismatch", ".wk-home .wk-upload")})
    held: list = []
    # ⑦ 업로드 중: 이름·상태·설명, 다시 누르면 파일 선택 창이 열리는지
    pg.evaluate(LIVE_JS)
    hold_upload(pg, held)
    choose(pg, [{"name": "진단_범위_업로드중.xlsx", "mimeType": XLSX, "buffer": VALID}], "enter")
    pg.wait_for_selector(".upload-drop.is-busy", timeout=15000)
    control_state(pg, "업로드 중 (시험에서 요청을 붙잡음)")
    opened = {}
    for how in ("click_zone", "enter"):  # 영역 클릭(초점이 본문으로 빠짐)을 먼저, 버튼 Enter를 나중에 — 완료 뒤 초점을 버튼 기준으로 본다
        try:
            with pg.expect_file_chooser(timeout=2500) as fc:
                if how == "enter":
                    pg.locator(control(pg)).focus()
                    pg.keyboard.press("Enter")
                else:
                    pg.locator(".wk-home .upload-drop-title").click()
            fc.value.set_files(files=[])
            opened[how] = True
        except Exception:  # noqa: BLE001 — 열리지 않음
            opened[how] = False
    rec("A1", {"what": "업로드 중 다시 눌렀을 때 파일 선택 창", "opened": opened})
    release(pg, held, "**/api/files")
    wait_upload_done(pg)
    control_state(pg, "업로드 완료 뒤")
    rec("D-02", {"what": "⑦ 업로드 중 → 완료 (목록 갱신이 이어짐)", "live": live_log(pg), "pickcount": pg.evaluate(PICK_JS)})
    # ⑧ 정상 + 지원하지 않는 형식
    choose(pg, [{"name": "진단_범위_혼합.xlsx", "mimeType": XLSX, "buffer": VALID}, {"name": "진단_범위_혼합.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 diag"}])
    wait_upload_done(pg)
    rec("D-02", {"what": "⑧ 정상 + 지원하지 않는 형식 함께", "live": live_log(pg),
                 "alerts_visible": pg.evaluate("[...document.querySelectorAll('.wk-upload [role=alert]')].filter(e => e.offsetWidth).map(e => e.innerText.trim().slice(0, 100))")})  # fmt: skip
    # ⑨ 다른 화면으로 옮긴 뒤 늦게 끝난 응답 (홈은 숨긴 채 마운트 유지 — M-10)
    hold_upload(pg, held)
    choose(pg, [{"name": "진단_범위_늦은응답.xlsx", "mimeType": XLSX, "buffer": VALID}])
    pg.wait_for_selector(".upload-drop.is-busy", timeout=15000)
    pg.locator(".side-link", has_text="실행 기록").first.click()
    pg.wait_for_timeout(800)
    live_log(pg)  # 화면 이동으로 생긴 기록은 버림
    release(pg, held, "**/api/files")
    pg.wait_for_function("() => !document.querySelector('.upload-drop.is-busy')", timeout=120000)
    pg.wait_for_timeout(1000)
    rec("D-02", {"what": "⑨ 다른 화면에 있는 동안 늦게 끝난 응답", "path": pg.evaluate("location.pathname"), "status": pg.evaluate(STATUS_JS),
                 "live": [x for x in live_log(pg) if "파일 업로드" in x["where"] or "검증" in x["where"]]})  # fmt: skip
    pg.locator(".side-link", has_text=re.compile(r"^\s*홈\s*$")).first.click()
    pg.wait_for_timeout(1200)
    rec("D-02", {"what": "⑨-2 홈으로 돌아옴 (지난 안내가 다시 바뀌는지)", "status": pg.evaluate(STATUS_JS), "live": [x for x in live_log(pg) if "파일 업로드" in x["where"]]})
    # ⑩ 업로드 중 로그아웃 (업로드 화면이 내려감) → 늦은 응답 → 새로 고치지 않고 다시 로그인
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    hold_upload(pg, held)
    choose(pg, [{"name": "진단_범위_로그아웃.xlsx", "mimeType": XLSX, "buffer": VALID}])
    pg.wait_for_selector(".upload-drop.is-busy", timeout=15000)
    pg.locator(".side-logout").click()
    pg.wait_for_selector(".login-card", timeout=15000)
    release(pg, held, "**/api/files")
    pg.wait_for_timeout(1500)
    login_inplace(pg, "user1")
    pg.evaluate(LIVE_JS)
    pg.wait_for_timeout(1500)
    rec("D-02", {"what": "⑩ 업로드 중 로그아웃 → 늦은 응답 → 다시 로그인 (다시 마운트)", "status": pg.evaluate(STATUS_JS),
                 "live": [x for x in live_log(pg) if "파일 업로드" in x["where"]], "page_errors": errors})  # fmt: skip


def check_analysis_tables(pg: Page) -> None:
    """상세(조건·요약)와 결과·제외·근거 표를 따로 불러올 때, 안내가 실제로 불러온 범위만 말하는지."""
    login(pg, "user1")
    rid = IDS["user1_analysis"]
    st_js = "() => { const s = document.querySelector('section.an') ? document.querySelector('section.an').previousElementSibling : null; return s && s.getAttribute('role') === 'status' ? s.innerText.trim() : null }"
    pages = re.compile(rf"/api/analysis/runs/{rid}/(groups|excluded|detail)\?")
    held: list = []
    pg.route(pages, lambda r: held.append(r))
    pg.goto(f"{URL}/analysis?id={rid}")
    pg.locator("section.an h2").wait_for(timeout=60000)
    pg.evaluate(LIVE_JS)
    pg.wait_for_timeout(600)
    pending = pg.evaluate(st_js)
    release(pg, held, pages)
    pg.locator("table.an-result tbody tr").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)
    rec("D-02", {"what": "분석 결과: 조건·요약만 왔고 표 3개는 아직 (시험에서 표 요청을 붙잡음) → 표 도착", "while_tables_pending": pending, "after_tables": pg.evaluate(st_js),
                 "live": live_log(pg)})  # fmt: skip
    groups = re.compile(rf"/api/analysis/runs/{rid}/groups\?")
    pg.route(groups, lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail": "diag"}'))
    pg.goto(f"{URL}/analysis?id={rid}")
    pg.locator("section.an h2").wait_for(timeout=60000)
    pg.wait_for_timeout(2000)
    rec("D-02", {"what": "분석 결과: 결과 표만 실패 (시험용 500 응답)", "status": pg.evaluate(st_js),
                 "visible_errors": pg.locator("section.an p.upload-error").all_inner_texts()})  # fmt: skip
    pg.unroute(groups)


DIALOG_JS = """() => { const d = document.querySelector('[role=alertdialog]'); if (!d) return null;
  const txt = ids => (ids || '').split(' ').filter(Boolean).map(id => { const e = document.getElementById(id); return e ? e.innerText.trim().slice(0, 140) : '(없는 id)' });
  const anc = []; for (let p = d.parentElement; p; p = p.parentElement) if (p.hasAttribute('aria-describedby')) anc.push(p.tagName + '.' + (p.className || '').toString().split(' ')[0]);
  return {count: document.querySelectorAll('[role=alertdialog]').length, labelledby: d.getAttribute('aria-labelledby'), title: txt(d.getAttribute('aria-labelledby')),
          describedby: d.getAttribute('aria-describedby'), description: txt(d.getAttribute('aria-describedby')), ancestors_with_describedby: anc} }"""


def dialog_probe(pg: Page, trigger, label: str) -> dict:
    """확인창 하나: 제목·본문 연결, 처음 초점, Tab·Shift+Tab 범위, Esc, 취소 뒤 초점 복귀. 확인 버튼은 누르지 않는다."""
    trigger.focus()
    pg.keyboard.press("Enter")
    pg.get_by_role("alertdialog").wait_for(timeout=15000)
    pg.wait_for_timeout(300)
    info, axn, first = pg.evaluate(DIALOG_JS), ax(pg, "[role=alertdialog]"), pg.evaluate(FOCUS_JS)
    tabs, shift = [], []
    for _ in range(4):
        pg.keyboard.press("Tab")
        tabs.append(pg.evaluate(FOCUS_JS))
    for _ in range(4):
        pg.keyboard.press("Shift+Tab")
        shift.append(pg.evaluate(FOCUS_JS))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(500)
    esc = {"closed": pg.get_by_role("alertdialog").count() == 0, "focus_on_trigger": trigger.evaluate("e => document.activeElement === e")}
    pg.keyboard.press("Enter")  # 같은 버튼으로 다시 열기
    pg.get_by_role("alertdialog").wait_for(timeout=15000)
    pg.wait_for_timeout(300)
    again = pg.evaluate(DIALOG_JS)
    pg.keyboard.press("Enter")  # 처음 초점 = 취소
    pg.wait_for_timeout(500)
    cancel = {"closed": pg.get_by_role("alertdialog").count() == 0, "focus_on_trigger": trigger.evaluate("e => document.activeElement === e")}
    out = {"dialog": label, "attrs": info, "ax": axn, "first_focus": first, "tab": tabs, "shift_tab": shift, "escape": esc, "reopen_attrs": again, "cancel": cancel}
    rec("dialog", out)
    return out


STD_COL = "진단합성표준확인열"  # 기본 표준 사전에 없는 합성 열 이름 — 서로 다른 사용자 3명이 확정하면 승격 후보가 된다


def ensure_standard_term() -> str:
    """표준 사전 '빼기' 확인창을 열 수 있게 합성 항목을 앱의 승격 절차로 만든다 (user1·user2·최고 관리자가 같은 합성 열을 인구수로 확정 →
    최고 관리자가 승격 승인). 이미 있으면 그대로 쓴다. 삭제는 하지 않는다. user3은 '자료 없는 사용자' 상태를 지키려고 쓰지 않는다."""
    owner = Api().login("owner")
    s, terms, _ = owner.call("GET", "/admin/standard-terms")
    if s == 200 and any(t["term"] == STD_COL for t in terms):
        return "이미 있음"
    for who in ("user1", "user2", "owner"):
        api = Api().login(who)
        s, body, _ = api.upload(f"진단_표준사전확인_{who}.xlsx", xlsx_bytes([["주민등록 인구 현황 2026년 9월 (단위: 명)"], ["시도", STD_COL], ["세종특별자치시", 1]]))
        fid = body[0]["id"]
        s, pv, _ = api.call("GET", f"/files/{fid}/preview?sheet_id=s0")
        cols = {x["name"]: x["column_id"] for x in pv["columns"]}
        api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols[STD_COL], "field_key": "population"}]})
    s, pl, _ = owner.call("GET", "/admin/promotions")
    cand = next((c for c in pl.get("items", []) if c["column_name"] == STD_COL), None) if s == 200 else None
    if cand is None:
        return f"승격 후보가 생기지 않음 (조회 {s})"
    s, _, _ = owner.call("POST", f"/admin/promotions/{cand['id']}/approve", {"note": "진단 합성 항목 (확인창 확인용)"})
    return f"승격 승인 {s} (후보 #{cand['id']}, 승인자 {cand['approver_count']}명)"


def check_dialogs(pg: Page) -> None:
    login(pg, "user1")
    pg.goto(URL + "/automations")
    pg.wait_for_load_state("networkidle")
    item = pg.get_by_text("월간 인구 취합").first
    if item.count():
        item.click()
        pg.wait_for_timeout(600)
    trig = pg.get_by_role("button", name="자동화 삭제")
    if trig.count():
        dialog_probe(pg, trig.first, "자동화 삭제")
    else:
        rec("dialog", {"dialog": "자동화 삭제", "result": "버튼 없음 (측정 안 함)"})
    # 최고 관리자: 용어사전 항목 삭제 두 건(본문이 다름) + 같은 화면의 다른 확인창(표준 사전 빼기)과 id가 섞이지 않는지
    rec("dialog", {"dialog": "표준 사전 합성 항목 준비", "result": ensure_standard_term()})
    pg.context.clear_cookies()
    login(pg, "owner")
    pg.goto(URL + "/dictionary")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    dels = pg.locator(".glossary-table button.wk-danger, table button.wk-danger", has_text="삭제")
    seen = []
    for i in range(min(2, dels.count())):
        seen.append(dialog_probe(pg, dels.nth(i), f"용어사전 삭제 {i + 1}"))
    rec("dialog", {"dialog": "용어사전 삭제 두 건 비교", "descriptions": [s["attrs"]["description"] if s["attrs"] else None for s in seen],
                   "describedby_ids": [s["attrs"]["describedby"] if s["attrs"] else None for s in seen]})  # fmt: skip
    std = pg.get_by_role("button", name="빼기")
    if std.count():
        s = dialog_probe(pg, std.first, "표준 사전 빼기")
        rec("dialog", {"dialog": "같은 화면의 두 확인창 id", "glossary_id": seen[0]["attrs"]["describedby"] if seen and seen[0]["attrs"] else None,
                       "standard_id": s["attrs"]["describedby"] if s["attrs"] else None})  # fmt: skip
    else:
        rec("dialog", {"dialog": "표준 사전 빼기", "result": "표준 사전 항목 없음 (측정 안 함)"})


# ---------------------------------------------------------------- 진단 3차: 화면에 실제로 나타난 입력 경계·초점 표시 대비

INPUTS_JS = """() => {
  const RULES = [['.mapping-pick', 'select'], ['.validation-form', 'input,select,textarea'], ['.glossary-search', 'input,select'], ['.review-controls', 'select'],
    ['.review-replace-row', 'select'], ['.automation-period', 'input'], ['.integration-automation', 'input'], ['.login-card', 'input'], ['.admin-form', 'input,select'],
    ['.admin-actions', 'input'], ['.wk-home', 'input,select,textarea'], ['.bd-choice', 'select']]
  const bgOf = e => { for (let p = e.parentElement; p; p = p.parentElement) { const c = getComputedStyle(p).backgroundColor; if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) return c } return 'rgb(255, 255, 255)' }
  return [...document.querySelectorAll('input, select, textarea')].filter(e => !['hidden', 'radio', 'checkbox', 'file'].includes(e.type) && !e.classList.contains('sr-only') && e.offsetWidth && !e.closest('[hidden]')).map(e => {
    const rules = e.classList.contains('bd-input') ? ['.bd-input'] : []  // 이 요소에 적용되는 입력 규칙 모두 (예: 검증 양식은 .validation-form과 .wk-home 둘 다)
    for (const [box, tags] of RULES) if (e.closest(box) && e.matches(tags)) rules.push(box + ' ' + e.tagName.toLowerCase())
    const rule = rules[0] || null
    const cs = getComputedStyle(e)
    return {rule, rules, tag: e.tagName.toLowerCase(), type: e.type, name: (e.getAttribute('aria-label') || e.labels?.[0]?.innerText.split('\\n')[0] || e.name || '').trim().slice(0, 30),
            border: cs.borderTopStyle + ' ' + cs.borderTopWidth + ' ' + cs.borderTopColor, color: cs.borderTopColor, width: cs.borderTopWidth, style: cs.borderTopStyle,
            inside_bg: cs.backgroundColor, outside_bg: bgOf(e), disabled: e.disabled}
  })
}"""


def inputs_case(pg: Page, page: str) -> None:
    for it in pg.evaluate(INPUTS_JS):
        fg, a = parse_color(it["color"])
        bg, _ = parse_color(it["outside_bg"])
        ib, ia = parse_color(it["inside_bg"])
        if it["style"] == "none" or it["width"] in ("0px", "") or fg is None or bg is None:
            rec("inputs", {"page": page, **{k: it[k] for k in ("rule", "rules", "tag", "type", "name", "border")}, "result": "테두리 없음 또는 색 해석 불가"})
            continue
        inner = over(ib, ia, bg)
        r_out, r_in = ratio(over(fg, a, bg), bg), ratio(over(fg, a, inner), inner)
        rec("inputs", {"page": page, **{k: it[k] for k in ("rule", "rules", "tag", "type", "name", "border", "inside_bg", "outside_bg", "disabled")},
                       "ratio_vs_outside": r_out, "ratio_vs_inside": r_in, "shown": [shown(r_out), shown(r_in)], "pass_3_to_1": min(r_out, r_in) >= 3.0})  # fmt: skip


def outline_case(pg: Page, what: str, element_js: str) -> None:
    """키보드 초점 외곽선(outline) vs 외곽선이 놓인 곳의 배경 (outline-offset 바깥 = 부모 배경)."""
    pg.wait_for_timeout(400)  # 배경·테두리 전환(0.15초)이 끝난 뒤 계산된 색을 읽는다
    o = pg.evaluate("""js => { const e = eval(js); if (!e) return null; const cs = getComputedStyle(e);
        let bg = 'rgb(255, 255, 255)'; for (let p = e.parentElement; p; p = p.parentElement) { const c = getComputedStyle(p).backgroundColor; if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) { bg = c; break } }
        return {outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor, color: cs.outlineColor, style: cs.outlineStyle, offset: cs.outlineOffset, around: bg,
                own_bg: cs.backgroundColor, focus_within: e.matches(':focus-within'), focus_visible_inside: !!e.querySelector(':focus-visible') || e.matches(':focus-visible')} }""", element_js)  # fmt: skip
    if not o or o["style"] == "none":
        rec("focus", {"what": what, "result": "외곽선 없음" if o else "요소 없음", **(o or {})})
        return
    fg, a = parse_color(o["color"])
    bg, _ = parse_color(o["around"])
    r = ratio(over(fg, a, bg), bg)
    rec("focus", {"what": what, **o, "ratio_vs_around": r, "shown": shown(r), "pass_3_to_1": r >= 3.0})


def check_inputs_focus(pg: Page) -> None:
    pg.goto(URL + "/")
    pg.wait_for_selector(".login-card")
    inputs_case(pg, "로그인")
    pg.get_by_role("button", name=re.compile("회원가입")).first.click()
    pg.wait_for_timeout(400)
    inputs_case(pg, "회원가입")
    login(pg, "user1")
    inputs_case(pg, "홈 (user1)")
    # 취합 방식 라디오 카드: 화살표 키로 옮겨 키보드 초점 상태를 만든다
    pg.locator("input[name=wk-result-mode]:checked").focus()
    pg.keyboard.press("ArrowDown")
    pg.wait_for_timeout(200)
    outline_case(pg, "취합 방식 라디오 카드 (키보드 초점)", "document.activeElement.closest('.wk-mode-card')")
    rec("focus", {"what": "라디오 원 자체의 링 (box-shadow)", "shadow": pg.evaluate("getComputedStyle(document.activeElement.nextElementSibling).boxShadow")})
    pg.keyboard.press("ArrowUp")
    shot = pg.locator(".wk-mode-card").first
    shot.screenshot(path=str(OUT / f"{LABEL}-radio-card-focus.png"))
    # 업로드 '파일 선택' 조작 요소의 키보드 초점 외곽선
    pg.locator(".wk-home input[type=search]").first.focus()
    for _ in range(40):
        if pg.evaluate(f"(() => {{ const e = document.querySelector({json.dumps(control(pg))}); return document.activeElement === e }})()"):
            break
        pg.keyboard.press("Shift+Tab")
    outline_case(pg, "업로드 조작 요소 (키보드 초점)", f"document.querySelector({json.dumps(control(pg))})")
    pg.locator(".wk-home .upload-drop").screenshot(path=str(OUT / f"{LABEL}-upload-focus.png"))
    for path, name in (("/account", "내 계정"), ("/automations", "자동화"), (f"/reviews?review={IDS['user1_review']}", "검토 상세"),
                       (f"/mapping?file={IDS['user1']['basic']}", "열 매핑")):  # fmt: skip
        pg.goto(URL + path)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(700)
        inputs_case(pg, name)
    # 묶기 요약 켬 + 항목 1개 → .bd-input(단위·기간을 확인할 수 없는 파일)·.bd-choice select(파일마다 단위가 다름)가 나타나는 상태
    u1 = Api().login("user1")
    extra = []
    for name, title in (("진단_묶기_단위기간없음.xlsx", "인구 현황"), ("진단_묶기_천명.xlsx", "주민등록 인구 현황 2026년 9월 (단위: 천명)")):
        s, body, _ = u1.upload(name, xlsx_bytes([[title], ["시도", "인구수"], ["세종특별자치시", 3], ["제주특별자치도", 4]]))
        fid = body[0]["id"]
        s, pv, _ = u1.call("GET", f"/files/{fid}/preview?sheet_id=s0")
        cols = {x["name"]: x["column_id"] for x in pv["columns"]}
        u1.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sido"},
                                                                          {"sheet_id": "s0", "column_id": cols["인구수"], "field_key": "population"}]})  # fmt: skip
        extra.append(fid)
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{extra[0]},{extra[1]}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(700)
    sw = pg.locator("label:has(input.peer.sr-only[type=checkbox]) input[type=checkbox]").first
    if not sw.is_checked():
        sw.focus()
        pg.keyboard.press("Space")
        pg.wait_for_timeout(800)
    add = pg.get_by_role("button", name="항목 추가", exact=True)
    if add.count():
        add.click()
        pg.wait_for_timeout(1500)
    inputs_case(pg, "묶기 (요약 켬·항목 1개)")
    pg.locator(".bd-summary").first.screenshot(path=str(OUT / f"{LABEL}-bundle-summary-inputs.png")) if pg.locator(".bd-summary").count() else None
    # 최고 관리자: 설정·사용자·사전, 그리고 오타 지역명 파일을 검증해 '연결 대상'(.mapping-pick select)이 나타나는 상태
    pg.context.clear_cookies()
    login(pg, "owner")
    for path, name in (("/settings", "설정"), ("/moajwoAdmin/users", "사용자 관리"), ("/dictionary", "사전 관리")):
        pg.goto(URL + path)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(700)
        inputs_case(pg, name)
    api = Api().login("owner")
    s, body, _ = api.upload("진단_후보지역.xlsx", xlsx_bytes([["주민등록 인구 현황 2026년 9월 (단위: 명)"], ["시도", "인구수"], ["강원특별자치", 7], ["충청북도", 8]]))
    fid = body[0]["id"] if isinstance(body, list) else body.get("id")
    s, pv, _ = api.call("GET", f"/files/{fid}/preview?sheet_id=s0")
    cols = {x["name"]: x["column_id"] for x in pv["columns"]}
    api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sido"},
                                                                       {"sheet_id": "s0", "column_id": cols["인구수"], "field_key": "population"}]})  # fmt: skip
    pg.goto(f"{URL}/?files={fid}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    start = pg.get_by_role("button", name=re.compile(r"^검증 실행"))
    if start.count() and start.first.is_enabled():
        start.first.click()
        try:
            pg.locator(".mapping-pick select").first.wait_for(timeout=60000)
        except Exception:  # noqa: BLE001 — 나타나지 않으면 기록에 남는다
            pass
    inputs_case(pg, "홈 검증 결과 (최고 관리자, 후보 있는 지역명)")
    if pg.locator(".mapping-pick select").count():
        pg.locator(".mapping-pick").first.screenshot(path=str(OUT / f"{LABEL}-mapping-pick.png"))


META: dict = {}


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = b.new_context()
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        META.update(run_meta("a11y_fix_check.py", LABEL, pg.evaluate(ASSET_JS)))  # 어떤 빌드를 쟀는지 (빌드 자산 파일 이름)
        cx.close()
        for name, fn in (("upload", check_upload), ("analysis", check_analysis_loading), ("contrast", check_contrast), ("signup", check_signup),
                         ("names", check_names), ("stale", check_stale_and_readonly), ("upload2", check_upload_scope), ("analysis2", check_analysis_tables),
                         ("dialog", check_dialogs), ("inputs", check_inputs_focus)):  # fmt: skip
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
    RES["meta"] = META
    (OUT / f"fix-check-{LABEL}.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
