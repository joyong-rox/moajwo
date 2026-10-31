"""배포·롤백 리허설의 API 확인 (격리 러너 전용, 표준 라이브러리만).

사용법:
  python3 deploy_probe.py seed  <주소> <출력 json>   — 최초 소유자·합성 CSV 업로드·열 매핑 확정·묶기 결과를 만든다
  python3 deploy_probe.py check <주소> <seed json> <단계 이름>  — 같은 계정 로그인·실행 기록·이전 결과 다시 내려받기(해시 비교)
  python3 deploy_probe.py tempflow <주소> <seed json> <비밀 파일>  — (0022 판) 사용자 발급 → 임시 로그인 확인 → 이용 중지. 임시 비밀번호는
                                                                   비밀 파일(결과 폴더 밖)에만 두고 출력하지 않는다
  python3 deploy_probe.py revoke <주소> <seed json> <비밀 파일> <결과 폴더>  — 오래된 버전 409 → 지금 버전으로 임시 비밀번호 폐기 200 →
                                                                   폐기 전 임시 비밀번호로 로그인 401 → 비밀 파일 삭제 · 결과 폴더에 그 값이 없는지
  python3 deploy_probe.py bprime <주소> <seed json> <단계 이름> <쿠키 파일>  — B′ 전·중·후: 공개 가입 응답(상태·detail), 로그인·/auth/status·
                                                                   관리자 API·로그아웃, 저장해 둔 세션(쿠키 파일)의 /auth/me, backend 직접 포트 도달 여부
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
DIRECT_BACKEND = "http://127.0.0.1:8000"  # docker-compose.yml 의 backend 호스트 포트 — B′ 동안에는 닿지 않아야 한다


class Client:
    def __init__(self, base: str, jar: http.cookiejar.CookieJar | None = None) -> None:
        self.base = base
        self.jar = jar if jar is not None else http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: object = None, csv: bytes | None = None, base: str | None = None):
        headers, data = {}, None
        if csv is not None:
            b = secrets.token_hex(8)
            data = (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="rehearsal.csv"\r\n'
                    "Content-Type: text/csv\r\n\r\n").encode() + csv + f"\r\n--{b}--\r\n".encode()  # fmt: skip
            headers["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request((base or self.base) + path, data=data, method=method, headers=headers)
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


def _owner(base: str, seed_file: str) -> tuple[Client, int, dict]:
    with open(seed_file, encoding="utf-8") as f:
        info = json.load(f)
    c = Client(base)
    s_login, me = c.call("POST", "/api/auth/login", {"username": info.get("username", "rehearsal"), "password": os.environ["PROBE_PW"]})
    return c, s_login, info


def check(base: str, seed_file: str, stage: str) -> dict:
    c, s_login, info = _owner(base, seed_file)
    me = c.call("GET", "/api/auth/me")[1]
    s_runs, runs = c.call("GET", "/api/runs")
    s_dl, raw = c.call("GET", f"/api/runs/{info['run_id']}/download")
    same = isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == info["result_sha256"]
    s_pv, _ = c.call("GET", f"/api/files/{info['file_id']}/preview")
    return {"stage": stage, "login": s_login, "role": me.get("role") if isinstance(me, dict) else None,
            "runs": s_runs, "run_count": len(runs) if isinstance(runs, list) else None, "download": s_dl,
            "result_same": same, "original_preview": s_pv}  # fmt: skip


TEMP_USER = "rh-temp"


def _listed(c: Client, username: str) -> dict | None:
    s, rows = c.call("GET", "/api/users")
    if s != 200 or not isinstance(rows, list):
        return None
    return next((u for u in rows if u["username"] == username), None)


def tempflow(base: str, seed_file: str, secret_file: str) -> dict:
    """0022 판에서: 사용자 발급(임시) → 그 임시 비밀번호로 로그인 200·업무 API 403 → 이용 중지(세션 종료). 임시 비밀번호는 비밀 파일에만."""
    c, s_login, _ = _owner(base, seed_file)
    s_prov, body = c.call("POST", "/api/users", {"username": TEMP_USER, "display_name": "rehearsal temp"})
    out: dict = {"owner_login": s_login, "provision": s_prov}
    if s_prov != 201:
        out["error"] = body if isinstance(body, dict) else str(body)[:120]
        return out
    temp = body["temp_password"]
    uid = body["user"]["id"]
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"username": TEMP_USER, "id": uid, "temp": temp}, f)
    u = Client(base)
    s_tlogin, me = u.call("POST", "/api/auth/login", {"username": TEMP_USER, "password": temp})
    s_files, _ = u.call("GET", "/api/files")
    s_susp, row = c.call("POST", f"/api/users/{uid}/suspend", {"reason": "rehearsal: suspended while temporary"})
    s_me_after, _ = u.call("GET", "/api/auth/me")
    out.update({"id": uid, "temp_login": s_tlogin, "temp_must_change": me.get("must_change_password") if isinstance(me, dict) else None,
                "temp_files": s_files, "suspend": s_susp, "status": row.get("status") if isinstance(row, dict) else None,
                "must_change": row.get("must_change_password") if isinstance(row, dict) else None,
                "temp_session_after_suspend": s_me_after, "temp_in_output": False})  # fmt: skip
    assert temp not in json.dumps(out)
    return out


def revoke(base: str, seed_file: str, secret_file: str, out_dir: str) -> dict:
    """오래된 버전으로 폐기 → 409(아무것도 안 바뀜) → 지금 버전으로 폐기 → 200(폐기됨·임시 상태 아님·중지 그대로) → 폐기 전 임시
    비밀번호로 로그인 401 → 재개해도 401 → 비밀 파일 삭제 → 결과 폴더 어디에도 그 값이 없음."""
    c, s_login, _ = _owner(base, seed_file)
    with open(secret_file, encoding="utf-8") as f:
        sec = json.load(f)
    temp, uid = sec["temp"], sec["id"]
    before = _listed(c, TEMP_USER) or {}
    version = before.get("auth_version")
    s_stale, _ = c.call("POST", f"/api/users/{uid}/password/revoke", {"expected_auth_version": (version or 1) - 1})
    mid = _listed(c, TEMP_USER) or {}
    s_rev, row = c.call("POST", f"/api/users/{uid}/password/revoke", {"expected_auth_version": version})
    u = Client(base)
    s_tlogin, _ = u.call("POST", "/api/auth/login", {"username": TEMP_USER, "password": temp})
    s_again, _ = c.call("POST", f"/api/users/{uid}/password/revoke", {"expected_auth_version": row.get("auth_version") if isinstance(row, dict) else 0})
    s_resume, resumed = c.call("POST", f"/api/users/{uid}/resume", {})
    s_tlogin2, _ = Client(base).call("POST", "/api/auth/login", {"username": TEMP_USER, "password": temp})
    s_susp2, _ = c.call("POST", f"/api/users/{uid}/suspend", {"reason": "rehearsal: suspended again after revoke"})
    after = _listed(c, TEMP_USER) or {}
    os.remove(secret_file)
    leaks = 0
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            try:
                with open(os.path.join(root, name), "rb") as f:
                    leaks += f.read().count(temp.encode())
            except OSError:
                pass
    out = {"owner_login": s_login, "version_before": version, "stale": s_stale,
           "unchanged_after_stale": mid.get("auth_version") == version and mid.get("must_change_password") is True,
           "revoke": s_rev, "status": row.get("status") if isinstance(row, dict) else None,
           "must_change": row.get("must_change_password") if isinstance(row, dict) else None,
           "password_revoked": row.get("password_revoked") if isinstance(row, dict) else None,
           "version_after": row.get("auth_version") if isinstance(row, dict) else None,
           "temp_login": s_tlogin, "revoke_again": s_again, "resume": s_resume,
           "resumed_status": resumed.get("status") if isinstance(resumed, dict) else None,
           "temp_login_after_resume": s_tlogin2, "suspend_again": s_susp2,
           "final_status": after.get("status"), "final_must_change": after.get("must_change_password"),
           "secret_file_removed": not os.path.exists(secret_file), "secret_in_out": leaks}  # fmt: skip
    assert temp not in json.dumps(out)
    return out


def bprime(base: str, seed_file: str, stage: str, jar_file: str) -> dict:
    """B′ 전(before)·중(during)·후(after). before에서 소유자 로그인 세션을 쿠키 파일에 저장하고, 모든 단계에서 그 세션으로 /auth/me를
    확인한다(기존 세션 유지). 공개 가입은 단계마다 다른 아이디로 시도한다 (구판: 201 → B′: 410 → 해제: 201)."""
    out: dict = {"stage": stage}
    saved = http.cookiejar.MozillaCookieJar(jar_file)
    if stage == "before":
        c, s_login, _ = _owner(base, seed_file)
        for ck in c.jar:
            saved.set_cookie(ck)
        saved.save(ignore_discard=True, ignore_expires=True)
        os.chmod(jar_file, 0o600)
        out["session_saved"] = s_login
    else:
        try:
            saved.load(ignore_discard=True, ignore_expires=True)
        except OSError:
            out["session_saved"] = "missing"
    kept = Client(base, saved)
    s_me, me = kept.call("GET", "/api/auth/me")
    out["session_me"] = s_me
    out["session_user"] = me.get("username") if isinstance(me, dict) else None
    fresh, s_login, _ = _owner(base, seed_file)
    out["login"] = s_login
    out["status"] = fresh.call("GET", "/api/auth/status")[0]
    out["users"] = fresh.call("GET", "/api/users")[0]  # 관리자 API
    out["health"] = fresh.call("GET", "/api/health")[0]
    name = f"rh-signup-{stage}-{secrets.token_hex(2)}"
    body = {"username": name, "display_name": "S", "password": "password-123", "password_confirm": "password-123"}
    s_signup, detail = fresh.call("POST", "/api/auth/signup", body)
    out["signup"] = s_signup
    out["detail"] = (detail.get("detail") if isinstance(detail, dict) else str(detail))[:60] if detail is not None else None
    out["setup"] = fresh.call("POST", "/api/auth/setup", {"username": "x", "display_name": "x", "password": "password-123"})[0]
    s_direct, msg = Client(base).call("POST", "/auth/signup", {}, base=DIRECT_BACKEND)
    out["direct_backend"] = "refused" if s_direct == 0 else "reachable"
    out["direct_backend_status"] = s_direct if s_direct else str(msg)[:60]
    out["logout"] = fresh.call("POST", "/api/auth/logout")[0]
    out["session_me_after"] = kept.call("GET", "/api/auth/me")[0]  # 저장해 둔 세션은 다른 로그인·로그아웃과 무관하게 그대로
    if stage == "after":
        os.remove(jar_file)
        out["jar_removed"] = not os.path.exists(jar_file)
    return out


if __name__ == "__main__":
    cmd, base = sys.argv[1], sys.argv[2].rstrip("/")
    if cmd == "seed":
        out = seed(base, sys.argv[3])
    elif cmd == "check":
        out = check(base, sys.argv[3], sys.argv[4])
    elif cmd == "tempflow":
        out = tempflow(base, sys.argv[3], sys.argv[4])
    elif cmd == "revoke":
        out = revoke(base, sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "bprime":
        out = bprime(base, sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        sys.exit(f"unknown command {cmd}")
    print(json.dumps(out, ensure_ascii=False))
