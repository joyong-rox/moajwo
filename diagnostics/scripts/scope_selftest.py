"""요청 허용 범위 자체 시험 — 루프백 임시 서버만 쓴다 (운영·진단 스택에 요청하지 않음).

127.0.0.1:8086(허용 주소, 진단 스택이 꺼져 있을 때만 실행)에 시험 서버를, 127.0.0.1:8099에 '금지' 서버를 띄우고
허용 서버가 금지 서버로 리다이렉트하거나 자원을 불러오게 한다. 금지 서버가 받은 요청 수로 판정한다.
- Python: diag_scope.opener → 리다이렉트를 따르기 전에 ScopeError, 금지 서버 요청 0
- 브라우저: route_guard + browser_proxy → 금지 서버 요청 0 (대조: 제한 없는 브라우저는 금지 서버에 요청함)
사용법: python scope_selftest.py <출력 json>
"""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from diag_scope import ScopeError, browser_proxy, diag_base, opener, route_guard

ALLOWED = diag_base(None)
FORBIDDEN_PORT = 8099
FORBIDDEN = f"http://127.0.0.1:{FORBIDDEN_PORT}"
HITS: list[str] = []
PAGE = f"""<!doctype html><html lang="ko"><head><title>scope</title><script src="{FORBIDDEN}/s.js"></script></head>
<body><img src="{FORBIDDEN}/img.png"><iframe src="/redir"></iframe>
<script>fetch('/redir').catch(() => 0); fetch('{FORBIDDEN}/fetch').catch(() => 0);</script><p id="ok">ok</p></body></html>"""


class Allowed(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", f"{FORBIDDEN}/after-redirect")
            self.end_headers()
            return
        body = PAGE.encode() if self.path.startswith("/page") else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if self.path.startswith("/page") else "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Forbidden(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        HITS.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"forbidden")

    def log_message(self, *a):
        pass


def free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    if not free(8086) or not free(FORBIDDEN_PORT):
        raise SystemExit("8086 또는 8099가 사용 중 — 진단 스택을 끄고 실행하세요")
    servers = [ThreadingHTTPServer(("127.0.0.1", 8086), Allowed), ThreadingHTTPServer(("127.0.0.1", FORBIDDEN_PORT), Forbidden)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    out: dict = {}
    try:
        op = opener(ALLOWED)
        out["python_allowed_ok"] = op.open(f"{ALLOWED}/ok", timeout=5).read() == b"ok"
        try:
            op.open(f"{ALLOWED}/redir", timeout=5)
            out["python_redirect"] = "따라감(실패)"
        except ScopeError as e:
            out["python_redirect"] = f"ScopeError: {str(e)[:80]}"
        out["python_forbidden_hits"] = len(HITS)
        HITS.clear()
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True, proxy=browser_proxy(ALLOWED))
            blocked: list[str] = []
            pg = b.new_page()
            pg.route("**/*", route_guard(ALLOWED, blocked))
            pg.goto(f"{ALLOWED}/page")
            pg.wait_for_timeout(1500)
            out["browser_page_ok"] = pg.locator("#ok").inner_text() == "ok"
            try:
                pg.goto(f"{ALLOWED}/redir", timeout=10000)
                out["browser_navigation_redirect"] = "열림"
            except Exception as e:  # noqa: BLE001 — 막혀서 실패하는 것이 기대
                out["browser_navigation_redirect"] = f"막힘 ({type(e).__name__})"
            pg.wait_for_timeout(500)
            out["browser_route_blocked_first_requests"] = len(blocked)
            out["browser_forbidden_hits"] = len(HITS)
            b.close()
            HITS.clear()
            # 대조: 제한 없는 브라우저는 금지 서버에 요청한다 (시험이 의미 있음을 보임)
            b = p.chromium.launch(channel="msedge", headless=True)
            pg = b.new_page()
            pg.goto(f"{ALLOWED}/page")
            pg.wait_for_timeout(1500)
            out["control_unguarded_forbidden_hits"] = len(HITS)
            b.close()
    finally:
        for s in servers:
            s.shutdown()
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
