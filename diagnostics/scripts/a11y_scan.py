"""웹 표준(Nu Html Checker)·자동 접근성(axe-core) 검사 — 진단 격리 스택의 화면 상태별 (데스크톱·모바일).

- 서버가 돌려준 문서(index.html)·CSS·SVG는 받은 그대로 Nu로 검사한다.
- 화면 상태마다 렌더링된 DOM(page.content())을 Nu로, 같은 상태를 axe로 검사한다 (서버 문서 검사와 구분해 기록).
- 상태에 도달하지 못하면 '검사 실패'로 남긴다 (통과로 세지 않음).
- 요청 범위는 diag_scope.py: 브라우저·Python 요청 모두 진단 주소만, Nu로 보내는 HTML은 로컬 검사기 주소만.
사용법: python a11y_scan.py <출력 폴더>
환경: AXE_JS(axe.min.js 경로), NU_URL(기본 http://127.0.0.1:8888 — 그 주소만 허용).
      NU_URL=off 이면 DOM·서버 문서만 저장하고 Nu 검사는 nu_check_dom.py로 따로 한다 (브라우저와 Nu를 동시에 띄우지 않기).
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from diag_common import EV, URL, USERS, creds
from diag_scope import browser_proxy, check_url, nu_base, opener, route_guard

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
AXE_JS = Path(os.environ["AXE_JS"])
NU = None if os.environ.get("NU_URL") == "off" else nu_base(os.environ.get("NU_URL"))  # 허용 밖이면 ScopeError
NU_OPENER = opener(NU) if NU else None
SITE = opener(URL)
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
IDS = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
U1 = IDS["user1"]
RESULTS: list[dict] = []
EXTERNAL: list[str] = []


def nu(body: bytes, ctype: str) -> list[dict] | None:
    if NU_OPENER is None:
        return None  # 나중에 nu_check_dom.py로 검사
    import urllib.request

    req = urllib.request.Request(check_url(f"{NU}/?out=json", NU), data=body, headers={"Content-Type": ctype}, method="POST")
    with NU_OPENER.open(req, timeout=120) as r:
        return json.loads(r.read())["messages"]


guard = route_guard(URL, EXTERNAL)


def settle(pg: Page) -> None:
    pg.wait_for_load_state("networkidle", timeout=60000)
    pg.wait_for_function("() => !document.body.innerText.includes('불러오는 중')", timeout=60000)
    pg.wait_for_timeout(400)


def login(pg: Page, who: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(USERS[who])
    pg.get_by_label("비밀번호", exact=True).fill(creds()[who])
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)


# 상태: (id, 사용자, 경로, 준비 동작 이름, 설명)
STATES = [
    ("login", None, "/", None, "로그인 화면"),
    ("login-error", None, "/", "bad_login", "로그인 실패 메시지"),
    ("signup", None, "/", "open_signup", "회원가입 양식"),
    ("home", "user1", "/", None, "홈(업로드·작업 단계)"),
    ("home-empty", "user3", "/", None, "홈 — 자료 없는 사용자"),
    ("mapping", "user1", f"/mapping?file={U1['basic']}", None, "열 매핑(확정된 파일)"),
    ("mapping-long", "user1", f"/mapping?file={U1['long']}", None, "열 매핑 — 긴 파일명·긴 열 이름·같은 표시값"),
    ("mapping-multisheet", "user1", f"/mapping?file={U1['multi']}&sheet=s1", None, "열 매핑 — 여러 시트(두 번째 시트)"),
    ("mapping-pii", "user1", f"/mapping?file={U1['pii']}", None, "열 매핑 — 개인정보 형식이 든 파일"),
    ("mapping-special", "user1", f"/mapping?file={U1['special']}", None, "열 매핑 — HTML·수식처럼 보이는 값"),
    ("mapping-common", "user1", "/mapping/common", None, "공통 항목 검토"),
    ("reviews", "user1", "/reviews", None, "검토 목록"),
    ("review-detail", "user1", f"/reviews?review={IDS['user1_review']}", None, "검토 상세(완료됨)"),
    ("reviews-empty", "user3", "/reviews", None, "검토 목록 — 빈 목록"),
    ("reviews-fail", "user1", "/reviews", "fail_reviews", "검토 목록 — 조회 실패(500 주입)"),
    ("runs", "user1", "/runs", None, "실행 기록"),
    ("runs-empty", "user3", "/runs", None, "실행 기록 — 빈 목록"),
    ("automations", "user1", "/automations", None, "자동화 목록"),
    ("automation-confirm", "user1", "/automations", "open_delete_confirm", "자동화 삭제 확인창"),
    ("bundle", "user1", f"/bundle?files={U1['basic']},{U1['many']}", None, "파일별 묶기(파일 2개 선택)"),
    ("bundle-multisheet", "user1", f"/bundle?files={U1['multi']}", None, "파일별 묶기 — 여러 시트 파일(시트 선택 필요)"),
    ("analysis-new", "user1", "/analysis", None, "데이터 분석 — 새 분석"),
    ("analysis-loading", "user1", f"/analysis?id={IDS['user1_analysis_many']}", "hold_analysis", "분석 결과 — 불러오는 중"),
    ("analysis-detail", "user1", f"/analysis?id={IDS['user1_analysis_many']}", None, "분석 결과 — 여러 쪽(120그룹)"),
    ("analysis-bins", "user1", f"/analysis?id={IDS['user1_analysis']}", None, "분석 결과 — 구간 분류"),
    ("analysis-other", "user2", f"/analysis?id={IDS['user1_analysis']}", None, "분석 결과 — 다른 사용자 자료(차단)"),
    ("analysis-readonly", "owner", f"/analysis?id={IDS['user1_analysis']}", None, "분석 결과 — 최고 관리자 조회 전용"),
    ("account", "user1", "/account", None, "내 계정"),
    ("account-invalid", "user1", "/account", "bad_password", "내 계정 — 잘못된 비밀번호 입력"),
    ("dictionary", "owner", "/dictionary", None, "사전 관리"),
    ("promotions", "owner", "/promotions", None, "승격 심사"),
    ("admin", "owner", "/moajwoAdmin", None, "관리자 개요"),
    ("admin-users", "owner", "/moajwoAdmin/users", None, "사용자·가입 신청(승인 대기 포함)"),
    ("admin-audit", "owner", "/moajwoAdmin/audit", None, "관리 이력"),
    ("settings", "owner", "/settings", None, "설정"),
    ("forbidden-route", "user1", "/settings", None, "일반 사용자가 관리자 화면 주소로 들어감"),
]
HELD: list = []


def prep(pg: Page, name: str | None) -> None:
    if name is None:
        return
    if name == "bad_login":
        pg.get_by_label("아이디").fill(USERS["user1"])
        pg.get_by_label("비밀번호", exact=True).fill("틀린-비밀번호-진단")
        pg.locator(".login-card button[type=submit]").click()
        pg.locator(".login-card [role=alert], .login-card .upload-error").first.wait_for(timeout=30000)
    elif name == "open_signup":
        pg.get_by_role("button", name=re.compile("회원가입")).first.click()
        pg.get_by_label("비밀번호 확인").wait_for(timeout=30000)
    elif name == "open_delete_confirm":
        pg.get_by_text("월간 인구 취합 (진단)").first.click()
        pg.get_by_role("button", name="자동화 삭제").click()
        pg.get_by_role("alertdialog").wait_for(timeout=30000)
    elif name == "bad_password":
        # 현재 비밀번호를 틀리게 넣어 서버가 거절하게 한다 (비밀번호는 바뀌지 않음)
        pg.get_by_label("현재 비밀번호").fill("틀린-현재-비밀번호")
        pg.get_by_label("새 비밀번호").fill("Diag-new-password-1")
        pg.get_by_role("button", name="비밀번호 변경").click()
        pg.wait_for_timeout(1500)


def run_state(pg: Page, vp: str, sid: str, who, path: str, action, desc: str) -> None:
    rec = {"id": sid, "viewport": vp, "user": who, "path": path, "desc": desc}
    try:
        if action == "fail_reviews":
            pg.route("**/api/reviews?*", lambda r: r.fulfill(status=500, content_type="application/json",
                                                             body='{"detail":"진단용 서버 오류 주입"}'))  # fmt: skip
        if action == "hold_analysis":
            pg.route("**/api/analysis/runs/*", lambda r: HELD.append(r))
        pg.goto(URL + path)
        if action == "hold_analysis":
            pg.wait_for_timeout(1500)
        elif action in (None, "fail_reviews"):
            settle(pg)
        else:
            settle(pg)
            prep(pg, action)
        html = pg.content()
        rec["title"] = pg.title()
        rec["lang"] = pg.evaluate("document.documentElement.lang")
        rec["h1"] = pg.locator("h1:visible").all_inner_texts()[:3]
        rec["dup_ids"] = pg.evaluate("(() => { const c = {}; document.querySelectorAll('[id]').forEach(e => c[e.id] = (c[e.id] || 0) + 1);"
                                     " return Object.entries(c).filter(([, n]) => n > 1).map(([k, n]) => k + ':' + n) })()")  # fmt: skip
        msgs = nu(html.encode("utf-8"), "text/html; charset=utf-8")
        rec["nu"] = None if msgs is None else [{"type": m["type"], "sub": m.get("subType"), "msg": m["message"][:220],
                                                 "extract": (m.get("extract") or "")[:120]} for m in msgs]  # fmt: skip
        pg.add_script_tag(path=str(AXE_JS))
        ax = pg.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations', 'incomplete']})", AXE_TAGS)
        bp = pg.evaluate("() => axe.run(document, {runOnly: {type: 'tag', values: ['best-practice']}, resultTypes: ['violations']})")
        pick = lambda xs: [{"id": v["id"], "impact": v["impact"], "tags": [t for t in v["tags"] if t.startswith(("wcag", "best"))],  # noqa: E731
                            "help": v["help"], "nodes": len(v["nodes"]), "targets": [n["target"] for n in v["nodes"][:6]]} for v in xs]  # fmt: skip
        rec["axe_violations"] = pick(ax["violations"])
        rec["axe_incomplete"] = pick(ax["incomplete"])
        rec["axe_best_practice"] = pick(bp["violations"])
        rec["axe_version"] = ax["testEngine"]["version"]
        pg.screenshot(path=str(OUT / f"{vp}-{sid}.png"), full_page=True)
        (OUT / "dom").mkdir(exist_ok=True)
        (OUT / "dom" / f"{vp}-{sid}.html").write_text(html, encoding="utf-8")
        rec["status"] = "검사함"
    except Exception as e:  # noqa: BLE001 — 도달 실패를 기록 (통과로 세지 않음)
        rec["status"] = "검사 실패"
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        for r in HELD:
            try:
                r.continue_()
            except Exception:  # noqa: BLE001
                pass
        HELD.clear()
        pg.unroute_all(behavior="ignoreErrors")
        pg.route("**/*", guard)
    RESULTS.append(rec)
    nerr = "나중" if rec.get("nu") is None and rec["status"] == "검사함" else sum(1 for m in rec.get("nu") or [] if m["type"] == "error")
    print(f"{vp:7} {sid:22} {rec['status']} Nu오류 {nerr} axe위반 {len(rec.get('axe_violations', []))} 확인필요 {len(rec.get('axe_incomplete', []))} "
          f"{rec.get('error', '')[:120]}", flush=True)  # fmt: skip


def server_documents() -> dict:
    """서버가 돌려준 문서를 받은 그대로 저장하고 (Nu가 켜져 있으면) 검사한다."""
    out = {}
    (OUT / "server").mkdir(exist_ok=True)
    with SITE.open(check_url(URL + "/", URL), timeout=30) as r:
        html = r.read()
    (OUT / "server" / "index.html").write_bytes(html)
    out["index.html"] = nu(html, "text/html; charset=utf-8")
    for asset in re.findall(rb'(?:href|src)="(/[^"]+\.(?:css|svg))"', html):
        a = asset.decode()
        with SITE.open(check_url(URL + a, URL), timeout=30) as r:
            body = r.read()
        (OUT / "server" / a.strip("/").replace("/", "__")).write_bytes(body)
        out[a] = nu(body, "text/css; charset=utf-8" if a.endswith(".css") else "image/svg+xml")
    return out


def main() -> None:
    docs = server_documents()
    (OUT / "nu-server-documents.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in docs.items():
        print("서버 문서", k, {t: sum(1 for m in v if m["type"] == t) for t in ("error", "info", "non-document-error")})
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for vp, size in VIEWPORTS.items():
            by_user: dict = {}
            only = set(filter(None, os.environ.get("ONLY_STATES", "").split(",")))
            for sid, who, path, action, desc in STATES:
                if only and sid not in only:
                    continue
                key = who or "anon"
                if key not in by_user:
                    cx = b.new_context(viewport=size, locale="ko-KR")
                    pg = cx.new_page()
                    pg.route("**/*", guard)
                    if who:
                        login(pg, who)
                    by_user[key] = (cx, pg)
                run_state(by_user[key][1], vp, sid, who, path, action, desc)
            for cx, _ in by_user.values():
                cx.close()
        b.close()
    summary = {"seconds": round(time.time() - t0), "states": len(RESULTS), "inspected": sum(r["status"] == "검사함" for r in RESULTS),
               "failed": [f"{r['viewport']}/{r['id']}" for r in RESULTS if r["status"] != "검사함"], "external_blocked": EXTERNAL}  # fmt: skip
    (OUT / "a11y-results.json").write_text(json.dumps({"summary": summary, "results": RESULTS}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
