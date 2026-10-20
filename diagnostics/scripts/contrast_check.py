"""색 대비 측정 (axe가 판정을 보류한 글자·비텍스트 대상) — 계산된 색으로 대비비를 구한다.

글자: 글자색 vs 가장 가까운 불투명 배경. 비텍스트: 입력 테두리·스위치·초점 표시 vs 인접 배경.
반투명·그라데이션·이미지 배경은 '측정 불가'로 남긴다. 사용법: python contrast_check.py <출력 폴더>
계산은 contrast_calc.py와 같다 (sRGB 경계 0.04045, 판정은 원시값 ratio_raw, 보이는 ratio는 소수 셋째 자리 내림).
2026-10-20 진단 3차 보완 전에는 경계 0.03928, 소수 둘째 자리 반올림이었다 (1차 원시 결과 raw/는 그 방식 그대로 보존).
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from diag_common import EV, URL, USERS, creds
from diag_scope import browser_proxy, route_guard

BLOCKED: list[str] = []  # 진단 주소 밖이라 막은 첫 요청

OUT = Path(sys.argv[1])
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
JS = """([sel, prop]) => {
  const parse = s => { const m = s.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1} }
  const lum = c => { const f = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b) }
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
  const bgOf = e => { let p = e; while (p) { const cs = getComputedStyle(p); if (cs.backgroundImage !== 'none') return 'image'; const c = parse(cs.backgroundColor); if (c && c.a === 1) return c; if (c && c.a > 0) return 'translucent'; p = p.parentElement } return {r: 255, g: 255, b: 255, a: 1} }
  return [...document.querySelectorAll(sel)].filter(e => e.offsetWidth).slice(0, 8).map(e => {
    const cs = getComputedStyle(e)
    const fg = parse(prop === 'text' ? cs.color : prop === 'border' ? cs.borderTopColor : prop === 'outline' ? cs.outlineColor : cs.backgroundColor)
    const bg = bgOf(prop === 'text' ? e : e.parentElement)
    const out = {sel, prop, text: (e.innerText || '').trim().slice(0, 20), fontSize: cs.fontSize, fontWeight: cs.fontWeight}
    if (!fg) return {...out, result: '측정 불가(색 없음)'}
    if (typeof bg === 'string') return {...out, result: '측정 불가(' + bg + ' 배경)'}
    if (fg.a < 1) return {...out, result: '측정 불가(반투명 색)', fg}
    return {...out, ratio_raw: ratio(fg, bg), ratio: Math.floor(ratio(fg, bg) * 1000) / 1000, fg: `rgb(${fg.r},${fg.g},${fg.b})`, bg: `rgb(${bg.r},${bg.g},${bg.b})`}
  })
}"""

CASES = [
    ("mobile", f"/analysis?id={IDS['user1_analysis']}", [("table.an-result td", "text"), ("table.an-result td::before", "text")]),
    ("desktop", f"/reviews?review={IDS['user1_review']}", [(".review-arrow", "text"), (".review-where", "text"), (".review-source", "text")]),
    ("desktop", "/account", [("input[type=password]", "border"), (".mw-muted", "text")]),
    ("desktop", f"/bundle?files={IDS['user1']['basic']},{IDS['user1']['many']}", [("[role=switch], .wk-switch, input[type=checkbox] + span", "background")]),
    ("desktop", "/", [(".upload-drop", "border"), (".upload-drop-hint", "text"), (".side-link", "text"), (".side-link.is-disabled", "text")]),
    ("desktop", "/moajwoAdmin/users", [(".dash-sub", "text"), (".upload-tag", "text")]),
]

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))  # 진단 주소 밖은 리다이렉트 이후까지 막음
    res = []
    for vp, path, sels in CASES:
        who = "owner" if "moajwoAdmin" in path else "user1"
        pg = b.new_page(viewport={"width": 390, "height": 844} if vp == "mobile" else {"width": 1280, "height": 800})
        pg.route("**/*", route_guard(URL, BLOCKED))
        pg.goto(URL + "/")
        pg.get_by_label("아이디").fill(USERS[who])
        pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
        pg.locator(".login-card button[type=submit]").click()
        pg.wait_for_selector(".shell")
        pg.goto(URL + path)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        for sel, prop in sels:
            if "::before" in sel:  # 모바일 카드의 data-label 머리글 (가상 요소)
                r = pg.evaluate("""() => { const e = document.querySelector('table.an-result td'); if (!e) return null; const cs = getComputedStyle(e, '::before');
                    return {content: cs.content, color: cs.color, fontSize: cs.fontSize} }""")
                res.append({"vp": vp, "path": path, "sel": sel, "pseudo": r})
                continue
            for x in pg.evaluate(JS, [sel, prop]):
                res.append({"vp": vp, "path": path, **x})
        # 초점 표시 색: 첫 버튼에 키보드 초점
        pg.keyboard.press("Tab")
        f = pg.evaluate("""() => { const e = document.activeElement; const cs = getComputedStyle(e); return {tag: e.tagName, outline: cs.outlineColor + ' ' + cs.outlineStyle + ' ' + cs.outlineWidth, shadow: cs.boxShadow} }""")
        res.append({"vp": vp, "path": path, "focus_style": f})
        pg.close()
    b.close()
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "contrast.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for r in res:
    print(json.dumps(r, ensure_ascii=False)[:300])
