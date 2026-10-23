"""U-01~U-03로 바뀐 화면의 axe 위반·가로 넘침 측정 (격리 서버, 기존 계정, 측정만 저장 — 판정하지 않음).

공통 항목 검토(미분석 파일만 고른 상태·전부 분석한 상태), 데이터 통계 분석 결과, 파일별 시트로 묶기(통계 요약 켬), 실행 기록을
1280px·390px 두 너비로 열어 axe(WCAG 2.x A·AA 규칙)를 돌리고, 문서가 창보다 넓어지는지(가로 넘침)와 캡처를 남긴다.
axe 통과는 전체 접근성 준수를 뜻하지 않는다 (스크린리더 낭독·확대·IME는 따로).
사용법: python review_a11y_check.py <출력 폴더> <라벨> <creds.json(계정: 비밀번호)> <axe.min.js> <분석 결과 번호> <파일 번호,...> [미분석 파일 번호,...]
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from diag_common import ASSET_JS, URL, run_meta
from diag_scope import browser_proxy, route_guard

OUT, LABEL, CREDS, AXE = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
RUN_ID, FILES = int(sys.argv[5]), [int(x) for x in sys.argv[6].split(",")]
UNANALYZED = [int(x) for x in sys.argv[7].split(",")] if len(sys.argv) > 7 else []
OUT.mkdir(parents=True, exist_ok=True)
REC: dict[str, list] = {}
BLOCKED: list[str] = []
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
OVERFLOW_JS = "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"


def check(br, width: int, height: int, name: str, password: str) -> None:  # noqa: ANN001 — Playwright Browser
    cx = br.new_context(viewport={"width": width, "height": height}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(name)
    pg.get_by_label("비밀번호", exact=True).fill(password)
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    asset = pg.evaluate(ASSET_JS)

    def measure(state: str) -> None:
        pg.wait_for_timeout(800)
        pg.add_script_tag(path=str(AXE))
        res = pg.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", TAGS)
        found = [{"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"]),
                  "targets": [n["target"] for n in v["nodes"][:5]]} for v in res["violations"]]  # fmt: skip
        rec = {"state": state, "width": width, "overflow": pg.evaluate(OVERFLOW_JS), "violations": found,
               "incomplete": [x["id"] for x in res["incomplete"]]}  # fmt: skip
        REC.setdefault("states", []).append(rec)
        print(json.dumps(rec, ensure_ascii=False)[:300], flush=True)
        pg.screenshot(path=str(OUT / f"{LABEL}-{width}-{state}.png"), full_page=True)

    def common(ids: list[int], state: str) -> None:
        pg.goto(URL + "/mapping/common")
        pg.locator("label.mw-file-option").first.wait_for(timeout=60000)
        for fid in ids:
            pg.locator("label.mw-file-option", has_text=f"#{fid} ").click()
        pg.get_by_role("button", name="공통 검토 열기").click()
        pg.locator(".wk-scope").wait_for(timeout=60000)
        measure(state)

    if UNANALYZED:
        common(UNANALYZED, "common-unanalyzed")
    common(FILES, "common-analyzed")
    pg.goto(f"{URL}/analysis?id={RUN_ID}")
    pg.locator("table.an-result tbody tr").first.wait_for(timeout=60000)
    measure("analysis-result")
    # 묶기 초안은 브라우저 저장소에 있어 새 문맥에는 없다 — 화면에서 요약을 켜고 항목·단위·반복 확인을 거친다
    pg.goto(f"{URL}/bundle?files={','.join(map(str, FILES))}")
    pg.get_by_text("통계 요약 시트 추가").first.click()
    pg.locator(".bd-add select").first.select_option("count")
    pg.get_by_role("button", name="항목 추가").click()
    pg.locator(".bd-item-head").first.wait_for(timeout=60000)
    pg.wait_for_timeout(3000)
    for box in pg.locator("table.bd-links input[aria-label$=' 단위']").all():
        box.fill("건")
    pg.wait_for_timeout(3000)
    for box in pg.get_by_label("반복 행 모두 더함").all():
        if not box.is_checked():
            box.check()
    pg.wait_for_timeout(3000)
    measure("bundle-summary")
    pg.goto(URL + "/runs")
    pg.locator("table.runs-table tbody tr").first.wait_for(timeout=60000)
    measure("runs")
    REC.setdefault("meta", []).append(run_meta("review_a11y_check.py", f"{LABEL}-{width}", asset))
    cx.close()


def main() -> None:
    name, password = next(iter(json.loads(CREDS.read_text(encoding="utf-8")).items()))
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for width, height in ((1280, 900), (390, 844)):
            check(br, width, height, name, password)
        br.close()
    REC["blocked_out_of_scope"] = BLOCKED
    (OUT / f"review-a11y-{LABEL}.json").write_text(json.dumps(REC, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
