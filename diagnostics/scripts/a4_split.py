"""A4(axe color-contrast '판정 보류') 전후 대상 나누기 — 저장한 DOM만 읽는다 (화면·서버에 접속하지 않음).

전후 비교에서 합성 파일이 늘면 목록 행이 늘어 판정 보류 대상도 늘 수 있다. 늘어난 수를 곧바로 새 결함이나 오탐으로 정하지 않고,
대상마다 1차 DOM에 그 요소가 있었는지(선택자를 1차 DOM에 적용) 보아 다음으로 나눈다.
- 기존 요소, 계속 판정 보류 / 기존 요소, 새로 판정 보류 / 1차 DOM에 없던 요소 (자료 증가 등) / 판정 보류에서 빠짐
판정 보류 자체(대비가 실제로 기준을 넘는지)는 여기서 판정하지 않는다.
사용법: python a4_split.py <1차 raw 폴더> <나중 a11y 폴더> <출력 md>
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from summarize_a11y import load_dir, load_round1

from diag_scope import DEAD_PROXY

RAW, AFTER, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
PROBE = "sel => { let e = null; try { e = document.querySelector(sel) } catch { return {ok: false, error: '선택자 오류'} } return {ok: true, found: !!e, text: e ? e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 40) : null} }"


def targets(state: dict) -> list[str]:
    for v in state.get("axe_incomplete") or []:
        if v["id"] == "color-contrast":
            return [t[-1] if isinstance(t, list) else t for t in v["targets"]]
    return []


def dom_path(state: dict, folder: Path) -> Path:
    return folder / "dom" / f"{state['viewport']}-{state['id']}.html"


def main() -> None:
    before = {(s["viewport"], s["id"]): s for s in load_round1(RAW)}
    after = {(s["viewport"], s["id"]): s for s in load_dir(AFTER)}
    rows, counts = [], {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy={"server": DEAD_PROXY})
        cx = b.new_context(java_script_enabled=False)
        cx.route("**/*", lambda r: r.abort())  # 저장한 문서 안의 CSS·이미지 요청도 내보내지 않음
        pg = cx.new_page()

        def probe(html: Path, sel: str) -> dict:
            if not html.exists():
                return {"ok": False, "error": "DOM 없음"}
            pg.set_content(html.read_text(encoding="utf-8"), wait_until="domcontentloaded")
            return pg.evaluate(PROBE, sel)

        for key, a in after.items():
            bs = before.get(key, {})
            tb, ta = targets(bs), targets(a)
            if not tb and not ta:
                continue
            b_dom = dom_path(bs, RAW / ("a11y-retry" if bs.get("_run", "").startswith("재실행") else "a11y")) if bs else None
            for sel in sorted(set(tb) | set(ta)):
                if sel in tb and sel in ta:
                    kind, bp, ap = "기존 요소, 계속 판정 보류", None, None
                elif sel in ta:
                    bp = probe(b_dom, sel) if b_dom else {"ok": False, "error": "1차 상태 없음"}
                    ap = probe(dom_path(a, AFTER), sel)
                    kind = "기존 요소, 새로 판정 보류" if bp.get("found") else "1차 DOM에 없던 요소 (자료 증가 등)" if bp.get("ok") else f"확인 불가 ({bp.get('error')})"
                else:
                    bp, ap = None, probe(dom_path(a, AFTER), sel)
                    kind = "판정 보류에서 빠짐"
                counts[kind] = counts.get(kind, 0) + 1
                rows.append((key[0], key[1], sel, sel in tb, sel in ta, kind, (ap or {}).get("text") or (bp or {}).get("text") or ""))
        b.close()
    lines = ["# A4 (axe color-contrast 판정 보류) 대상 구분 — 저장한 DOM 기준", "", f"- 1차: `{RAW}` · 나중: `{AFTER}`", ""]
    lines += [f"- {k}: {v}개" for k, v in sorted(counts.items())]
    lines += ["", "| 화면 | 상태 | 선택자 | 1차 판정 보류 | 나중 판정 보류 | 구분 | 요소 글자 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {v} | {i} | `{s}` | {'예' if x else '-'} | {'예' if y else '-'} | {k} | {t} |" for v, i, s, x, y, k, t in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
