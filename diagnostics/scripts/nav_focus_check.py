"""화면 이동·초점·긴 내용·표 탐색 측정 (진단 격리 스택, 합성 자료) — 측정만 저장하고 판정은 a11y_regress.py가 한다.

- 본문 바로가기: 첫 Tab에 나오는지, 누르면 지금 화면의 제목(숨긴 홈·다른 화면 제목이 아님)으로 가는지, 주소가 그대로인지
- 화면 이동 뒤 초점: 메뉴 → 새 화면 제목, 목록 → 상세 시작, 상세 → 목록(연 버튼), 뒤로가기·앞으로가기, 주소만 고치는 선택은 초점 유지
- 긴 내용: 검토 위치가 접힌 줄에서 잘릴 때 펼치면 전체가 보이는지, '전체 보기' 이름이 서로 구분되는지, 근거 창을 닫으면 연 버튼으로 돌아오는지
- 분석 표: 스크롤 영역이 넘칠 때만 이름·Tab 정지점이 있는지, 키보드로 스크롤되는지, 머리글·쪽 이동이 그대로인지
화면 읽기 프로그램 낭독은 확인하지 않는다 (초점 위치·접근성 속성 기록).
진단 자료에 쓰는 동작: user1로 긴 합성 지역명 파일 업로드·매핑·검토 생성('전체 보기'를 띄우려는 것, 검토는 완료하지 않음).
사용법: python nav_focus_check.py <출력 폴더> <라벨>   환경: ONLY_CHECKS=skip,menu,reviews,analysis,summary,history,failure,stale,keep,longtext,tables
시험에서만 하는 일: 상세 요청을 붙잡아 '기다리는 중'을 만들거나 시험용 404·500 응답을 준다, 대기 중 확인창은 시험용 <dialog>로 만든다 (앱 동작을 늦추지 않음).
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from diag_common import ASSET_JS, EV, URL, USERS, Api, creds, run_meta, xlsx
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
LABEL = sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
RES: dict[str, list] = {}
BLOCKED: list[str] = []
FOCUS_JS = """() => { const e = document.activeElement; if (!e || e === document.body || e === document.documentElement) return {where: '(본문)'};
  return {where: 'element', tag: e.tagName, text: (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50), key: e.getAttribute('data-focus-key'),
          cls: (e.className || '').toString().split(' ')[0], in_hidden: !!e.closest('[hidden]'), in_main: !!e.closest('main'), in_dialog: !!e.closest('dialog,[role=alertdialog]')} }"""


def rec(group: str, item: dict) -> None:
    RES.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:300], flush=True)


def focus(pg: Page) -> dict:
    return pg.evaluate(FOCUS_JS)


def login(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


def tab_to(pg: Page, pred: str, n: int = 80, back: bool = False) -> bool:
    for _ in range(n):
        pg.keyboard.press("Shift+Tab" if back else "Tab")
        if pg.evaluate(f"(() => {{ const e = document.activeElement; return !!e && ({pred}) }})()"):
            return True
    return False


def settle(pg: Page, ms: int = 900) -> None:
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(ms)


def check_skip(pg: Page) -> None:
    login(pg, "user1")
    for path, name in (("/", "홈"), ("/runs", "실행 기록")):
        pg.goto(URL + path)
        settle(pg)
        pg.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        pg.keyboard.press("Tab")
        first = focus(pg)
        shown = pg.evaluate("(() => { const e = document.activeElement; const r = e.getBoundingClientRect(); return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && getComputedStyle(e).transform === 'none' })()")
        url0 = pg.evaluate("location.pathname + location.search + location.hash")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(300)
        rec("nav", {"what": f"본문 바로가기 — {name}", "first_tab": first, "visible_when_focused": shown, "after_enter": focus(pg),
                    "url_unchanged": pg.evaluate("location.pathname + location.search + location.hash") == url0})  # fmt: skip
        pg.screenshot(path=str(OUT / f"{LABEL}-skip-{path.strip('/') or 'home'}.png"))


def check_menu(pg: Page) -> None:
    login(pg, "user1")
    for label, title in (("실행 기록", "실행 기록"), ("검토 목록", "검토 목록"), ("홈", "홈")):
        link = pg.locator(".side-link", has_text=label).first if label != "홈" else pg.locator(".side-link").filter(has_text="홈").first
        link.focus()
        pg.keyboard.press("Enter")
        settle(pg, 600)
        rec("nav", {"what": f"메뉴 → {label}", "expected_title": title, "after": focus(pg), "path": pg.evaluate("location.pathname")})


def check_reviews(pg: Page) -> None:
    login(pg, "user1")
    pg.locator(".side-link", has_text="검토 목록").first.click()
    settle(pg)
    btn = pg.locator("button[data-focus-key^='review-row-']").first
    if not btn.count():
        rec("nav", {"what": "검토 목록 → 상세", "result": "연 버튼 없음 (data-focus-key 없음 — 수정 전 구조이거나 목록 비어 있음)"})
        btn = pg.get_by_role("button", name="검토 보기").first
    key = btn.get_attribute("data-focus-key")
    btn.focus()
    pg.keyboard.press("Enter")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)
    rec("nav", {"what": "검토 목록 → 상세 (Enter)", "opener_key": key, "after": focus(pg), "url": pg.evaluate("location.pathname + location.search")})
    back = pg.get_by_role("button", name="← 검토 목록으로")
    back.focus()
    pg.keyboard.press("Enter")
    settle(pg, 1500)
    rec("nav", {"what": "상세 → 목록 ('검토 목록으로')", "opener_key": key, "after": focus(pg), "url": pg.evaluate("location.pathname + location.search")})
    # 뒤로가기·앞으로가기: 목록에서 다시 상세를 연 뒤 브라우저 뒤로 → 앞으로
    btn = pg.locator(f"button[data-focus-key='{key}']").first if key else pg.get_by_role("button", name="검토 보기").first
    btn.focus()
    pg.keyboard.press("Enter")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)
    pg.go_back()
    settle(pg, 1500)
    rec("nav", {"what": "상세에서 브라우저 뒤로", "opener_key": key, "after": focus(pg), "url": pg.evaluate("location.pathname + location.search"),
                "scroll_y": pg.evaluate("Math.round(scrollY)")})  # fmt: skip
    pg.go_forward()
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    settle(pg, 1200)
    rec("nav", {"what": "브라우저 앞으로 (다시 상세)", "after": focus(pg), "url": pg.evaluate("location.pathname + location.search")})
    # 검토 상세를 다른 화면(실행 기록)에서 열기
    pg.locator(".side-link", has_text="실행 기록").first.click()
    settle(pg)
    rb = pg.locator("button.wk-run-review").first
    if rb.count():
        rkey = rb.get_attribute("data-focus-key")
        rb.focus()
        pg.keyboard.press("Enter")
        pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
        pg.wait_for_timeout(800)
        rec("nav", {"what": "실행 기록 → 검토 상세", "opener_key": rkey, "after": focus(pg)})
        pg.go_back()
        settle(pg, 1500)
        rec("nav", {"what": "검토 상세에서 브라우저 뒤로 (실행 기록)", "opener_key": rkey, "after": focus(pg), "url": pg.evaluate("location.pathname")})


def check_analysis(pg: Page) -> None:
    login(pg, "user1")
    pg.locator(".side-link", has_text="데이터 통계 분석").first.click()
    settle(pg)
    summary = pg.locator("details.an-past > summary").first
    if not summary.count():
        rec("nav", {"what": "분석: 지난 결과 열기", "result": "지난 결과 없음 (측정 안 함)"})
        return
    summary.focus()
    pg.keyboard.press("Enter")
    pg.keyboard.press("Tab")
    link_text = focus(pg).get("text")
    pg.keyboard.press("Enter")
    pg.locator("section.an h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(1000)
    rec("nav", {"what": "분석: 지난 결과 열기 → 결과", "link": link_text, "after": focus(pg)})
    new = pg.get_by_role("link", name="새 통계 분석")
    new.focus()
    pg.keyboard.press("Enter")
    settle(pg, 1500)
    rec("nav", {"what": "분석: 결과 → '새 분석'", "after": focus(pg), "url": pg.evaluate("location.pathname + location.search")})


SUMMARY_JS = """() => { const s = document.querySelector('details.an-past > summary'); return s ? {tabindex_attr: s.getAttribute('tabindex'), tabIndex: s.tabIndex,
  open: s.parentElement.open, focused: document.activeElement === s} : null }"""
IS_SUMMARY = "e === document.querySelector('details.an-past > summary')"


def check_summary(pg: Page) -> None:
    """'새 분석'으로 돌아와 '내 분석 결과'(summary)에 초점이 온 뒤에도 기본 키보드 동작이 그대로인지 (복귀 직후 + 이후 이동)."""
    login(pg, "user1")
    pg.locator(".side-link", has_text="데이터 통계 분석").first.click()
    settle(pg)
    if not pg.locator("details.an-past > summary").count():
        rec("summary", {"what": "summary", "result": "지난 결과 없음 (측정 안 함)"})
        return
    rec("summary", {"what": "처음 상태", **pg.evaluate(SUMMARY_JS)})
    s = pg.locator("details.an-past > summary").first
    s.focus()
    pg.keyboard.press("Enter")
    pg.keyboard.press("Tab")
    pg.keyboard.press("Enter")
    pg.locator("section.an h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)
    new = pg.get_by_role("link", name="새 통계 분석")
    new.focus()
    pg.keyboard.press("Enter")
    settle(pg, 1500)
    rec("summary", {"what": "'새 분석'으로 돌아온 직후", **pg.evaluate(SUMMARY_JS), "focus": focus(pg)})
    # 다른 요소로 옮긴 뒤 Shift+Tab·Tab으로 다시 닿는지
    pg.keyboard.press("Tab")
    away = focus(pg)
    back = tab_to(pg, IS_SUMMARY, n=12, back=True)
    rec("summary", {"what": "Tab으로 벗어난 뒤 Shift+Tab으로 다시", "moved_to": away, "reached": back, **pg.evaluate(SUMMARY_JS)})
    pg.evaluate("(() => { const h = [...document.querySelectorAll('main h1')].find(e => !e.closest('[hidden]')); h.setAttribute('tabindex', '-1'); h.focus(); h.removeAttribute('tabindex') })()")
    fwd = tab_to(pg, IS_SUMMARY, n=40)
    rec("summary", {"what": "화면 제목에서 Tab으로 다시", "reached": fwd, **pg.evaluate(SUMMARY_JS)})
    # Enter·Space로 펼치고 접기
    s.focus()
    o0 = pg.evaluate(SUMMARY_JS)["open"]
    pg.keyboard.press("Enter")
    o1 = pg.evaluate(SUMMARY_JS)["open"]
    pg.keyboard.press("Space")
    pg.wait_for_timeout(100)
    o2 = pg.evaluate(SUMMARY_JS)["open"]
    rec("summary", {"what": "Enter·Space로 펼치고 접기", "open_before": o0, "after_enter": o1, "after_space": o2})


def open_review_row(pg: Page, key: str) -> None:
    btn = pg.locator(f"button[data-focus-key='{key}']").first
    btn.focus()
    pg.keyboard.press("Enter")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)


def check_history(pg: Page) -> None:
    """뒤로·앞으로·번갈아 열기·같은 상세를 다른 곳에서 열기·주소 직접 접근."""
    login(pg, "user1")
    pg.locator(".side-link", has_text="검토 목록").first.click()
    settle(pg)
    keys = pg.evaluate("[...document.querySelectorAll('button[data-focus-key^=\"review-row-\"]')].map(b => b.getAttribute('data-focus-key')).slice(0, 2)")
    if len(keys) < 2:
        rec("history", {"what": "번갈아 열기", "result": "검토가 2개 미만 (측정 안 함)"})
        return
    a, b = keys
    open_review_row(pg, a)
    pg.go_back()
    settle(pg, 1500)
    open_review_row(pg, b)
    detail_b = pg.evaluate("location.search")
    pg.go_back()
    settle(pg, 1500)
    after_back = focus(pg)
    pg.go_forward()
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    settle(pg, 1200)
    after_fwd = focus(pg)
    pg.go_back()
    settle(pg, 1500)
    rec("history", {"what": "번갈아 열기: A 열고 뒤로 → B 열고 뒤로 → 앞으로 → 뒤로", "a": a, "b": b, "b_url": detail_b, "after_back_b": after_back,
                    "after_forward_b": after_fwd, "after_back_again": focus(pg)})  # fmt: skip
    # 같은 상세 주소를 실행 기록과 검토 목록에서 각각 열기
    pg.locator(".side-link", has_text="실행 기록").first.click()
    settle(pg)
    rb = pg.locator("button.wk-run-review").first
    rkey = rb.get_attribute("data-focus-key")
    rb.focus()
    pg.keyboard.press("Enter")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(800)
    rid = pg.evaluate("new URLSearchParams(location.search).get('review')")
    pg.go_back()
    settle(pg, 1500)
    from_runs = focus(pg)
    pg.locator(".side-link", has_text="검토 목록").first.click()
    settle(pg)
    lkey = f"review-row-{rid}"
    if pg.locator(f"button[data-focus-key='{lkey}']").count():
        open_review_row(pg, lkey)
        pg.go_back()
        settle(pg, 1500)
        from_list = focus(pg)
    else:
        from_list = {"where": "측정 안 함 (목록에 같은 검토 줄 없음)"}
    rec("history", {"what": "같은 상세를 실행 기록·검토 목록에서 열고 뒤로", "review": rid, "runs_key": rkey, "back_to_runs": from_runs, "list_key": lkey, "back_to_list": from_list})
    # 주소 직접 접근·새로고침: 처음 그릴 때는 초점을 옮기지 않는다 (브라우저 기본 — 문서 처음, 첫 Tab은 본문 바로가기)
    pg.goto(f"{URL}/reviews?review={rid}")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    settle(pg, 1200)
    first = focus(pg)
    pg.keyboard.press("Tab")
    rec("history", {"what": "상세 주소 직접 접근(새로고침)", "after_load": first, "first_tab": focus(pg)})


def check_failure(pg: Page) -> None:
    """상세를 열지 못할 때: 검토 404, 분석 결과 500 (시험용 응답 — 서버에는 보내지 않음)."""
    login(pg, "user1")
    pg.locator(".side-link", has_text="검토 목록").first.click()
    settle(pg)
    key = pg.locator("button[data-focus-key^='review-row-']").first.get_attribute("data-focus-key")
    pat = re.compile(r"/api/reviews/\d+$")
    pg.route(pat, lambda r: r.fulfill(status=404, content_type="application/json", body='{"detail": "검토를 찾을 수 없습니다."}') if r.request.method == "GET" else r.continue_())
    btn = pg.locator(f"button[data-focus-key='{key}']").first
    btn.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(1500)
    rec("failure", {"what": "검토 상세를 찾지 못함 (시험용 404)", "after": focus(pg), "alerts": pg.locator("section.review-panel [role=alert]").all_inner_texts()})
    pg.unroute(pat)
    pg.locator(".side-link", has_text="데이터 통계 분석").first.click()
    settle(pg)
    if not pg.locator("details.an-past > summary").count():
        rec("failure", {"what": "분석 결과를 불러오지 못함", "result": "지난 결과 없음 (측정 안 함)"})
        return
    apat = re.compile(r"/api/analysis/runs/\d+$")
    pg.route(apat, lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail": "진단 시험 오류"}'))
    s = pg.locator("details.an-past > summary").first
    s.focus()
    pg.keyboard.press("Enter")
    pg.keyboard.press("Tab")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(1000)
    at_1s = focus(pg)
    pg.wait_for_timeout(2600)
    rec("failure", {"what": "분석 결과를 불러오지 못함 (시험용 500)", "after_1s": at_1s, "after_3_6s": focus(pg), "alerts": pg.locator("section.an [role=alert]").all_inner_texts()})
    pg.unroute(apat)


def start_held_analysis(pg: Page, held: list):
    """분석 지난 결과를 열되 상세 응답을 붙잡아 둔다 (시험에서 '기다리는 중'을 만든다). 붙잡은 경로를 돌려준다."""
    pg.locator(".side-link", has_text="데이터 통계 분석").first.click()
    settle(pg)
    apat = re.compile(r"/api/analysis/runs/\d+$")
    pg.route(apat, lambda r: held.append(r))
    s = pg.locator("details.an-past > summary").first
    s.focus()
    pg.keyboard.press("Enter")
    pg.keyboard.press("Tab")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(500)
    return apat


def release_held(pg: Page, held: list, apat) -> None:
    for r in held:
        try:
            r.continue_()
        except Exception:  # noqa: BLE001 — 화면이 바뀌어 요청이 이미 끝난 경우
            pass
    held.clear()
    pg.unroute(apat)


def check_stale(pg: Page) -> None:
    """상세를 기다리는 동안 다른 일이 생겼을 때 이전 대기가 지금 화면의 초점을 빼앗지 않는지 (시간 초과 3초를 넘겨 확인)."""
    login(pg, "user1")
    if not (pg.locator(".side-link", has_text="데이터 통계 분석").count()):
        return
    held: list = []
    # A) 기다리는 동안 메뉴로 다른 화면 → 사용자가 글자를 눌러 초점이 본문으로 → 3.6초 뒤
    apat = start_held_analysis(pg, held)
    pg.locator(".side-link", has_text="실행 기록").first.click()
    settle(pg, 600)
    at_nav = focus(pg)
    pg.evaluate("document.activeElement && document.activeElement.blur()")  # 글자를 눌러 초점이 본문으로 간 상태
    release_held(pg, held, apat)
    pg.wait_for_timeout(3600)
    rec("stale", {"what": "상세 대기 중 다른 화면으로 → 초점이 본문으로 → 3.6초 뒤", "after_nav": at_nav, "after_3_6s": focus(pg), "path": pg.evaluate("location.pathname")})
    # B) 기다리는 동안 로그아웃 → 새로 고치지 않고 다시 로그인 → 3.6초 뒤
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    apat = start_held_analysis(pg, held)
    t0 = pg.evaluate("performance.now()")  # 상세 대기가 시작된 뒤 (시간 초과 3초 안에 다시 로그인해야 시험이 성립)
    pg.locator(".side-logout").click()
    pg.wait_for_selector(".login-card", timeout=15000)
    pg.get_by_label("아이디").fill(USERS["user1"])
    pg.get_by_label("비밀번호", exact=True).fill(creds()["user1"])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    relogin_ms = round(pg.evaluate("performance.now()") - t0) + 500  # start_held_analysis 안에서 기다린 0.5초 포함
    release_held(pg, held, apat)
    after_login = focus(pg)
    pg.wait_for_timeout(3600)
    rec("stale", {"what": "상세 대기 중 로그아웃 → 다시 로그인 → 3.6초 뒤", "relogin_ms_after_wait_start": relogin_ms, "valid_before_timeout": relogin_ms < 3000,
                  "after_login": after_login, "after_3_6s": focus(pg), "page_errors": errors})  # fmt: skip
    # C) 기다리는 동안 사용자가 다른 요소로 초점을 옮김 → 상세 도착
    apat = start_held_analysis(pg, held)
    pg.locator(".side-link", has_text="내 계정").first.focus()
    moved = focus(pg)
    release_held(pg, held, apat)
    pg.locator("section.an h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(1200)
    rec("stale", {"what": "상세 대기 중 사용자가 다른 요소로 이동 → 상세 도착", "moved_to": moved, "after": focus(pg)})
    # D) 기다리는 동안 확인창이 열림 → 상세 도착 (시험용 <dialog>를 넣어 확인 — 이 화면에는 대기 중 열 수 있는 앱 확인창이 없음)
    pg.goto(URL + "/")
    settle(pg)
    apat = start_held_analysis(pg, held)
    pg.evaluate("""() => { const d = document.createElement('dialog'); d.id = 'diag-test-dialog'; d.innerHTML = '<p>시험용 확인창</p><button>닫기</button>';
        document.body.appendChild(d); d.showModal() }""")
    in_dialog = focus(pg)
    release_held(pg, held, apat)
    pg.locator("section.an h2").first.wait_for(timeout=60000)
    pg.wait_for_timeout(1200)
    during = focus(pg)
    pg.evaluate("document.getElementById('diag-test-dialog').close(); document.getElementById('diag-test-dialog').remove()")
    pg.wait_for_timeout(500)
    rec("stale", {"what": "상세 대기 중 확인창(시험용 dialog)이 열림 → 상세 도착", "opened": in_dialog, "while_open": during, "after_close": focus(pg)})


def check_keep(pg: Page) -> None:
    """주소만 고치는 선택·검색·백그라운드 갱신에서는 초점을 옮기지 않는다."""
    login(pg, "user1")
    search = pg.get_by_label("검사할 파일 검색")
    search.focus()
    pg.keyboard.type("진단")
    pg.wait_for_timeout(800)
    rec("nav", {"what": "홈 검색 입력", "after": focus(pg)})
    search.fill("")
    pg.goto(f"{URL}/?files={IDS['user1']['basic']}")  # 주소의 고른 파일을 반영한 뒤 주소에서 빼는(replaceSearch) 화면
    settle(pg, 1500)
    pg.get_by_label("검사할 파일 검색").focus()
    rec("nav", {"what": "고른 파일 반영 뒤 주소 정리 (replaceSearch)", "url": pg.evaluate("location.pathname + location.search"), "after": focus(pg)})
    box = pg.locator("input[type=checkbox][aria-label$='검증 대상']").first
    box.focus()
    pg.keyboard.press("Space")
    pg.wait_for_timeout(800)
    rec("nav", {"what": "검증 대상 체크 (Space)", "after": focus(pg)})
    pg.goto(f"{URL}/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}")
    settle(pg)
    rm = pg.locator("button", has_text="이 파일 빼기").first
    if rm.count():
        rm.focus()
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1000)
        f1 = focus(pg)
        pg.keyboard.press("Tab")
        rec("nav", {"what": "묶기: 파일 빼기 (replaceSearch, 누른 버튼이 사라짐)", "url": pg.evaluate("location.pathname + location.search"), "after": f1, "next_tab": focus(pg)})


LONG_NAMES = ["진단용합성지역이름" * 7, "확인용다른합성지역" * 7]  # 용어사전에 없는 긴 이름 둘 → 근거가 110자를 넘어 '전체 보기'가 두 줄에 생긴다 (합성 값)


def long_review() -> int | None:
    """긴 근거·긴 위치가 있는 합성 검토를 만든다 (user1: 합성 파일 업로드·매핑·검토 생성, 완료하지 않음)."""
    api = Api().login("user1")
    s, body, _ = api.upload("진단_긴이름_전체보기확인용_합성파일.xlsx", xlsx({"현황": [["주민등록 인구 현황 2026년 9월 (단위: 명)"], ["시도", "인구수"], [LONG_NAMES[0], 1], [LONG_NAMES[1], 3], ["충청북도", 2]]}))
    if s not in (200, 201):
        return None
    fid = body[0]["id"]
    s, pv, _ = api.call("GET", f"/files/{fid}/preview?sheet_id=s0")
    cols = {x["name"]: x["column_id"] for x in pv["columns"]}
    api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sido"},
                                                                       {"sheet_id": "s0", "column_id": cols["인구수"], "field_key": "population"}]})  # fmt: skip
    s, rv, _ = api.call("POST", "/reviews", {"file_ids": [fid], "expected_period": "2026-09"})
    return rv["id"] if s == 201 else None


def check_longtext(pg: Page) -> None:
    rid = long_review()
    rec("longtext", {"what": "합성 검토 준비 (긴 이름)", "review_id": rid})
    login(pg, "user1")
    pg.set_viewport_size({"width": 1024, "height": 800})
    pg.goto(f"{URL}/reviews?review={rid or IDS['user1_review']}")
    pg.locator("section.review-panel h2").first.wait_for(timeout=60000)
    settle(pg)
    rows = pg.evaluate("""() => [...document.querySelectorAll('.review-compact')].map((d, i) => { const w = d.querySelector('summary .review-where');
        return w ? {i, truncated: w.scrollWidth > w.clientWidth + 1, text: w.textContent.trim(), title: w.getAttribute('title')} : null }).filter(Boolean)""")
    cand = next((r for r in rows if r["truncated"]), rows[0] if rows else None)
    if cand is None:
        rec("longtext", {"what": "검토 위치", "result": "검토 항목 없음 (측정 안 함)"})
    else:
        summ = pg.locator(".review-compact").nth(cand["i"]).locator("summary")
        if not pg.locator(".review-compact").nth(cand["i"]).evaluate("d => d.open"):
            summ.focus()
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(400)
        full = pg.locator(".review-compact").nth(cand["i"]).locator(".review-where-full")
        info = full.evaluate("e => ({text: e.textContent.replace(/^\\s*위치\\s*/, '').trim(), clipped: e.scrollWidth > e.clientWidth + 1, visible: e.getClientRects().length > 0})") if full.count() else None
        rec("longtext", {"what": "검토 위치: 접힌 줄에서 잘림 → 펼치면 전체", "summary_truncated": cand["truncated"], "summary_text": cand["text"], "full": info,
                         "viewport": "1024x800"})  # fmt: skip
        pg.locator(".review-compact").nth(cand["i"]).screenshot(path=str(OUT / f"{LABEL}-review-where.png"))
    names = pg.evaluate("[...document.querySelectorAll('.review-reason-more')].map(b => b.getAttribute('aria-label') || b.textContent.trim())")  # 접힌 항목 안은 innerText가 비므로 textContent
    rec("longtext", {"what": "'전체 보기' 이름", "count": len(names), "distinct": len(set(names)), "names": names[:6]})
    more = pg.locator(".review-reason-more").first
    if more.count():
        if not more.is_visible():
            more.evaluate("b => b.closest('details').open = true")
        more.focus()
        pg.keyboard.press("Enter")
        pg.locator("dialog.review-dialog[open]").wait_for(timeout=10000)
        first = focus(pg)
        moves = []
        for _ in range(3):
            pg.keyboard.press("Tab")
            moves.append(focus(pg))
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        rec("longtext", {"what": "근거 전체 보기 창", "first": first, "tab": moves, "closed": pg.locator("dialog.review-dialog[open]").count() == 0,
                         "focus_back_on_opener": more.evaluate("b => document.activeElement === b")})  # fmt: skip
    else:
        rec("longtext", {"what": "근거 전체 보기 창", "result": "긴 근거 항목 없음 (측정 안 함)"})


TABLES_JS = """() => [...document.querySelectorAll('section.an .upload-table-wrap')].map(w => ({label: w.getAttribute('aria-label'), role: w.getAttribute('role'), tabindex: w.getAttribute('tabindex'),
  scrolls: w.scrollWidth > w.clientWidth + 1 || w.scrollHeight > w.clientHeight + 1, th_col: w.querySelectorAll('th[scope=col]').length, th_row: w.querySelectorAll('th[scope=row]').length,
  focusables_inside: w.querySelectorAll('a[href],button,input,select,textarea,[tabindex]').length}))"""


def check_tables(pg: Page) -> None:
    login(pg, "user1")
    for vp, size in (("desktop", {"width": 1280, "height": 800}), ("mobile", {"width": 390, "height": 844})):
        pg.set_viewport_size(size)
        pg.goto(f"{URL}/analysis?id={IDS['user1_analysis_many']}")
        pg.locator("section.an h2").first.wait_for(timeout=60000)
        settle(pg, 1500)
        tables = pg.evaluate(TABLES_JS)
        scrolled = None
        region = pg.locator("section.an .upload-table-wrap[tabindex='0']").first
        if region.count():
            region.focus()
            before = region.evaluate("w => [w.scrollLeft, w.scrollTop]")
            pg.keyboard.press("ArrowRight")
            pg.keyboard.press("ArrowDown")
            pg.wait_for_timeout(300)
            scrolled = {"before": before, "after": region.evaluate("w => [w.scrollLeft, w.scrollTop]"), "focus": focus(pg)}
        pager = pg.locator("section.an .mw-pager").count()
        rec("tables", {"viewport": vp, "tables": tables, "keyboard_scroll": scrolled, "pagers": pager})
    pg.set_viewport_size({"width": 1280, "height": 800})


META: dict = {}


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = b.new_context()
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        META.update(run_meta("nav_focus_check.py", LABEL, pg.evaluate(ASSET_JS)) | {"browser": f"msedge {b.version}"})
        cx.close()
        for name, fn in (("skip", check_skip), ("menu", check_menu), ("reviews", check_reviews), ("analysis", check_analysis), ("summary", check_summary),
                         ("history", check_history), ("failure", check_failure), ("stale", check_stale), ("keep", check_keep),
                         ("longtext", check_longtext), ("tables", check_tables)):  # fmt: skip
            if os.environ.get("ONLY_CHECKS") and name not in os.environ["ONLY_CHECKS"].split(","):
                continue
            cx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            pg = cx.new_page()
            try:
                fn(pg)
            except Exception as e:  # noqa: BLE001 — 측정 실패로 기록 (통과로 세지 않음)
                rec("error", {"check": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                pg.screenshot(path=str(OUT / f"{LABEL}-error-{name}.png"), full_page=True)
            cx.close()
        b.close()
    RES["blocked_out_of_scope"] = BLOCKED
    RES["meta"] = META
    (OUT / f"nav-check-{LABEL}.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
