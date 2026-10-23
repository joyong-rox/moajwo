"""5장 두 차례 기능 검증 — 기존 진단 스크립트에 없던 확인 (격리 서버, 합성 자료, 이 실행이 만든 계정).

U. 화면 업로드 (계정 A = diag-user1): CSV(CP949)·XLS(옛 Excel)·같은 이름 파일 두 번·업로드 실패 뒤 다시 올리기
   (연결 끊김, 지원하지 않는 형식, 읽지 못하는 xlsx)
C. 공통 항목 검토 20/21개 (A): 화면에서 21번째를 고를 수 없는지와 그 이유, API로 21개를 보내면 거절되는지
D. 중복 제출 (A): 검토 완료·통합 결과 만들기·엑셀 만들기(묶기)·통계 분석 실행을 빠르게 두 번 눌러 기록이 하나만 생기는지
M. 관리자 변경 (최고 관리자, 이 실행이 만든 계정만): 가입 승인·반려, 이용 중지·재개(남아 있던 세션 포함), 역할 바꾸기 시도,
   기준 시간 설정(되돌림), 승격 승인 → 학습 이력 없는 계정의 제안(되돌림), 용어사전 별칭(되돌림)
B. 차단 요청 전후 비교 (A·B·대기 계정·로그인 없음): 표별 건수·기존 행 내용 해시·결과 파일 수.
   그 뒤 B의 정상 확정으로 생기는 학습(새 행)과 기존 행 변경을 나눠 적는다.
V. 이번에 바뀐 화면 상태 (A): DB 연결 불가 안내(로그인 화면 — 상태 응답만 브라우저에서 503으로 바꿈)·업로드 한도 안내·
   공통 검토 '다시 열기 필요'·업로드 중 '지금은 고를 수 없음'을 320/390/768/1366px에서 가로 넘침·axe로,
   실제 브라우저 확대 200%(1280×800 창, 확장의 chrome.tabs.setZoom)에서 가로 넘침으로 잰다.
K. 키보드만으로 (A): 페이지 처음부터 Tab으로 메뉴 '검토 목록' → Enter → 검토 링크 → Enter → Esc, 위치·초점을 적는다.
기대와 실제를 함께 적고 판정(ok)은 기대 조건과의 비교일 뿐이다. 취소 기능이 없는 곳은 '기능 없음'으로 적고 통과로 세지 않는다.
사용법: python round5_check.py <출력 폴더> <차례 1|2> [부분, 예: U,C,D,M,B,V,K]
환경: ISO_DATA(격리 데이터 폴더 — 결과 파일 수, 없으면 건너뜀), R5_AXE(axe.min.js), R5_ZOOMEXT(확대 확장 폴더).
DB 조회는 diag_common.db (local_diag.py가 격리 DB에 맞춘다).
"""

import csv
import hashlib
import io
import json
import os
import re
import secrets
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

import diag_common
from diag_common import ASSET_JS, EV, URL, USERS, XLSX, Api, creds, run_meta, xlsx
from diag_scope import browser_proxy, route_guard

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ROUND = sys.argv[2]
PARTS = set((sys.argv[3] if len(sys.argv) > 3 else "U,C,D,M,B,V,K").split(","))
DATA = Path(os.environ["ISO_DATA"]) if os.environ.get("ISO_DATA") else None
ACCOUNTS = EV / "round5-accounts.json"  # 이 실행이 만든 계정 (Git 밖 증거 폴더)
RES: list[dict] = []
BLOCKED: list[str] = []
META: list[dict] = []
CSV_TYPE = "text/csv"
XLS_TYPE = "application/vnd.ms-excel"
# 확인 대상 표 (건수·내용 해시) — 실행 기록·결과·학습·승격·검토·계정
TABLES = ["source_files", "mapping_decisions", "learned_aliases", "mapping_approvals", "promotion_candidates", "standard_terms",
          "review_sessions", "review_items", "runs", "run_changes", "analysis_runs", "mapping_analyses", "automations",
          "glossary_entries", "glossary_aliases", "app_settings", "users", "validation_runs"]  # fmt: skip


def rec(part: str, step: str, ok: bool | None, detail="") -> None:
    """ok: True/False = 기대 조건과 비교, None = 측정만 (판정 없음)."""
    shown = detail if isinstance(detail, dict | list) else str(detail)[:500]
    RES.append({"round": ROUND, "part": part, "step": step, "ok": ok, "detail": shown})
    mark = "측정" if ok is None else ("통과" if ok else "실패")
    print(f"{mark} | {part} {step} | {str(detail)[:220]}", flush=True)


def db(sql: str) -> list:
    return diag_common.db(sql)


# ---------------------------------------------------------------- 계정
def accounts() -> dict:
    return json.loads(ACCOUNTS.read_text(encoding="utf-8")) if ACCOUNTS.exists() else {}


def save_accounts(acc: dict) -> None:
    ACCOUNTS.write_text(json.dumps(acc), encoding="utf-8")


def login(username: str, password: str) -> tuple[int, Api]:
    api = Api()
    return api.call("POST", "/auth/login", {"username": username, "password": password})[0], api


def user_id(owner: Api, username: str) -> int:
    return next(u["id"] for u in owner.call("GET", "/users")[1] if u["username"] == username)


def signup(name: str, display: str) -> tuple[int, str]:
    pw = "R5-" + secrets.token_urlsafe(12) + "7z"
    s, _, _ = Api().call("POST", "/auth/signup", {"username": name, "display_name": display, "password": pw, "password_confirm": pw})
    return s, pw


def account_b(owner: Api) -> tuple[str, str]:
    """계정 B: 1차에 정상 절차(가입 신청 → 최고 관리자 승인)로 만들고 2차에 다시 쓴다. 학습 이력 없음."""
    acc = accounts()
    if "b" not in acc:
        name = f"r5-b-{secrets.token_hex(3)}"
        s, pw = signup(name, "5장 확인 B")
        if s != 201:
            raise RuntimeError(f"B 가입 신청 실패: {s}")
        owner.call("POST", f"/users/{user_id(owner, name)}/approve", {"reason": "5장 확인용 합성 계정"})
        acc["b"] = {"username": name, "password": pw}
        save_accounts(acc)
    return acc["b"]["username"], acc["b"]["password"]


# ---------------------------------------------------------------- 자료
def pop_rows(tag: str, extra: int = 0) -> list[list]:
    """제목(합성 표시) + 머리글 + 데이터 3행 (extra=1이면 4행). 표시 글자는 제목에만 둔다 — 데이터 행으로 세지 않게."""
    rows = [[f"주민등록 인구 현황 2026년 9월 (단위: 명) 합성 {tag}"], ["시도", "인구수"]]  # CP949로 저장되는 글자만 (줄표 등 제외)
    rows += [[n, 500 + i] for i, n in enumerate(["강원특별자치도", "충청북도", "전북특별자치도", "제주특별자치도"][: 3 + min(extra, 1)])]
    return rows


def csv_cp949(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue().encode("cp949")


def xls_bytes(rows: list[list]) -> bytes:
    import xlwt  # 시험 자료를 만드는 데만 씀 (앱 의존성 아님)

    wb = xlwt.Workbook()
    ws = wb.add_sheet("시트1")
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is not None:
                ws.write(r, c, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def confirm_pop(api: Api, fid: int) -> int:
    cols = {c["name"]: c["column_id"] for c in api.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]["columns"]}
    body = {"decisions": [{"sheet_id": "s0", "column_id": cols["시도"], "field_key": "sido"},
                          {"sheet_id": "s0", "column_id": cols["인구수"], "field_key": "population"}]}  # fmt: skip
    return api.call("PUT", f"/files/{fid}/mapping/decisions", body)[0]


def counts() -> dict:
    out = {t: db(f"select count(*) from {t}")[0][0] for t in TABLES}
    if DATA is not None:
        out["files:outputs"] = sum(1 for p in (DATA / "outputs").rglob("*") if p.is_file()) if (DATA / "outputs").exists() else 0
        out["files:uploads"] = sum(1 for p in (DATA / "uploads").rglob("*") if p.is_file()) if (DATA / "uploads").exists() else 0
    return out


KEY = {"app_settings": "key"}  # 나머지 표의 기본 키는 id


def digests(upto: dict[str, int] | None = None) -> dict[str, str]:
    """표별 내용 해시 (기본 키 순). upto: 표별 최대 id — 그 이하(기존 행)만 해시한다 (id가 없는 표는 전체)."""
    out = {}
    for t in TABLES:
        key = KEY.get(t, "id")
        cond = f" where id <= {int(upto[t])}" if upto and t in upto else ""
        rows = db(f"select * from {t}{cond} order by {key}")
        out[t] = hashlib.sha256(json.dumps(rows, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
    return out


def max_ids() -> dict[str, int]:
    return {t: db(f"select coalesce(max(id), 0) from {t}")[0][0] for t in TABLES if t not in KEY}


def delta(before: dict, after: dict) -> dict:
    return {k: after[k] - before[k] for k in before if after.get(k) != before[k]}


# ---------------------------------------------------------------- 브라우저
def browser_login(pg: Page, username: str, password: str) -> None:
    pg.goto(URL + "/")
    pg.get_by_label("아이디").fill(username)
    pg.get_by_label("비밀번호", exact=True).fill(password)
    pg.locator(".login-card button[type=submit]").click()
    pg.wait_for_selector(".shell", timeout=60000)
    pg.wait_for_load_state("networkidle")


CHOOSER_RETRIES: list[str] = []  # 선택 창 요청이 첫 Enter에 오지 않아 다시 누른 경우 (결과에 그대로 적는다)


def choose(pg: Page, files: list[dict]) -> None:
    """홈 업로드 영역의 '파일 선택' 버튼을 키보드로 열고 파일을 고른다 (실제 OS 대화상자는 열지 않음).
    화면이 막 다시 그려질 때 초점이 풀려 Enter가 빠질 수 있어, 10초 안에 요청이 없으면 초점을 다시 주고 한 번 더 누른다."""
    drop = pg.locator(".wk-home .upload-drop .wk-drop-button, .wk-home .upload-drop[role=button]").first
    drop.wait_for(state="visible", timeout=30000)
    for attempt in (1, 2):
        drop.focus()
        try:
            with pg.expect_file_chooser(timeout=10000) as fc:
                pg.keyboard.press("Enter")
            break
        except Exception:  # noqa: BLE001 — 두 번째도 오지 않으면 그대로 오류
            if attempt == 2:
                raise
            CHOOSER_RETRIES.append(f"{pg.viewport_size['width']}px {files[0]['name']}")
    fc.value.set_files(files=files)


def wait_upload(pg: Page) -> None:
    pg.wait_for_timeout(300)
    pg.wait_for_function("() => !document.querySelector('.upload-drop.is-busy')", timeout=120000)
    pg.wait_for_timeout(600)


def cards(pg: Page) -> list[str]:
    return [x.inner_text().replace("\n", " ")[:200] for x in pg.locator(".wk-upload .upload-results > li").all()]


def alert_text(pg: Page) -> str:
    box = pg.locator(".wk-upload [role=alert]")
    return box.first.inner_text().replace("\n", " ")[:300] if box.count() else ""


def part_upload(b, a_pw: str) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    browser_login(pg, USERS["user1"], a_pw)
    META.append(run_meta("round5_check.py", f"r{ROUND}-U", pg.evaluate(ASSET_JS)))
    tag = f"R{ROUND}"
    me = db(f"select id from users where username = '{USERS['user1']}'")[0][0]
    files_of = lambda: db(f"select count(*) from source_files where uploaded_by = {me}")[0][0]  # noqa: E731

    # U1 CSV (CP949) · U2 XLS — 화면에서 키보드로 파일 선택
    for step, name, ctype, data, enc in (
        ("U1 CSV(CP949로 저장) 화면 업로드", f"r5_인구_{tag}.csv", CSV_TYPE, csv_cp949(pop_rows(tag)), ("EUC-KR", "CP949")),
        ("U2 XLS(옛 Excel) 화면 업로드", f"r5_인구_{tag}.xls", XLS_TYPE, xls_bytes(pop_rows(tag)), None),
    ):
        choose(pg, [{"name": name, "mimeType": ctype, "buffer": data}])
        wait_upload(pg)
        card = next((c for c in cards(pg) if name in c), "")
        # 한글 CSV 인코딩은 서버가 판별한 이름으로 보인다 (CP949로 저장한 한글 범위 글자는 EUC-KR로 판별될 수 있음)
        ok = "올림 완료" in card and "데이터 3행" in card and (enc is None or any(e in card for e in enc))
        rec("U", step, ok, card)

    # U3 같은 이름 두 번 (내용 다름) → 서로 다른 번호, 홈 목록에서 번호로 구분
    same = f"r5_같은이름_{tag}.xlsx"
    ids = []
    for extra in (0, 1):
        choose(pg, [{"name": same, "mimeType": XLSX, "buffer": xlsx({"시트1": pop_rows(tag, extra)})}])
        wait_upload(pg)
        hit = [c for c in cards(pg) if same in c]
        ids += [int(m.group(1)) for m in [re.search(r"#(\d+)", hit[0])] if m] if hit else []
    pg.goto(URL + "/")
    pg.wait_for_load_state("networkidle")
    pg.get_by_label("검사할 파일 검색").fill(f"r5_같은이름_{tag}")
    pg.wait_for_timeout(800)
    found = {i: pg.get_by_label(re.compile(rf"#{i} .*검증 대상")).count() for i in ids}  # 홈 목록의 파일 체크박스 (번호로 구분)
    shown = pg.evaluate("(n) => [...document.querySelectorAll('input[type=checkbox]')].map(e => e.getAttribute('aria-label') || e.closest('label')?.innerText || '').filter(t => t.includes(n))", same)
    rec("U", "U3 같은 이름 두 번 → 다른 번호·목록에서 구분", len(set(ids)) == 2 and all(v == 1 for v in found.values()), {"ids": ids, "home_found": found, "home_labels": shown})

    # U4 연결 끊김 → 오류 안내 → 같은 파일 다시 올리기
    name = f"r5_재시도_{tag}.xlsx"
    data = xlsx({"시트1": pop_rows(tag)})
    before = files_of()
    state = {"aborted": 0}

    def cut_once(route) -> None:
        if route.request.method == "POST" and not state["aborted"]:
            state["aborted"] += 1
            route.abort("connectionreset")
        else:
            route.fallback()

    pg.route("**/api/files", cut_once)
    choose(pg, [{"name": name, "mimeType": XLSX, "buffer": data}])
    wait_upload(pg)
    first = {"alert": alert_text(pg), "records_added": files_of() - before}
    choose(pg, [{"name": name, "mimeType": XLSX, "buffer": data}])
    wait_upload(pg)
    pg.unroute("**/api/files", cut_once)
    card = next((c for c in cards(pg) if name in c), "")
    rec("U", "U4 연결 끊김 → 안내 → 다시 올리기", bool(first["alert"]) and first["records_added"] == 0 and "올림 완료" in card,
        {"first": first, "retry_card": card, "records_added_total": files_of() - before})  # fmt: skip

    # U5 지원하지 않는 형식 → 요청 없이 안내 → 맞는 형식으로 다시
    sent = []
    pg.on("request", lambda r: sent.append(r.url) if r.method == "POST" and r.url.endswith("/api/files") else None)
    choose(pg, [{"name": f"r5_메모_{tag}.txt", "mimeType": "text/plain", "buffer": "합성 메모".encode()}])
    pg.wait_for_timeout(800)
    msg, n = alert_text(pg), len(sent)
    choose(pg, [{"name": f"r5_메모대신_{tag}.csv", "mimeType": CSV_TYPE, "buffer": csv_cp949(pop_rows(tag))}])
    wait_upload(pg)
    card = next((c for c in cards(pg) if f"r5_메모대신_{tag}.csv" in c), "")
    rec("U", "U5 지원하지 않는 형식 → 요청 없이 안내 → 다시", "지원하지 않는 형식" in msg and n == 0 and "올림 완료" in card,
        {"alert": msg, "requests_for_txt": n, "retry_card": card})  # fmt: skip

    # U6 읽지 못하는 xlsx(깨진 파일) → 카드에 실패 이유 → 정상 파일로 다시 (실패 기록은 '읽지 못한 파일'로 남는 기존 동작)
    bad = f"r5_깨진파일_{tag}.xlsx"
    before = files_of()
    choose(pg, [{"name": bad, "mimeType": XLSX, "buffer": b"PK\x03\x04 not really a workbook"}])
    wait_upload(pg)
    bad_card = next((c for c in cards(pg) if bad in c), "")
    choose(pg, [{"name": f"r5_정상파일_{tag}.xlsx", "mimeType": XLSX, "buffer": xlsx({"시트1": pop_rows(tag)})}])
    wait_upload(pg)
    good = next((c for c in cards(pg) if f"r5_정상파일_{tag}.xlsx" in c), "")
    rec("U", "U6 읽지 못하는 파일 → 실패 이유 → 다시", "실패" in bad_card and "올림 완료" in good,
        {"bad_card": bad_card, "good_card": good, "records_added": files_of() - before})  # fmt: skip
    pg.screenshot(path=str(OUT / f"r{ROUND}-U-upload.png"), full_page=True)
    cx.close()


# ---------------------------------------------------------------- C. 공통 검토 20/21
def part_common(b, a: Api, a_pw: str) -> None:
    tag = f"R{ROUND}"
    ids = []
    for start, n in ((0, 20), (20, 1)):  # 한 요청 최대 20개
        parts = []
        for i in range(start, start + n):
            parts.append((f"r5_공통{tag}_{i + 1:02d}.csv", csv_cp949(pop_rows(f"{tag}-{i}"))))
        boundary = secrets.token_hex(8)
        body = b"".join(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{nm}"\r\nContent-Type: text/csv\r\n\r\n').encode() + d + b"\r\n"
            for nm, d in parts
        ) + f"--{boundary}--\r\n".encode()  # fmt: skip
        s, res, _ = a.call("POST", "/files", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        ids += [r["id"] for r in res if r.get("id") and not r.get("error")] if s == 200 else []
    s21, b21, _ = a.call("POST", "/mapping/common", {"file_ids": ids[:21]})
    s20, b20, _ = a.call("POST", "/mapping/common", {"file_ids": ids[:20]})
    rec("C", "C1 API 21개 → 거절, 20개 → 열림", s21 == 422 and s20 == 200,
        {"files": len(ids), "21": s21, "21_detail": str(b21)[:160], "20": s20, "20_members": len((b20 or {}).get("members", [])) if s20 == 200 else None})  # fmt: skip

    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    browser_login(pg, USERS["user1"], a_pw)
    pg.goto(URL + "/mapping/common")
    pg.wait_for_load_state("networkidle")
    pg.get_by_label("공통 검토할 파일 검색").fill(f"r5_공통{tag}_")
    pg.wait_for_timeout(800)
    boxes = pg.locator(".mw-file-list label.wk-filepick input[type=checkbox]")
    total = boxes.count()
    for i in range(min(total, 20)):
        boxes.nth(i).check()
    pg.wait_for_timeout(400)
    st = {
        "listed": total,
        "checked": pg.locator(".mw-file-list input[type=checkbox]:checked").count(),
        "disabled_unchecked": pg.locator(".mw-file-list input[type=checkbox]:not(:checked):disabled").count(),
        "count_text": pg.locator("#wk-common-files .wk-pickcount").first.inner_text().replace("\n", " "),
        "legend": pg.locator("#wk-common-files legend").first.inner_text(),
    }
    if total > 20:
        try:
            boxes.nth(20).click(timeout=2000)
        except Exception as e:  # noqa: BLE001 — 비활성이면 누를 수 없음 (기대)
            st["click_21st"] = type(e).__name__
        st["checked_after_21st"] = pg.locator(".mw-file-list input[type=checkbox]:checked").count()
    rec("C", "C2 화면 20개 고른 뒤 21번째 고를 수 없음·이유 표시",
        st["checked"] == 20 and st.get("checked_after_21st", 20) == 20 and st["disabled_unchecked"] >= 1 and "최대 20" in st["count_text"], st)  # fmt: skip
    pg.screenshot(path=str(OUT / f"r{ROUND}-C-common20.png"), full_page=True)
    cx.close()


# ---------------------------------------------------------------- D. 중복 제출
def part_duplicate(b, a: Api, a_pw: str) -> None:
    tag = f"R{ROUND}"
    me = db(f"select id from users where username = '{USERS['user1']}'")[0][0]
    runs_of = lambda: db(f"select count(*) from runs where user_id = {me}")[0][0]  # noqa: E731
    reviews_done = lambda: db(f"select count(*) from review_sessions where created_by = {me} and status = 'ready'")[0][0]  # noqa: E731 ('ready' = 검토 완료)
    analyses_of = lambda: db(f"select count(*) from analysis_runs where created_by = {me}")[0][0]  # noqa: E731
    fids = []
    for i in range(2):
        s, res, _ = a.upload(f"r5_중복_{tag}_{i + 1}.xlsx", xlsx({"시트1": pop_rows(f"{tag}-d{i}")}))
        fids.append(res[0]["id"])
        confirm_pop(a, fids[-1])

    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR", accept_downloads=True)
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    browser_login(pg, USERS["user1"], a_pw)
    # D1 검토 완료·통합 결과 만들기 두 번 누르기 (취합 방식은 '하나의 통합표' — flow_check와 같은 조작)
    done_before, runs_before = reviews_done(), runs_of()
    statuses: list[str] = []  # '완료 200'·'통합 201'처럼 요청 종류와 응답 — 두 번 누름이 요청 두 개가 되었는지 본다
    kinds = {"complete": "완료", "integrate": "통합"}
    pg.on("response", lambda r: statuses.append(f"{kinds[m.group(1)]} {r.status}") if (m := re.search(r"/api/reviews/\d+/(complete|integrate)$", r.url)) else None)
    info: dict = {}
    try:
        pg.goto(f"{URL}/?files={fids[0]}")
        pg.wait_for_load_state("networkidle")
        table = pg.locator("input[name=wk-result-mode][value=table]")
        pg.locator(".wk-mode-card", has=table).locator(".wk-mode-text strong").click()
        pg.get_by_role("button", name=re.compile("사전 검토 열기")).click()
        panel = pg.locator("section[aria-label='통합 전 사전 검토']")
        panel.locator(".review-score-value").wait_for(timeout=120000)
        pg.wait_for_timeout(800)
        for _ in range(10):
            btn = panel.get_by_role("button", name="승인", exact=True)
            if not btn.count() or not btn.first.is_enabled():
                break
            btn.first.click()
            pg.wait_for_timeout(700)
        done = panel.get_by_role("button", name=re.compile("^검토 완료"))
        info["complete_enabled"] = bool(done.count() and done.first.is_enabled())
        if info["complete_enabled"]:
            done.first.dblclick()
            pg.wait_for_timeout(2000)
        make = pg.get_by_role("button", name=re.compile("통합 결과 만들기"))
        make.first.wait_for(timeout=60000)
        make.first.dblclick()
        pg.locator("a", has_text=re.compile(r"통합결과_.*\.xlsx")).first.wait_for(timeout=120000)
        pg.wait_for_timeout(1500)
    except Exception as e:  # noqa: BLE001 — 기록하고 D2·D3은 계속 잰다
        info["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        pg.screenshot(path=str(OUT / f"r{ROUND}-D1-error.png"), full_page=True)
    # 기대: 완료 요청 성공 1번(두 번째 클릭은 버튼이 막혀 요청이 없거나, 가더라도 409) · 통합 요청 성공 1번 (PR #39)
    rec("D", "D1 검토 완료·통합 결과 만들기 두 번 누름 → 완료 1·실행 1, 성공한 완료 요청 1",
        "error" not in info and reviews_done() - done_before == 1 and runs_of() - runs_before == 1 and statuses.count("완료 200") == 1,
        {**info, "completed_reviews_added": reviews_done() - done_before, "runs_added": runs_of() - runs_before, "responses": statuses})  # fmt: skip

    # D2 묶기 '엑셀 만들기' 두 번
    runs_before = runs_of()
    pg.goto(f"{URL}/bundle?files={fids[0]},{fids[1]}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    make = pg.get_by_role("button", name=re.compile("엑셀 만들기"))
    pg.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.innerText.startsWith('엑셀 만들기') && !b.disabled)", timeout=60000)
    make.first.dblclick()
    pg.get_by_role("link", name="내려받기").first.wait_for(timeout=120000)
    pg.wait_for_timeout(1500)
    rec("D", "D2 묶기 엑셀 만들기 두 번 누름 → 실행 1", runs_of() - runs_before == 1, {"runs_added": runs_of() - runs_before})

    # D3 통계 분석 실행 두 번
    # D3 두 파일에 같은 대상(지역)이 있으면 실행이 막히고(두 번 더하지 않음), 한 파일만 남기면 실행 — 그 실행을 두 번 누른다
    before = analyses_of()
    pg.goto(URL + "/analysis")
    pg.get_by_label("분석할 항목").select_option("population")
    for fid in fids:
        pg.locator("label.an-file", has_text=f"#{fid} ").locator("input").check()
    pg.get_by_role("button", name="대상 확인 (계산 안 함)").click()
    pg.locator("fieldset.an-step legend", has_text="2. 대상").wait_for(timeout=60000)
    run_btn = pg.get_by_role("button", name="통계 분석 실행")
    overlap = {"enabled": run_btn.is_enabled(), "notes": [x.inner_text()[:160] for x in pg.locator("section.an .mapping-warn, section.an .upload-error").all()][:3]}
    pg.locator("label.an-file", has_text=f"#{fids[1]} ").locator("input").uncheck()
    # 고른 파일이 바뀌면 버튼 이름이 '대상 확인'으로 돌아간다 (같은 파일로 다시 볼 때만 '다시 확인')
    pg.get_by_role("button", name=re.compile(r"^(대상|다시) 확인 \(계산 안 함\)$")).click()
    pg.wait_for_timeout(2500)
    enabled = run_btn.is_enabled()
    if enabled:
        run_btn.dblclick()
        pg.locator("section.an h2", has_text="분석 결과 #").wait_for(timeout=120000)
        pg.wait_for_timeout(1500)
    rec("D", "D3 같은 대상 두 파일은 실행 막힘 → 한 파일로 통계 분석 실행 두 번 누름 → 결과 1",
        overlap["enabled"] is False and enabled and analyses_of() - before == 1,
        {"two_files_same_targets": overlap, "one_file_run_enabled": enabled, "analysis_runs_added": analyses_of() - before})  # fmt: skip
    rec("D", "D4 진행 중 취소", None, "업로드·검토·통합·묶기·분석에 취소 기능 없음 — 기능 없음으로 기록 (통과로 세지 않음)")
    cx.close()


# ---------------------------------------------------------------- M. 관리자 변경
def part_admin(owner: Api, a: Api) -> None:
    tag = f"r{ROUND}{secrets.token_hex(2)}"
    # M1 가입 승인 / M2 반려
    s, pw = signup(f"r5-p-{tag}", "5장 승인 대기")
    before = login(f"r5-p-{tag}", pw)[0]
    pid = user_id(owner, f"r5-p-{tag}")
    s_ap = owner.call("POST", f"/users/{pid}/approve", {"reason": "5장 확인"})[0]
    after = login(f"r5-p-{tag}", pw)[0]
    dup = owner.call("POST", f"/users/{pid}/approve", {})[0]
    rec("M", "M1 가입 승인 → 로그인 가능, 다시 승인은 거절", (s, before, s_ap, after, dup) == (201, 403, 200, 200, 409),
        {"signup": s, "login_before": before, "approve": s_ap, "login_after": after, "approve_again": dup})  # fmt: skip
    s, pw = signup(f"r5-r-{tag}", "5장 반려 대상")
    rid = user_id(owner, f"r5-r-{tag}")
    s_rj = owner.call("POST", f"/users/{rid}/reject", {"reason": "5장 확인"})[0]
    rec("M", "M2 가입 반려 → 로그인 거절", (s_rj, login(f"r5-r-{tag}", pw)[0]) == (200, 403), {"reject": s_rj})

    # M3 이용 중지 → 남아 있던 세션·새 로그인 거절 → 재개 → 로그인
    s, pw_s = signup(f"r5-s-{tag}", "5장 중지 대상")
    sid = user_id(owner, f"r5-s-{tag}")
    owner.call("POST", f"/users/{sid}/approve", {})
    _, sess = login(f"r5-s-{tag}", pw_s)
    alive = sess.call("GET", "/auth/me")[0]
    s_su = owner.call("POST", f"/users/{sid}/suspend", {"reason": "5장 확인"})[0]
    old_session = sess.call("GET", "/auth/me")[0]
    new_login = login(f"r5-s-{tag}", pw_s)[0]
    s_re = owner.call("POST", f"/users/{sid}/resume", {})[0]
    old_after_resume = sess.call("GET", "/auth/me")[0]
    relogin = login(f"r5-s-{tag}", pw_s)[0]
    a_me = a.call("GET", "/auth/me")[0]
    rec("M", "M3 이용 중지 → 기존 세션·새 로그인 거절 → 재개 뒤 새 로그인만 (다른 계정 영향 없음)",
        (alive, s_su, old_session, new_login, s_re, old_after_resume, relogin, a_me) == (200, 200, 401, 403, 200, 401, 200, 200),
        {"me_before": alive, "suspend": s_su, "old_session": old_session, "login_suspended": new_login, "resume": s_re,
         "old_session_after_resume": old_after_resume, "login_after_resume": relogin, "account_a_me": a_me})  # fmt: skip

    # M4 역할 바꾸기: 기능 없음 — 바꾸려는 요청은 거절되는지만 확인
    a_id = db(f"select id from users where username = '{USERS['user1']}'")[0][0]
    patch_role = owner.call("PATCH", f"/users/{a_id}", {"display_name": "확인", "role": "owner"})[0]
    signup_role = Api().call("POST", "/auth/signup", {"username": f"r5-x-{tag}", "display_name": "x", "password": "Xx-" + secrets.token_urlsafe(10),
                                                      "password_confirm": "-", "role": "owner"})[0]  # fmt: skip
    user_admin = {p: a.call("GET", p)[0] for p in ("/users", "/admin/promotions", "/settings", "/admin/overview")}
    role_now = db(f"select role from users where id = {a_id}")[0][0]
    rec("M", "M4 역할 바꾸기 요청 거절·일반 계정의 관리 API 거절 (역할 변경 기능 없음)",
        patch_role == 422 and signup_role == 422 and all(v == 403 for v in user_admin.values()) and role_now == "user",
        {"patch_role": patch_role, "signup_with_role": signup_role, "user_admin_api": user_admin, "role_after": role_now})  # fmt: skip

    # M5 기준 시간 설정 → 반영 → 되돌림
    s0, cur, _ = owner.call("GET", "/settings")
    prev = cur.get("manual_baseline_minutes") if s0 == 200 else None
    s1, new, _ = owner.call("PUT", "/settings", {"manual_baseline_minutes": 37})
    s2, back, _ = owner.call("PUT", "/settings", {"manual_baseline_minutes": prev})
    rec("M", "M5 기준 시간 설정 37분 → 반영 → 되돌림", s1 == 200 and new.get("manual_baseline_minutes") == 37 and s2 == 200 and back.get("manual_baseline_minutes") == prev,
        {"before": prev, "set": new.get("manual_baseline_minutes") if s1 == 200 else s1, "restored": back.get("manual_baseline_minutes") if s2 == 200 else s2})  # fmt: skip

    # M6 승격: 서로 다른 3명이 같은 열 이름을 같은 항목으로 확정 → 대기열 → 승인 → 학습 이력 없는 새 계정에 제안 → 되돌림
    column = f"합성거주자수{tag}"
    makers = []
    for i in range(3):
        nm = f"r5-m{i}-{tag}"
        _, pwm = signup(nm, f"5장 승격 {i}")
        owner.call("POST", f"/users/{user_id(owner, nm)}/approve", {})
        _, api = login(nm, pwm)
        s, res, _ = api.upload(f"r5_승격_{tag}_{i}.xlsx", xlsx({"시트1": [["시도", column], ["강원특별자치도", 10 + i], ["충청북도", 20 + i]]}))
        fid = res[0]["id"]
        cols = {c["name"]: c["column_id"] for c in api.call("GET", f"/files/{fid}/preview?sheet_id=s0")[1]["columns"]}
        makers.append(api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": [{"sheet_id": "s0", "column_id": cols[column], "field_key": "population"}]})[0])
    pend = owner.call("GET", "/admin/promotions")[1]["items"]
    cand = next((c for c in pend if c["column_name"] == column), None)
    approved = owner.call("POST", f"/admin/promotions/{cand['id']}/approve", {})[0] if cand else None
    nm = f"r5-new-{tag}"
    _, pwn = signup(nm, "5장 학습 없는 계정")
    owner.call("POST", f"/users/{user_id(owner, nm)}/approve", {})
    _, fresh = login(nm, pwn)
    learned = db(f"select count(*) from learned_aliases where user_id = {user_id(owner, nm)}")[0][0]
    s, res, _ = fresh.upload(f"r5_승격확인_{tag}.xlsx", xlsx({"시트1": [["시도", column], ["강원특별자치도", 1], ["충청북도", 2]]}))
    m = fresh.call("POST", f"/files/{res[0]['id']}/mapping?use_llm=false")[1]
    col = next((c for c in m["sheets"][0]["columns"] if c["column"] == column), {})
    terms = owner.call("GET", "/admin/standard-terms")[1]
    term = next((t for t in terms if t.get("column_name") == column or t.get("normalized") == column), None)
    removed = owner.call("DELETE", f"/admin/standard-terms/{term['id']}")[0] if term else None
    rec("M", "M6 승격: 3명 확정 → 대기열 → 승인 → 학습 없는 계정에 표준 제안 → 표준 항목 삭제로 되돌림",
        all(x == 200 for x in makers) and cand is not None and approved == 200 and learned == 0 and col.get("field_key") == "population" and removed == 204,
        {"confirms": makers, "candidate": {k: cand[k] for k in ("approver_count", "field_key", "status")} if cand else None, "approve": approved,
         "fresh_account_learned_aliases": learned, "suggestion": {k: col.get(k) for k in ("field_key", "method", "status", "score")}, "term_removed": removed})  # fmt: skip

    # M7 용어사전 별칭 추가 → 이름 해석 바뀜 → 삭제로 되돌림
    base = a.call("GET", "/glossary/resolve?name=" + "%EA%B0%95%EC%9B%90%ED%8A%B9%EB%B3%84%EC%9E%90%EC%B9%98%EB%8F%84&prefer=region")[1]  # 강원특별자치도
    code = (base.get("ref") or {}).get("code")
    alias = f"합성강원{tag}"
    from urllib.parse import quote

    before = a.call("GET", f"/glossary/resolve?name={quote(alias)}&prefer=region")[1]
    s_add, made, _ = owner.call("POST", "/glossary/aliases", {"alias": alias, "target_code": code, "note": "5장 확인 (되돌림)"})
    during = a.call("GET", f"/glossary/resolve?name={quote(alias)}&prefer=region")[1]
    s_del = owner.call("DELETE", f"/glossary/aliases/{made['id']}")[0] if s_add == 201 else None
    after = a.call("GET", f"/glossary/resolve?name={quote(alias)}&prefer=region")[1]
    rec("M", "M7 용어사전 별칭 추가 → 이름 해석 바뀜 → 삭제로 되돌림",
        s_add == 201 and (during.get("ref") or {}).get("code") == code and s_del == 204 and before.get("status") == after.get("status") != during.get("status"),
        {"code": code, "before": before.get("status"), "during": [during.get("status"), during.get("via")], "after": after.get("status"), "add": s_add, "delete": s_del})  # fmt: skip


# ---------------------------------------------------------------- B. 차단 요청 전후 비교
def part_blocked(owner: Api, a: Api) -> None:
    tag = f"R{ROUND}"
    b_name, b_pw = account_b(owner)
    s, b = login(b_name, b_pw)
    a_files = db(f"select id from source_files where uploaded_by = (select id from users where username = '{USERS['user1']}') order by id desc")
    a_fid = a_files[0][0]
    a_review = db(f"select id from review_sessions where created_by = (select id from users where username = '{USERS['user1']}') order by id desc")
    a_rid = a_review[0][0] if a_review else 0
    # A의 미완료 검토 하나 (완료 전 통합 → 409)
    s_u, res, _ = a.upload(f"r5_미완료_{tag}.xlsx", xlsx({"시트1": pop_rows(f"{tag}-open")}))
    open_fid = res[0]["id"]
    confirm_pop(a, open_fid)
    s_rv, rv, _ = a.call("POST", "/reviews", {"file_ids": [open_fid]})
    open_rid = rv.get("id") if s_rv == 201 else None
    pend_name = f"r5-wait-{tag}-{secrets.token_hex(2)}"
    _, pend_pw = signup(pend_name, "5장 대기")

    before_counts, before_max = counts(), max_ids()
    before_hash = digests()
    anon = Api()
    blocked = {
        "B→A 미리보기": b.call("GET", f"/files/{a_fid}/preview")[0],
        "B→A 매핑 확정": b.call("PUT", f"/files/{a_fid}/mapping/decisions", {"decisions": []})[0],
        "B→A 매핑 분석": b.call("POST", f"/files/{a_fid}/mapping?use_llm=false")[0],
        "B→A 검토 보기": b.call("GET", f"/reviews/{a_rid}")[0],
        "B→A 검토 완료": b.call("POST", f"/reviews/{a_rid}/complete")[0],
        "B→A 통합": b.call("POST", f"/reviews/{a_rid}/integrate")[0],
        "B→A 파일로 검토": b.call("POST", "/reviews", {"file_ids": [a_fid]})[0],
        # 형식이 맞는 요청으로 보낸다 (실행 요청은 확인값이 필요해 형식 오류 422가 먼저 나므로 소유권 판정이 안 됨)
        "B→A 파일로 묶기 구성 확인": b.call("POST", "/bundle/plan", {"file_ids": [a_fid]})[0],
        "B→A 파일로 분석 대상 확인": b.call("POST", "/analysis/preview", {"field_key": "population", "targets": [{"file_id": a_fid}]})[0],
        "A 미완료 검토 통합": a.call("POST", f"/reviews/{open_rid}/integrate")[0] if open_rid else None,
        "A 관리 API(승격 승인)": a.call("POST", "/admin/promotions/1/approve", {})[0],
        "A 설정 변경": a.call("PUT", "/settings", {"manual_baseline_minutes": 1})[0],
        "대기 계정 로그인": login(pend_name, pend_pw)[0],
        "로그인 없이 업로드": anon.upload(f"r5_익명_{tag}.xlsx", xlsx({"시트1": pop_rows(tag)}))[0],
        "로그인 없이 통합": anon.call("POST", f"/reviews/{a_rid}/integrate")[0],
    }
    after_counts, after_hash = counts(), digests()
    rec("B", "B1 차단 요청 응답", all(v in (401, 403, 404, 409, 422) for v in blocked.values() if v is not None), blocked)
    rec("B", "B2 차단 요청 전후 건수·결과 파일 수 같음", delta(before_counts, after_counts) == {}, {"before": before_counts, "delta": delta(before_counts, after_counts)})
    changed = [t for t in TABLES if before_hash[t] != after_hash[t]]
    # 로그인 실패·관리 거절은 계정 이력(account_events)에 남을 수 있다 — 위 TABLES에는 넣지 않았다 (업무 자료만 비교)
    rec("B", "B3 차단 요청 전후 표 내용 해시 같음", not changed, {"changed_tables": changed})

    # B4 B의 정상 확정: 새 행(학습)과 기존 행 변경을 나눠 적는다
    s, res, _ = b.upload(f"r5_B정상_{tag}.xlsx", xlsx({"시트1": pop_rows(f"{tag}-b")}))
    b_fid = res[0]["id"]
    mapped = b.call("POST", f"/files/{b_fid}/mapping?use_llm=false")[1]
    first_cols = {c["column"]: c.get("method") for c in mapped["sheets"][0]["columns"]}
    s_conf = confirm_pop(b, b_fid)
    after2 = counts()
    old_rows = digests(before_max)  # 차단 요청 전에 있던 행(id 기준)만 다시 해시
    old_changed = [t for t in TABLES if before_hash[t] != old_rows[t]]
    rec("B", "B4 B의 정상 확정: 새 행만 늘고 차단 전부터 있던 행 내용은 그대로", s_conf == 200 and not old_changed,
        {"b_first_mapping_methods": first_cols, "new_rows": delta(after_counts, after2), "existing_rows_changed": old_changed})  # fmt: skip


# ---------------------------------------------------------------- V. 바뀐 화면 상태의 폭·확대
WIDTHS = [320, 390, 768, 1366]
AXE = os.environ.get("R5_AXE")
ZOOMEXT = os.environ.get("R5_ZOOMEXT")
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
DB_DOWN = {"detail": "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요. 계정·자료는 그대로입니다."}  # backend main.DB_UNAVAILABLE
LOOK_JS = "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, innerWidth, devicePixelRatio]"


def look(pg: Page, axe: bool) -> dict:
    sw, cw, iw, dpr = pg.evaluate(LOOK_JS)
    item = {"overflow_px": sw - cw, "css_width": iw, "dpr": dpr}
    if axe and AXE:
        pg.add_script_tag(path=AXE)
        res = pg.evaluate("(tags) => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS)
        item["violations"] = [[v["id"], v["impact"], len(v["nodes"])] for v in res["violations"]]
    return item


def show_db_down(pg: Page) -> str:
    pg.route("**/api/auth/status", lambda r: r.fulfill(status=503, content_type="application/json", body=json.dumps(DB_DOWN)))
    pg.goto(URL + "/")
    box = pg.locator(".login-card [role=alert]")
    box.wait_for(timeout=30000)
    pg.unroute("**/api/auth/status")
    return box.inner_text()


def show_home(pg: Page) -> str:
    pg.goto(URL + "/")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_function("() => [...document.querySelectorAll('.upload-drop-hint')].some(e => e.innerText.includes('MiB'))", timeout=30000)
    return pg.locator(".upload-drop-hint", has_text="MiB").first.inner_text()


def show_common_changed(pg: Page, fids: list[int]) -> str:
    pg.goto(URL + "/mapping/common")
    pg.locator("label.mw-file-option").first.wait_for(timeout=60000)
    for fid in fids[:2]:
        pg.locator("label.mw-file-option", has_text=f"#{fid} ").click()
    pg.get_by_role("button", name="공통 검토 열기").click()
    pg.wait_for_function("() => ![...document.querySelectorAll('button')].some(b => b.innerText.includes('모으는 중'))", timeout=60000)
    pg.locator("label.mw-file-option", has_text=f"#{fids[2]} ").click()
    step = pg.locator(".mw-common nav.wk-steps li", has_text="다시 열기 필요").first
    step.wait_for(timeout=15000)
    return step.inner_text().replace("\n", " ")


def show_busy(pg: Page) -> tuple[str, list]:
    held: list = []
    pg.route("**/api/files", lambda r: held.append(r) if r.request.method == "POST" else r.fallback())
    pg.goto(URL + "/")
    pg.wait_for_load_state("networkidle")
    choose(pg, [{"name": f"r5_붙잡기_R{ROUND}.csv", "mimeType": CSV_TYPE, "buffer": csv_cp949(pop_rows("hold"))}])
    why = pg.locator(".wk-upload .wk-why").first
    why.wait_for(timeout=15000)
    return why.inner_text().replace("\n", " "), held


def release(pg: Page, held: list) -> None:
    for r in held:
        r.continue_()
    pg.unroute("**/api/files")
    wait_upload(pg)


def part_views(b, p, a: Api, a_pw: str) -> None:
    fids = []
    for i in range(3):
        s, res, _ = a.upload(f"r5_화면_R{ROUND}_{i + 1}.xlsx", xlsx({"시트1": pop_rows(f"v{i}")}))
        fids.append(res[0]["id"])
    for w in WIDTHS:
        cx = b.new_context(viewport={"width": w, "height": 900}, locale="ko-KR")
        cx.route("**/*", route_guard(URL, BLOCKED))
        pg = cx.new_page()
        text = show_db_down(pg)
        rec("V", f"{w}px DB 연결 불가 안내", None, {"text": text, **look(pg, True)})
        browser_login(pg, USERS["user1"], a_pw)
        rec("V", f"{w}px 업로드 한도 안내", None, {"text": show_home(pg)[:200], **look(pg, True)})
        rec("V", f"{w}px 공통 검토 다시 열기 필요", None, {"text": show_common_changed(pg, fids), **look(pg, True)})
        text, held = show_busy(pg)
        rec("V", f"{w}px 업로드 중 고를 수 없음", None, {"text": text, **look(pg, True)})
        if w in (320, 1366):
            pg.screenshot(path=str(OUT / f"r{ROUND}-V-busy-{w}.png"), full_page=True)
        release(pg, held)
        cx.close()
    if not ZOOMEXT:
        rec("V", "실제 200% 확대", False, "R5_ZOOMEXT 없음 — 재지 않음")
        return
    import tempfile

    cx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="msedge", headless=True, viewport={"width": 1280, "height": 800}, locale="ko-KR",
                                              proxy=browser_proxy(URL), args=[f"--disable-extensions-except={ZOOMEXT}", f"--load-extension={ZOOMEXT}"])  # fmt: skip
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.pages[0] if cx.pages else cx.new_page()
    pg.goto(URL + "/")
    sw = cx.service_workers[0] if cx.service_workers else cx.wait_for_event("serviceworker", timeout=15000)

    def zoom() -> None:
        sw.evaluate("chrome.tabs.query({}).then(ts => Promise.all(ts.map(t => chrome.tabs.setZoom(t.id, 2.0))))")
        pg.wait_for_timeout(800)

    text = show_db_down(pg)
    zoom()
    rec("V", "200% DB 연결 불가 안내", None, {"text": text, **look(pg, False)})
    pg.screenshot(path=str(OUT / f"r{ROUND}-V-zoom200-dbdown.png"), full_page=True)
    browser_login(pg, USERS["user1"], a_pw)
    show_home(pg)
    zoom()
    rec("V", "200% 업로드 한도 안내", None, look(pg, False))
    text = show_common_changed(pg, fids)
    zoom()
    rec("V", "200% 공통 검토 다시 열기 필요", None, {"text": text, **look(pg, False)})
    pg.screenshot(path=str(OUT / f"r{ROUND}-V-zoom200-common.png"), full_page=True)
    text, held = show_busy(pg)
    zoom()
    rec("V", "200% 업로드 중 고를 수 없음", None, {"text": text, **look(pg, False)})
    pg.screenshot(path=str(OUT / f"r{ROUND}-V-zoom200-busy.png"), full_page=True)
    release(pg, held)
    cx.close()


# ---------------------------------------------------------------- K. 키보드만으로: 메뉴 → 검토 목록 → 검토 열기 → Esc
FOCUS_JS = "() => { const e = document.activeElement; return e ? {tag: e.tagName.toLowerCase(), name: (e.getAttribute('aria-label') || e.innerText || e.value || '').trim().slice(0, 80)} : null }"


def tab_until(pg: Page, pred, limit: int) -> tuple[int, dict | None]:
    for i in range(1, limit + 1):
        pg.keyboard.press("Tab")
        f = pg.evaluate(FOCUS_JS)
        if f and pred(f):
            return i, f
    return limit, None


def part_keyboard(b, a_pw: str) -> None:
    cx = b.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
    cx.route("**/*", route_guard(URL, BLOCKED))
    pg = cx.new_page()
    browser_login(pg, USERS["user1"], a_pw)
    pg.goto(URL + "/")
    pg.wait_for_load_state("networkidle")
    pg.evaluate("() => document.activeElement && document.activeElement.blur()")  # 페이지 처음부터 Tab
    n_menu, f = tab_until(pg, lambda f: f["tag"] == "a" and f["name"] == "검토 목록", 15)
    info: dict = {"tabs_to_menu": n_menu if f else None}
    if f:
        pg.keyboard.press("Enter")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(600)
        info["after_menu"] = pg.evaluate("location.pathname")
        n_rev, r = tab_until(pg, lambda f: f["tag"] == "button" and f["name"] in ("검토 보기", "검토 이어서 하기"), 60)  # 목록의 행 버튼
        info["review_link"] = (n_rev, r)
        if r:
            pg.keyboard.press("Enter")
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(800)
            info["after_open"] = {"url": pg.evaluate("location.pathname + location.search"), "focus": pg.evaluate(FOCUS_JS)}
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(600)
            info["after_escape"] = {"url": pg.evaluate("location.pathname + location.search"), "focus": pg.evaluate(FOCUS_JS)}
    ok = info.get("tabs_to_menu") is not None and info.get("after_menu") == "/reviews" and bool(info.get("after_open"))
    rec("K", "K1 키보드만으로 메뉴 '검토 목록' → 검토 열기 → Esc (페이지 처음부터 Tab)", ok, info)
    cx.close()


def main() -> None:
    a_pw = creds()["user1"]
    owner = Api().login("owner")
    a = Api().login("user1")
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(URL))
        for key, fn in (("U", lambda: part_upload(br, a_pw)), ("C", lambda: part_common(br, a, a_pw)), ("D", lambda: part_duplicate(br, a, a_pw)),
                        ("M", lambda: part_admin(owner, a)), ("B", lambda: part_blocked(owner, a)), ("V", lambda: part_views(br, p, a, a_pw)),
                        ("K", lambda: part_keyboard(br, a_pw))):  # fmt: skip
            if key not in PARTS:
                continue
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — 시험 도구 오류로 기록 (통과로 세지 않음)
                rec(key, "시험 도구 오류", False, f"{type(e).__name__}: {str(e)[:300]}")
        br.close()
    out = {"round": ROUND, "results": RES, "meta": META, "chooser_retries": CHOOSER_RETRIES, "blocked_out_of_scope": BLOCKED}
    # 같은 차례에 부분을 나눠 여러 번 돌려도 덮어쓰지 않게 부분 이름을 붙인다
    name = f"round5-r{ROUND}-{''.join(k for k in 'UCDMBVK' if k in PARTS)}.json"
    (OUT / name).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    judged = [r for r in RES if r["ok"] is not None]
    print(f"RESULT 통과 {sum(r['ok'] for r in judged)} · 실패 {sum(not r['ok'] for r in judged)} · 측정만 {len(RES) - len(judged)} · 막힌 범위 밖 요청 {len(BLOCKED)}")


if __name__ == "__main__":
    main()
