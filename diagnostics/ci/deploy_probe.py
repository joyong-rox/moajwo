"""배포·롤백 리허설의 API 확인 (격리 러너 전용, 표준 라이브러리만).

사용법:
  python3 deploy_probe.py seed  <주소> <출력 json>   — 최초 소유자·합성 CSV 업로드·열 매핑 확정·묶기 결과를 만든다
  python3 deploy_probe.py check <주소> <seed json> <단계 이름>  — 같은 계정 로그인·실행 기록·이전 결과 다시 내려받기(해시 비교)
비밀번호는 환경 변수 PROBE_PW로만 받는다 (출력·파일에 남기지 않음). 결과는 한 줄 JSON.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

CSV = "시도,시군구,인구수\n서울특별시,종로구,10\n서울특별시,중구,20\n서울특별시,용산구,30\n".encode()


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: object = None, csv: bytes | None = None):
        headers, data = {}, None
        if csv is not None:
            b = secrets.token_hex(8)
            data = (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="rehearsal.csv"\r\n'
                    "Content-Type: text/csv\r\n\r\n").encode() + csv + f"\r\n--{b}--\r\n".encode()  # fmt: skip
            headers["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.op.open(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in b"[{" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw[:200]
        except OSError as e:
            return 0, str(e)[:200]


def seed(base: str, out: str) -> dict:
    c, pw = Client(base), os.environ["PROBE_PW"]
    # 이전 판(공개 최초 설정이 열려 있던 코드)이면 설정으로 계정을 만들고, 0022 이후 판(410)이면 호출한 쪽이 먼저 설치 초기화
    # 명령(app.adminctl init)으로 만든 admin으로 로그인한다. 어느 쪽 계정인지는 seed json에 남겨 check가 같은 계정으로 들어간다
    username = "rehearsal"
    s_setup, _ = c.call("POST", "/api/auth/setup", {"username": username, "display_name": "리허설", "password": pw})
    if s_setup == 410:
        username = "admin"
        s_setup, _ = c.call("POST", "/api/auth/login", {"username": username, "password": pw})
    s, up = c.call("POST", "/api/files", csv=CSV)
    fid = up[0]["id"]
    pv = c.call("GET", f"/api/files/{fid}/preview")[1]
    cols = {col["name"]: col["column_id"] for col in pv["columns"]}
    decisions = [{"sheet_id": pv["sheet_id"], "column_id": cols[n], "field_key": k}
                 for n, k in {"시도": "sido", "시군구": "sigungu", "인구수": "population"}.items()]  # fmt: skip
    s_map, _ = c.call("PUT", f"/api/files/{fid}/mapping/decisions", {"decisions": decisions})
    plan = c.call("POST", "/api/bundle/plan", {"file_ids": [fid]})[1]
    s_run, run = c.call("POST", "/api/bundle/run", {"file_ids": [fid], "expected_basis": plan["basis"]})
    s_dl, raw = c.call("GET", f"/api/runs/{run['id']}/download")
    info = {"setup": s_setup, "username": username, "upload": s, "file_id": fid, "mapping": s_map, "bundle": s_run, "run_id": run["id"],
            "download": s_dl, "result_sha256": hashlib.sha256(raw).hexdigest() if isinstance(raw, bytes) else None}  # fmt: skip
    with open(out, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return info


def check(base: str, seed_file: str, stage: str) -> dict:
    with open(seed_file, encoding="utf-8") as f:
        info = json.load(f)
    c = Client(base)
    s_login, me = c.call("POST", "/api/auth/login", {"username": seed.get("username", "rehearsal"), "password": os.environ["PROBE_PW"]})
    s_runs, runs = c.call("GET", "/api/runs")
    s_dl, raw = c.call("GET", f"/api/runs/{info['run_id']}/download")
    same = isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == info["result_sha256"]
    s_pv, _ = c.call("GET", f"/api/files/{info['file_id']}/preview")
    return {"stage": stage, "login": s_login, "role": me.get("role") if isinstance(me, dict) else None,
            "runs": s_runs, "run_count": len(runs) if isinstance(runs, list) else None, "download": s_dl,
            "result_same": same, "original_preview": s_pv}  # fmt: skip


if __name__ == "__main__":
    cmd, base = sys.argv[1], sys.argv[2].rstrip("/")
    out = seed(base, sys.argv[3]) if cmd == "seed" else check(base, sys.argv[3], sys.argv[4])
    print(json.dumps(out, ensure_ascii=False))
