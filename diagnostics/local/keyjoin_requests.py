"""공통 key 연결의 요청 번호(request_id) 재전송·동시 실행을 실제 API·별도 프로세스로 확인한다 (2026-10-27 지시 3-1).

같은 데이터 폴더·같은 DB를 쓰는 격리 backend 프로세스 2개(A·B)를 keyjoin_hook_server.py로 띄우고(시작점 맞춤·응답 지연·
강제 종료·주입 실패 장치), 새 소유자와 새 일반 계정 2개로 합성 CSV를 올려 시나리오를 돌린다. 경우마다 응답 코드·실행 번호·
같은 요청 번호의 실행 수·결과 파일 수·상태를 기록하고 '수정 뒤 정책'의 기대와 대조한다 — 수정 전 코드에서 돌리면 결함이
'기대 어긋남'으로 남는다. 동시성은 sleep 운이 아니라 제어 파일로 순서를 정한다.

- 운영(8080·8000)·외부 요청 없음. 합성 자료만. DB 주소는 환경 변수(DATABASE_URL)로만 받고 출력·기록하지 않는다.
- 끝나면 띄운 프로세스(기록한 PID)만 멈춘다. 포트가 이미 쓰이면 시작하지 않는다.

사용법: python keyjoin_requests.py --backend <backend 폴더> --out <증거 폴더> --data <새 데이터 폴더> [--db sqlite|pg]
        [--ports 8181,8182] [--only S1,S2,...]   (pg: 호출 쪽이 DATABASE_URL·MOAJWO_MIGRATION_DATABASE_URL을 넘긴다)
"""

import argparse
import contextlib
import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--backend", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--db", default="sqlite", choices=["sqlite", "pg"])
ap.add_argument("--ports", default="8181,8182")
ap.add_argument("--only", default="")
ARGS = ap.parse_args()
BACKEND = Path(ARGS.backend).resolve()
OUT = Path(ARGS.out)
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path(ARGS.data)
CTL = DATA.parent / (DATA.name + "-ctl")
PORTS = [int(p) for p in ARGS.ports.split(",")]
ONLY = {s for s in ARGS.only.split(",") if s}
LOG = OUT / "requests.log"
RESULTS: list[dict] = []
PROCS: dict[str, subprocess.Popen] = {}
PW = {"owner": uuid.uuid4().hex + "Aa1!", "u1": uuid.uuid4().hex + "Aa1!", "u2": uuid.uuid4().hex + "Aa1!"}


def say(text: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def base_env() -> dict:
    drop = ("MOAJWO_TEST_DATABASE_URL", "MOAJWO_TEST_PG_ADMIN_URL")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    if ARGS.db == "sqlite":
        for k in ("DATABASE_URL", "MOAJWO_DB_MODE", "MOAJWO_MIGRATION_DATABASE_URL"):
            env.pop(k, None)
    else:
        assert env.get("DATABASE_URL"), "pg 모드는 DATABASE_URL이 필요 (호출 쪽이 넘긴다)"
        env["MOAJWO_DB_MODE"] = "external"
        env.setdefault("MOAJWO_MIGRATION_DATABASE_URL", env["DATABASE_URL"])
    env.update(MOAJWO_DATA_DIR=str(DATA), OLLAMA_BASE_URL="http://127.0.0.1:9", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1", KJ_CTL=str(CTL))  # fmt: skip
    return env


def start(name: str, port: int) -> None:
    log = (OUT / f"server-{name}.log").open("a", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(HERE / "keyjoin_hook_server.py"), str(BACKEND), str(port)], cwd=BACKEND,
                         env=base_env(), stdout=log, stderr=subprocess.STDOUT)  # fmt: skip
    PROCS[name] = p
    for _ in range(120):
        if p.poll() is not None:
            raise SystemExit(f"{name} 서버가 바로 끝남 (종료 {p.returncode}) — server-{name}.log")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    say(f"서버 {name} 시작: 포트 {port} · PID {p.pid}")
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise SystemExit(f"{name} 서버 준비 시간 초과")


def stop(name: str) -> None:
    p = PROCS.pop(name, None)
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(10)
        say(f"서버 {name} 멈춤 (PID {p.pid})")


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, port: int, method: str, path: str, body=None, data=None, headers=None, timeout: float = 300):  # noqa: ANN001
        if body is not None:
            data, headers = json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers or {})
        try:
            with self.op.open(req, timeout=timeout) as r:
                c = r.read()
                return r.status, json.loads(c) if c[:1] in (b"{", b"[") else c
        except urllib.error.HTTPError as e:
            c = e.read()
            return e.code, json.loads(c) if c[:1] in (b"{", b"[") else c

    def upload(self, port: int, name: str, content: bytes, ctype: str = "text/csv") -> int:
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()  # fmt: skip
        s, j = self.call(port, "POST", "/files", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        assert s == 200 and j[0].get("id"), (s, str(j)[:200])
        return j[0]["id"]


def csv(rows: list[list]) -> bytes:
    return ("\n".join(",".join(str(c) for c in r) for r in rows) + "\n").encode("utf-8-sig")


# ---------------------------------------------------------------- DB·파일 상태 (읽기 전용)


def db_rows(sql: str, args: tuple = ()) -> list[tuple]:
    if ARGS.db == "sqlite":
        con = sqlite3.connect(f"file:{DATA / 'moajwo.db'}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    import psycopg  # noqa: PLC0415 — pg 모드에서만

    with psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")) as con:
        return con.execute(sql.replace("?", "%s"), args).fetchall()


def runs_for(rid: str) -> list[tuple]:
    """같은 요청 번호의 key 연결 실행 (요약 JSON의 request_id — 수정 전·후 공통으로 셀 수 있는 근거)."""
    rows = db_rows("SELECT id, user_id, status, summary FROM runs ORDER BY id")
    out = []
    for rid_, uid, status, summary in rows:
        try:
            s = json.loads(summary or "{}")
        except ValueError:
            continue
        if isinstance(s, dict) and s.get("mode") == "keyjoin" and s.get("request_id") == rid:
            out.append((rid_, uid, status))
    return out


def outputs() -> list[str]:
    d = DATA / "outputs"
    return sorted(p.name for p in d.glob("*") if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []


def runs_total() -> int:
    return db_rows("SELECT count(*) FROM runs")[0][0]


# ---------------------------------------------------------------- key 연결 요청


def kj_body(fids: list[int], scope: str = "base", cols: dict[int, list[str]] | None = None,
            names: dict[tuple[int, str], str] | None = None, base: int = 0) -> dict:  # fmt: skip
    inputs = []
    for fid in fids:
        pick = (cols or {}).get(fid, ["c1"])
        inputs.append({"file_id": fid, "sheet_id": "s0", "key_columns": ["c0"],
                       "columns": [{"column_id": c, "out_name": (names or {}).get((fid, c))} for c in pick]})  # fmt: skip
    return {"inputs": inputs, "scope": scope, "base": {"file_id": fids[base], "sheet_id": "s0"}}


def plan(c: Client, port: int, body: dict) -> dict:
    s, j = c.call(port, "POST", "/keyjoin/plan", body)
    assert s == 200 and j.get("ready"), (s, str(j)[:300])
    return j


def run(c: Client, port: int, body: dict, basis: str, rid: str, timeout: float = 300) -> tuple[int, dict | str]:
    try:
        s, j = c.call(port, "POST", "/keyjoin/run", {**body, "expected_basis": basis, "request_id": rid}, timeout=timeout)
    except TimeoutError:
        return -1, "client timeout (응답 유실 흉내)"
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        return -2, f"connection error {type(e).__name__}"
    return s, j


def brief(code: int, j) -> str:  # noqa: ANN001
    if isinstance(j, dict):
        d = j.get("detail")
        if isinstance(d, dict):
            return f"{code} {d.get('code')} run_id={d.get('run_id')}"
        if "id" in j:
            return f"{code} run #{j['id']} {j.get('status')}"
    return f"{code} {str(j)[:80]}"


def record(sid: str, what: str, ok: bool, detail: dict) -> None:
    RESULTS.append({"scenario": sid, "check": what, "expected_met": ok, "detail": detail})
    say(f"{'기대 충족' if ok else '기대 어긋남'} | {sid} | {what} | {json.dumps(detail, ensure_ascii=False)[:600]}")


def ctl(name: str, text: str = "") -> Path:
    p = CTL / name
    p.write_text(text, encoding="utf-8")
    return p


def wait_for(pred, limit: float = 120, step: float = 0.05) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


# ---------------------------------------------------------------- 시나리오


def setup() -> dict:
    A, B = PORTS
    owner = Client()
    s, _ = owner.call(A, "POST", "/auth/setup", {"username": "kjreq-owner", "display_name": "요청 시험 소유자", "password": PW["owner"]})
    assert s == 201, s
    for u in ("u1", "u2"):
        s, j = owner.call(A, "POST", "/users", {"username": f"kjreq-{u}", "display_name": f"요청 시험 {u}", "status": "active", "password": PW[u]})
        assert s == 201, (s, str(j)[:200])
    g = "코드,정식명칭,유형\n1741000,행정안전부,중앙행정기관\n1352000,보건복지부,중앙행정기관\n"
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="g.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() + g.encode() + f"\r\n--{b}--\r\n".encode()
    s, j = owner.call(A, "POST", "/glossary/import", data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    assert s == 200, (s, str(j)[:200])
    users = {}
    for u in ("u1", "u2"):
        c = Client()
        s, _ = c.call(A, "POST", "/auth/login", {"username": f"kjreq-{u}", "password": PW[u]})
        assert s == 200, s
        users[u] = c
    u1, u2 = users["u1"], users["u2"]
    f = {
        "a": u1.upload(A, "A.csv", csv([["사번", "부서", "비고"], ["001", "총무", "x"], ["002", "기획", "y"], ["003", "정보", "z"]])),
        "b": u1.upload(A, "B.csv", csv([["사용자번호", "교육 건수", "메모"], ["001", 10, "m"], ["003", 30, "n"], ["004", 40, "o"]])),
        "c": u1.upload(A, "C.csv", csv([["사용자ID", "출장 건수", "기타"], ["001", 1, "p"], ["002", 2, "q"], ["005", 5, "r"]])),
        "u2a": u2.upload(A, "U2A.csv", csv([["사번", "부서"], ["101", "총무"], ["102", "기획"]])),
        "u2b": u2.upload(A, "U2B.csv", csv([["사번", "건수"], ["101", 1], ["103", 3]])),
        "bundle": u1.upload(A, "묶기.csv", csv([["시도", "인구"], ["서울", 100], ["부산", 200]])),
        "integ": u1.upload(A, "통합.csv", csv([["기관명", "집행액"], ["행정안전부", 1000], ["보건복지부", 2500]])),
    }
    say(f"준비: 소유자·일반 계정 2개·합성 파일 {len(f)}개 (DB {ARGS.db}, 서버 A {A}·B {B})")
    return {"u1": u1, "u2": u2, "f": f}


def s1(ctx) -> None:  # noqa: ANN001
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["a"], f["b"]])
    p = plan(u1, A, body)
    rid, before = uuid.uuid4().hex, outputs()
    c1, j1 = run(u1, A, body, p["basis"], rid)
    c2, j2 = run(u1, A, body, p["basis"], rid)
    runs = runs_for(rid)
    ok = c1 == 201 and c2 == 200 and isinstance(j2, dict) and j2.get("id") == j1.get("id") and len(runs) == 1 and len(outputs()) == len(before) + 1
    record("S1", "같은 번호·같은 내용 재전송 → 201 뒤 200(같은 실행), 실행 1·파일 +1", ok,
           {"first": brief(c1, j1), "resend": brief(c2, j2), "runs_for_rid": len(runs), "new_files": len(outputs()) - len(before)})  # fmt: skip
    ctx["s1"] = {"rid": rid, "run": j1.get("id"), "body": body, "basis": p["basis"]}


def s2(ctx) -> None:  # noqa: ANN001
    """완료 뒤 다른 실행을 50개 넘게(key 연결·묶기·통합 섞어) 만든 다음 처음 요청을 재전송."""
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    first = ctx.get("s1") or {}
    if not first:
        s1(ctx)
        first = ctx["s1"]
    made = {"keyjoin": 0, "bundle": 0, "integration": 0}
    kbody = kj_body([f["a"], f["c"]])
    kp = plan(u1, A, kbody)
    for _ in range(44):
        c, _j = run(u1, A, kbody, kp["basis"], uuid.uuid4().hex)
        made["keyjoin"] += c == 201
    bbody = {"file_ids": [f["bundle"]]}
    for _ in range(6):
        s, bp = u1.call(A, "POST", "/bundle/plan", bbody)
        if s == 200 and bp.get("ready"):
            s, _j = u1.call(A, "POST", "/bundle/run", {**bbody, "expected_basis": bp["basis"]})
            made["bundle"] += s == 201
    for _ in range(2):
        s, rv = u1.call(A, "POST", "/reviews", {"file_ids": [f["integ"]]})
        if s not in (200, 201):
            continue
        for item in [i for i in rv["items"] if i["category"] == "pending"]:
            cands = item["payload"].get("candidates") or []
            dec = {"decision": "approved", "choice": cands[0]["code"]} if (item["kind"] != "mapping" and cands) else {"decision": "rejected"}
            u1.call(A, "POST", f"/reviews/{rv['id']}/items/{item['id']}/decision", dec)
        u1.call(A, "POST", f"/reviews/{rv['id']}/complete")
        s, _j = u1.call(A, "POST", f"/reviews/{rv['id']}/integrate")
        made["integration"] += s == 201
    later = runs_total()
    before = outputs()
    c, j = run(u1, A, first["body"], first["basis"], first["rid"])
    runs = runs_for(first["rid"])
    ok = c == 200 and isinstance(j, dict) and j.get("id") == first["run"] and len(runs) == 1 and outputs() == before
    record("S2", "다른 실행 50개 넘게(key 연결·묶기·통합) 뒤 처음 요청 재전송 → 200(처음 실행), 새 실행·파일 없음", ok,
           {"made_between": made, "runs_total": later, "resend": brief(c, j), "runs_for_rid": len(runs), "new_files": len(outputs()) - len(before)})  # fmt: skip


def s3(ctx) -> None:  # noqa: ANN001
    """같은 번호·처음 expected_basis를 그대로 두고 가져올 열·범위·결과 이름·기준 입력을 하나씩 바꿔 재전송."""
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["a"], f["b"]])
    p = plan(u1, A, body)
    rid = uuid.uuid4().hex
    c0, j0 = run(u1, A, body, p["basis"], rid)
    variants = {
        "가져올 열": kj_body([f["a"], f["b"]], cols={f["a"]: ["c1", "c2"]}),
        "범위": kj_body([f["a"], f["b"]], scope="all"),
        "결과 이름": kj_body([f["a"], f["b"]], names={(f["b"], "c1"): "교육 횟수"}),
        "기준 입력": kj_body([f["a"], f["b"]], base=1),
    }
    for label, vb in variants.items():
        before = outputs()
        c, j = run(u1, A, vb, p["basis"], rid)
        code = j.get("detail", {}).get("code") if isinstance(j, dict) and isinstance(j.get("detail"), dict) else None
        runs = runs_for(rid)
        ok = c == 409 and code == "KEYJOIN_REQUEST_MISMATCH" and len(runs) == 1 and outputs() == before
        record("S3", f"같은 번호·처음 basis로 {label}만 바꿈 → 409 KEYJOIN_REQUEST_MISMATCH, 새 실행·파일 없음", ok,
               {"first": brief(c0, j0), "changed": brief(c, j), "runs_for_rid": len(runs), "new_files": len(outputs()) - len(before)})  # fmt: skip


def s4(ctx) -> None:  # noqa: ANN001
    """서로 다른 프로세스 A·B가 같은 DB로 같은 번호를 동시에 — 둘 다 계획 다시 판정 지점에 세운 뒤 함께 놓는다."""
    A, B = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["a"], f["c"]])
    p = plan(u1, A, body)
    rid, before = uuid.uuid4().hex, outputs()
    ctl(f"armed-plan-{rid}", "2")
    res: dict[str, tuple] = {}

    def go(name: str, port: int) -> None:
        res[name] = run(u1, port, body, p["basis"], rid)

    ts = [threading.Thread(target=go, args=(n, port)) for n, port in (("A", A), ("B", B))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(300)
    codes = sorted(res[n][0] for n in res)
    ids = {res[n][1].get("id") for n in res if isinstance(res[n][1], dict) and "id" in res[n][1]}
    runs = runs_for(rid)
    arrived = len(list(CTL.glob(f"arrived-plan-{rid}-*")))
    ok = codes == [200, 201] and len(ids) == 1 and len(runs) == 1 and len(outputs()) == len(before) + 1
    record("S4", "다른 프로세스 2개·같은 번호 동시(둘 다 판정 지점에서 만남) → 201 하나 + 200(같은 실행), 실행 1·파일 +1", ok,
           {"A": brief(*res.get("A", (0, ""))), "B": brief(*res.get("B", (0, ""))), "met_at_gate": arrived,
            "runs_for_rid": [list(r) for r in runs], "new_files": len(outputs()) - len(before)})  # fmt: skip
    # 같은 프로세스 안의 둘째: 첫 요청을 판정 지점에 붙잡고 둘째를 보낸다 → 503(시작 안 함)
    rid2, before = uuid.uuid4().hex, outputs()
    hold = ctl(f"hold-plan-{rid2}")
    out: dict[str, tuple] = {}
    t = threading.Thread(target=lambda: out.__setitem__("first", run(u1, A, body, p["basis"], rid2)))
    t.start()
    held = wait_for(lambda: bool(list(CTL.glob(f"passed-plan-held-{rid2}-*"))), 60)
    second = run(u1, A, body, p["basis"], rid2)
    hold.unlink()
    t.join(300)
    first = out.get("first", (0, ""))
    det = second[1].get("detail", {}) if isinstance(second[1], dict) else {}
    ok = held and first[0] == 201 and second[0] == 503 and isinstance(det, dict) and det.get("code") == "output_busy" and len(runs_for(rid2)) == 1
    record("S4b", "같은 프로세스·같은 번호 둘째(첫 요청이 판정 중) → 503 output_busy(시작 안 함), 첫 요청 201", ok,
           {"first": brief(*first), "second": brief(*second), "runs_for_rid": len(runs_for(rid2)), "new_files": len(outputs()) - len(before)})  # fmt: skip


def s5(ctx) -> None:  # noqa: ANN001
    """응답 유실: 결과 파일을 쓴 뒤 완료 기록 직전에 붙잡고 클라이언트가 기다리다 끊는다 → 재전송."""
    A, B = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["a"], f["b"]], scope="all")
    p = plan(u1, A, body)
    rid, before = uuid.uuid4().hex, outputs()
    hold = ctl(f"hold-prepare-{rid}")
    out: dict[str, tuple] = {}
    t = threading.Thread(target=lambda: out.__setitem__("first", run(u1, A, body, p["basis"], rid, timeout=3)))
    t.start()
    t.join(60)
    held = wait_for(lambda: bool(list(CTL.glob(f"passed-prepare-held-{rid}-*"))), 60)
    again_a = run(u1, A, body, p["basis"], rid)
    again_b = run(u1, B, body, p["basis"], rid)
    hold.unlink()
    wait_for(lambda: any(r[2] != "running" for r in runs_for(rid)), 120)
    final = run(u1, A, body, p["basis"], rid)
    runs = runs_for(rid)
    da = again_a[1].get("detail", {}) if isinstance(again_a[1], dict) else {}
    ok = (out.get("first", (0,))[0] == -1 and held and again_a[0] == 503 and isinstance(da, dict) and da.get("code") == "output_busy"
          and again_b[0] == 200 and isinstance(again_b[1], dict) and again_b[1].get("status") == "running"
          and final[0] == 200 and isinstance(final[1], dict) and final[1].get("status") == "done"
          and len(runs) == 1 and len(outputs()) == len(before) + 1)  # fmt: skip
    record("S5", "응답 유실 뒤 재전송: 처리 중 같은 프로세스 503·다른 프로세스 200(실행 중 — 완료 아님), 끝난 뒤 200(완료), 실행 1·파일 +1", ok,
           {"first": brief(*out.get("first", (0, ""))), "held": held, "again_same_process": brief(*again_a), "again_other_process": brief(*again_b),
            "after_done": brief(*final), "runs_for_rid": [list(r) for r in runs], "new_files": len(outputs()) - len(before)})  # fmt: skip


def s6(ctx) -> None:  # noqa: ANN001
    """서버가 결과를 만들다 죽음 → 다시 띄움(기동 정리) → 같은 번호 재전송: 새 실행 없이 기존 실행의 상태(완료 아님)."""
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["b"], f["c"]])
    p = plan(u1, A, body)
    rid, before = uuid.uuid4().hex, outputs()
    ctl(f"kill-{rid}")
    c, j = run(u1, A, body, p["basis"], rid, timeout=60)
    killed = (CTL / f"killed-{rid}").exists()
    (CTL / f"kill-{rid}").unlink()
    PROCS.pop("A", None)
    start("A", A)
    settled = wait_for(lambda: any(r[2] != "running" for r in runs_for(rid)), 120)
    runs_mid = runs_for(rid)
    c2, j2 = run(u1, A, body, p["basis"], rid)
    runs = runs_for(rid)
    status = runs[0][2] if runs else None
    code2 = j2.get("detail", {}).get("code") if isinstance(j2, dict) and isinstance(j2.get("detail"), dict) else None
    ok = (killed and c == -2 and settled and len(runs) == 1 and status in ("check", "failed")
          and ((status == "check" and c2 == 200 and isinstance(j2, dict) and j2.get("status") == "check")
               or (status == "failed" and c2 == 409 and code2 == "KEYJOIN_REQUEST_USED")))  # fmt: skip
    record("S6", "만드는 중 서버 종료 → 재기동 정리 → 재전송: 새 실행 없음, '결과 확인 필요'(200·check) 또는 실패(409 USED) — 완료로 보이지 않음", ok,
           {"first": brief(c, j), "killed": killed, "settled": settled, "status_after_restart": [list(r) for r in runs_mid],
            "resend": brief(c2, j2), "runs_for_rid": len(runs), "files": outputs()[-3:], "new_files": len(outputs()) - len(before)})  # fmt: skip


def s7(ctx) -> None:  # noqa: ANN001
    """생성 시작 뒤 실패 → 같은 번호 재전송은 409 KEYJOIN_REQUEST_USED(실패 기록 유지), 새 번호는 새 실행."""
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["a"], f["b"], f["c"]])
    p = plan(u1, A, body)
    rid, before = uuid.uuid4().hex, outputs()
    ctl(f"fail-{rid}")
    c, j = run(u1, A, body, p["basis"], rid)
    (CTL / f"fail-{rid}").unlink()
    c2, j2 = run(u1, A, body, p["basis"], rid)
    runs = runs_for(rid)
    code = j.get("detail", {}).get("code") if isinstance(j, dict) and isinstance(j.get("detail"), dict) else None
    code2 = j2.get("detail", {}).get("code") if isinstance(j2, dict) and isinstance(j2.get("detail"), dict) else None
    ok = c == 409 and code == "KEYJOIN_FAILED" and c2 == 409 and code2 == "KEYJOIN_REQUEST_USED" and len(runs) == 1 and runs[0][2] == "failed" and outputs() == before
    record("S7", "시작 뒤 실패 → 409 KEYJOIN_FAILED(실패 기록), 같은 번호 재전송 → 409 KEYJOIN_REQUEST_USED, 결과 파일 없음", ok,
           {"first": brief(c, j), "resend": brief(c2, j2), "runs_for_rid": [list(r) for r in runs], "new_files": len(outputs()) - len(before)})  # fmt: skip


def s8(ctx) -> None:  # noqa: ANN001
    """두 사용자가 우연히 같은 번호 → 각자 자기 실행 (사용자별 범위)."""
    A, B = PORTS
    u1, u2, f = ctx["u1"], ctx["u2"], ctx["f"]
    rid = uuid.uuid4().hex
    b1, b2 = kj_body([f["a"], f["b"]]), kj_body([f["u2a"], f["u2b"]])
    p1, p2 = plan(u1, A, b1), plan(u2, B, b2)
    c1, j1 = run(u1, A, b1, p1["basis"], rid)
    c2, j2 = run(u2, B, b2, p2["basis"], rid)
    runs = runs_for(rid)
    ok = c1 == 201 and c2 == 201 and len(runs) == 2 and len({r[1] for r in runs}) == 2
    record("S8", "두 사용자·같은 번호 → 각자 201, 사용자마다 실행 1", ok, {"u1": brief(c1, j1), "u2": brief(c2, j2), "runs_for_rid": [list(r) for r in runs]})


def s9(ctx) -> None:  # noqa: ANN001
    """완료된 실행의 결과 파일이 없어진 뒤 재전송 → 같은 실행(200), 다시 만들지 않음, 내려받기 410."""
    A, _ = PORTS
    u1, f = ctx["u1"], ctx["f"]
    body = kj_body([f["b"], f["a"]])
    p = plan(u1, A, body)
    rid = uuid.uuid4().hex
    c, j = run(u1, A, body, p["basis"], rid)
    path = db_rows("SELECT output_path FROM runs WHERE id = ?", (j["id"],))[0][0]
    target = DATA / path
    os.chmod(target, stat.S_IWRITE)
    target.unlink()
    before = outputs()
    c2, j2 = run(u1, A, body, p["basis"], rid)
    s, _d = u1.call(A, "GET", f"/runs/{j['id']}/download")
    runs = runs_for(rid)
    ok = c == 201 and c2 == 200 and isinstance(j2, dict) and j2.get("id") == j["id"] and s == 410 and len(runs) == 1 and outputs() == before
    record("S9", "결과 파일이 없어진 완료 실행의 재전송 → 200(같은 실행)·다시 만들지 않음·내려받기 410", ok,
           {"first": brief(c, j), "resend": brief(c2, j2), "download": s, "runs_for_rid": len(runs), "new_files": len(outputs()) - len(before)})  # fmt: skip


SCENARIOS = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5, "S6": s6, "S7": s7, "S8": s8, "S9": s9}


def main() -> int:
    for port in PORTS:
        if busy(port):
            say(f"포트 {port}가 이미 쓰이고 있어 시작하지 않음 (다른 프로세스는 건드리지 않음)")
            return 97
    for d in (DATA, CTL):
        if d.exists():
            def writable(func, path, _exc) -> None:  # noqa: ANN001
                os.chmod(path, stat.S_IWRITE)
                func(path)

            shutil.rmtree(d, onexc=writable)
        d.mkdir(parents=True)
    head = subprocess.run(["git", "-C", str(BACKEND), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(BACKEND), "status", "--short", "--", "."], capture_output=True, text=True).stdout.strip()
    say(f"코드 {BACKEND} HEAD {head} (backend 작업 중 변경 {'있음' if dirty else '없음'}) · DB {ARGS.db} · 데이터 {DATA}")
    with (OUT / "alembic.log").open("a", encoding="utf-8") as h:
        rc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=base_env(), stdout=h, stderr=subprocess.STDOUT).returncode
    say(f"alembic upgrade head → {rc}")
    if rc:
        return 96
    try:
        start("A", PORTS[0])
        start("B", PORTS[1])
        ctx = setup()
        for sid, fn in SCENARIOS.items():
            if ONLY and sid not in ONLY:
                continue
            try:
                fn(ctx)
            except Exception as e:  # noqa: BLE001 — 시나리오 하나의 도구 오류가 나머지를 막지 않게 (오류로 기록)
                record(sid, "시나리오 실행 오류 (도구 또는 제품 — 기록 확인)", False, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
    finally:
        for name in list(PROCS):
            stop(name)
        meta = {"head": head, "backend_dirty": bool(dirty), "db": ARGS.db, "results": RESULTS}
        (OUT / "requests.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        met = sum(r["expected_met"] for r in RESULTS)
        say(f"요약 | {met}/{len(RESULTS)} 기대 충족 | 어긋남 {[r['scenario'] + ' ' + r['check'][:30] for r in RESULTS if not r['expected_met']]}")
    return 0 if all(r["expected_met"] for r in RESULTS) else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
