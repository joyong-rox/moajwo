"""반영 전 격리 기동 점검 — 후보 backend·frontend 이미지(ID)를 운영과 분리해 띄우고 기동·DB 연결·CA·TLS·핵심 취합·내려받기를
확인한다 (docs/DEPLOY.md 11장). 운영 컨테이너·데이터 볼륨·포트를 쓰지 않는다: 새 내부 네트워크(--internal, 바깥 연결 없음)와 새 임시
볼륨에 띄우고 호스트 포트를 열지 않으며, 끝나면 만든 컨테이너·볼륨·네트워크를 지운다. 자료는 점검 안에서 만든 합성 자료뿐이고,
비밀번호는 점검 DB에만 쓰는 임의 값이라 기록하지 않는다.

사용법: python scripts/image_smoke.py <backend 이미지> <frontend 이미지> <결과.json>   (모두 통과면 종료 0)

요청은 backend 컨테이너 안의 Python이 보낸다 (호스트 포트를 열지 않기 위해):
  1. 기동: backend HEALTHCHECK healthy, 새 SQLite DB의 alembic current = heads, frontend 응답
  2. DB 연결: GET /health → status ok · database ok
  3. CA·TLS: 기본 신뢰 저장소가 OS CA 묶음을 읽음. 점검 안에서 만든 시험 CA로 TLS 서버를 띄워
     Python ssl — 시험 CA + 맞는 이름 연결 / 기본 신뢰 저장소는 모르는 CA 거절 / 이름 다름 거절,
     psycopg(libpq, sslmode=verify-full) — 시험 CA + 맞는 이름은 TLS 성립 / 이름 다름 거절 / sslrootcert=system은 모르는 CA 거절
  4. frontend: 첫 화면 200·CSP·nosniff, 프록시 /api/health
  5. 핵심 취합·내려받기 (frontend 프록시를 거쳐): 최초 소유자 설정 → CSV·XLSX 업로드 → 공통 key 연결 확인·만들기 → 결과 내려받아
     내용 확인 → 묶기 확인·만들기 → 결과 내려받기
  6. 로그: backend Traceback·ERROR 없음, frontend [error]·[emerg] 없음
"""

import json
import os
import subprocess
import sys
import time

TOOL = "image_smoke 1"
PROD_VOLUME = "moajwo_moajwo-data"

CHECK_PY = r"""
import io, json, os, secrets, socket, ssl, subprocess, tempfile, threading, time, urllib.error, urllib.request

R = []
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rec(item, ok, detail=None):
    R.append({"item": item, "ok": bool(ok), "detail": detail})


def step(item, fn):
    try:
        fn()
    except Exception as e:  # 한 점검의 예외가 나머지 점검을 막지 않게
        rec(item, False, {"error": type(e).__name__ + ": " + str(e)[:300]})


def sh(*a):
    r = subprocess.run(list(a), capture_output=True, text=True, cwd="/app", timeout=120)
    return r.returncode, r.stdout + "\n" + r.stderr


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COOKIE = {}


def http(method, url, body=None, files=None, timeout=180):
    headers, data = {}, None
    if COOKIE.get("v"):
        headers["Cookie"] = COOKIE["v"]
    if files is not None:
        bd = "smoke" + secrets.token_hex(8)
        data = b"".join(
            f'--{bd}\r\nContent-Disposition: form-data; name="files"; filename="{n}"\r\nContent-Type: {t}\r\n\r\n'.encode() + c + b"\r\n"
            for n, c, t in files
        ) + f"--{bd}--\r\n".encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + bd
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def js(b):
    try:
        return json.loads(b)
    except ValueError:
        return {}


def boot():
    rc1, cur = sh("alembic", "current")
    rc2, heads = sh("alembic", "heads")
    cur_l = [x.strip() for x in cur.splitlines() if "(head)" in x]
    head_l = [x.strip() for x in heads.splitlines() if "(head)" in x]
    rec("기동: 새 SQLite DB의 alembic current = heads", rc1 == 0 and rc2 == 0 and len(head_l) == 1 and cur_l[:1] == head_l,
        {"current": cur_l, "heads": head_l})


def db():
    s, _, b = http("GET", "http://127.0.0.1:8000/health")
    d = js(b)
    rec("DB 연결: GET /health → status ok · database ok", s == 200 and d.get("status") == "ok" and d.get("database") == "ok",
        {"status": s, "body": d})


TMP = tempfile.mkdtemp(prefix="smoke-tls-")
HS = []  # 시험 서버 쪽에서 본 TLS 성립 결과: (psycopg 서버인지, 결과)
TLS = {}


def ossl(*a):
    r = subprocess.run(["openssl", *a], capture_output=True, text=True, cwd=TMP, timeout=60)
    if r.returncode:
        raise RuntimeError("openssl " + a[0] + ": " + r.stderr[-200:])


def serve(pg):
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(8)

    def loop():
        while True:
            c, _ = ls.accept()
            try:
                c.settimeout(10)
                if pg:  # PostgreSQL 첫 요청(SSLRequest)에 'S'로 답한 뒤 TLS — 그 뒤 시작 메시지를 받으면 끊는다
                    if c.recv(8)[4:8] != (80877103).to_bytes(4, "big"):
                        c.close()
                        continue
                    c.sendall(b"S")
                t = TLS["server"].wrap_socket(c, server_side=True)
                HS.append((pg, "ok"))
                if not pg:
                    t.sendall(b"ok")
                try:
                    t.recv(1024)
                except Exception:
                    pass
                t.close()
            except Exception as e:
                HS.append((pg, type(e).__name__))
                c.close()

    threading.Thread(target=loop, daemon=True).start()
    return ls.getsockname()[1]


def py_client(port, cafile, name):
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s, ctx.wrap_socket(s, server_hostname=name) as t:
            return ["ok", t.recv(8).decode()]
    except ssl.SSLCertVerificationError as e:
        return ["verify-failed", e.verify_message]
    except Exception as e:
        return ["error", type(e).__name__ + ": " + str(e)[:160]]


def tls():
    ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
         "-subj", "/CN=moajwo-smoke-ca")
    ossl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=smoke.test")
    with open(os.path.join(TMP, "ext.cnf"), "w") as f:
        f.write("subjectAltName=DNS:smoke.test\nextendedKeyUsage=serverAuth\n")
    ossl("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem",
         "-days", "2", "-extfile", "ext.cnf")
    TLS["ca"] = os.path.join(TMP, "ca.pem")
    TLS["server"] = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    TLS["server"].load_cert_chain(os.path.join(TMP, "srv.pem"), os.path.join(TMP, "srv.key"))
    paths = ssl.get_default_verify_paths()
    stats = ssl.create_default_context().cert_store_stats()
    real = os.path.realpath(paths.openssl_cafile)
    rec("CA: 기본 신뢰 저장소가 OS CA 묶음을 읽음", real == "/etc/ssl/certs/ca-certificates.crt" and stats.get("x509_ca", 0) >= 100,
        {"cafile": paths.openssl_cafile, "realpath": real, "x509_ca": stats.get("x509_ca"), "openssl": ssl.OPENSSL_VERSION})
    port = serve(False)
    a, b, c = py_client(port, TLS["ca"], "smoke.test"), py_client(port, None, "smoke.test"), py_client(port, TLS["ca"], "other.test")
    rec("TLS(Python ssl): 시험 CA + 맞는 이름 → 연결", a == ["ok", "ok"], {"result": a})
    rec("TLS(Python ssl): 기본 신뢰 저장소 → 모르는 CA 거절", b[0] == "verify-failed", {"result": b})
    rec("TLS(Python ssl): 이름 다름 → 거절", c[0] == "verify-failed" and "mismatch" in (c[1] or "").lower(), {"result": c})


def pg_client(port, rootcert, host):
    import psycopg

    HS.clear()
    info = (f"host={host} hostaddr=127.0.0.1 port={port} dbname=smoke user=smoke sslmode=verify-full sslrootcert={rootcert}"
            " gssencmode=disable connect_timeout=10")
    try:
        psycopg.connect(info).close()
        msg = "connected"
    except Exception as e:
        msg = " ".join(str(e).split())[:300]
    for _ in range(30):  # 서버 쪽 결과가 기록될 때까지 잠깐 기다림
        if HS:
            break
        time.sleep(0.1)
    return msg, [h for p, h in HS if p]


def pg_tls():
    import psycopg

    port = serve(True)
    a, b, c = pg_client(port, TLS["ca"], "smoke.test"), pg_client(port, TLS["ca"], "other.test"), pg_client(port, "system", "smoke.test")
    lib = {"libpq": psycopg.pq.version(), "impl": psycopg.pq.__impl__}
    rec("TLS(psycopg·libpq verify-full): 시험 CA + 맞는 이름 → TLS 성립",
        "ok" in a[1] and "certificate verify failed" not in a[0] and "does not match" not in a[0], {**lib, "server": a[1], "client": a[0]})
    rec("TLS(psycopg·libpq verify-full): 이름 다름 → 거절", "does not match host name" in b[0], {"server": b[1], "client": b[0]})
    rec("TLS(psycopg·libpq verify-full): sslrootcert=system → 모르는 CA 거절", "certificate verify failed" in c[0],
        {"server": c[1], "client": c[0]})


def front():
    s, h, _ = http("GET", "http://frontend:8080/")
    rec("frontend: 첫 화면 200 · CSP · nosniff", s == 200 and bool(h.get("content-security-policy")) and h.get("x-content-type-options") == "nosniff",
        {"status": s, "csp": bool(h.get("content-security-policy")), "nosniff": h.get("x-content-type-options")})
    s, _, b = http("GET", "http://frontend:8080/api/health")
    d = js(b)
    rec("frontend 프록시: /api/health → backend ok", s == 200 and d.get("database") == "ok", {"status": s, "body": d})


API = "http://frontend:8080/api"


def flow():
    import openpyxl

    s, h, _ = http("POST", API + "/auth/setup", {"username": "smoke-owner", "display_name": "점검", "password": "Sm-" + secrets.token_hex(12)})
    cookie = (h.get("set-cookie") or "").split(";")[0]
    COOKIE["v"] = cookie
    rec("핵심 취합 준비: 최초 소유자 설정(점검 DB) 201 · 세션 쿠키", s == 201 and "=" in cookie, {"status": s, "cookie": bool(cookie)})
    csv_a = "코드,인구\nK001,100\nK002,200\nK003,300\n".encode("utf-8")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "예산"
    for row in (["코드", "예산"], ["K001", 10], ["K002", 20], ["K004", 40]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    s, _, b = http("POST", API + "/files", files=[("smoke_a.csv", csv_a, "text/csv"), ("smoke_b.xlsx", buf.getvalue(), XLSX)])
    up = js(b) if s == 200 else []
    ids = [x.get("id") for x in up if isinstance(x, dict) and x.get("id") and not x.get("error")]
    rec("업로드(프록시): CSV·XLSX 2개를 읽음", s == 200 and len(ids) == 2,
        {"status": s, "rows": [x.get("data_rows") for x in up if isinstance(x, dict)], "errors": [x.get("error") for x in up if isinstance(x, dict)]})
    if len(ids) != 2:
        return
    fa, fb = ids
    body = {"inputs": [{"file_id": f, "sheet_id": "s0", "key_columns": ["c0"], "columns": [{"column_id": "c1"}]} for f in (fa, fb)],
            "scope": "base", "base": {"file_id": fa, "sheet_id": "s0"}}
    s, _, b = http("POST", API + "/keyjoin/plan", body)
    plan = js(b)
    rec("공통 key 연결 확인(plan): 준비됨", s == 200 and plan.get("ready") is True,
        {"status": s, "result": plan.get("result"), "problems": [p.get("text") for p in plan.get("problems", []) if isinstance(p, dict)][:3]})
    s, _, b = http("POST", API + "/keyjoin/run", {**body, "expected_basis": plan.get("basis") or "x" * 16, "request_id": secrets.token_hex(16)})
    run = js(b)
    rec("공통 key 연결 만들기(run): 201", s == 201 and bool(run.get("id")), {"status": s, "id": run.get("id"), "state": run.get("status")})
    s, h, b = http("GET", API + f"/runs/{run.get('id')}/download")
    ok = s == 200 and h.get("content-type", "").startswith(XLSX)
    detail = {"status": s, "type": h.get("content-type"), "bytes": len(b)}
    if ok:
        book = openpyxl.load_workbook(io.BytesIO(b), read_only=True)

        def head(w):
            h = list(next(w.iter_rows(values_only=True), []))
            while h and h[-1] is None:
                h.pop()
            return h

        def norm(v):
            return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)

        sheet = next((w for w in book.worksheets if head(w) == plan.get("header")), None)
        rows = [list(r) for r in sheet.iter_rows(values_only=True)][1:] if sheet is not None else []
        keys = sorted({v for r in rows for v in r if isinstance(v, str) and v.startswith("K00")})
        k1 = [norm(v) for v in next((r for r in rows if "K001" in r), [])]
        detail.update({"sheets": book.sheetnames, "header": plan.get("header"), "first_row": [head(w) for w in book.worksheets][:1],
                       "data_sheet": sheet.title if sheet is not None else None, "keys": keys, "K001": k1})
        ok = sheet is not None and keys == ["K001", "K002", "K003"] and "100" in k1 and "10" in k1
    rec("공통 key 연결 결과 내려받기(프록시): 기준 key 3개 · K001 행에 두 파일 값", ok, detail)
    s, _, b = http("POST", API + "/bundle/plan", {"file_ids": [fa, fb]})
    bp = js(b)
    s2, _, b2 = http("POST", API + "/bundle/run", {"file_ids": [fa, fb], "expected_basis": bp.get("basis") or "x" * 16})
    br = js(b2)
    s3, h3, b3 = http("GET", API + f"/runs/{br.get('id')}/download")
    ok = s == 200 and s2 == 201 and s3 == 200 and h3.get("content-type", "").startswith(XLSX)
    detail = {"plan": s, "run": s2, "download": s3}
    if ok:
        names = openpyxl.load_workbook(io.BytesIO(b3), read_only=True).sheetnames
        detail["sheets"] = len(names)
        ok = len(names) >= 2
    rec("묶기 확인·만들기·내려받기(프록시): 시트 2개 이상", ok, detail)


step("기동", boot)
step("DB 연결", db)
step("CA·TLS(Python ssl)", tls)
step("TLS(psycopg)", pg_tls)
step("frontend", front)
step("핵심 취합·내려받기", flow)
print("@@RESULT " + json.dumps(R, ensure_ascii=False))
"""


def docker(*args: str, stdin: bytes | None = None, check: bool = True, timeout: int = 600) -> str:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")  # Windows Git Bash가 /app/... 인자를 경로로 바꾸지 않게
    r = subprocess.run(["docker", *args], input=stdin, capture_output=True, env=env, timeout=timeout, check=False)
    out = r.stdout.decode("utf-8", "replace") + (r.stderr.decode("utf-8", "replace") if not check else "")
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])} 실패 (코드 {r.returncode}): {r.stderr.decode('utf-8', 'replace')[:400]}")
    return out.strip()


def wait(pred, seconds: int) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 — 아직 뜨지 않음
            pass
        time.sleep(2)
    return False


def main() -> int:
    sys.stdout.reconfigure(errors="replace")  # Windows 콘솔(cp949)에서 못 쓰는 글자로 멈추지 않게
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    b_img, f_img, out = sys.argv[1:4]
    tag = f"moajwo-smoke-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    net, vol, bname, fname = tag + "-net", tag + "-data", tag + "-backend", tag + "-frontend"
    assert vol != PROD_VOLUME
    checks: list[dict] = []
    made: dict[str, list[str]] = {"container": [], "volume": [], "network": []}

    def rec(item: str, ok: bool, detail: object = None) -> None:
        checks.append({"item": item, "ok": bool(ok), "detail": detail})

    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    ids = {"backend": b_img, "frontend": f_img}
    try:
        ids = {k: docker("image", "inspect", img, "--format", "{{.Id}}") for k, img in ids.items()}
        docker("network", "create", "--internal", net)
        made["network"].append(net)
        docker("volume", "create", vol)
        made["volume"].append(vol)
        docker("run", "-d", "--name", bname, "--network", net, "--network-alias", "backend", "-v", f"{vol}:/app/data",
               "-e", "OLLAMA_BASE_URL=http://127.0.0.1:9", "--memory", "1g", ids["backend"])  # fmt: skip
        made["container"].append(bname)
        healthy = wait(lambda: docker("inspect", bname, "--format", "{{.State.Health.Status}}") == "healthy", 150)
        rec("기동: backend healthy (HEALTHCHECK)", healthy, {"state": docker("inspect", bname, "--format", "{{.State.Status}} {{.RestartCount}}", check=False)})
        if healthy:
            docker("run", "-d", "--name", fname, "--network", net, "--network-alias", "frontend", "--memory", "256m", ids["frontend"])
            made["container"].append(fname)
            up = wait(lambda: subprocess.run(["docker", "exec", fname, "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"],
                                             capture_output=True, check=False).returncode == 0, 60)  # fmt: skip
            rec("기동: frontend 응답 (컨테이너 안 8080)", up)
            if up:
                text = docker("exec", "-i", bname, "python", "-I", "-", stdin=CHECK_PY.encode("utf-8"), timeout=900)
                line = next((x for x in text.splitlines() if x.startswith("@@RESULT ")), None)
                if line is None:
                    rec("점검 스크립트 결과", False, {"output_tail": text[-600:]})
                else:
                    checks.extend(json.loads(line[len("@@RESULT ") :]))
        blog = docker("logs", bname, check=False)
        rec("로그: backend Traceback·ERROR 없음", "Traceback" not in blog and " ERROR" not in blog,
            {"traceback": blog.count("Traceback"), "error": blog.count(" ERROR"), "lines": blog.count("\n")})  # fmt: skip
        if fname in made["container"]:
            flog = docker("logs", fname, check=False)
            rec("로그: frontend [error]·[emerg] 없음", "[error]" not in flog and "[emerg]" not in flog,
                {"error": flog.count("[error]"), "emerg": flog.count("[emerg]")})  # fmt: skip
    except Exception as e:  # noqa: BLE001 — 기록하고 정리로 간다
        rec("점검 진행", False, {"error": f"{type(e).__name__}: {str(e)[:400]}"})
    finally:
        for c in made["container"]:
            docker("rm", "-f", c, check=False)
        for v in made["volume"]:
            docker("volume", "rm", v, check=False)
        for n in made["network"]:
            docker("network", "rm", n, check=False)
    left = docker("ps", "-a", "--filter", f"name={tag}", "--format", "{{.Names}}", check=False)
    rec("정리: 점검용 컨테이너·볼륨·네트워크 지움", not left and not docker("volume", "ls", "-q", "--filter", f"name={tag}", check=False),
        {"left": left})  # fmt: skip
    ok = bool(checks) and all(c["ok"] for c in checks)
    result = {"tool": TOOL, "started": started, "ended": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "images": ids,
              "isolation": {"network": net + " (--internal)", "volume": vol + " (새 임시 볼륨)", "host_ports": "열지 않음"},
              "checks": checks, "ok": ok}  # fmt: skip
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    for c in checks:
        print(f"{'통과' if c['ok'] else '실패'} | {c['item']} | {json.dumps(c['detail'], ensure_ascii=False)[:300]}")
    print(f"요약 | 격리 기동 점검 {'통과' if ok else '실패'} {sum(c['ok'] for c in checks)}/{len(checks)} | backend {ids['backend']} | frontend {ids['frontend']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
