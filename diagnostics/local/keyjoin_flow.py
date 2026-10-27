"""공통 key 취합(/keyjoin) 화면 흐름 확인 — 실제 Edge(headless), 격리 스택(127.0.0.1:8088 → 8096), 합성 자료 (지시 6-3, 설계 7.5·6장).

소유자 API로 새 격리 일반 계정을 만들고 그 계정으로 화면에서: 업로드 → 홈 결과 형태 '공통 key로 연결하기' → 7단계(파일·시트, key 열,
가져올 열+결과 이름, 범위) → '연결 확인' → 엑셀 만들기 → 실행 기록·내려받기. 세 범위(기준 A만·모든 파일·공통)를 각각 만들고
내려받은 xlsx를 openpyxl로만 열어(제품 함수를 부르지 않음) 설계 7.1의 상수 기대값·expected.json과 대조한다. 거절 경로(중복 key·공통
없음·개인정보 확인 전)는 plan에서 차단되고 실행 기록이 늘지 않음을 API 건수로 본다. 반응형(320·390·1366)·200% 확대·키보드 완주·
초점 불변·늦은 응답·긴 이름·axe(있으면)와 기존 기능 스모크(행 이어 붙이기 통합·묶기 요약 끔/켬·검토 재검사·완료·가림·다운로드)도
같은 스택에서 한다. DB는 읽지 않는다(SQLite·PostgreSQL 차례가 같은 스크립트) — 기록 건수는 GET /runs로만.
DOM의 role·속성·글자만 확인하며 실제 화면 읽기 프로그램 낭독은 미검증이다. 기대 문구는 화면·서버 코드의 문구 그대로다.
사용법: python keyjoin_flow.py <데이터 폴더> <nginx.conf> <증거 폴더> [--db sqlite|pg]
  환경 변수: KEYJOIN_SYNTH(합성 입력 폴더 — A.csv·B.xlsx·C.xls·D.csv·expected.json), AXE_JS(axe.min.js — 없으면 axe 미수행)
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import expect as pw_expect

URL = "http://127.0.0.1:8088"
DATA, NGINX, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
DB_MODE = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else "sqlite"
ROOT = Path(__file__).resolve().parents[2]
_synth = os.environ.get("KEYJOIN_SYNTH")
SYNTH = Path(_synth) if _synth else ROOT / "backups" / "review-20261026b" / "keyjoin-synth"
if not SYNTH.exists():
    SYNTH = Path.home() / "Documents" / "Moajwo" / "backups" / "review-20261026b" / "keyjoin-synth"
_axe = os.environ.get("AXE_JS")
AXE = Path(_axe) if _axe else ROOT / "diagnostics" / "tools" / "axe.min.js"
ZOOMEXT = ROOT / "diagnostics" / "tools" / "zoomext"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "downloads").mkdir(exist_ok=True)
CSP = re.search(r'Content-Security-Policy "([^"]+)"', NGINX.read_text(encoding="utf-8")).group(1)
PW = {"owner": "Uo-" + secrets.token_hex(10), "user": "Uu-" + secrets.token_hex(10)}  # 실행 때 만들고 남기지 않음
OWNER, USER = "kj-owner", "kj-user"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME = {".csv": "text/csv", ".xlsx": XLSX, ".xls": "application/vnd.ms-excel"}
REC: list[dict] = []
FACTS: dict = {}
ABORTED: list[str] = []
CONSOLE: list[dict] = []

# ---------------------------------------------------------------- 설계 7.1의 상수 기대값 (제품 함수를 부르지 않은 리터럴)
EXPECTED_HEADER = ["사번", "부서", "교육 건수", "출장 건수", "처리 건수"]
ROWS_BASE = [("001", "총무", 10, 1, 100), ("002", "기획", None, 2, None), ("003", "정보", 30, None, 300)]
ROWS_ALL = [*ROWS_BASE, ("004", None, 40, None, None), ("005", None, None, 5, None), ("006", None, None, None, 600)]
ROWS_COMMON = [ROWS_BASE[0]]
SOURCES_BASE = [[1, 2, 2, 2, 2], [2, 3, None, 3, None], [3, 4, 3, None, 3]]
SOURCES_ALL = [*SOURCES_BASE, [4, None, 4, None, None], [5, None, None, 4, None], [6, None, None, None, 4]]
SOURCES_COMMON = [SOURCES_BASE[0]]
SCOPES = {"base": (ROWS_BASE, SOURCES_BASE, 6, 1, 0), "all": (ROWS_ALL, SOURCES_ALL, 6, 1, 0), "common": (ROWS_COMMON, SOURCES_COMMON, 6, 1, 5)}
SHEETS = ["연결 결과", "출처", "연결 요약"]
UNPICKED_NAMES = ["입사연도", "비고", "지역", "담당"]  # 열 이름 — 부분 일치로 찾는다 (머리글·요약 문장 어디에도 없어야)
# 열 값 — 칸 전체 일치로만 ('집합'은 '합집합'·'교집합', 숫자는 해시·시각 문자열의 우연한 부분 일치가 있어 부분 일치는 오판)
UNPICKED_VALUES = ["온라인", "집합", "세종", "대전", "부산", "민원", "통계", "감사", "2019", "2021", "2020", 2019, 2021, 2020]
KEYS_ALL = ["001", "002", "003", "004", "005", "006"]
PHONE = "010-0000-0001"  # 합성 전화번호 (개인정보 확인 경로)
SCOPE_LABEL = {"base": "기준 파일의 사용자만", "all": "모든 파일의 사용자", "common": "모든 파일의 공통 사용자"}
SCOPE_SHORT = {"base": "기준", "all": "모든", "common": "공통"}
STALE_TEXT = "확인하는 동안 설정이 바뀌어 이전 설정의 결과는 쓰지 않았습니다 — 지금 설정으로 다시 확인하세요."
CHANGED_TEXT = "설정을 바꿨습니다 — 다시 확인해야 만들 수 있습니다."
CHECKING_TEXT = "연결을 확인하는 중… (파일은 만들지 않음)"
MODE_TITLE = "공통 key로 연결하기 (검토 없음)"

# ---------------------------------------------------------------- 선택자
SEC = "section.keyjoin"
STATUS = f"{SEC} p.kj-status[role=status]"
ALERT = f"{SEC} .an-problems[role=alert]"
FS = {n: f"{SEC} fieldset.bd-step:has(> legend:text-matches('^{n}\\.'))" for n in range(1, 8)}
CHECK_BTN = f"{FS[5]} button.mapping-run"
MAKE_BTN = f"{FS[6]} button.mapping-run"
MADE = f"{FS[7]} .bd-made"
KEY_SELECTS = f"{FS[2]} select[aria-label$=' key 열']"
PICK_BLOCKS = f"{FS[3]} .kj-input:has(.kj-cols)"
PANEL = "section.review-panel"


# ---------------------------------------------------------------- 기록


def rec(item: str, step: str, ok: bool | None, detail: object = "", kind: str = "check") -> None:
    """ok None = 정보(판정 아님)."""
    if not isinstance(detail, (str, int, float, bool, type(None))):
        detail = json.loads(json.dumps(detail, ensure_ascii=False, default=str))
    if ok is None and kind == "check":
        kind = "info"
    REC.append({"item": item, "step": step, "ok": None if ok is None else bool(ok), "kind": kind, "detail": detail})
    tag = "정보" if ok is None else ("통과" if ok else "실패")
    print(f"{tag} | [{item}] {step} | {json.dumps(detail, ensure_ascii=False)[:700]}", flush=True)


def guarded(item: str, name: str, page: Page | None, fn) -> None:
    """한 단계의 예외(시간 초과 등)는 '실행 오류'로 남기고 다음 단계로 간다 — 통과로 세지 않는다."""
    t0 = time.time()
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        shot = None
        if page is not None:
            shot = OUT / f"error-{item}-{len([r for r in REC if r['kind'] == 'error']) + 1}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:  # noqa: BLE001
                shot = None
        REC.append({"item": item, "step": f"{name} — 실행 오류", "ok": None, "kind": "error",
                    "detail": f"{type(exc).__name__}: {str(exc)[:1500]}", "trace": traceback.format_exc()[-4000:],
                    "screenshot": shot.name if shot else None})  # fmt: skip
        print(f"오류 | [{item}] {name} | {type(exc).__name__}: {str(exc)[:500]}", flush=True)
    print(f"  ({item} {name}: {time.time() - t0:.1f}초)", flush=True)


# ---------------------------------------------------------------- API (준비·건수 확인 — 확인하는 동작은 화면에서)


def payload(name: str, data: bytes, mime: str = XLSX) -> dict:
    return {"name": name, "mimeType": mime, "buffer": data}


def api_upload(page: Page, name: str, data: bytes, mime: str | None = None) -> int:
    r = page.request.post(f"{URL}/api/files", multipart={"files": payload(name, data, mime or MIME[Path(name).suffix])})
    body = r.json()[0]
    assert r.ok and body["id"], (r.status, body.get("error"))
    return body["id"]


def api_decide(page: Page, fid: int, keys: dict[int, str | None]) -> None:
    pv = page.request.get(f"{URL}/api/files/{fid}/preview").json()
    dec = [{"sheet_id": pv["sheet_id"], "column_id": pv["columns"][i]["column_id"], "field_key": k} for i, k in keys.items()]
    r = page.request.put(f"{URL}/api/files/{fid}/mapping/decisions", data={"decisions": dec})
    assert r.ok, (r.status, r.text()[:300])


def login(page: Page, who: str) -> None:
    r = page.request.post(f"{URL}/api/auth/login", data={"username": who, "password": PW["owner" if who == OWNER else "user"]})
    assert r.ok, r.status


def runs_count(page: Page) -> int:
    r = page.request.get(f"{URL}/api/runs?limit=200")
    assert r.ok, r.status
    return len(r.json())


def run_json(page: Page, run_id: int) -> dict:
    r = page.request.get(f"{URL}/api/runs/{run_id}")
    assert r.ok, r.status
    return r.json()


def request_body(fids: list[int], scope: str, picks: dict[int, list[str]] | None = None) -> dict:
    """화면이 보내는 것과 같은 꼴의 요청 (거절 경로를 API로 강제할 때만)."""
    return {
        "inputs": [{"file_id": f, "sheet_id": "s0", "key_columns": ["c0"],
                    "columns": [{"column_id": c, "out_name": None} for c in (picks or {}).get(f, ["c1"])]} for f in fids],  # fmt: skip
        "scope": scope,
        "base": {"file_id": fids[0], "sheet_id": "s0"},
        "key_names": None,
    }


# ---------------------------------------------------------------- DOM 보조 (ui_final_check.py와 같은 측정)

SCAN_JS = r"""(root, frags) => {
  const red = (s) => { let o = String(s); for (const [, f] of frags) o = o.split(f).join('[원문]'); return o.slice(0, 160) }
  const hits = []
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let n
  while ((n = w.nextNode())) {
    const t = n.nodeValue || ''
    for (const [label, f] of frags) if (t.includes(f)) {
      const p = n.parentElement
      hits.push({ frag: label, at: 'text', tag: p ? p.tagName : '', cls: p ? String(p.className).slice(0, 60) : '', text: red(t) })
    }
  }
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const a of Array.from(el.attributes || [])) {
      if (a.name === 'class' || a.name === 'style') continue
      for (const [label, f] of frags) if ((a.value || '').includes(f))
        hits.push({ frag: label, at: 'attr:' + a.name, tag: el.tagName, cls: String(el.className).slice(0, 60), text: red(a.value) })
    }
    if (typeof el.value === 'string' && el.value && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
      for (const [label, f] of frags) if (el.value.includes(f)) hits.push({ frag: label, at: 'value', tag: el.tagName, cls: '', text: red(el.value) })
  }
  return hits
}"""

MEASURE_JS = r"""(el) => {
  const de = document.documentElement
  const r = el.getBoundingClientRect()
  const cs = getComputedStyle(el)
  const clips = []
  for (let a = el.parentElement; a && a !== document.body && a !== de; a = a.parentElement) {
    const s = getComputedStyle(a)
    if (s.overflowX !== 'visible') {
      const ar = a.getBoundingClientRect()
      if (r.left < ar.left - 1 || r.right > ar.right + 1)
        clips.push({ tag: a.tagName, cls: String(a.className).slice(0, 60), overflowX: s.overflowX, left: Math.round(ar.left), right: Math.round(ar.right) })
    }
  }
  const range = document.createRange()
  range.selectNodeContents(el)
  const lines = Array.from(range.getClientRects()).filter((q) => q.width > 0)
  const outside = lines.filter((q) => q.left < -1 || q.right > innerWidth + 1).length
  const inline = cs.display === 'inline'
  return {
    viewport: innerWidth, page_scroll_width: de.scrollWidth, page_client_width: de.clientWidth,
    page_overflow: de.scrollWidth > de.clientWidth + 1,
    left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width), height: Math.round(r.height),
    inside_viewport: r.left >= -1 && r.right <= innerWidth + 1,
    self_overflow_x: !inline && el.scrollWidth > el.clientWidth + 1,
    white_space: cs.whiteSpace, text_overflow: cs.textOverflow, overflow_wrap: cs.overflowWrap,
    clipping_ancestors: clips, line_rects: lines.length, line_rects_outside: outside,
  }
}"""

OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth
  const page = document.documentElement.scrollWidth - vw
  const out = []
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect()
    if (!r.width || r.right <= vw + 1) continue
    let p = e.parentElement, inScroller = false
    while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { inScroller = true; break } p = p.parentElement }
    if (!inScroller) out.push((e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : e.tagName.toLowerCase()) + ' ' + Math.round(r.right))
  }
  return { page_overflow_px: page, scroll_width: document.documentElement.scrollWidth, client_width: vw, outside: [...new Set(out)].slice(0, 12) }
}"""

ACTIVE_JS = """() => { const a = document.activeElement; if (!a || a === document.body) return 'body'
  return a.tagName + (a.type ? '[' + a.type + ']' : '') + '.' + String(a.className).slice(0, 30) + ':' + (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 40) }"""


def scan(target: Page | Locator, frags: list[list[str]]) -> list[dict]:
    if isinstance(target, Page):
        return target.evaluate(f"(frags) => ({SCAN_JS})(document.body, frags)", frags)
    return target.evaluate(SCAN_JS, frags)


def measure(loc: Locator) -> dict:
    m = loc.evaluate(MEASURE_JS)
    m["ok"] = (not m["page_overflow"] and m["inside_viewport"] and not m["self_overflow_x"] and m["text_overflow"] != "ellipsis"
               and not m["clipping_ancestors"] and m["line_rects_outside"] == 0)  # fmt: skip
    return m


WIDTHS_JS = """() => {
  const w = (sel) => [...document.querySelectorAll(sel)].map((e) => e.offsetWidth)
  const cs = (sel, prop) => { const e = document.querySelector(sel); return e ? getComputedStyle(e)[prop] : null }
  return { viewport: innerWidth, main: w('main'), main_min_width: cs('main', 'minWidth'), main_display: cs('main', 'display'),
           main_parent_display: (() => { const m = document.querySelector('main'); return m && m.parentElement ? getComputedStyle(m.parentElement).display : null })(),
           section: w('section.keyjoin'), fieldsets: w('section.keyjoin fieldset.bd-step'),
           table_wraps: [...document.querySelectorAll('section.keyjoin .upload-table-wrap')].map((e) => [e.clientWidth, e.scrollWidth]),
           tables: [...document.querySelectorAll('section.keyjoin table')].map((t) => [t.getAttribute('aria-label'), t.offsetWidth, t.rows[0] ? t.rows[0].cells.length : 0]),
           preview_cell_min_width: cs('section.keyjoin table.kj-preview td', 'minWidth') }
}"""


def overflow(page: Page) -> dict:
    o = page.evaluate(OVERFLOW_JS)
    if o["page_overflow_px"] > 0:
        o["widths"] = page.evaluate(WIDTHS_JS)  # 어디가 넓어졌는지 (본문·영역·표)
    return o


def active(page: Page) -> str:
    return page.evaluate(ACTIVE_JS)


def mark_active(page: Page) -> str:
    """지금 초점 요소를 기억해 두고(window.__focusMark) 그 설명을 돌려준다 — 뒤에 same_active로 같은 요소인지 본다."""
    page.evaluate("() => { window.__focusMark = document.activeElement }")
    return active(page)


def same_active(page: Page) -> bool:
    return page.evaluate("() => document.activeElement === window.__focusMark")


def tab_to(page: Page, target: Locator, limit: int = 400, back: bool = False) -> dict:
    """Tab(또는 Shift+Tab)만 눌러 target에 초점이 갈 때까지. 몇 번 눌렀는지·초점 표시(:focus-visible)를 돌려준다."""
    handle = target.element_handle(timeout=15000)
    trail = []
    for n in range(1, limit + 1):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if handle.evaluate("(e) => e === document.activeElement"):
            return {"presses": n, "key": "Shift+Tab" if back else "Tab", "focus_visible": handle.evaluate("(e) => e.matches(':focus-visible')")}
        if n > limit - 6:
            trail.append(active(page))
    raise AssertionError(f"{'Shift+Tab' if back else 'Tab'} {limit}번 안에 닿지 못함 — 마지막 초점: {trail}")


def shot(page: Page, name: str, loc: Locator | None = None, full: bool = False) -> list[str]:
    files = []
    if loc is not None:
        try:
            loc.scroll_into_view_if_needed()
            loc.screenshot(path=str(OUT / f"{name}-part.png"))
            files.append(f"{name}-part.png")
        except Exception:  # noqa: BLE001 — 캡처 실패는 판정에 넣지 않음
            pass
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    files.append(f"{name}.png")
    return files


def text_of(loc: Locator) -> str:
    return loc.inner_text().strip()


def is_post(path_end: str):
    return lambda r: r.request.method == "POST" and r.url.split("?")[0].endswith(path_end)


def status_text(page: Page) -> str:
    return (page.locator(STATUS).text_content() or "").strip()


def disposition_name(header: str) -> str | None:
    from urllib.parse import unquote

    m = re.search(r"filename\*=UTF-8''([^;]+)", header, re.I)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="?([^";]+)"?', header, re.I)
    return m.group(1) if m else None


# ---------------------------------------------------------------- 기대 문구 (KeyJoin.tsx의 규칙 그대로 — 응답으로 다시 만든다)


def summary_text(plan: dict) -> str:
    unlinked = ", ".join(f"입력 {i['index'] + 1} {i['keys']['unlinked']:,}" if i["keys"] else f"입력 {i['index'] + 1} -" for i in plan["inputs"])
    parts = [f"확인 결과: 결과 {plan['result']['rows']:,}행 · 열 {plan['result']['columns']}개", f"미연결(입력별) {unlinked}"]
    if plan["problems"]:
        parts.append(f"만들 수 없는 이유 {len(plan['problems'])}건")
    if plan["warnings"]:
        parts.append(f"참고 {len(plan['warnings'])}건")
    return " · ".join(parts) + " — " + ("만들 수 있습니다." if plan["ready"] else "문제를 해결해야 만들 수 있습니다.")


def made_text(run: dict, created: bool = True) -> str:
    if created:
        return f"실행 #{run['id']} 완료 — 결과 {run['row_count']:,}행. 7단계에서 내려받을 수 있습니다."
    return f"실행 #{run['id']}는 같은 요청 번호로 이미 만든 실행입니다 — 새로 만들지 않았습니다. 7단계에서 내려받을 수 있습니다."


# ---------------------------------------------------------------- 화면 조작 (keyjoin)


def wait_meta(page: Page, n: int) -> None:
    lis = page.locator(f"{FS[1]} ul.bd-files > li")
    pw_expect(lis).to_have_count(n, timeout=30000)
    for i in range(n):
        pw_expect(lis.nth(i)).to_contain_text("데이터", timeout=30000)
    pw_expect(page.locator(KEY_SELECTS)).to_have_count(n, timeout=30000)


def configure(page: Page, n: int, picks: dict[int, list[int]] | None = None) -> None:
    """마우스: 입력마다 key 열 = 첫 열(c0), 가져올 열 = 둘째 열(c1) (picks로 바꿀 수 있음: 입력 순번 → 열 순번 목록)."""
    for i in range(n):
        page.locator(KEY_SELECTS).nth(i).select_option("c0")
    for i in range(n):
        for j in (picks or {}).get(i, [1]):
            page.locator(PICK_BLOCKS).nth(i).locator("input[type=checkbox]").nth(j).check()


def do_check(page: Page, keyboard: bool = False) -> tuple[int, dict]:
    btn = page.locator(CHECK_BTN)
    pw_expect(btn).to_have_attribute("aria-disabled", "false", timeout=30000)
    with page.expect_response(is_post("/api/keyjoin/plan"), timeout=180000) as ri:
        if keyboard:
            page.keyboard.press("Enter")
        else:
            btn.click()
    resp = ri.value
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text()}
    pw_expect(page.locator(FS[5])).not_to_have_attribute("aria-busy", "true", timeout=30000)
    pw_expect(page.locator(STATUS)).not_to_have_text(CHECKING_TEXT, timeout=30000)
    return resp.status, body


def do_make(page: Page, keyboard: bool = False) -> tuple[int, dict]:
    btn = page.locator(MAKE_BTN)
    pw_expect(btn).to_have_attribute("aria-disabled", "false", timeout=30000)
    with page.expect_response(is_post("/api/keyjoin/run"), timeout=180000) as ri:
        if keyboard:
            page.keyboard.press("Enter")
        else:
            btn.click()
    resp = ri.value
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text()}
    if resp.status in (200, 201):
        pw_expect(page.locator(MADE)).to_contain_text(f"#{body['id']}", timeout=30000)
    return resp.status, body


def download_made(page: Page, name: str, keyboard: bool = False) -> tuple[Path, str]:
    link = page.locator(MADE).get_by_role("link", name="내려받기")
    with page.expect_download(timeout=60000) as di:
        if keyboard:
            page.keyboard.press("Enter")
        else:
            link.click()
    d = di.value
    path = OUT / "downloads" / name
    d.save_as(str(path))
    return path, d.suggested_filename


def pick_scope(page: Page, scope: str) -> None:
    page.locator(f"{FS[4]} .kj-scope label", has=page.locator("strong", has_text=re.compile(rf"^{re.escape(SCOPE_LABEL[scope])}$"))).click()


# ---------------------------------------------------------------- 내려받은 파일 검사 (openpyxl만 — 제품 함수 없음)


def cell_desc(c) -> str:  # noqa: ANN001
    return f"{c.coordinate}={c.value!r}({c.data_type})"


def check_workbook(item: str, scope: str, path: Path, fids: list[int]) -> None:
    rows_exp, src_exp, union, inter, excluded = SCOPES[scope]
    is_zip = zipfile.is_zipfile(path) and "[Content_Types].xml" in zipfile.ZipFile(path).namelist()
    wb = openpyxl.load_workbook(path)
    rec(item, f"{scope}: xlsx(zip)·시트 순서 {SHEETS}", is_zip and wb.sheetnames == SHEETS, {"sheets": wb.sheetnames, "size": path.stat().st_size})
    ws = wb[SHEETS[0]]
    header = [c.value for c in ws[1]]
    rec(item, f"{scope}: 연결 결과 머리글 순서 == {EXPECTED_HEADER}", header == EXPECTED_HEADER, header)
    rec(item, f"{scope}: 연결 결과 열 수 5 (key 1 + 선택 4)·행 수 {len(rows_exp)}", ws.max_column == 5 and ws.max_row == 1 + len(rows_exp),
        {"max_column": ws.max_column, "max_row": ws.max_row})  # fmt: skip
    bad = []
    for r, exp in enumerate(rows_exp, start=2):
        for j, want in enumerate(exp, start=1):
            c = ws.cell(row=r, column=j)
            if j == 1:
                ok = c.data_type == "s" and c.value == want and isinstance(c.value, str)
            elif want is None:
                ok = c.value is None
            elif isinstance(want, str):
                ok = c.value == want and c.data_type == "s"
            else:
                ok = c.value == want and c.data_type == "n" and not isinstance(c.value, bool) and isinstance(c.value, int)
            if not ok:
                bad.append({"want": want, "got": cell_desc(c)})
    rec(item, f"{scope}: 모든 셀 값·자료형 (key 글자 칸 's'·값 숫자 칸 'n'·미연결 None·실제 값 그대로)", not bad, bad[:12])
    comments = [c.coordinate for row in ws.iter_rows() for c in row if c.comment]
    rec(item, f"{scope}: 연결 결과 시트에 메모 없음 (수식 결과 누락 칸 없음)", not comments, comments)
    # 선택하지 않은 열 이름·값이 워크북 전체(모든 시트·메모)에 없음
    hits = []
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for c in row:
                texts = [str(c.value)] if c.value is not None else []
                if c.comment:
                    texts.append(c.comment.text)
                for t in texts:
                    for frag in UNPICKED_NAMES:
                        if frag in t:
                            hits.append({"sheet": sheet.title, "cell": c.coordinate, "frag": frag})
                if c.value in UNPICKED_VALUES:
                    hits.append({"sheet": sheet.title, "cell": c.coordinate, "value": c.value})
    rec(item, f"{scope}: 선택하지 않은 열 이름(부분 일치)·값(칸 전체)이 워크북 어디에도 없음 ({UNPICKED_NAMES} 등)", not hits, hits[:12])
    # 출처 시트
    ws2 = wb[SHEETS[1]]
    head2 = [c.value for c in ws2[1]]
    want_head2 = ["결과 행", *(f"입력 {i + 1} · #{f} · 1번째 시트 원본 행" for i, f in enumerate(fids))]
    got_src = [[c.value for c in row] for row in ws2.iter_rows(min_row=2)]
    rec(item, f"{scope}: 출처 시트 머리글·행 번호 == {src_exp}", head2 == want_head2 and got_src == src_exp, {"head": head2, "rows": got_src})
    # 요약 시트: key 원문 없음 (칸 전체 일치 + 글자 칸 부분 일치 — 실행 번호·시각·basis 줄 제외)
    ws3 = wb[SHEETS[2]]
    key_hits = []
    for row in ws3.iter_rows():
        label = str(row[0].value) if row and row[0].value is not None else ""
        for c in row:
            if c.value is None:
                continue
            t = str(c.value)
            if t in KEYS_ALL:
                key_hits.append(cell_desc(c))
            elif label not in ("실행 번호", "처리 시각", "확인 기준(basis)") and any(k in t for k in KEYS_ALL):
                key_hits.append(cell_desc(c))
    labels = [str(r[0].value) for r in ws3.iter_rows() if r and r[0].value is not None]
    notes = [str(r[1].value) for r in ws3.iter_rows() if r and r[0].value == "안내" and len(r) > 1]
    rec(item, f"{scope}: 연결 요약 시트에 key 원문 없음 · 고정 안내 7줄(행 포함/제외 확인·병합 채움·오류 코드 글자 포함)·범위 문구", not key_hits and len(notes) == 7
        and any("포함/제외 확인" in n for n in notes) and any("병합 칸" in n for n in notes) and any("오류 코드 모양" in n for n in notes)
        and any(SCOPE_LABEL[scope] == str(r[1].value) for r in ws3.iter_rows() if r[0].value == "범위"),
        {"key_hits": key_hits[:8], "labels": labels[:14], "notes": len(notes)})  # fmt: skip
    counts = {str(r[0].value): r[1].value for r in ws3.iter_rows() if r[0].value in ("결과 행", "모든 입력의 사용자(합집합)",
                                                                                   "모든 입력에 있는 사용자(교집합)", "범위에서 빠진 사용자")}  # fmt: skip
    rec(item, f"{scope}: 요약 시트 개수 (결과 행 {len(rows_exp)} · 합집합 {union} · 교집합 {inter} · 빠짐 {excluded})",
        counts == {"결과 행": len(rows_exp), "모든 입력의 사용자(합집합)": union, "모든 입력에 있는 사용자(교집합)": inter,
                   "범위에서 빠진 사용자": excluded}, counts)  # fmt: skip
    wb.close()


def check_expected_json(item: str, scope: str, path: Path) -> None:
    """독립 기대값 파일(expected.json)과의 대조 — 7.1 리터럴과 별도로 본다."""
    exp = json.loads((SYNTH / "expected.json").read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = [[c.value for c in r] for r in wb[SHEETS[0]].iter_rows()]
    header, data = rows[0], rows[1:]
    if scope == "base":
        want = exp["기준 A (3행)"]
        got = [{"key": r[0], "A 부서": r[1], "B 교육 건수": r[2], "C 출장 건수": r[3], "D 처리 건수": r[4]} for r in data]
        same = len(got) == len(want) and all(
            all((str(g[k]) == str(w[k])) if w[k] is not None else g[k] is None for k in w) for g, w in zip(got, want, strict=True))
        src = [[c.value for c in r] for r in wb[SHEETS[1]].iter_rows()][1:]
        want_src = exp["출처 (기준 A, 원본 행 번호 1-based: 머리글 1행)"]
        same_src = [r[1:] for r in src] == [[v["A"], v["B"], v["C"], v["D"]] for v in want_src.values()]
        ok = same and same_src
    else:
        want = exp["모든 파일 (6행 key)" if scope == "all" else "모든 파일 공통 (1행 key)"]
        ok = [r[0] for r in data] == want
    ok = ok and len(header) == exp["결과 데이터 열 수"]
    wb.close()
    rec(item, f"{scope}: expected.json(독립 기대값)과 일치", ok, {"rows": len(data), "columns": len(header)})


def check_run_record(item: str, scope: str, run: dict, plan: dict, fids: list[int], rows: int) -> None:
    s = run["summary"]
    file_ids = run.get("file_ids")
    if isinstance(file_ids, str):
        file_ids = json.loads(file_ids)
    ok = (s.get("mode") == "keyjoin" and s.get("mode_label") == "공통 key 연결" and s.get("scope") == scope and run["row_count"] == rows
          and run["input_file_count"] == len(set(fids)) and run["change_count"] == 0 and s.get("basis") == plan["basis"]
          and re.fullmatch(r"[0-9a-f]{32}", s.get("request_id") or "") is not None and s.get("sheet_order") == SHEETS
          and s.get("result") == {"rows": rows, "columns": 5, "key_columns": 1} and s.get("header") == EXPECTED_HEADER
          and (run.get("file_name") or "").startswith("key연결_") and run["status"] == "done"
          and (file_ids is None or sorted(file_ids) == sorted(set(fids))))  # fmt: skip
    text = json.dumps(run, ensure_ascii=False)
    leak = [f for f in UNPICKED_NAMES if f in text] + [v for v in UNPICKED_VALUES if isinstance(v, str) and f'"{v}"' in text]
    rec(item, f"{scope}: 실행 기록(RunOut) summary.mode=keyjoin·scope·row_count {rows}·input_file_count {len(set(fids))}·change_count 0·"
        "basis·request_id(32hex)·sheet_order·header·파일 이름 key연결_", ok and not leak,
        {"id": run["id"], "file_name": run.get("file_name"), "row_count": run["row_count"], "input_file_count": run["input_file_count"],
         "change_count": run["change_count"], "warning_count": run["warning_count"], "summary_keys": sorted(s.keys())[:30],
         "unpicked_in_record": leak})  # fmt: skip


def plan_shape(plan: dict) -> dict:
    return {k: (plan.get(k) is not None) for k in ("header", "result", "preview", "limits", "budget", "basis", "ready", "problems", "warnings")}


# ---------------------------------------------------------------- 단계


def step_setup(page: Page) -> None:
    r = page.request.post(f"{URL}/api/auth/setup", data={"username": OWNER, "display_name": "key 취합 확인 소유자", "password": PW["owner"]})
    assert r.status == 201, (r.status, r.text()[:200])
    r = page.request.post(f"{URL}/api/users", data={"username": USER, "display_name": "key 취합 확인", "status": "active", "password": PW["user"]})
    assert r.status == 201, (r.status, r.text()[:200])
    # 기존 기능 스모크(검토 승인)에 쓸 기본 용어사전 — 소유자만 가져올 수 있다 (합성 기관 코드·이름)
    g = "코드,정식명칭,유형\n1741000,행정안전부,중앙행정기관\n1352000,보건복지부,중앙행정기관\n1051000,기획재정부,중앙행정기관\n"
    r = page.request.post(f"{URL}/api/glossary/import", multipart={"file": payload("기본사전.csv", g.encode("utf-8"), "text/csv")})
    assert r.ok and r.json().get("applied"), r.text()[:300]
    r = page.request.post(f"{URL}/api/auth/logout")
    login(page, USER)
    me = page.request.get(f"{URL}/api/auth/me").json()
    FACTS["user_id"] = me.get("id")
    rec("준비", "소유자 계정(최초 설정) → 소유자 API로 새 격리 일반 계정 → 그 계정으로 로그인 (API)", me.get("username") == USER and me.get("role") == "user",
        {"username": me.get("username"), "role": me.get("role"), "db": DB_MODE, "synth": str(SYNTH)}, kind="setup")  # fmt: skip


def step_upload(page: Page) -> None:
    page.goto(URL + "/")
    inp = page.locator("section.wk-upload input[type=file]")
    inp.wait_for(state="attached", timeout=30000)
    names = ["A.csv", "B.xlsx", "C.xls", "D.csv"]
    with page.expect_response(is_post("/api/files"), timeout=120000) as ri:
        inp.set_input_files([str(SYNTH / n) for n in names])
    res = ri.value.json()
    FACTS["fids"] = [r["id"] for r in res]
    pw_expect(page.locator("section.wk-upload h3.wk-subhead")).to_contain_text("이번에 올린 파일 4개", timeout=30000)
    cards = page.locator("section.wk-upload ul.upload-results > li")
    heads = [text_of(cards.nth(i).locator(".upload-file-head")) for i in range(cards.count())]
    ok = all(r["error"] is None and r["id"] for r in res) and len(res) == 4 and all("올림 완료" in h for h in heads)
    rec("1", "합성 입력 A.csv·B.xlsx·C.xls·D.csv 업로드 (화면) — 4개 모두 올림 완료", ok,
        {"ids": FACTS["fids"], "cards": heads, "pii": [bool(r.get("pii")) for r in res]})  # fmt: skip
    shot(page, "desktop-1-upload")


def pick_mode_card(page: Page, title: str) -> Locator:
    card = page.locator("label.wk-mode-card", has=page.locator("strong", has_text=re.compile(rf"^{re.escape(title)}$")))
    card.click()
    pw_expect(card.locator("input[type=radio]")).to_be_checked(timeout=10000)
    return card


def step_home(page: Page) -> None:
    a, b, c, d = FACTS["fids"]
    page.goto(f"{URL}/?files={a}")
    pw_expect(page.locator(".wk-val-summary")).to_contain_text("검증 대상 1개", timeout=30000)
    card = pick_mode_card(page, "공통 key로 연결하기")
    desc = text_of(card)
    btn = page.get_by_role("button", name=re.compile(r"^다음: key·가져올 열 설정"))
    pw_expect(btn).to_be_visible(timeout=10000)
    note = page.locator(".wk-mode-next p.wk-why[role=note]").first
    rec("2", "홈 결과 형태 카드 '공통 key로 연결하기' (설명·예시 문구)", "사용자별 한 행" in desc and "「연결 결과」" in desc and "열 매핑·검토 없이도" in desc,
        desc[:200])  # fmt: skip
    rec("2", "파일 1개: '다음' 버튼 비활성 + '누를 수 없음' 안내 (2개 이상)", btn.is_disabled() and "2개 이상" in text_of(note),
        {"button": text_of(btn), "note": text_of(note)})  # fmt: skip
    steps = text_of(page.locator("nav.wk-steps"))
    rec("2", "홈 단계 안내가 key 연결 흐름으로 바뀜 ('다음 화면 — key 열·가져올 열·범위')", "다음 화면 — key 열·가져올 열·범위" in steps and "공통 key 연결" in steps,
        steps[:300])  # fmt: skip
    page.goto(f"{URL}/?files={a},{b},{c},{d}")
    pw_expect(page.locator(".wk-val-summary")).to_contain_text("검증 대상 4개", timeout=30000)
    pick_mode_card(page, "공통 key로 연결하기")
    btn = page.get_by_role("button", name="다음: key·가져올 열 설정 (4개 파일)")
    pw_expect(btn).to_be_enabled(timeout=10000)
    shot(page, "desktop-2-home")
    btn.click()
    page.wait_for_url(re.compile(r"/keyjoin\?files="), timeout=30000)
    url = page.url
    rec("2", "파일 4개: '다음' → /keyjoin?files=…(고른 파일 번호)", url.endswith(f"/keyjoin?files={a},{b},{c},{d}"), url)
    pw_expect(page.locator(SEC)).to_be_visible(timeout=30000)
    legends = [text_of(x) for x in page.locator(f"{SEC} fieldset.bd-step > legend").all()]
    rec("2", "7단계 fieldset(legend 1~7)", [x.split(".")[0] for x in legends] == [str(n) for n in range(1, 8)], legends)
    rec("2", "이동 뒤 초점 (정보: focus to page)", None, active(page))


def step_base(page: Page) -> None:
    fids = FACTS["fids"]
    wait_meta(page, 4)
    sel0 = page.locator(KEY_SELECTS).nth(0)
    desc = sel0.get_attribute("aria-describedby") or ""
    desc_ok = bool(desc) and all(page.locator(f"[id='{i}']").count() == 1 and "key 열을 고르세요" in text_of(page.locator(f"[id='{i}']")) for i in desc.split())
    rec("3", "key 미선택: 2단계 key select에 aria-invalid=true + aria-describedby가 '고칠 것 … key 열을 고르세요' 문구(id)를 가리킴",
        sel0.get_attribute("aria-invalid") == "true" and desc_ok, {"aria_invalid": sel0.get_attribute("aria-invalid"), "describedby": desc})  # fmt: skip
    lis = page.locator(f"{FS[1]} ul.bd-files > li")
    metas = [text_of(lis.nth(i)) for i in range(4)]
    rec("3", "1단계: 파일마다 '#번호 이름 · 시트 1개 · 열 3개 · 데이터 3행' (시트 1개라 시트 select 없음)",
        all("시트 1개" in m and "열 3개" in m and "데이터 3행" in m for m in metas) and page.locator(f"{FS[1]} select").count() == 0, metas)  # fmt: skip
    configure(page, 4)
    sel_vals = [page.locator(KEY_SELECTS).nth(i).input_value() for i in range(4)]
    key_help = text_of(page.locator(f"{FS[2]} > p.mw-muted").first)
    rec("3", "2단계: 입력마다 key 열 select(단일) — 첫 열 선택·고정 설명 문구", sel_vals == ["c0"] * 4 and "정수 123은 글자 '123'과 연결되고" in key_help,
        {"values": sel_vals, "help": key_help[:120]})  # fmt: skip
    disabled = [page.locator(PICK_BLOCKS).nth(i).locator("input[type=checkbox]").nth(0).is_disabled() for i in range(4)]
    lists = [text_of(page.locator(f"{FS[3]} ol.kj-list").nth(i)) for i in range(4)]
    names = page.locator(f"{PICK_BLOCKS} ol.kj-list input.bd-input")  # key 결과 이름 input은 따로 (아래)
    placeholders = [names.nth(i).get_attribute("placeholder") for i in range(names.count())]
    key_name = page.locator(f"{FS[3]} input[aria-label='key 1 결과 이름']")
    rec("3", "3단계: key 열 체크 비활성('key 열은 결과에 이미 들어갑니다')·둘째 열 선택·결과 이름 input(placeholder=원본 열 이름)",
        all(disabled) and all("1. B열" in x for x in lists) and placeholders == ["부서", "교육 건수", "출장 건수", "처리 건수"]
        and page.locator(f"{FS[3]} .kj-cols label.mw-muted", has_text="key 열은 결과에 이미 들어갑니다").count() == 4
        and key_name.count() == 1 and key_name.get_attribute("placeholder") == "사번",
        {"disabled": disabled, "lists": lists, "placeholders": placeholders, "key_name_placeholder": key_name.get_attribute("placeholder")})  # fmt: skip
    radios = page.locator(f"{FS[4]} .kj-scope input[type=radio]")
    checked = [radios.nth(i).is_checked() for i in range(radios.count())]
    base_sel = page.locator(f"{FS[4]} select[aria-label='기준 입력 (파일·시트)']")
    rec("3", "4단계: 범위 라디오 3개 (기본 '기준 파일의 사용자만')·기준 입력 select (첫 입력)", checked == [True, False, False]
        and base_sel.input_value() == f"{fids[0]}:s0" and base_sel.locator("option").count() == 4,
        {"checked": checked, "base": base_sel.input_value(), "options": base_sel.locator("option").all_inner_texts()})  # fmt: skip
    before = page.locator(MAKE_BTN).get_attribute("aria-disabled")
    why0 = text_of(page.locator(f"{FS[6]} p.wk-why[role=note]"))
    rec("3", "확인 전: 6단계 '엑셀 만들기' aria-disabled + 사유 '5단계에서 연결을 먼저 확인하세요.'", before == "true" and "5단계에서 연결을 먼저 확인하세요" in why0,
        {"aria-disabled": before, "why": why0})  # fmt: skip
    status, plan = do_check(page)
    FACTS["plan_base"] = plan
    rec("3", "5단계 '연결 확인' → 200·ready·header·result(3행·5열·합 6·공통 1)·preview 3행·inputs[].keys", status == 200 and plan.get("ready") is True
        and plan["header"] == EXPECTED_HEADER and plan["result"] == {"rows": 3, "key_columns": 1, "columns": 5, "union": 6, "intersection": 1, "excluded": 0}
        and len(plan["preview"]["rows"]) == 3 and plan["preview"]["total"] == 3 and all(i["keys"] and i["keys"]["indexed"] == 3 for i in plan["inputs"])
        and all(i["rows"]["data"] == 3 for i in plan["inputs"]) and plan["problems"] == [] and len(plan["basis"]) == 64,
        {"status": status, "shape": plan_shape(plan), "result": plan.get("result"), "warnings": [w["code"] for w in plan.get("warnings", [])],
         "kinds": [k["kinds"] for i in plan["inputs"] for k in i["key_columns"]][:1]})  # fmt: skip
    plan_text = json.dumps(plan, ensure_ascii=False)
    rec("3", "plan 응답에 선택하지 않은 열 이름·값 없음", not any(f in plan_text for f in UNPICKED_NAMES)
        and not any(f'"{v}"' in plan_text for v in UNPICKED_VALUES if isinstance(v, str)), "")  # fmt: skip
    st = status_text(page)
    rec("3", "상태 줄(role=status) = 응답으로 만든 확인 결과 문구 (결과·열·미연결·참고·만들 수 있습니다)", st == summary_text(plan), {"shown": st, "want": summary_text(plan)})
    n_status = page.locator(f"{SEC} [role=status]").count()
    n_alert = page.locator(f"{SEC} [role=alert]").count()
    rec("3", "role=status는 영역 안에 하나(kj-status), role=alert는 없음 (차단 문제 없음)", n_status == 1 and n_alert == 0, {"status": n_status, "alert": n_alert})
    total = text_of(page.locator(f"{FS[5]} .bd-total"))
    prev = page.locator(f"{FS[5]} table.kj-preview tbody tr")
    keys = [text_of(prev.nth(i).locator("th[scope=row]")) for i in range(prev.count())]
    first = [text_of(x) for x in prev.nth(0).locator("td").all()]
    kinds_line = text_of(page.locator(f"{FS[2]} .kj-input").nth(0).locator("p.mw-muted", has_text="확인 결과"))
    rec("3", "확인 결과 표시: 결과 3행·열 5개·합집합 6·공통 1, 미리보기 3행(key 001·002·003 첫 열 th), 2단계에 kinds('글자 3 · 정수 0 · 빈 칸 0 · 병합 채움 0')",
        "결과 3행" in total and "열 5개" in total and "합집합 6" in total and "공통 1" in total and keys == ["001", "002", "003"]
        and first[:4] == ["총무", "10", "1", "100"] and "글자 3 · 정수 0 · 빈 칸 0 · 병합 채움 0" in kinds_line,
        {"total": total, "keys": keys, "first_row": first, "kinds": kinds_line})  # fmt: skip
    srcs = [text_of(prev.nth(i).locator("td").last) for i in range(prev.count())]
    rec("3", "미리보기 '원본 행 (입력 순)' = 입력 순서 자리에 맞춰 표시, 미연결 입력은 '—' (001: 2/2/2/2 · 002: 3/—/3/— · 003: 4/3/—/3)",
        srcs == ["2 / 2 / 2 / 2", "3 / — / 3 / —", "4 / 3 / — / 3"], srcs)  # fmt: skip
    sel_ok = page.locator(KEY_SELECTS).nth(0)
    rec("3", "key 선택 뒤: key select aria-invalid=false·aria-describedby 없음", sel_ok.get_attribute("aria-invalid") == "false"
        and sel_ok.get_attribute("aria-describedby") is None, {"aria_invalid": sel_ok.get_attribute("aria-invalid")})  # fmt: skip
    warn = page.locator(f"{FS[5]} .bd-warnings")
    rec("3", "참고(경고) 목록은 role=alert가 아닌 영역에 (미연결 안내 3건)", warn.count() == 1 and warn.get_attribute("role") is None
        and warn.locator("li").count() == len(plan["warnings"]), {"count": warn.locator("li").count(), "codes": [w["code"] for w in plan["warnings"]]})  # fmt: skip
    summ = text_of(page.locator(f"{FS[6]} dl.kj-summary"))
    rec("3", "6단계 설정 요약: 입력 4개·기준·범위·결과 열(확인 결과)", "입력 4개" in summ and "· 기준" in summ and SCOPE_LABEL["base"] in summ
        and " · ".join(EXPECTED_HEADER) in summ, summ[:300])  # fmt: skip
    shot(page, "desktop-3-plan", page.locator(FS[5]), full=True)
    runs_before = runs_count(page)
    status, run = do_make(page)
    FACTS["run_base"] = run.get("id")
    rec("3", "6단계 '엑셀 만들기' → 201 (새 실행)", status == 201 and run.get("status") == "done", {"status": status, "id": run.get("id"), "file": run.get("file_name")})
    check_run_record("3", "base", run, plan, fids, 3)
    st = status_text(page)
    made = text_of(page.locator(MADE))
    rec("3", "상태 줄 = '실행 #n 완료 — 결과 3행…' · 7단계에 실행·파일 이름·내려받기·실행 기록 링크·시트 순서", st == made_text(run)
        and f"실행 #{run['id']} 완료" in made and run["file_name"] in made and "시트 순서: 연결 결과 → 출처 → 연결 요약" in made
        and page.locator(MADE).get_by_role("link", name="내려받기").count() == 1
        and page.locator(MADE).get_by_role("link", name="실행 기록에서 보기").count() == 1, {"status": st, "made": made})  # fmt: skip
    rec("3", "실행 기록이 1건 늘어남 (GET /runs)", runs_count(page) == runs_before + 1, {"before": runs_before, "after": runs_count(page)})
    again = page.locator(f"{FS[6]} button", has_text="같은 설정으로 한 번 더 만들기")
    why_made = text_of(page.locator(f"{FS[6]} p.wk-why[role=note]"))
    rec("3", "만든 뒤 같은 설정: '엑셀 만들기' aria-disabled + 사유 '이미 만들었습니다 (실행 #n)' · '같은 설정으로 한 번 더 만들기' 단추 따로",
        page.locator(MAKE_BTN).get_attribute("aria-disabled") == "true" and f"이미 만들었습니다 (실행 #{run['id']})" in why_made and again.count() == 1,
        {"why": why_made, "again": again.count()})  # fmt: skip
    shot(page, "desktop-3-made", page.locator(FS[7]))
    path, suggested = download_made(page, f"keyjoin-base-{run['id']}.xlsx")
    direct = page.request.get(f"{URL}/api/runs/{run['id']}/download")
    same = hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(direct.body()).hexdigest()
    rec("3", "7단계 내려받기: 서버 파일 이름 그대로·API 본문과 같음", suggested == disposition_name(direct.headers.get("content-disposition", ""))
        and same and suggested == run["file_name"], {"suggested": suggested, "same_as_api": same})  # fmt: skip
    check_workbook("3", "base", path, fids)
    check_expected_json("3", "base", path)


def step_scope(item: str, page: Page, scope: str) -> None:
    fids = FACTS["fids"]
    prev_run = FACTS.get("run_base") if scope == "all" else FACTS.get("run_all")
    pick_scope(page, scope)
    pw_expect(page.locator(STATUS)).to_have_text(CHANGED_TEXT, timeout=10000)
    why = text_of(page.locator(f"{FS[6]} p.wk-why[role=note]"))
    stale = page.locator(f"{FS[5]} .bd-stale .bd-checking")
    warn_made = page.locator(f"{MADE} p.mapping-warn")
    rec(item, f"{scope}: 범위를 바꾸면 상태 줄 '{CHANGED_TEXT}'·만들기 비활성(사유 '다시 확인')·직전 결과는 '직전 확인 결과' 표시·7단계 '구성을 바꿨습니다'",
        page.locator(MAKE_BTN).get_attribute("aria-disabled") == "true" and "5단계에서 다시 확인하세요" in why and stale.count() == 1
        and warn_made.count() == 1, {"why": why, "stale": stale.count(), "made_warn": text_of(warn_made) if warn_made.count() else None})  # fmt: skip
    status, plan = do_check(page)
    FACTS[f"plan_{scope}"] = plan
    rows_exp, _, union, inter, excluded = SCOPES[scope]
    rec(item, f"{scope}: '연결 확인' → ready·결과 {len(rows_exp)}행·합 {union}·공통 {inter}·빠짐 {excluded}·header 그대로", status == 200 and plan["ready"]
        and plan["result"] == {"rows": len(rows_exp), "key_columns": 1, "columns": 5, "union": union, "intersection": inter, "excluded": excluded}
        and plan["header"] == EXPECTED_HEADER and plan["scope"] == scope, {"status": status, "result": plan.get("result"),
                                                                        "warnings": [w["code"] for w in plan.get("warnings", [])]})  # fmt: skip
    st = status_text(page)
    rec(item, f"{scope}: 상태 줄 = 확인 결과 문구", st == summary_text(plan), {"shown": st})
    prev = page.locator(f"{FS[5]} table.kj-preview tbody tr")
    keys = [text_of(prev.nth(i).locator("th[scope=row]")) for i in range(prev.count())]
    rec(item, f"{scope}: 미리보기 key 순서 {[r[0] for r in rows_exp]}", keys == [r[0] for r in rows_exp], keys)
    if scope == "common":
        scope_line = text_of(page.locator(f"{FS[4]} p.mw-muted", has_text="확인 결과"))
        rec(item, "common: 4단계에 '빠지는 사용자 5명'", "빠지는 사용자 5명" in scope_line and "합집합 6명" in scope_line and "공통 1명" in scope_line, scope_line)
    runs_before = runs_count(page)
    status, run = do_make(page)
    FACTS[f"run_{scope}"] = run.get("id")
    rec(item, f"{scope}: '엑셀 만들기' → 201·새 실행 번호", status == 201 and run.get("id") != prev_run, {"status": status, "id": run.get("id")})
    check_run_record(item, scope, run, plan, fids, len(rows_exp))
    rec(item, f"{scope}: 실행 기록 1건 늘어남", runs_count(page) == runs_before + 1, "")
    path, _ = download_made(page, f"keyjoin-{scope}-{run['id']}.xlsx")
    check_workbook(item, scope, path, fids)
    check_expected_json(item, scope, path)
    shot(page, f"desktop-{item}-{scope}", page.locator(FS[7]))


def step_runs(page: Page) -> None:
    page.goto(URL + "/runs")
    pw_expect(page.locator("table.runs-table")).to_be_visible(timeout=30000)
    hint = text_of(page.locator("section.runs p.upload-drop-hint").first)
    rec("6", "실행 기록 안내문에 'key 취합'", "key 취합" in hint, hint[:120])
    for scope in ("base", "all", "common"):
        run_id = FACTS.get(f"run_{scope}")
        if not run_id:
            rec("6", f"{scope} 실행 행", False, "앞 단계에서 만들지 못함")
            continue
        row = page.locator("table.runs-table tbody tr", has=page.locator("td[data-label='번호']", has_text=re.compile(rf"^#{run_id}$")))
        pw_expect(row).to_have_count(1, timeout=30000)
        way = row.locator("td[data-label='방식']")
        result_cell = row.locator("td[data-label='결과 파일']")
        rows = SCOPES[scope][0]
        rec("6", f"{scope} 실행 #{run_id}: 방식 열 'key 취합' + '범위 {SCOPE_SHORT[scope]}', title '{MODE_TITLE} · {SCOPE_LABEL[scope]}', 결과 열 '연결: 입력 4개 · 결과 {len(rows)}행 · 열 5개' + 내려받기",
            text_of(way).replace("\n", " ") == f"key 취합 범위 {SCOPE_SHORT[scope]}" and way.get_attribute("title") == f"{MODE_TITLE} · {SCOPE_LABEL[scope]}"
            and f"연결: 입력 4개 · 결과 {len(rows)}행 · 열 5개" in text_of(result_cell) and result_cell.get_by_role("link", name="내려받기").count() == 1
            and "완료" in text_of(row.locator("td[data-label='상태']")),
            {"way": text_of(way), "title": way.get_attribute("title"), "result": text_of(result_cell)[:160]})  # fmt: skip
    run_id = FACTS["run_base"]
    row = page.locator("table.runs-table tbody tr", has=page.locator("td[data-label='번호']", has_text=re.compile(rf"^#{run_id}$")))
    link = row.get_by_role("link", name="내려받기")
    direct = page.request.get(f"{URL}/api/runs/{run_id}/download")
    with page.expect_download(timeout=60000) as di:
        link.click()
    d = di.value
    path = OUT / "downloads" / f"runs-page-{run_id}.xlsx"
    d.save_as(str(path))
    same = hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(direct.body()).hexdigest()
    rec("6", f"실행 기록 화면의 내려받기 링크 (#{run_id}): 서버 이름 그대로·API 본문과 같음·오류 안내 없음",
        d.suggested_filename == disposition_name(direct.headers.get("content-disposition", "")) and same and row.locator(".run-download-problem").count() == 0,
        {"name": d.suggested_filename, "same": same})  # fmt: skip
    rec("6", "실행 기록 화면 가로 넘침 없음 (1280)", not overflow(page)["page_overflow_px"] > 0, overflow(page))
    shot(page, "desktop-6-runs", full=True)


def blocked_case(item: str, page: Page, label: str, fids: list[int], scope: str, code: str, picks: dict[int, list[int]] | None = None) -> dict:
    page.goto(f"{URL}/keyjoin?files={','.join(map(str, fids))}")
    wait_meta(page, len(fids))
    configure(page, len(fids), picks)
    if scope != "base":
        pick_scope(page, scope)
    before = runs_count(page)
    status, plan = do_check(page)
    alert = page.locator(ALERT)
    pw_expect(alert).to_be_visible(timeout=30000)
    a_text = text_of(alert)
    st = status_text(page)
    why = text_of(page.locator(f"{FS[6]} p.wk-why[role=note]"))
    codes = [p["code"] for p in plan.get("problems", [])]
    rec(item, f"{label}: plan 200·ready=false·problems {code}·응답에 header/result/preview/limits/budget 모두 있음", status == 200 and plan.get("ready") is False
        and code in codes and all(plan_shape(plan).values()), {"codes": codes, "shape": plan_shape(plan), "result": plan.get("result"),
                                                              "keys_null": [i["keys"] is None for i in plan["inputs"]]})  # fmt: skip
    rec(item, f"{label}: 차단 목록 role=alert에 서버 문구([{code}])·상태 줄 '만들 수 없는 이유 n건 … 문제를 해결해야'·만들기 비활성(사유)",
        f"[{code}]" in a_text and f"만들 수 없는 이유 {len(codes)}건" in st and "문제를 해결해야 만들 수 있습니다" in st
        and page.locator(MAKE_BTN).get_attribute("aria-disabled") == "true" and f"만들 수 없는 이유 {len(codes)}건을 먼저 해결하세요" in why,
        {"alert": a_text[:300], "status": st, "why": why})  # fmt: skip
    rec(item, f"{label}: 차단 문구에 key 원문 없음 (001~006·901·902)", not any(k in a_text for k in [*KEYS_ALL, "901", "902"]), "")
    rec(item, f"{label}: 화면 오류 없음 (pageerror 0)·렌더 유지(단계 7개·상태 줄 1개)", not [c for c in CONSOLE if c["type"] == "pageerror"]
        and page.locator(f"{SEC} fieldset.bd-step").count() == 7 and page.locator(f"{SEC} [role=status]").count() == 1,
        {"pageerrors": [c["text"] for c in CONSOLE if c["type"] == "pageerror"][:3]})  # fmt: skip
    body = {**request_body(fids, scope, {f: [f"c{j}" for j in js] for f, js in zip(fids, [(picks or {}).get(i, [1]) for i in range(len(fids))], strict=True)}),
            "expected_basis": plan["basis"], "request_id": secrets.token_hex(16)}  # fmt: skip
    r = page.request.post(f"{URL}/api/keyjoin/run", data=body)
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text()
    after = runs_count(page)
    rec(item, f"{label}: 같은 구성으로 run을 강제해도 409 KEYJOIN_NOT_READY(problems에 같은 문구)·실행 기록 수 그대로 (API 건수)",
        r.status == 409 and isinstance(detail, dict) and detail.get("code") == "KEYJOIN_NOT_READY" and any(f"[{code}]" in p for p in detail.get("problems", []))
        and after == before, {"status": r.status, "code": detail.get("code") if isinstance(detail, dict) else detail, "runs": [before, after]})  # fmt: skip
    shot(page, f"desktop-{item}-{label}", alert)
    return plan


def step_rejections(page: Page) -> None:
    a = FACTS["fids"][0]
    dup = "사번,점수,비고\n001,5,e1\n001,6,e2\n002,7,e3\n".encode("utf-8-sig")
    none = "사번,점수,비고\n901,1,f1\n902,2,f2\n".encode("utf-8-sig")
    e = api_upload(page, "E_중복key.csv", dup)
    f = api_upload(page, "F_공통없음.csv", none)
    FACTS["fid_dup"], FACTS["fid_none"] = e, f
    plan = blocked_case("7", page, "dup", [a, e], "base", "KEYJOIN_KEY_DUPLICATE")
    p = next((x for x in plan.get("problems", []) if x["code"] == "KEYJOIN_KEY_DUPLICATE"), {})
    rec("7", "dup: 문제 객체 — 입력 2·A열·rows [2,3]·count 2·groups 1·문구 '2행(1묶음, 2행·3행)'", p.get("input") == 1 and p.get("column") == "A"
        and p.get("rows") == [2, 3] and p.get("count") == 2 and p.get("groups") == 1 and "2행(1묶음, 2행·3행)" in p.get("text", ""), p)  # fmt: skip
    plan = blocked_case("7", page, "nocommon", [a, f], "common", "KEYJOIN_NO_COMMON")
    rec("7", "nocommon: 결과 0행·교집합 0·합집합 5 — 미리보기 '보일 행이 없습니다'", plan["result"]["rows"] == 0 and plan["result"]["intersection"] == 0
        and plan["result"]["union"] == 5 and page.locator(f"{FS[5]} p.mw-muted", has_text="보일 행이 없습니다").count() == 1, plan["result"])  # fmt: skip


def step_pii(page: Page) -> None:
    """싼 검사에서 차단된 응답(keys·kinds null, preview 없음)의 렌더 + 개인정보 확인 뒤 자동 재확인 + 가림·다운로드."""
    a = FACTS["fids"][0]
    rows = "사번,휴대전화,메모\n001,010-0000-0001,g1\n002,010-0000-0002,g2\n003,010-0000-0003,g3\n".encode("utf-8-sig")
    g = api_upload(page, "G_전화.csv", rows)
    FACTS["fid_pii"] = g
    pii = page.request.get(f"{URL}/api/files").json()
    mine = next((x for x in pii if x["id"] == g), {})
    rec("7", "pii 준비: 전화번호 열이 든 파일이 '개인정보 확인 필요'로 올라감 (API)", bool(mine.get("pii")) and not mine["pii"].get("acknowledged"),
        {"pii": mine.get("pii", {}).get("counts") if mine.get("pii") else None}, kind="setup")  # fmt: skip
    page.goto(f"{URL}/keyjoin?files={a},{g}")
    wait_meta(page, 2)
    configure(page, 2)
    before = runs_count(page)
    status, plan = do_check(page)
    inp = plan["inputs"][1]
    rec("7", "pii: 싼 검사 차단 — plan 200·ready=false·KEYJOIN_PII_ACK_REQUIRED·inputs[1].keys=null·key_columns[].kinds=null·preview.rows=[]·result/budget/limits 있음",
        status == 200 and plan["ready"] is False and [p["code"] for p in plan["problems"]] == ["KEYJOIN_PII_ACK_REQUIRED"] and inp["keys"] is None
        and inp["key_columns"][0]["kinds"] is None and plan["preview"]["rows"] == [] and all(plan_shape(plan).values()) and inp["pii"] == "needs_ack",
        {"shape": plan_shape(plan), "result": plan.get("result"), "budget": plan.get("budget"), "pii": inp["pii"]})  # fmt: skip
    table = page.locator(f"{FS[5]} table[aria-label='입력별 확인 결과'] tbody tr").nth(1)
    kinds_line = text_of(page.locator(f"{FS[2]} .kj-input").nth(1).locator("p.mw-muted", has_text="확인 결과"))
    empty = page.locator(f"{FS[5]} p.mw-muted", has_text="보일 행이 없습니다")
    st = status_text(page)
    rec("7", "pii: 차단 응답 렌더 — 표에 '분석 전'·'개인정보 확인 필요', 2단계 kinds '분석 전', 미리보기 '보일 행이 없습니다 — 위 문제를 해결하면 key를 분석합니다', 상태 줄 '입력 2 -', 화면 오류 0",
        "분석 전" in text_of(table) and "개인정보 확인 필요" in text_of(table) and "분석 전" in kinds_line and "위 문제를 해결하면 key를 분석합니다" in text_of(empty)
        and "입력 2 -" in st and st == summary_text(plan) and not [c for c in CONSOLE if c["type"] == "pageerror"],
        {"row": text_of(table)[:200], "kinds": kinds_line, "status": st})  # fmt: skip
    notice = page.locator(f"{FS[1]} .pii-notice[role=alert]")
    alerts = page.locator(f"{SEC} [role=alert]").count()
    rec("7", "pii: 1단계에 개인정보 안내(role=alert, 가린 위치)·확인 버튼 — role=alert는 차단 문제 2곳뿐", notice.count() == 1 and alerts == 2
        and "개인정보 의심 패턴 발견" in text_of(notice) and not scan(page, [["합성 전화번호", PHONE]]), {"alerts": alerts, "notice": text_of(notice)[:120]})  # fmt: skip
    shot(page, "desktop-7-pii-blocked", notice)
    with page.expect_response(is_post("/api/keyjoin/plan"), timeout=180000) as ri:
        notice.get_by_role("button", name="개인정보 포함을 확인했습니다").click()
    plan2 = ri.value.json()
    pw_expect(page.locator(STATUS)).to_have_text(summary_text(plan2), timeout=30000)
    rec("7", "pii: 확인 버튼 → 자동 재확인 1회 → ready·KEYJOIN_PII_IN_RESULT 경고·keys 분석됨·상태 줄 갱신", plan2["ready"] is True
        and "KEYJOIN_PII_IN_RESULT" in [w["code"] for w in plan2["warnings"]] and plan2["inputs"][1]["keys"]["indexed"] == 3
        and page.locator(ALERT).count() == 0, {"warnings": [w["code"] for w in plan2["warnings"]], "result": plan2["result"]})  # fmt: skip
    cell = page.locator(f"{FS[5]} table.kj-preview tbody tr").nth(0).locator("td").nth(1)
    hits = scan(page, [["합성 전화번호", PHONE]])
    rec("7", "가림: 미리보기의 전화번호 칸은 가린 값(원문 아님), 화면·plan 응답에 원문 없음", text_of(cell) not in ("", PHONE) and not hits
        and PHONE not in json.dumps(plan2, ensure_ascii=False), {"shown": text_of(cell), "hits": hits})  # fmt: skip
    status, run = do_make(page)
    after = runs_count(page)
    rec("7", "pii 확인 뒤 만들기 201·실행 기록 +1", status == 201 and after == before + 1, {"status": status, "id": run.get("id")})
    path, _ = download_made(page, f"keyjoin-pii-{run.get('id')}.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = [[c.value for c in r] for r in wb[SHEETS[0]].iter_rows()]
    wb.close()
    col = rows[0].index("휴대전화") if "휴대전화" in rows[0] else -1
    raw_cell = rows[1][col] if col >= 0 and len(rows) > 1 else None
    record_text = json.dumps(run_json(page, run["id"]), ensure_ascii=False)
    rec("7", "다운로드: 결과 파일의 전화번호 열에는 원래 값(합성 전화번호) 그대로, 실행 기록(GET /runs/{id})에는 없음", raw_cell == PHONE and PHONE not in record_text,
        {"header": rows[0], "raw_in_file": raw_cell == PHONE, "raw_in_record": PHONE in record_text})  # fmt: skip
    FACTS["run_pii"] = run.get("id")


def step_focus(page: Page) -> None:
    a, b, c, _ = FACTS["fids"]
    page.goto(f"{URL}/keyjoin?files={a},{b},{c}")
    wait_meta(page, 3)
    configure(page, 3)
    # 열 재선택: 체크 상자 Space 두 번, key select 화살표 — 초점 그대로
    cb = page.locator(PICK_BLOCKS).nth(0).locator("input[type=checkbox]").nth(2)
    cb.focus()
    mark_active(page)
    page.keyboard.press("Space")
    s1 = same_active(page) and cb.is_checked()
    page.keyboard.press("Space")
    s2 = same_active(page) and not cb.is_checked()
    sel = page.locator(KEY_SELECTS).nth(0)
    sel.focus()
    mark_active(page)
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(200)
    moved = sel.input_value()
    s3 = same_active(page)
    picked_after = text_of(page.locator(f"{FS[3]} .kj-input").nth(0))
    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(200)
    s4 = same_active(page) and sel.input_value() == "c0"
    rec("8", "열 재선택 때 초점 불변: 체크 상자 Space(켜고 끔)·key select 화살표(c1로 바꾸면 가져올 열에서 빠짐, 되돌림) — document.activeElement 그대로",
        s1 and s2 and s3 and s4 and moved == "c1" and "1. B열" not in picked_after, {"space": [s1, s2], "arrow": [s3, s4], "moved_to": moved,
                                                                                    "picked_block_after_key_c1": picked_after[:160]})  # fmt: skip
    page.locator(PICK_BLOCKS).nth(0).locator("input[type=checkbox]").nth(1).check()
    # 늦은 응답: plan 응답을 붙들고 설정을 바꾼 뒤 풀어 준다 → 늦은 응답은 버려진다 (STALE_TEXT)
    held: list = []
    page.route("**/api/keyjoin/plan", lambda route: held.append(route))
    try:
        btn = page.locator(CHECK_BTN)
        btn.focus()
        mark_active(page)
        page.keyboard.press("Enter")
        pw_expect(page.locator(STATUS)).to_have_text(CHECKING_TEXT, timeout=10000)
        page.wait_for_function("() => true")
        during = {"held": len(held), "focus_same": same_active(page), "aria_disabled": btn.get_attribute("aria-disabled"),
                  "busy": page.locator(FS[5]).get_attribute("aria-busy"), "label": text_of(btn)}  # fmt: skip
        pick_scope(page, "all")  # 응답을 기다리는 동안 설정을 바꾼다
        mark_active(page)
        assert held, "plan 요청이 가로채기에 잡히지 않음"
        with page.expect_response(is_post("/api/keyjoin/plan"), timeout=180000) as ri:
            held[0].continue_()
        late = ri.value.json()
        pw_expect(page.locator(STATUS)).to_have_text(STALE_TEXT, timeout=30000)
        after = {"status": status_text(page), "focus_same": same_active(page), "make_disabled": page.locator(MAKE_BTN).get_attribute("aria-disabled"),
                 "why": text_of(page.locator(f"{FS[6]} p.wk-why[role=note]")), "bd_total": page.locator(f"{FS[5]} .bd-total").count(),
                 "late_scope": late.get("scope"), "late_rows": late.get("result", {}).get("rows")}  # fmt: skip
    finally:
        page.unroute("**/api/keyjoin/plan")
    rec("8", "'연결 확인' 중: 버튼 aria-disabled=true(disabled 아님)·영역 aria-busy·라벨 '확인 중…'·초점 그대로", during["held"] == 1 and during["focus_same"]
        and during["aria_disabled"] == "true" and during["busy"] == "true" and during["label"] == "확인 중…", during)  # fmt: skip
    rec("8", "늦은 응답(이전 설정 base 3행)이 화면을 덮지 않음: 상태 줄 STALE 문구·결과 영역 없음·만들기 비활성('먼저 확인')·초점 그대로",
        after["status"] == STALE_TEXT and after["focus_same"] and after["make_disabled"] == "true" and "5단계에서 연결을 먼저 확인하세요" in after["why"]
        and after["bd_total"] == 0 and after["late_scope"] == "base", after)  # fmt: skip
    status, plan = do_check(page)
    rec("8", "늦은 응답 뒤 다시 확인하면 지금 설정(all, A·B·C → 5행)으로 결과", status == 200 and plan["scope"] == "all" and plan["result"]["rows"] == 5
        and status_text(page) == summary_text(plan), plan.get("result"))  # fmt: skip
    shot(page, "desktop-8-late", page.locator(FS[5]))
    # 파일 빼기: 키보드로 셋째 파일의 '이 파일 빼기' → 주소·설정에서 빠지고 초점을 다른 곳으로 옮기지 않음
    remove = page.locator(f"{FS[1]} ul.bd-files > li").nth(2).get_by_role("button", name="이 파일 빼기")
    kb = tab_to(page, remove, back=True)  # 5단계 버튼에서 Shift+Tab으로 거슬러 올라간다
    scroll_before = page.evaluate("() => window.scrollY")
    page.keyboard.press("Enter")
    pw_expect(page.locator(f"{FS[1]} ul.bd-files > li")).to_have_count(2, timeout=10000)
    try:  # 초점 이동이 자리 잡을 짧은 시간 (못 옮기면 아래 판정이 body로 기록한다)
        page.wait_for_function("() => document.activeElement && document.activeElement.tagName === 'LEGEND'", timeout=2000)
    except Exception:  # noqa: BLE001 — 판정은 아래 rec가 한다
        pass
    draft = page.evaluate("() => JSON.parse(sessionStorage.getItem('moajwo.keyjoin.draft') || 'null')")
    act = active(page)
    page.keyboard.press("Tab")
    next_focus = active(page)
    from urllib.parse import unquote

    rec("8", "파일 빼기(키보드 Enter): 주소 files=A,B·입력 2개·저장 설정에서 그 파일 제거·스크롤 그대로·초점은 body로 떨어지지 않고 1단계 제목(legend)으로",
        unquote(page.url).endswith(f"files={a},{b}") and draft and draft.get("files") == f"{a},{b}" and not any(k.startswith(f"{c}:") for k in draft["draft"]["inputs"])
        and f"{a}:s0" in draft["draft"]["inputs"] and page.locator(KEY_SELECTS).count() == 2 and act.startswith("LEGEND") and "1. 파일·시트" in act
        and page.evaluate("() => window.scrollY") == scroll_before,
        {"url": page.url, "tab": kb, "draft_inputs": sorted(draft["draft"]["inputs"]) if draft else None, "active_after": act,
         "next_tab_lands_on": next_focus, "status": status_text(page)})  # fmt: skip
    shot(page, "desktop-8-removed")


def step_keyboard(page: Page) -> None:
    """390×844, 키보드만: 홈 카드 → 다음 → key 열(화살표) → 가져올 열(Space) → 범위(화살표) → 연결 확인(Enter) → 만들기(Enter) → 내려받기(Enter)."""
    a, b = FACTS["fids"][:2]
    presses: dict[str, object] = {}
    page.goto(f"{URL}/?files={a},{b}")
    pw_expect(page.locator(".wk-val-summary")).to_contain_text("검증 대상 2개", timeout=30000)
    radio = page.locator("input[name='wk-result-mode'][value='keyjoin']")
    # 라디오 묶음은 Tab이 고른 항목(기본 '하나의 통합표')에만 서고, 항목 사이는 화살표로 옮긴다
    presses["card"] = tab_to(page, page.locator("input[name='wk-result-mode']:checked"))
    for _ in range(3):
        if radio.is_checked():
            break
        page.keyboard.press("ArrowDown")
    pw_expect(radio).to_be_checked()
    presses["card"]["arrows"] = "ArrowDown ×2 (통합표 → 파일별 시트 → 공통 key)"
    nxt = page.get_by_role("button", name="다음: key·가져올 열 설정 (2개 파일)")
    presses["next"] = tab_to(page, nxt)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/keyjoin\?files="), timeout=30000)
    wait_meta(page, 2)
    presses["focus_after_navigate"] = active(page)
    for i in range(2):
        sel = page.locator(KEY_SELECTS).nth(i)
        presses[f"key{i + 1}"] = tab_to(page, sel)
        page.keyboard.press("ArrowDown")
        pw_expect(sel).to_have_value("c0", timeout=5000)
        cb = page.locator(PICK_BLOCKS).nth(i).locator("input[type=checkbox]").nth(1)
        presses[f"pick{i + 1}"] = tab_to(page, cb)
        page.keyboard.press("Space")
        pw_expect(cb).to_be_checked()
    name_input = page.locator(f"{FS[3]} ol.kj-list input.bd-input").nth(0)
    presses["name"] = tab_to(page, name_input)
    page.keyboard.type("부서명")  # 한글: keyboard.type (IME 조합은 흉내 내지 않음)
    radios = page.locator(f"{FS[4]} .kj-scope input[type=radio]")
    presses["scope"] = tab_to(page, radios.nth(0))
    page.keyboard.press("ArrowDown")
    pw_expect(radios.nth(1)).to_be_checked()
    page.keyboard.press("ArrowUp")
    pw_expect(radios.nth(0)).to_be_checked()
    base_sel = page.locator(f"{FS[4]} select[aria-label='기준 입력 (파일·시트)']")
    presses["base"] = tab_to(page, base_sel)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    pw_expect(base_sel).to_have_value(f"{a}:s0")
    presses["check"] = tab_to(page, page.locator(CHECK_BTN))
    mark_active(page)
    status, plan = do_check(page, keyboard=True)
    focus_check = same_active(page)
    rec("9", "390px 키보드: 카드 Space → 다음 Enter → key 열 ArrowDown → 가져올 열 Space → 결과 이름 입력 → 범위 Arrow → 기준 Arrow → '연결 확인' Enter (초점 그대로)",
        status == 200 and plan["ready"] and plan["header"] == ["사번", "부서명", "교육 건수"] and focus_check,
        {"presses": presses, "header": plan.get("header"), "focus_same_after_check": focus_check})  # fmt: skip
    rec("9", "390px 상태 줄 = 확인 결과 문구", status_text(page) == summary_text(plan), status_text(page))
    o = overflow(page)
    rec("9", "390px 확인 결과까지 그린 화면 가로 넘침 0", o["page_overflow_px"] <= 0 and not o["outside"], o)
    shot(page, "narrow-9-plan", full=True)
    presses["make"] = tab_to(page, page.locator(MAKE_BTN))
    mark_active(page)
    status, run = do_make(page, keyboard=True)
    focus_make = same_active(page)
    rec("9", "390px '엑셀 만들기' Enter → 201·초점 그대로·상태 줄 '실행 #n 완료'", status == 201 and focus_make and status_text(page) == made_text(run),
        {"id": run.get("id"), "focus_same": focus_make, "presses": presses["make"]})  # fmt: skip
    link = page.locator(MADE).get_by_role("link", name="내려받기")
    presses["download"] = tab_to(page, link)
    path, suggested = download_made(page, f"keyboard-{run.get('id')}.xlsx", keyboard=True)
    wb = openpyxl.load_workbook(path, read_only=True)
    sheets, header = wb.sheetnames, [c.value for c in next(wb[SHEETS[0]].iter_rows(max_row=1))]
    wb.close()
    rec("9", "390px 7단계 '내려받기' Enter → xlsx(시트 3개, 머리글에 사용자 결과 이름 '부서명')", sheets == SHEETS and header == ["사번", "부서명", "교육 건수"]
        and suggested == run.get("file_name"), {"sheets": sheets, "header": header, "presses": presses["download"]})  # fmt: skip
    FACTS["run_keyboard"] = run.get("id")
    o = overflow(page)
    rec("9", "390px 만든 뒤 화면 가로 넘침 0", o["page_overflow_px"] <= 0 and not o["outside"], o)
    shot(page, "narrow-9-made", full=True)
    go = page.locator(MADE).get_by_role("link", name="실행 기록에서 보기")
    presses["runs"] = tab_to(page, go)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/runs$"), timeout=30000)
    pw_expect(page.locator("table.runs-table")).to_be_visible(timeout=30000)
    row = page.locator("table.runs-table tbody tr", has=page.locator("td[data-label='번호']", has_text=re.compile(rf"^#{run['id']}$")))
    o = overflow(page)
    rec("9", "390px '실행 기록에서 보기' Enter → /runs에 그 실행(key 취합)·가로 넘침 0", row.count() == 1 and "key 취합" in text_of(row) and o["page_overflow_px"] <= 0,
        {"focus_after": active(page), "overflow": o})  # fmt: skip
    shot(page, "narrow-9-runs", full=True)


def seed_draft(page: Page, fids: list[int]) -> None:
    """이 브라우저 문맥에 설정 초안을 둔다 (제품의 sessionStorage 규칙 'moajwo.keyjoin.draft' — 선택은 ID로만)."""
    draft = {"files": ",".join(map(str, fids)),
             "draft": {"sheets": {}, "inputs": {f"{f}:s0": {"key_columns": ["c0"], "columns": [{"column_id": "c1"}]} for f in fids},
                       "scope": "base", "base": None}}  # fmt: skip
    page.goto(URL + "/runs")
    page.evaluate("(d) => sessionStorage.setItem('moajwo.keyjoin.draft', JSON.stringify(d))", draft)


def axe_run(page: Page) -> dict | None:
    """axe는 문맥의 init script로 넣는다 (CSP script-src 'self'를 그대로 둔 채 — add_script_tag는 CSP에 막힌다)."""
    if not AXE.exists() or not page.evaluate("() => typeof axe !== 'undefined'"):
        return None
    res = page.evaluate("() => axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
    return {"violations": [[v["id"], v["impact"], len(v["nodes"]), [n["target"][0] for n in v["nodes"]][:3]] for v in res["violations"]],
            "incomplete": sorted({v["id"] for v in res["incomplete"]})}  # fmt: skip


def step_responsive(br) -> None:
    fids = FACTS["fids"]
    for width in (320, 390, 1366):
        cx, page = new_page(br, width, 900, f"반응형 {width}", axe=True)
        try:
            login(page, USER)
            page.goto(f"{URL}/?files={fids[0]},{fids[1]}")
            pw_expect(page.locator(".wk-val-summary")).to_contain_text("검증 대상 2개", timeout=30000)
            pick_mode_card(page, "공통 key로 연결하기")
            o = overflow(page)
            ax = axe_run(page)
            rec("10", f"{width}px 홈(key 카드 선택) 가로 넘침 0" + (" · axe wcag2a/aa 위반 0" if ax else ""), o["page_overflow_px"] <= 0 and not o["outside"]
                and (ax is None or not ax["violations"]), {"overflow": o, "axe": ax})  # fmt: skip
            page.screenshot(path=str(OUT / f"reflow-{width}-home.png"), full_page=True)
            seed_draft(page, fids)
            page.goto(f"{URL}/keyjoin?files={','.join(map(str, fids))}")
            wait_meta(page, 4)
            restored = [page.locator(KEY_SELECTS).nth(i).input_value() for i in range(4)]
            status, plan = do_check(page)
            o = overflow(page)
            ax = axe_run(page)
            rec("10", f"{width}px /keyjoin 확인 결과까지(표·미리보기 포함) 가로 넘침 0 · 저장 초안 복원(key 열 4개)" + (" · axe 위반 0" if ax else " (axe 없음 — 미수행)"),
                o["page_overflow_px"] <= 0 and not o["outside"] and restored == ["c0"] * 4 and status == 200 and plan.get("ready")
                and (ax is None or not ax["violations"]), {"overflow": o, "restored": restored, "axe": ax})  # fmt: skip
            page.screenshot(path=str(OUT / f"reflow-{width}-keyjoin.png"), full_page=True)
            page.goto(URL + "/runs")
            pw_expect(page.locator("table.runs-table")).to_be_visible(timeout=30000)
            o = overflow(page)
            ax = axe_run(page)
            rec("10", f"{width}px 실행 기록(key 취합 행 포함) 가로 넘침 0" + (" · axe 위반 0" if ax else ""), o["page_overflow_px"] <= 0 and not o["outside"]
                and (ax is None or not ax["violations"]), {"overflow": o, "axe": ax})  # fmt: skip
        finally:
            cx.close()
    if not AXE.exists():
        rec("10", "axe(wcag2a/aa) 검사", None, f"미수행 — axe.min.js 없음 ({AXE})", kind="info")


def step_long_names(br) -> None:
    a = FACTS["fids"][0]
    long_file = "긴이름_" + "가" * 120 + ".csv"
    long_col = "열" * 100
    cx, page = new_page(br, 320, 900, "긴 이름 320")
    try:
        login(page, USER)
        fid = api_upload(page, long_file, f"사번,{long_col},비고\n001,1,x\n002,2,y\n".encode("utf-8-sig"))
        page.goto(f"{URL}/keyjoin?files={a},{fid}")
        wait_meta(page, 2)
        name_el = page.locator(f"{FS[1]} ul.bd-files > li").nth(1).locator(".bd-file-name")
        m_file = measure(name_el)
        configure(page, 2)
        col_el = page.locator(PICK_BLOCKS).nth(1).locator("label").nth(1).locator(".kj-name")
        m_col = measure(col_el)
        list_el = page.locator(f"{FS[3]} ol.kj-list").nth(1).locator(".kj-name").first
        m_list = measure(list_el)
        o1 = overflow(page)
        status, plan = do_check(page)
        o2 = overflow(page)
        th = page.locator(f"{FS[5]} table.kj-preview thead th").nth(2)
        wrap = page.locator(f"{FS[5]} .upload-table-wrap").nth(1)
        in_scroller = wrap.evaluate("(e) => getComputedStyle(e).overflowX")
        rec("10", "320px 긴 파일 이름(127자)·긴 열 이름(100자): 이름 요소가 줄바꿈(overflow-wrap anywhere, 잘림·넘침 없음)·페이지 가로 넘침 0 (확인 전·후)",
            m_file["ok"] and m_col["ok"] and m_list["ok"] and o1["page_overflow_px"] <= 0 and o2["page_overflow_px"] <= 0 and not o2["outside"]
            and status == 200 and plan.get("ready") and m_file["line_rects"] > 1 and m_col["line_rects"] > 1,
            {"file": {k: m_file[k] for k in ("ok", "line_rects", "overflow_wrap", "right", "viewport")},
             "column": {k: m_col[k] for k in ("ok", "line_rects", "overflow_wrap", "right")}, "list": m_list["ok"],
             "overflow_before": o1, "overflow_after": o2, "preview_th_lines": th.evaluate(MEASURE_JS)["line_rects"], "table_wrap_overflow_x": in_scroller})  # fmt: skip
        page.screenshot(path=str(OUT / "reflow-320-longnames.png"), full_page=True)
    finally:
        cx.close()


def step_zoom(p) -> None:
    """실제 브라우저 확대 200% (1280×800 창, zoomext의 chrome.tabs.setZoom) — CDP 캡처. 확장을 못 쓰면 640px·DPR 2로 대신한다."""
    fids = FACTS["fids"]
    cx = None
    try:
        cx = p.chromium.launch_persistent_context(tempfile.mkdtemp(prefix="kj-zoom-"), channel="msedge", headless=True,
                                                  viewport={"width": 1280, "height": 800}, locale="ko-KR",
                                                  args=[f"--disable-extensions-except={ZOOMEXT}", f"--load-extension={ZOOMEXT}"])  # fmt: skip
        cx.route("**/*", with_csp)
        page = cx.pages[0] if cx.pages else cx.new_page()
        page.goto(URL + "/")
        sw = cx.service_workers[0] if cx.service_workers else cx.wait_for_event("serviceworker", timeout=20000)
        login(page, USER)
        seed_draft(page, fids)
        page.goto(f"{URL}/keyjoin?files={','.join(map(str, fids))}")
        wait_meta(page, 4)
        sw.evaluate("chrome.tabs.query({}).then(ts => Promise.all(ts.map(t => chrome.tabs.setZoom(t.id, 2.0))))")
        page.wait_for_timeout(800)
        css = page.evaluate("() => [innerWidth, devicePixelRatio]")
        status, plan = do_check(page)
        o = overflow(page)
        cdp = cx.new_cdp_session(page)
        cs = cdp.send("Page.getLayoutMetrics")["contentSize"]
        data = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                   "clip": {"x": 0, "y": 0, "width": cs["width"], "height": min(cs["height"], 8000), "scale": 1}})  # fmt: skip
        (OUT / "zoom200-keyjoin.png").write_bytes(base64.b64decode(data["data"]))
        rec("11", "200% 확대(실제 브라우저 확대, CSS 폭 640): 확인 결과까지 가로 넘침 0·캡처 zoom200-keyjoin.png", css[0] <= 641 and css[1] >= 2
            and o["page_overflow_px"] <= 0 and not o["outside"] and status == 200, {"innerWidth_dpr": css, "overflow": o})  # fmt: skip
    except Exception as exc:  # noqa: BLE001 — 확장 로드 실패 등은 대체 방법으로
        rec("11", "200% 확대: 확장 방식 실패 → 640px·DPR 2 문맥으로 대신 (정보)", None, f"{type(exc).__name__}: {str(exc)[:200]}", kind="info")
        if cx is not None:
            cx.close()
            cx = None
        br = p.chromium.launch(channel="msedge", headless=True)
        c2 = br.new_context(viewport={"width": 640, "height": 800}, device_scale_factor=2, locale="ko-KR")
        c2.route("**/*", with_csp)
        page = c2.new_page()
        login(page, USER)
        seed_draft(page, fids)
        page.goto(f"{URL}/keyjoin?files={','.join(map(str, fids))}")
        wait_meta(page, 4)
        status, plan = do_check(page)
        o = overflow(page)
        page.screenshot(path=str(OUT / "zoom200-keyjoin-fallback.png"), full_page=True)
        rec("11", "200% 상당(640 CSS px · DPR 2): 확인 결과까지 가로 넘침 0", o["page_overflow_px"] <= 0 and not o["outside"] and status == 200, o)
        c2.close()
        br.close()
    finally:
        if cx is not None:
            cx.close()


# ---------------------------------------------------------------- 기존 기능 스모크 (같은 스택·같은 계정)


def open_review(page: Page, fids: list[int]) -> tuple[int, dict | str]:
    page.goto(f"{URL}/?files={','.join(map(str, fids))}")
    pw_expect(page.locator(".wk-val-summary")).to_contain_text(f"검증 대상 {len(fids)}개", timeout=30000)
    pick_mode_card(page, "하나의 통합표로 만들기")
    btn = page.get_by_role("button", name="사전 검토 열기 (통합 전)")
    pw_expect(btn).to_be_enabled(timeout=30000)
    with page.expect_response(is_post("/api/reviews"), timeout=180000) as ri:
        btn.click()
    resp = ri.value
    if resp.status == 201:
        pw_expect(page.locator(f"{PANEL} .review-score")).to_be_visible(timeout=60000)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text()
    return resp.status, body


def approve_all(page: Page) -> int:
    lis = page.locator(f"{PANEL} .review-group.is-pending ul.review-items > li")
    done = 0
    for i in range(lis.count()):
        li = lis.nth(i)
        btn = li.locator("button.review-approve")
        if btn.get_attribute("aria-pressed") == "true":
            continue
        pw_expect(btn).to_be_enabled(timeout=30000)
        with page.expect_response(lambda r: "/items/" in r.url and r.url.endswith("/decision"), timeout=60000):
            btn.click()
        pw_expect(li.locator(".review-state")).to_contain_text("승인됨", timeout=30000)
        done += 1
    return done


def complete_review(page: Page) -> tuple[dict, str]:
    panel = page.locator(PANEL)
    btn = panel.locator(".review-footer .review-actions button.mapping-run")
    pw_expect(btn).to_be_enabled(timeout=30000)
    with page.expect_response(lambda r: r.url.endswith("/complete"), timeout=60000) as ri:
        btn.click()
    msg = panel.locator(".review-footer p.mapping-message[role=status]")
    pw_expect(msg).to_contain_text("검토 완료", timeout=30000)
    return ri.value.json(), text_of(msg)


def integrate(page: Page) -> tuple[int, dict | str]:
    box = page.locator(f"{PANEL} .integration")
    btn = box.locator("button.mapping-run")
    pw_expect(btn).to_be_enabled(timeout=30000)
    with page.expect_response(lambda r: r.url.endswith("/integrate"), timeout=180000) as ri:
        btn.click()
    resp = ri.value
    if resp.status == 201:
        pw_expect(box.locator(".integration-latest")).to_be_visible(timeout=30000)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text()
    return resp.status, body


def step_smoke_integration(page: Page) -> None:
    x = api_upload(page, "통합1.csv", "기관명,인구수\n행정안전부,1\n보건복지부,2\n".encode("utf-8-sig"))
    y = api_upload(page, "통합2.csv", "기관명,인구수\n기획재정부,3\n".encode("utf-8-sig"))
    for f in (x, y):
        api_decide(page, f, {0: "org_name", 1: "population"})
    FACTS["smoke_x"], FACTS["smoke_y"] = x, y
    status, body = open_review(page, [x, y])
    assert status == 201, (status, str(body)[:300])
    rid = body["id"]
    approved = approve_all(page)
    resp, msg = complete_review(page)
    status2, run = integrate(page)
    FACTS["run_int"] = run.get("id") if isinstance(run, dict) else None
    rec("12", "행 이어 붙이기 통합: 검토 만들기 201 → 항목 승인 → 검토 완료(role=status 문구) → 통합 201 done", status2 == 201 and isinstance(run, dict)
        and run.get("status") == "done" and run["summary"].get("mode") not in ("sheets", "keyjoin") and "검토 완료" in msg,
        {"review": rid, "approved": approved, "complete": msg[:80], "run": FACTS["run_int"], "row_count": run.get("row_count") if isinstance(run, dict) else None})  # fmt: skip
    shot(page, "desktop-12-integrate", page.locator(f"{PANEL} .integration"))
    # 검토 재검사: 원본을 고친 파일로 바꿔 다시 검사 → 새 검토(201), 이전 검토는 대체됨
    x2 = api_upload(page, "통합1_수정.csv", "기관명,인구수\n행정안전부,11\n보건복지부,22\n".encode("utf-8-sig"))
    api_decide(page, x2, {0: "org_name", 1: "population"})
    status, body = open_review(page, [x])
    assert status == 201, (status, str(body)[:300])
    old = body["id"]
    panel = page.locator(PANEL)
    panel.locator("details.review-replace > summary").click()
    sel = panel.locator("select[aria-label$='대신 검사할 파일']")
    sel.select_option(str(x2))
    btn = panel.locator(".review-footer .review-actions button.mapping-confirm", has_text="다시 검사")
    with page.expect_response(is_post(f"/api/reviews/{old}/recheck"), timeout=180000) as ri:
        btn.click()
    new = ri.value.json() if ri.value.status == 201 else {}
    pw_expect(panel.locator(".review-head h2")).to_contain_text(f"#{new.get('id', 0)}", timeout=60000)
    old_state = page.request.get(f"{URL}/api/reviews/{old}").json()
    rec("12", "검토 재검사(고친 파일로 바꿔 다시 검사): 201 새 검토·패널이 새 번호·이전 검토는 superseded", ri.value.status == 201 and new.get("id") != old
        and old_state.get("status") == "superseded", {"old": old, "new": new.get("id"), "old_status": old_state.get("status")})  # fmt: skip


def step_smoke_bundle(page: Page) -> None:
    x = FACTS["smoke_x"]
    page.goto(f"{URL}/bundle?files={x}")
    btn = page.get_by_role("button", name=re.compile(r"^엑셀 만들기"))
    pw_expect(btn).to_be_enabled(timeout=60000)
    with page.expect_response(is_post("/api/bundle/run"), timeout=180000) as ri:
        btn.click()
    off = ri.value.json()
    pw_expect(page.locator("section.bundle .bd-made")).to_contain_text(f"#{off.get('id')}", timeout=60000)
    rec("12", "묶기 요약 끔: 엑셀 만들기 201·summary.mode=sheets·stats_summary 아님", ri.value.ok and off.get("summary", {}).get("mode") == "sheets"
        and not off.get("summary", {}).get("stats_summary"), {"run": off.get("id"), "sheets": off.get("summary", {}).get("sheet_order")})  # fmt: skip
    page.locator("section.bundle label", has_text="통계 요약 시트 추가").click()
    add = page.get_by_role("button", name="항목 추가")
    pw_expect(add).to_be_visible(timeout=30000)
    add.click()
    pw_expect(page.locator("section.bundle fieldset[aria-busy=true]")).to_have_count(0, timeout=60000)
    # 합성 시트에는 단위·기간 표기가 없어 요약 항목이 '확인 필요' — 표의 단위·기간 칸을 적어야 만들 수 있다 (묶기의 기존 규칙)
    unit = page.locator("section.bundle input[aria-label$=' 단위']")
    if unit.count():
        unit.first.fill("명")
    period = page.locator("section.bundle input[aria-label$=' 기간']")
    if period.count():
        period.first.fill("2026-09")
    btn = page.get_by_role("button", name=re.compile(r"^엑셀 만들기"))
    pw_expect(page.locator("section.bundle fieldset[aria-busy=true]")).to_have_count(0, timeout=60000)
    pw_expect(btn).to_be_enabled(timeout=60000)
    with page.expect_response(is_post("/api/bundle/run"), timeout=180000) as ri:
        btn.click()
    on = ri.value.json()
    pw_expect(page.locator("section.bundle .bd-made")).to_contain_text(f"#{on.get('id')}", timeout=60000)
    rec("12", "묶기 요약 켬: 항목 추가 → 엑셀 만들기 201·stats_summary·시트 순서에 '통계 요약'", ri.value.ok and on.get("summary", {}).get("stats_summary") is True
        and "통계 요약" in (on.get("summary", {}).get("sheet_order") or []), {"run": on.get("id"), "sheets": on.get("summary", {}).get("sheet_order")})  # fmt: skip
    FACTS["run_bundle_off"], FACTS["run_bundle_on"] = off.get("id"), on.get("id")
    shot(page, "desktop-12-bundle", page.locator("section.bundle .bd-made"))
    page.goto(URL + "/runs")
    pw_expect(page.locator("table.runs-table")).to_be_visible(timeout=30000)
    for label, run_id, want in (("통합", FACTS.get("run_int"), "통합 데이터"), ("묶기(요약 켬)", on.get("id"), "통계 요약")):
        if not run_id:
            rec("12", f"{label} 내려받기", False, "앞 단계에서 만들지 못함")
            continue
        row = page.locator("table.runs-table tbody tr", has=page.locator("td[data-label='번호']", has_text=re.compile(rf"^#{run_id}$")))
        with page.expect_download(timeout=60000) as di:
            row.get_by_role("link", name="내려받기").click()
        path = OUT / "downloads" / f"smoke-{label}-{run_id}.xlsx"
        di.value.save_as(str(path))
        wb = openpyxl.load_workbook(path, read_only=True)
        sheets = wb.sheetnames
        wb.close()
        rec("12", f"{label} 결과 #{run_id} 실행 기록 화면에서 내려받기 — xlsx·시트에 '{want}'", want in sheets and row.locator(".run-download-problem").count() == 0,
            {"sheets": sheets, "way": text_of(row.locator("td[data-label='방식']")).replace("\n", " ")})  # fmt: skip


# ---------------------------------------------------------------- 실행


def with_csp(route, request) -> None:  # 문서 응답에 nginx와 같은 CSP를 덧씌우고, 격리 스택 밖 요청은 막는다
    if request.resource_type == "document" and request.url.startswith(URL):
        res = route.fetch()
        route.fulfill(response=res, headers={**res.headers, "content-security-policy": CSP})
    elif request.url.startswith(URL):
        route.continue_()
    else:
        ABORTED.append(request.url[:120])
        route.abort()


def step_rows(page: Page) -> None:
    """13. 소계로 추정되지만 key가 있는 행(선택하지 않은 메모 열의 'Total'): 자동 제외 없이 '확인이 필요한 행'에서 포함/제외를 정한다."""
    r1 = api_upload(page, "R_소계추정.csv", "사번,부서,구분\n001,총무,일반\n002,기획,Total\n003,정보,일반\n".encode("utf-8-sig"))
    r2 = api_upload(page, "R_소계추정b.csv", "사번,건수\n001,1\n002,2\n003,3\n".encode("utf-8-sig"))
    page.goto(f"{URL}/keyjoin?files={r1},{r2}")
    wait_meta(page, 2)
    configure(page, 2)
    status, plan = do_check(page)
    alert = page.locator(ALERT)
    pw_expect(alert).to_be_visible(timeout=30000)
    review = page.locator(f"{SEC} section.kj-review")
    head = text_of(review.locator("h3")) if review.count() else ""
    rows = review.locator("tbody tr")
    cells = [text_of(x) for x in rows.nth(0).locator("th, td").all()] if rows.count() else []
    counts = text_of(page.locator(f"{FS[5]} p.mw-muted", has_text="행 수 (입력별"))
    rec("13", "확인 결과: 차단 KEYJOIN_ROWS_UNRESOLVED(role=alert)·'확인이 필요한 행' 표 1행(3행 · C열 · 소계 근거, 미결정 띠)·행 수 줄(원본 3 · 대상 2 · 제외 0 · 미해결 1)",
        status == 200 and plan.get("ready") is False and "KEYJOIN_ROWS_UNRESOLVED" in [p["code"] for p in plan["problems"]]
        and review.count() == 1 and "확인이 필요한 행 1행" in head and "미결정 1" in head and rows.count() == 1 and cells[:3] == ["3행", "C열", "소계·합계로 보이는 값"]
        and "needs-fix" in (rows.nth(0).get_attribute("class") or "") and "입력 1 3 · 2 · 0 · 1" in counts
        and plan["inputs"][0]["row_review"]["items"] == [{"row": 3, "column": "C", "reason": "subtotal", "in_key": False, "decision": None}],
        {"status": status, "head": head, "cells": cells, "counts": counts, "codes": [p["code"] for p in plan.get("problems", [])]})  # fmt: skip
    plan_text = json.dumps(plan, ensure_ascii=False)
    rec("13", "차단 문구·목록에 칸 값('Total'·'기획')이 없다 — 위치(행·열 문자)만", "Total" not in plan_text and "기획" not in plan_text
        and "Total" not in text_of(review) and "기획" not in text_of(review), "")  # fmt: skip
    include = review.get_by_role("radio", name="3행 포함")
    include.focus()
    mark_active(page)
    page.keyboard.press("Space")
    pw_expect(include).to_be_checked()
    changed = status_text(page)
    rec("13", "포함 선택(키보드 Space): 초점 그대로·상태 줄 '설정을 바꿨습니다'·만들기 비활성(다시 확인)", same_active(page) and changed == CHANGED_TEXT
        and page.locator(MAKE_BTN).get_attribute("aria-disabled") == "true", {"status": changed})  # fmt: skip
    status, plan = do_check(page)
    rr = plan["inputs"][0]["row_review"]
    rec("13", "포함 뒤 다시 확인: ready·결과 3행·row_review included 1·미해결 0·차단 없음", status == 200 and plan["ready"] and plan["result"]["rows"] == 3
        and rr["included"] == 1 and rr["unresolved"] == 0 and rr["link_rows"] == 3 and plan["problems"] == [], {"result": plan.get("result"), "rr": rr})  # fmt: skip
    status, run = do_make(page)
    path, _ = download_made(page, f"keyjoin-rows-include-{run.get('id')}.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True)
    got = [[c.value for c in r] for r in wb[SHEETS[0]].iter_rows()]
    wb.close()
    rec("13", "포함으로 만든 결과: 연결 결과 3행 (002 기획 2 포함)", status == 201 and got[1:] == [["001", "총무", 1], ["002", "기획", 2], ["003", "정보", 3]], got)
    exclude = review.get_by_role("radio", name="3행 제외")
    exclude.check()
    pw_expect(page.locator(STATUS)).to_have_text(CHANGED_TEXT, timeout=10000)
    status, plan = do_check(page)
    rr = plan["inputs"][0]["row_review"]
    rec("13", "제외로 바꿔 다시 확인: ready·결과 2행·excluded 1·참고 KEYJOIN_ROWS_EXCLUDED", status == 200 and plan["ready"] and plan["result"]["rows"] == 2
        and rr["excluded"] == 1 and "KEYJOIN_ROWS_EXCLUDED" in [w["code"] for w in plan["warnings"]], {"result": plan.get("result"), "rr": rr})  # fmt: skip
    status, run = do_make(page)
    path, _ = download_made(page, f"keyjoin-rows-exclude-{run.get('id')}.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True)
    got = [[c.value for c in r] for r in wb[SHEETS[0]].iter_rows()]
    summary = [[c.value for c in r] for r in wb[SHEETS[2]].iter_rows()]
    wb.close()
    listed = any(r and r[0] == "제외한 행" and r[1] == "입력 1" and str(r[2]) == "3" for r in summary)
    rec("13", "제외로 만든 결과: 연결 결과 2행·연결 요약에 '제외한 행 입력 1 3'", status == 201 and got[1:] == [["001", "총무", 1], ["003", "정보", 3]] and listed,
        {"rows": got[1:], "listed": listed})  # fmt: skip
    page.set_viewport_size({"width": 320, "height": 900})
    page.wait_for_timeout(300)
    o = overflow(page)
    wrap = review.locator(".upload-table-wrap")
    rec("13", "320px: 확인이 필요한 행 표가 페이지를 넓히지 않음(가로 넘침 0)·표 래퍼가 스크롤이면 region+tabindex", o["page_overflow_px"] <= 0 and not o["outside"]
        and (wrap.count() == 0 or wrap.evaluate("(e) => e.scrollWidth <= e.clientWidth + 1 || (e.getAttribute('role') === 'region' && e.getAttribute('tabindex') === '0')")),
        o)  # fmt: skip
    shot(page, "narrow-13-rows", full=True)
    page.set_viewport_size({"width": 1366, "height": 900})
    page.locator(f"{SEC} section.kj-review").get_by_role("button", name="결정 지우기").click()
    pw_expect(page.locator(STATUS)).to_have_text(CHANGED_TEXT, timeout=10000)
    draft = page.evaluate("() => JSON.parse(sessionStorage.getItem('moajwo.keyjoin.draft') || 'null')")
    rows_draft = (draft or {}).get("draft", {}).get("rows", {})
    rec("13", "'결정 지우기' 뒤 저장 초안의 행 결정이 비고 상태 줄 '설정을 바꿨습니다'", all(not v.get("include") and not v.get("exclude") for v in rows_draft.values()),
        {"rows_draft": rows_draft})  # fmt: skip


def new_page(br, width: int, height: int, label: str, axe: bool = False):
    cx = br.new_context(viewport={"width": width, "height": height}, locale="ko-KR", accept_downloads=True)
    cx.route("**/*", with_csp)
    if axe and AXE.exists():
        cx.add_init_script(path=str(AXE))  # 시험 페이지에만 넣는 검사기 (제품 코드 아님)
    page = cx.new_page()
    page.on("console", lambda m: CONSOLE.append({"page": label, "type": "console", "text": m.text[:300]}) if m.type == "error" else None)
    page.on("pageerror", lambda e: CONSOLE.append({"page": label, "type": "pageerror", "text": str(e)[:300]}))
    return cx, page


ITEMS = ["준비", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "공통"]


def summarize() -> dict:
    out = {}
    for it in ITEMS:
        checks = [r for r in REC if r["item"] == it and r["kind"] == "check"]
        errors = [r for r in REC if r["item"] == it and r["kind"] == "error"]
        failed = [r["step"] for r in checks if r["ok"] is False]
        result = "실행 오류 있음" if errors else ("실패" if failed else ("통과" if checks else "판정 없음"))
        out[it] = {"result": result, "checks": len(checks), "passed": sum(1 for r in checks if r["ok"]), "failed": failed,
                   "errors": [e["step"] for e in errors]}  # fmt: skip
    return out


def main() -> int:
    started = datetime.now().isoformat(timespec="seconds")
    assert (SYNTH / "A.csv").exists(), f"합성 입력이 없음: {SYNTH}"
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True)
        FACTS["edge"] = br.version
        cx, page = new_page(br, 1280, 900, "일반 사용자 1280")
        guarded("준비", "계정", page, lambda: step_setup(page))
        guarded("1", "업로드", page, lambda: step_upload(page))
        guarded("2", "홈 결과 형태·다음", page, lambda: step_home(page))
        guarded("3", "7단계 (기준 A만)", page, lambda: step_base(page))
        guarded("4", "범위 바꿈: 모든 파일", page, lambda: step_scope("4", page, "all"))
        guarded("5", "범위 바꿈: 공통", page, lambda: step_scope("5", page, "common"))
        guarded("6", "실행 기록 화면", page, lambda: step_runs(page))
        guarded("7", "거절 경로 (중복 key·공통 없음)", page, lambda: step_rejections(page))
        guarded("7", "싼 검사 차단(개인정보)·가림·다운로드", page, lambda: step_pii(page))
        guarded("8", "초점 불변·늦은 응답·파일 빼기", page, lambda: step_focus(page))
        guarded("13", "확인이 필요한 행 포함/제외", page, lambda: step_rows(page))
        guarded("12", "통합·재검사", page, lambda: step_smoke_integration(page))
        guarded("12", "묶기 요약 끔/켬·내려받기", page, lambda: step_smoke_bundle(page))
        cx.close()
        cx, page = new_page(br, 390, 844, "키보드 390")
        guarded("9", "로그인", page, lambda: login(page, USER))
        guarded("9", "키보드 완주", page, lambda: step_keyboard(page))
        cx.close()
        guarded("10", "반응형 320·390·1366 (+axe)", None, lambda: step_responsive(br))
        guarded("10", "긴 이름 320", None, lambda: step_long_names(br))
        guarded("11", "200% 확대", None, lambda: step_zoom(p))
        br.close()
    unexpected = [c for c in CONSOLE if not re.search(r"Failed to load resource: the server responded with a status of 4\d\d", c["text"])]
    rec("공통", "콘솔 오류·페이지 오류 없음 (거절 응답 4xx의 'Failed to load resource'는 제외)", not unexpected, {"전체": len(CONSOLE), "그 밖": unexpected[:10]})
    rec("공통", "격리 스택(127.0.0.1:8088) 밖으로 나간 요청 없음 (route가 막은 요청 수)", not ABORTED, ABORTED[:10])
    rec("공통", "실제 화면 읽기 프로그램 낭독 (NVDA·내레이터 등)", None, "미검증 — role·속성·글자만 DOM으로 확인", kind="info")
    summary = summarize()
    meta = {"started": started, "finished": datetime.now().isoformat(timespec="seconds"), "url": URL, "db": DB_MODE, "edge": FACTS.get("edge"),
            "synth": str(SYNTH), "axe": str(AXE) if AXE.exists() else None, "viewports": ["1280x900 (마우스)", "390x844 (키보드)", "320/390/1366 (반응형)", "1280 ×2.0 (확대)"],
            "csp": CSP}  # fmt: skip
    facts = {k: v for k, v in FACTS.items() if not k.startswith("plan_")}
    (OUT / "keyjoin_flow_result.json").write_text(json.dumps({"meta": meta, "summary": summary, "records": REC, "facts": facts,
                                                              "plans": {k: v for k, v in FACTS.items() if k.startswith("plan_")},
                                                              "console": CONSOLE, "aborted": ABORTED}, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")  # fmt: skip
    for it, s in summary.items():
        print(f"요약 | {it} | {s['result']} | {s['passed']}/{s['checks']} | 실패 {s['failed']} | 오류 {s['errors']}", flush=True)
    bad = any(s["result"] not in ("통과", "판정 없음") for s in summary.values())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
