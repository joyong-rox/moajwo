"""Nu CSS 검사기가 모른다고 한 문법을 로컬 Edge가 지원하는지 (빈 페이지, 서버 접속 없음). 사용법: python css_support_check.py <출력 json>

진단 3차 보완(2026-10-20): 지원 여부(CSS.supports)만이 아니라 말줄임 선언이 실제로 줄 수를 줄이는지 렌더링 높이로 잰다.
앱의 .review-reason-text.is-clamped와 같은 선언 묶음, -webkit- 선언만, 접두어 없는 line-clamp만(두 가지)을 같은 글자·폭으로 비교한다.
"""
import json
import sys

from playwright.sync_api import sync_playwright

from diag_scope import DEAD_PROXY

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, proxy={"server": DEAD_PROXY})  # 네트워크 요청 없음 — 모든 요청을 막음
    pg = b.new_page()
    pg.set_content("""<style>@property --diag-len { syntax: '<length>'; inherits: false; initial-value: 3px; }
      .p { --diag-len: 7px; } .c { width: calc(var(--diag-len) * 2); }</style><div class="p"><div class="c" id="c"></div></div><div id="d"></div>
      <style>.t { width: 200px; font: 16px/20px sans-serif; } .box { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
      .app { -webkit-line-clamp: 2; line-clamp: 2; } .wk { -webkit-line-clamp: 2; } .std { line-clamp: 2; } .stdblock { line-clamp: 2; overflow: hidden; }</style>
      <div id="none" class="t">LONG</div><div id="app" class="t box app">LONG</div><div id="wk" class="t box wk">LONG</div>
      <div id="std" class="t box std">LONG</div><div id="stdblock" class="t stdblock">LONG</div>""".replace("LONG", "가나다라마바사 아자차카타파하 " * 12))
    r = pg.evaluate("""() => ({
      registerProperty: typeof CSS.registerProperty === 'function',
      at_property_initial_value: getComputedStyle(document.getElementById('d')).getPropertyValue('--diag-len').trim(),
      at_property_not_inherited: getComputedStyle(document.getElementById('c')).getPropertyValue('--diag-len').trim(),
      supports_line_clamp: CSS.supports('line-clamp', '2'),
      supports_webkit_line_clamp: CSS.supports('-webkit-line-clamp', '2'),
      supports_margin_trim: CSS.supports('margin-trim', 'inline'),
      supports_condition_with_unknown_property_parses: (() => { try { return CSS.supports('(not (margin-trim:inline))') } catch (e) { return 'error ' + e } })(),
      line_clamp_render: Object.fromEntries(['none', 'app', 'wk', 'std', 'stdblock'].map(id => { const e = document.getElementById(id), cs = getComputedStyle(e);
        return [id, {height_px: e.getBoundingClientRect().height, lines: Math.round(e.getBoundingClientRect().height / 20), computed_webkit_line_clamp: cs.webkitLineClamp,
                     computed_line_clamp: cs.getPropertyValue('line-clamp') || '(없음)'}] })),
    })""")
    r["browser"] = f"msedge {b.version}"
    b.close()
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(r, ensure_ascii=False, indent=1))
print(json.dumps(r, ensure_ascii=False))
