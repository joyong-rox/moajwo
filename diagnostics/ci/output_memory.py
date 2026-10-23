"""결과 파일 생성(묶기·통합)의 메모리 — 격리 러너 전용. 운영 PC·운영 Docker 엔진에서 돌리지 않는다.

512MiB 컨테이너(Render 무료와 같은 크기, docs/MEMORY.md의 Render 읽기 한도)에서 두 부분을 잰다.
A. 재현: 결과 칸 예산을 끈 컨테이너(MOAJWO_MAX_OUTPUT_CELLS 아주 큼)에서 묶기 20만 행 → 메모리 부족으로 죽는지, 다시 띄우면
   '실행 중' 기록이 '실패'와 이유로 바뀌고 쓰다 만 임시 파일이 지워지는지 (app/recovery.py).
B. Render 권장 예산(30만 칸): 한도 아래·경계·위의 묶기, 통계 요약을 넣은 묶기('서로 다른 기록' 표시), 큰 묶기 중 다른 요청
   (큰 xlsx 올리기·실행 기록 조회), 검토 → 통합(한도 아래·위). 단계마다 cgroup 누적 최대 메모리·시간·결과 크기.
중단 기준: 컨테이너가 죽으면(B에서) 더 큰 단계는 하지 않는다. 합성 자료만 쓴다. 비밀번호는 실행 때 만들고 출력하지 않는다.
사용법: python3 output_memory.py <A|B> <backend 주소> <컨테이너 이름> <합성 파일 폴더> <출력 폴더>
"""

from __future__ import annotations

import http.cookiejar
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PART, BASE, CONTAINER, FILES, OUT = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAP = {"시도": "sido", "시군구": "sigungu", "인구수": "population"}
PW = "Ci-" + secrets.token_hex(12)


def log(**row: object) -> None:
    row = {"part": PART, **row}
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


class Client:
    def __init__(self) -> None:
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: object = None, files: list[Path] | None = None, timeout: int = 900):
        headers, data = {}, None
        if files is not None:
            b = secrets.token_hex(8)
            parts = [f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"\r\n'
                     f"Content-Type: {XLSX}\r\n\r\n".encode() + f.read_bytes() + b"\r\n" for f in files]  # fmt: skip
            data = b"".join(parts) + f"--{b}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with self.op.open(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in b"[{" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw[:200]
        except Exception as e:  # noqa: BLE001 — 연결이 끊김(컨테이너 종료 등)
            return 0, f"{type(e).__name__}: {e}"[:200]


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True).stdout.strip()


def mem() -> dict:
    peak = docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.peak")
    cur = docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.current")
    ev = docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.events")
    events = dict(line.split() for line in ev.splitlines() if line)
    return {"peak_mib": round(int(peak) / 2**20) if peak.isdigit() else None,
            "current_mib": round(int(cur) / 2**20) if cur.isdigit() else None,
            "oom_kill": int(events["oom_kill"]) if "oom_kill" in events else None}  # fmt: skip


def alive() -> bool:
    return docker("inspect", "--format", "{{.State.Running}}", CONTAINER) == "true"


def state() -> str:
    return docker("inspect", "--format", "running={{.State.Running}} OOMKilled={{.State.OOMKilled}} exit={{.State.ExitCode}}",
                  CONTAINER)  # fmt: skip


def wait_healthy() -> None:
    for _ in range(90):
        if docker("inspect", "--format", "{{.State.Health.Status}}", CONTAINER) == "healthy":
            return
        time.sleep(2)


def upload(c: Client, names: list[str], confirm: bool = True) -> list[int]:
    ids: list[int] = []
    for i in range(0, len(names), 10):
        s, body = c.call("POST", "/files", files=[FILES / n for n in names[i : i + 10]])
        if s != 200:
            raise RuntimeError(f"upload {s} {str(body)[:120]}")
        for x in body:
            if x.get("error") or not x.get("id"):
                raise RuntimeError(f"upload rejected {x.get('original_name')}: {x.get('error')}")
            ids.append(x["id"])
    for fid in ids if confirm else []:
        pv = c.call("GET", f"/files/{fid}/preview?sheet=s0")[1]
        cols = {col["name"]: col["column_id"] for col in pv["columns"]}
        items = [{"sheet_id": "s0", "column_id": cols[n], "field_key": k} for n, k in MAP.items() if n in cols]
        c.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": items})
    return ids


def bundle(c: Client, label: str, fids: list[int], rows: int, summary: bool = False, side: list[str] | None = None) -> int:
    body: dict = {"file_ids": fids}
    if summary:  # 합성 자료는 같은 지역이 여러 행 — '서로 다른 기록'으로 표시해 더한다
        links = [{"file_id": f, "sheet_id": "s0", "rows_are_records": True} for f in fids]
        body["summary"] = {"items": [{"field_key": "population", "aggregates": ["sum", "count"], "links": links}]}
    t = time.monotonic()
    s, plan = c.call("POST", "/bundle/plan", body)
    plan_ms = round((time.monotonic() - t) * 1000)
    if s != 200 or not isinstance(plan, dict) or not plan.get("ready"):
        problems = plan.get("problems") if isinstance(plan, dict) else str(plan)
        rs, run = c.call("POST", "/bundle/run", {**body, "expected_basis": plan.get("basis", "x" * 16)}) if s == 200 else (None, None)
        log(case=label, kind="bundle", rows=rows, summary=summary, plan=s, ready=False, problems=(problems or [])[:2],
            run=rs, run_detail=str(run)[:120] if run else None, plan_ms=plan_ms, **mem(), state=state())  # fmt: skip
        return 409
    others: list[dict] = []
    stop = threading.Event()

    def poke() -> None:  # 결과를 만드는 동안 다른 요청 (같은 계정의 다른 연결)
        other = Client()
        other.op = c.op
        for name in side or []:
            t0 = time.monotonic()
            st, body2 = other.call("POST", "/files", files=[FILES / name], timeout=300)
            ok = st == 200 and isinstance(body2, list) and not body2[0].get("error")
            others.append({"what": f"POST /files {name}", "status": st, "ok": ok, "ms": round((time.monotonic() - t0) * 1000)})
        while not stop.is_set():
            t0 = time.monotonic()
            st, _ = other.call("GET", "/runs", timeout=120)
            others.append({"what": "GET /runs", "status": st, "ms": round((time.monotonic() - t0) * 1000)})
            stop.wait(3)

    th = threading.Thread(target=poke, daemon=True) if side is not None else None
    if th:
        th.start()
    t = time.monotonic()
    s, run = c.call("POST", "/bundle/run", {**body, "expected_basis": plan["basis"]})
    ms = round((time.monotonic() - t) * 1000)
    stop.set()
    if th:
        th.join(330)
    size = None
    if s == 201:
        ds, raw = c.call("GET", f"/runs/{run['id']}/download")
        size = len(raw) if ds == 200 and isinstance(raw, bytes) else f"download {ds}"
    log(case=label, kind="bundle", rows=rows, summary=summary, cells=plan.get("total_rows", rows) * 5, status=s, ms=ms,
        plan_ms=plan_ms, bytes=size, detail=None if s == 201 else str(run)[:160], others=others or None,
        **mem(), state=state())  # fmt: skip
    return s


def integrate(c: Client, label: str, fids: list[int], rows: int) -> int:
    t = time.monotonic()
    s, review = c.call("POST", "/reviews", {"file_ids": fids})
    review_ms = round((time.monotonic() - t) * 1000)
    if s != 201:
        log(case=label, kind="integrate", rows=rows, review=s, detail=str(review)[:160], **mem(), state=state())
        return s
    rid = review["id"]
    for it in review.get("items", []):
        if it.get("category") == "pending":
            cands = (it.get("payload") or {}).get("candidates") or []
            choice = (cands[0].get("code") or cands[0].get("key")) if cands else None
            c.call("POST", f"/reviews/{rid}/items/{it['id']}/decision",
                   {"decision": "approved", "choice": choice} if choice else {"decision": "rejected"})  # fmt: skip
    cs, done = c.call("POST", f"/reviews/{rid}/complete")
    t = time.monotonic()
    s, run = c.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
    ms = round((time.monotonic() - t) * 1000)
    log(case=label, kind="integrate", rows=rows, files=len(fids), review_ms=review_ms, complete=cs, status=s, ms=ms,
        detail=None if s == 201 else str(run if s else done)[:200], **mem(), state=state())  # fmt: skip
    return s


def leftovers(tag: str) -> dict:
    """실행 기록·결과 폴더의 남은 상태 (컨테이너가 멈췄어도 docker cp로 읽는다)."""
    tmp = OUT / f"left-{PART}-{tag}"
    tmp.mkdir(exist_ok=True)
    docker("cp", f"{CONTAINER}:/app/data/moajwo.db", str(tmp / "moajwo.db"))
    docker("cp", f"{CONTAINER}:/app/data/outputs", str(tmp / "outputs"))
    runs, errors = {}, []
    if (tmp / "moajwo.db").exists():
        con = sqlite3.connect(tmp / "moajwo.db")
        runs = dict(con.execute("SELECT status, count(*) FROM runs GROUP BY status").fetchall())
        errors = [r[0] for r in con.execute("SELECT error FROM runs WHERE status = 'failed'")]
        con.close()
        (tmp / "moajwo.db").unlink()  # 합성 자료지만 남기지 않는다
    outs = list((tmp / "outputs").glob("*")) if (tmp / "outputs").exists() else []
    info = {"runs_by_status": runs, "failed_errors": errors, "output_files": len([o for o in outs if o.suffix == ".xlsx"]),
            "tmp_files": [o.name for o in outs if o.name.endswith(".tmp")]}  # fmt: skip
    log(case=f"leftover-{tag}", **info, state=state())
    return info


def names(prefix: str, n: int) -> list[str]:
    return [f"{prefix}_{i}.xlsx" for i in range(n)]


def part_a(c: Client) -> None:
    fids = upload(c, names("b20000", 10))
    bundle(c, "A 묶기 200,000행 (예산 끔)", fids, 200_000)
    before = leftovers("killed")
    if alive():
        log(case="A note", text="죽지 않음 — 재현 안 됨")
        return
    docker("start", CONTAINER)
    wait_healthy()
    after = leftovers("restarted")
    c2 = Client()
    s, _ = c2.call("POST", "/auth/login", {"username": "ci-out", "password": PW})
    st, runs = c2.call("GET", "/runs")
    shown = [(r.get("status"), r.get("status_label"), (r.get("error") or "")[:40]) for r in runs] if isinstance(runs, list) else runs
    log(case="A 재시작 뒤 실행 기록 (화면 API)", login=s, runs=st, shown=shown,
        recovered=before.get("runs_by_status", {}).get("running", 0) > 0 and "running" not in after.get("runs_by_status", {}))  # fmt: skip


def part_b(c: Client) -> None:
    fids = {n: upload(c, names(f"b{n}", 10)) for n in (5000, 6000, 6200)}
    if bundle(c, "B 묶기 50,000행 (250,000칸)", fids[5000], 50_000) != 201:
        return
    bundle(c, "B 묶기+통계 요약 50,000행", fids[5000], 50_000, summary=True)
    if not alive():
        return
    bundle(c, "B 묶기 60,000행 (300,000칸 = 한도) + 동시에 큰 xlsx 올리기·실행 기록 조회", fids[6000], 60_000,
           side=["x_big.xlsx"])  # fmt: skip
    if not alive():
        return
    bundle(c, "B 묶기 62,000행 (310,000칸 > 한도)", fids[6200], 62_000)
    for n_files, per in ((1, 20000), (3, 20000), (4, 20000)):
        fs = upload(c, names(f"i{per}", 4)[:n_files], confirm=True)
        if integrate(c, f"B 통합 {n_files * per:,}행", fs, n_files * per) == 0 or not alive():
            return


def part_c(c: Client) -> None:
    """통계 요약을 넣은 묶기(파일마다 기준 달이 달라 같은 대상이 겹치지 않음, 파일 안의 같은 지역은 '서로 다른 기록')와
    한도 가까운 통합 (8열 × 37,000행 ≈ 29.6만 칸)."""
    for per in (5000, 6000):
        fids = upload(c, names(f"m{per}", 10))
        if bundle(c, f"C 묶기+통계 요약 {per * 10:,}행 ({per * 10 * 5:,}칸)", fids, per * 10, summary=True) == 0 or not alive():
            return
    fs = upload(c, ["i37000_0.xlsx"])
    integrate(c, "C 통합 37,000행 (약 29.6만 칸)", fs, 37_000)


def main() -> None:
    c = Client()
    s, _ = c.call("POST", "/auth/setup", {"username": "ci-out", "display_name": "CI", "password": PW})
    log(case="setup", status=s, **mem())
    try:
        {"A": part_a, "B": part_b, "C": part_c}[PART](c)
    except RuntimeError as exc:
        log(case="stopped", error=str(exc), **mem(), state=state())
    leftovers("end")


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / f"output-memory-{PART}.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
