"""S-03 확인: 보안 응답 헤더·CSP를 건 frontend(nginx)에서 주요 화면이 그대로 동작하는지
(격리 서버, 새 계정·합성 자료, 측정만 저장 — 판정하지 않음).

일반 계정으로 화면 업로드(진행 막대의 인라인 스타일 포함) → 열 매핑 → 데이터 통계 분석 → 묶기 → 검토 목록·실행 기록·자동화·
내 계정을 열고, 최고 관리자로 사전 관리·승격 심사·설정·관리자 화면을 연다. 화면마다 CSP 위반(securitypolicyviolation 이벤트·
콘솔 'Content Security Policy'), 페이지 오류, 화면 제목, 응답 헤더를 적는다.
사용법: python csp_check.py <출력 폴더> <라벨>
"""

import io
import json
import secrets
import sys
from pathlib import Path

import openpyxl
from playwright.sync_api import Page, sync_playwright

from diag_common import ASSET_JS, URL, USERS, Api, creds, run_meta
from diag_scope import browser_proxy, route_guard

OUT, LABEL = Path(sys.argv[1]), sys.argv[2]
OUT.mkdir(parents=True, exist_ok=True)
BLOCKED: list[str] = []
REC: list[dict] = []
CATCH = """() => { window.__csp = []; document.addEventListener('securitypolicyviolation', e =>
  window.__csp.push({directive: e.violatedDirective, blocked: e.blockedURI, sample: (e.sample || '').slice(0, 80)})) }"""


def synthetic_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "현황"
    ws.append(["2026년 9월 시도별 인구 (단위: 명)"])
    ws.append(["시도", "인구"])
    for name, n in (("서울특별시", 100), ("부산광역시", 200), ("대구광역시", 300)):
        ws.append([name, n])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def visit(pg: Page, who: str, path: str, console: list[str], errors: list[str]) -> None:
    console.clear()
    errors.clear()
    res = pg.goto(URL + path)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(300)
    title = pg.locator("h1.page-title:visible, h1:visible").first
    item = {
        "who": who,
        "path": path,
        "status": res.status if res else None,
        "csp_header": (res.headers.get("content-security-policy") if res else None) is not None,
        "title": " ".join(title.inner_text().split()) if title.count() else None,
        "csp_events": pg.evaluate("() => window.__csp || []"),
        "csp_console": [c for c in console if "Content Security Policy" in c or "Refused to" in c],
        "page_errors": list(errors),
    }
    REC.append(item)
    print(json.dumps(item, ensure_ascii=False)[:400], flush=True)


def login(pg: Page, name: str, password: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(name)
    pg.get_by_label("비밀번호", exact=True).fill(password)
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)


def main() -> None:
    owner_api = Api().login("owner")
    name, password = f"csp-{LABEL}", "Cs-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner_api.call("POST", "/users", {"username": name, "display_name": "CSP 확인", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    src = OUT / "csp-합성.xlsx"
    src.write_bytes(synthetic_xlsx())
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for who, account in (("user", (name, password)), ("owner", (USERS["owner"], creds()["owner"]))):
            cx = br.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
            cx.route("**/*", route_guard(URL, BLOCKED))
            cx.add_init_script(CATCH)
            pg = cx.new_page()
            console: list[str] = []
            errors: list[str] = []
            pg.on("console", lambda m, c=console: c.append(f"{m.type}: {m.text}"[:300]))
            pg.on("pageerror", lambda e, r=errors: r.append(str(e)[:300]))
            login(pg, *account)
            asset = pg.evaluate(ASSET_JS)
            if who == "user":
                visit(pg, who, "/", console, errors)
                pg.locator("input[type=file]").first.set_input_files(str(src))  # 화면 업로드 (진행 막대)
                pg.wait_for_timeout(3000)
                pg.wait_for_load_state("networkidle")
                REC.append({"who": who, "step": "upload", "csp_events": pg.evaluate("() => window.__csp || []"),
                            "csp_console": [c for c in console if "Content Security Policy" in c or "Refused to" in c],
                            "page_errors": list(errors)})  # fmt: skip
                print("upload", json.dumps(REC[-1], ensure_ascii=False)[:300], flush=True)
                for path in ("/mapping", "/analysis", "/bundle", "/reviews", "/runs", "/automations", "/account"):
                    visit(pg, who, path, console, errors)
            else:
                for path in ("/", "/dictionary", "/promotions", "/settings", "/moajwoAdmin", "/moajwoAdmin/users", "/moajwoAdmin/audit"):
                    visit(pg, who, path, console, errors)
            cx.close()
        br.close()
    meta = run_meta("csp_check.py", LABEL, asset)
    out = {"meta": meta, "blocked": BLOCKED, "visits": REC}
    (OUT / f"csp-{LABEL}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
