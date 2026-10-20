"""로그인 실패 직후 로그인 버튼의 대비 — 측정 시점에 따라 달라지는지 확인 (진단 격리 스택, 서버가 거절하는 로그인만).

72개 상태 검사에서 모바일 login-error의 로그인 버튼 대비 위반이 한 번 나온 원인을 찾는다. 오류 알림이 나타난 순간부터
0.5초 동안 프레임마다 버튼의 비활성 여부·마우스/초점 상태·계산된 배경색·글자색·투명도·전환 설정을 기록하고,
알림 직후와 0.3초 뒤에 axe color-contrast를 그 버튼에만 돌린다. 같은 과정을 화면 폭마다 여러 번 되풀이한다.
진단 자료에 쓰는 동작은 없다 (틀린 비밀번호 로그인 시도만, 서버가 거절). 사용법: python login_contrast_probe.py <출력 json> [반복 횟수, 기본 5]
환경: AXE_JS=axe.min.js 경로
"""

import json
import os
import sys
from pathlib import Path

from contrast_calc import oklab_to_srgb, over, ratio
from playwright.sync_api import sync_playwright

from diag_common import URL, USERS
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
AXE_JS = os.environ["AXE_JS"]
BLOCKED: list[str] = []
WATCH_JS = """() => { window.__diagWatch = new Promise(resolve => {
  const btn = document.querySelector('.login-card button[type=submit]')
  const samples = [], axe_runs = []
  const sample = (t0) => { const cs = getComputedStyle(btn), card = getComputedStyle(btn.closest('.login-card'));
    samples.push({t: Math.round(performance.now() - t0), disabled: btn.disabled, text: btn.textContent.trim(), bg: cs.backgroundColor, color: cs.color, opacity: cs.opacity,
                  card_bg: card.backgroundColor, transition: cs.transitionProperty + ' ' + cs.transitionDuration, hover: btn.matches(':hover'), focus_visible: btn.matches(':focus-visible'),
                  focused: document.activeElement === btn}) }
  const runAxe = async (label, t0) => { const r = await axe.run(btn, {runOnly: {type: 'rule', values: ['color-contrast']}});
    axe_runs.push({label, t: Math.round(performance.now() - t0), violations: r.violations.length, incomplete: r.incomplete.length, passes: r.passes.length,
                   summary: (r.violations[0] && r.violations[0].nodes[0] && r.violations[0].nodes[0].failureSummary || '').slice(0, 200)}) }
  const obs = new MutationObserver(() => {
    if (!document.querySelector('.login-card [role=alert], .login-card .upload-error')) return
    obs.disconnect()
    const t0 = performance.now()
    runAxe('알림 직후', t0)
    const loop = () => { sample(t0); if (performance.now() - t0 < 500) requestAnimationFrame(loop); else setTimeout(async () => { await runAxe('0.3초 뒤 (전환 끝)', t0); resolve({samples, axe_runs}) }, 0) }
    loop()
  })
  obs.observe(document.body, {subtree: true, childList: true})
}) }"""  # 약속은 window에 걸어 두고 바로 돌아온다 (클릭한 뒤 기다림)


def rgb(s: str):
    """계산된 색 'rgb(...)'·'rgba(...)'·'oklab(...)' → (sRGB 0~255 세 값, 알파)"""
    import re

    p = [float(x) for x in re.findall(r"-?[\d.]+", s)]
    if s.startswith("oklab"):
        return oklab_to_srgb(*p[:3]), (p[3] if len(p) > 3 else 1.0)
    return tuple(p[:3]), (p[3] if len(p) > 3 else 1.0)


def main() -> None:
    res = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for vp, size in (("mobile", {"width": 390, "height": 844}), ("desktop", {"width": 1280, "height": 800})):
            for i in range(ROUNDS):
                cx = b.new_context(viewport=size, locale="ko-KR")
                cx.route("**/*", route_guard(URL, BLOCKED))
                pg = cx.new_page()
                pg.goto(URL + "/")
                pg.wait_for_selector(".login-card")
                pg.add_script_tag(path=AXE_JS)
                pg.get_by_label("아이디").fill(USERS["user1"])
                pg.get_by_label("비밀번호", exact=True).fill("틀린-비밀번호-진단")
                pg.evaluate(WATCH_JS)  # 알림 감시 설치 (클릭 전)
                pg.locator(".login-card button[type=submit]").click()  # a11y_scan.py와 같은 방법 (마우스 클릭)
                data = pg.evaluate("() => window.__diagWatch")
                for s in data["samples"]:
                    (fg, fa), (bg, ba), (card, _) = rgb(s["color"]), rgb(s["bg"]), rgb(s["card_bg"])
                    op = float(s["opacity"])
                    bg_on = over(bg, ba * op, card)  # 요소 전체 투명도까지 카드 배경과 합성
                    s["ratio"] = round(ratio(over(fg, fa * op, bg_on), bg_on), 3)
                low = [s for s in data["samples"] if not s["disabled"] and s["ratio"] < 4.5]
                res.append({"viewport": vp, "round": i + 1, "axe_runs": data["axe_runs"], "frames": len(data["samples"]),
                            "first": data["samples"][0] if data["samples"] else None, "last": data["samples"][-1] if data["samples"] else None,
                            "enabled_frames_below_4_5": len(low), "below_until_ms": max((s["t"] for s in low), default=None),
                            "samples": data["samples"][:12]})  # fmt: skip
                print(vp, i + 1, data["axe_runs"], "활성인데 4.5 미만 프레임", len(low), "마지막", low[-1]["t"] if low else "-", flush=True)
                cx.close()
        b.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rounds": res, "blocked_out_of_scope": BLOCKED}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
