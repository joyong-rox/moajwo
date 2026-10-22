"""U-01·U-02 확인: 공통 항목 검토의 단계 표시(대상·처리·제외)와 '데이터 통계 분석'으로 가는 버튼 (격리 서버, 새 계정, 측정만 저장).

사례: 선택 0개 → 고른 5개 모두 분석 기록 없음 → 버튼으로 이동 → 2개만 분석(일부 처리) → 5개 모두 분석 → 조회 실패(이 화면 요청만 500 주입)
→ 열어 둔 뒤 선택 변경. 단계 안내·적용 대상·안내 상자·상태 카드·버튼의 이름과 설명·도착 화면을 그대로 적는다 (판정하지 않음).
계정은 새로 만든다 (첫 사용). 비밀번호는 무작위로 만들어 출력 폴더의 creds 파일에만 둔다 (Git 밖).
사용법: python review_status_check.py <출력 폴더> <라벨> <새 계정 이름> <입력 파일 폴더> [파일 이름 ...]
"""

import json
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from diag_common import ASSET_JS, URL, XLSX, Api, run_meta
from diag_scope import browser_proxy, route_guard

OUT, LABEL, NAME, SRC = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
FILES = sys.argv[5:] or [f"{m} 사용량.xlsx" for m in ("행안부", "기획예산처", "재정경제부", "외교부", "통일부")]
OUT.mkdir(parents=True, exist_ok=True)
REC: dict[str, list] = {}
BLOCKED: list[str] = []

STATE_JS = r"""() => {
  const text = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : null)
  // 홈 화면은 숨긴 채 함께 남아 있으므로 공통 검토 화면 안의 단계만 읽는다
  const steps = [...document.querySelectorAll('.mw-common nav.wk-steps li')].map((li) => ({
    cls: [...li.classList].filter((c) => c.startsWith('is-')).join(' '), text: text(li) }))
  const btns = [...document.querySelectorAll('#wk-common-files .admin-actions button')].map((b) => ({
    text: text(b), disabled: b.disabled,
    describedby: b.getAttribute('aria-describedby') ? text(document.getElementById(b.getAttribute('aria-describedby'))) : null }))
  return {
    steps, buttons: btns,
    scope: text(document.querySelector('.wk-scope')),
    banners: [...document.querySelectorAll('.mw-common .wk-banner')].map(text),
    warns: [...document.querySelectorAll('.mw-common .mapping-warn')].map(text),
    errors: [...document.querySelectorAll('.mw-common .upload-error')].map(text),
    cards: text(document.querySelector('.mw-common .wk-cards')),
    groups: document.querySelectorAll('.mw-common .mw-groups > li').length,
  }
}"""


def rec(group: str, item: dict) -> None:
    REC.setdefault(group, []).append(item)
    print(group, json.dumps(item, ensure_ascii=False)[:400], flush=True)


def main() -> None:
    owner = Api().login("owner")
    password = "Rv-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": NAME, "display_name": f"검토 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    (OUT / f"creds-{LABEL}.json").write_text(json.dumps({NAME: password}), encoding="utf-8")
    u = Api()
    if u.call("POST", "/auth/login", {"username": NAME, "password": password})[0] != 200:
        raise RuntimeError("새 계정 로그인 실패")
    fids = []
    for name in FILES:
        s, body, _ = u.upload(name, (SRC / name).read_bytes(), XLSX)
        if s != 200:
            raise RuntimeError(f"업로드 실패 {name}: {s}")
        fids.append(body[0]["id"])
    rec("setup", {"user": NAME, "files": fids})

    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        cx = br.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(NAME)
        pg.get_by_label("비밀번호", exact=True).fill(password)
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell", timeout=60000)
        asset = pg.evaluate(ASSET_JS)

        def shot(case: str) -> None:
            pg.wait_for_timeout(400)
            rec("state", {"case": case, **pg.evaluate(STATE_JS)})
            pg.screenshot(path=str(OUT / f"{LABEL}-{case}.png"), full_page=True)

        def pick(ids: list[int], on: bool = True) -> None:
            for fid in ids:
                box = pg.locator("label.mw-file-option", has_text=f"#{fid} ").locator("input[type=checkbox]")
                if box.is_checked() != on:
                    pg.locator("label.mw-file-option", has_text=f"#{fid} ").click()

        def open_review() -> None:
            pg.get_by_role("button", name="공통 검토 열기").click()
            pg.wait_for_function("() => ![...document.querySelectorAll('button')].some(b => b.innerText.includes('모으는 중'))", timeout=60000)

        pg.goto(URL + "/mapping/common")
        pg.locator("label.mw-file-option").first.wait_for(timeout=60000)
        shot("0-none-picked")
        pick(fids)
        open_review()
        shot("1-all-unanalyzed")
        # U-02: 공통 검토의 분석 버튼이 어디로 가는지
        go = pg.locator("#wk-common-files .admin-actions button").nth(1)
        name = go.inner_text().strip()
        go.click()
        pg.wait_for_url("**/analysis**", timeout=30000)
        pg.wait_for_timeout(600)
        note = pg.locator("section.an p[role=note]:visible")
        rec("navigate", {"button": name, "url": pg.evaluate("location.pathname + location.search"), "title": pg.title(),
                         "section": pg.locator("section.an:visible").first.get_attribute("aria-label"),
                         "heading": pg.locator("h1.page-title:visible").first.inner_text().strip(),
                         "passed_note": note.first.inner_text().strip() if note.count() else None})  # fmt: skip
        pg.screenshot(path=str(OUT / f"{LABEL}-2-analysis-page.png"), full_page=True)
        pg.go_back()
        pg.locator("label.mw-file-option").first.wait_for(timeout=60000)

        for fid in fids[:2]:
            s, _, _ = u.call("POST", f"/files/{fid}/mapping?use_llm=false")
            rec("analyze", {"file": fid, "status": s})
        pick(fids)
        open_review()
        shot("3-two-of-five-analyzed")
        for fid in fids[2:]:
            s, _, _ = u.call("POST", f"/files/{fid}/mapping?use_llm=false")
            rec("analyze", {"file": fid, "status": s})
        open_review()
        shot("4-all-analyzed")

        def fail(route) -> None:  # noqa: ANN001 — 이 화면의 공통 검토 요청만 실패시킨다 (서버에는 보내지 않음)
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "시험용 실패 (주입)"}))

        pg.route("**/api/mapping/common", fail)
        open_review()
        shot("5-fetch-failed")
        pg.unroute("**/api/mapping/common", fail)
        open_review()
        pick(fids[-1:], on=False)
        shot("6-selection-changed")
        REC["meta"] = [run_meta("review_status_check.py", LABEL, asset)]
        br.close()
    REC["blocked_out_of_scope"] = BLOCKED
    (OUT / f"review-status-{LABEL}.json").write_text(json.dumps(REC, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
