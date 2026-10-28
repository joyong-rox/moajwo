"""9장 2차 — 같은 계정의 재진입: 새로고침, 검토 목록·실행 기록, 직접 주소, 뒤로/앞으로, 다시 내려받기, 로그아웃 뒤 재로그인.
1차 시드 계정(diag-user1)과 그 자료 번호(seed-ids.json)를 쓴다. 측정만 적는다.
사용법: python s9_reentry.py <출력 폴더>"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from diag_common import EV, URL, USERS, creds
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
REC: list[dict] = []
BLOCKED: list[str] = []


def rec(step: str, ok: bool, detail: object = "") -> None:
    REC.append({"step": step, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"{'통과' if ok else '실패'} | {step} | {str(detail)[:200]}", flush=True)


def title(pg: Page) -> str:
    h = pg.locator("h1.page-title:visible, main h1:visible").first
    return " ".join(h.inner_text().split()) if h.count() else ""


def login(pg: Page) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS["user1"])
    pg.get_by_label("비밀번호", exact=True).fill(creds()["user1"])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
    cx = br.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", accept_downloads=True)
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    login(pg)
    rec("R1. 로그인", pg.locator(".shell").count() == 1, title(pg))
    pg.reload()
    pg.wait_for_load_state("networkidle")
    rec("R2. 새로고침 뒤 로그인 유지", pg.locator(".shell").count() == 1 and pg.locator(".login-card").count() == 0, title(pg))

    pg.goto(URL + "/reviews")
    pg.wait_for_load_state("networkidle")
    rows = pg.locator("main table tbody tr, main .review-list li, main [data-review-id]")
    rec("R3. 검토 목록", "검토" in title(pg), f"제목 {title(pg)!r} · 행 {rows.count()}")

    pg.goto(URL + "/runs")
    pg.wait_for_load_state("networkidle")
    links = pg.get_by_role("link", name=re.compile("내려받기"))  # 링크 글자는 '내려받기' (파일 이름은 옆 칸)
    rec("R4. 실행 기록", "실행 기록" in title(pg), f"내려받기 링크 {links.count()}")
    if links.count():
        # 빈 데이터 폴더로 재배포한 뒤(R5_FILES_GONE=1)에는 이전 결과 파일이 없다 — 내려받기 대신 410 안내가 기대값
        gone = os.environ.get("R5_FILES_GONE") == "1"
        try:
            with pg.expect_download(timeout=20000 if gone else 60000) as d:
                links.first.click()
            path = OUT / "reentry-download.xlsx"
            d.value.save_as(path)
            rec("R5. 이전 결과 다시 내려받기", path.stat().st_size > 0 and not gone, f"{path.stat().st_size} bytes")
            path.unlink()
        except Exception as e:  # noqa: BLE001 — 내려받기가 없으면 화면에 보인 것을 적고 다음 단계로
            body = pg.locator("body").inner_text()[:200]
            rec("R5. 이전 결과 다시 내려받기" + (" (재배포 뒤 — 파일 없음 안내 기대)" if gone else ""),
                gone and "결과 파일이 서버에 없습니다" in body, f"{type(e).__name__} · 위치 {pg.url.replace(URL, '')} · 화면 {body}")  # fmt: skip
            pg.goto(URL + "/runs")
            pg.wait_for_load_state("networkidle")

    aid, rid = IDS.get("user1_analysis"), IDS.get("user1_review")
    if aid:
        pg.goto(f"{URL}/analysis?id={aid}")
        pg.wait_for_load_state("networkidle")
        pg.locator(".an-result tbody tr").first.wait_for(timeout=60000)
        rec("R6. 직접 주소 — 분석 결과", pg.locator(".an-result tbody tr").count() > 0, f"#{aid} · 결과 행 {pg.locator('.an-result tbody tr').count()}")
    if rid:
        pg.goto(f"{URL}/reviews?review={rid}")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        body = " ".join(pg.locator("main").inner_text().split())
        rec("R7. 직접 주소 — 검토", f"#{rid}" in body or "검토" in body, body[:160])

    seq = []
    for path in ("/", "/runs", "/reviews"):
        pg.goto(URL + path)
        pg.wait_for_load_state("networkidle")
        seq.append(title(pg))
    pg.go_back()
    pg.wait_for_load_state("networkidle")
    back1 = title(pg)
    pg.go_back()
    pg.wait_for_load_state("networkidle")
    back2 = title(pg)
    pg.go_forward()
    pg.wait_for_load_state("networkidle")
    fwd = title(pg)
    rec("R8. 뒤로·뒤로·앞으로", back1 == seq[1] and back2 == seq[0] and fwd == seq[1], f"{seq} → {back1} → {back2} → {fwd}")

    pg.locator("button", has_text="로그아웃").first.click()
    pg.wait_for_selector(".login-card", timeout=60000)
    rec("R9. 로그아웃", pg.locator(".login-card").count() == 1, pg.url.replace(URL, ""))
    pg.goto(f"{URL}/runs")
    pg.wait_for_load_state("networkidle")
    rec("R10. 로그아웃 뒤 직접 주소는 로그인 화면", pg.locator(".login-card").count() == 1, pg.url.replace(URL, ""))
    login(pg)
    rec("R11. 다시 로그인", pg.locator(".shell").count() == 1, title(pg))
    br.close()
(OUT / "reentry.json").write_text(json.dumps({"steps": REC, "blocked": BLOCKED}, ensure_ascii=False, indent=1), encoding="utf-8")
