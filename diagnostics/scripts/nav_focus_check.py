"""화면 이동·초점·긴 내용·표 탐색 측정 (진단 격리 스택, 합성 자료) — 측정만 저장하고 판정은 a11y_regress.py가 한다.

- 본문 바로가기: 첫 Tab에 나오는지, 누르면 지금 화면의 제목(숨긴 홈·다른 화면 제목이 아님)으로 가는지, 주소가 그대로인지
- 화면 이동 뒤 초점: 메뉴 → 새 화면 제목, 목록 → 상세 시작, 상세 → 목록(연 버튼), 뒤로가기·앞으로가기, 주소만 고치는 선택은 초점 유지
- 긴 내용: 검토 위치가 접힌 줄에서 잘릴 때 펼치면 전체가 보이는지, '전체 보기' 이름이 서로 구분되는지, 근거 창을 닫으면 연 버튼으로 돌아오는지
- 분석 표: 스크롤 영역이 넘칠 때만 이름·Tab 정지점이 있는지, 키보드로 스크롤되는지, 머리글·쪽 이동이 그대로인지
화면 읽기 프로그램 낭독은 확인하지 않는다 (초점 위치·접근성 속성 기록).
진단 자료에 쓰는 동작: user1로 긴 합성 지역명 파일 업로드·매핑·검토 생성('전체 보기'를 띄우려는 것, 검토는 완료하지 않음).
사용법: python nav_focus_check.py <출력 폴더> <라벨>   환경: ONLY_CHECKS=skip,menu,reviews,analysis,keep,longtext,tables
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from diag_common import EV, URL, USERS, Api, creds, xlsx
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
    pg.locator(".side-link", has_text="데이터 분석").first.click()
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
    new = pg.get_by_role("link", name="새 분석")
    new.focus()
    pg.keyboard.press("Enter")
    settle(pg, 1500)
    rec("nav", {"what": "분석: 결과 → '새 분석'", "after": focus(pg), "url": pg.evaluate("location.pathname + location.search")})


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


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for name, fn in (("skip", check_skip), ("menu", check_menu), ("reviews", check_reviews), ("analysis", check_analysis), ("keep", check_keep),
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
    RES["browser"] = "msedge"
    (OUT / f"nav-check-{LABEL}.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
