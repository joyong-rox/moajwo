"""Nu CSS 검사기가 모른다고 한 문법을 로컬 Edge가 지원하는지 (빈 페이지, 서버 접속 없음). 사용법: python css_support_check.py <출력 json>"""
import json
import sys

from playwright.sync_api import sync_playwright

from diag_scope import DEAD_PROXY

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, proxy={"server": DEAD_PROXY})  # 네트워크 요청 없음 — 모든 요청을 막음
    pg = b.new_page()
    pg.set_content("""<style>@property --diag-len { syntax: '<length>'; inherits: false; initial-value: 3px; }
      .p { --diag-len: 7px; } .c { width: calc(var(--diag-len) * 2); }</style><div class="p"><div class="c" id="c"></div></div><div id="d"></div>""")
    r = pg.evaluate("""() => ({
      registerProperty: typeof CSS.registerProperty === 'function',
      at_property_initial_value: getComputedStyle(document.getElementById('d')).getPropertyValue('--diag-len').trim(),
      at_property_not_inherited: getComputedStyle(document.getElementById('c')).getPropertyValue('--diag-len').trim(),
      supports_line_clamp: CSS.supports('line-clamp', '2'),
      supports_webkit_line_clamp: CSS.supports('-webkit-line-clamp', '2'),
      supports_margin_trim: CSS.supports('margin-trim', 'inline'),
      supports_condition_with_unknown_property_parses: (() => { try { return CSS.supports('(not (margin-trim:inline))') } catch (e) { return 'error ' + e } })(),
    })""")
    r["browser"] = f"msedge {b.version}"
    b.close()
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(r, ensure_ascii=False, indent=1))
print(json.dumps(r, ensure_ascii=False))
