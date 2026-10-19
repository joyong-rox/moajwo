"""진단 스크립트 공용 도구 — 진단 격리 스택(127.0.0.1:8086)만 상대한다. 운영 주소는 쓰지 않는다.

계정 비밀번호는 실행 때 무작위로 만들어 Git 밖 증거 폴더(DIAG_EVIDENCE, 기본 backups/diag-<날짜>)의 creds.json에만 둔다.
"""

import http.cookiejar
import io
import json
import os
import secrets
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import openpyxl

URL = os.environ.get("DIAG_URL", "http://127.0.0.1:8086")
API = URL + "/api"
assert URL.startswith(("http://127.0.0.1:8086", "http://frontend")), "진단 격리 주소만 허용"
ROOT = Path(__file__).resolve().parents[2]
EV = Path(os.environ.get("DIAG_EVIDENCE", ROOT / "backups" / "diag-20261020"))
CREDS = EV / "creds.json"
BACKEND = "moajwo-diag-backend-1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
USERS = {"owner": "diag-owner", "user1": "diag-user1", "user2": "diag-user2", "user3": "diag-user3"}


def creds() -> dict[str, str]:
    if CREDS.exists():
        return json.loads(CREDS.read_text(encoding="utf-8"))
    c = {k: "Dg-" + secrets.token_urlsafe(14) + "7z" for k in (*USERS, "pending")}
    EV.mkdir(parents=True, exist_ok=True)
    CREDS.write_text(json.dumps(c), encoding="utf-8")
    return c


class Api:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body=None, data=None, headers=None, raw=False):
        if body is not None:
            data, headers = json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(API + path, data=data, method=method, headers=headers or {})
        try:
            with self.op.open(req, timeout=300) as r:
                c = r.read()
                return r.status, c if raw else _parse(c), dict(r.headers)
        except urllib.error.HTTPError as e:
            c = e.read()
            return e.code, c if raw else _parse(c), dict(e.headers)

    def login(self, who: str) -> "Api":
        s, b, _ = self.call("POST", "/auth/login", {"username": USERS[who], "password": creds()[who]})
        assert s == 200, (who, s, b)
        return self

    def upload(self, name: str, content: bytes, ctype: str = XLSX):
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()  # fmt: skip
        return self.call("POST", "/files", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})

    def cookie(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)


def _parse(c: bytes):
    if c and c[:1] in b"[{":
        try:
            return json.loads(c)
        except ValueError:
            return c
    return c


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"  # 시험 자료: 수식처럼 보이는 글자를 글자로 둔다
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def db(sql: str) -> list:
    """진단 스택 DB 읽기 전용 조회 (운영 DB 아님)."""
    code = f"import sqlite3,json; print(json.dumps(sqlite3.connect('file:/app/data/moajwo.db?mode=ro', uri=True).execute({sql!r}).fetchall(), default=str))"
    p = subprocess.run(["docker", "exec", BACKEND, "python", "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr[-400:]
    return json.loads(p.stdout)
