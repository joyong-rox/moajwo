"""결과 파일 생성(묶기·통합)의 메모리 — 격리 러너 전용. 운영 PC·운영 Docker 엔진에서 돌리지 않는다.

512MiB 컨테이너(Render 무료와 같은 크기, docs/MEMORY.md의 Render 읽기 한도)에 합성 파일을 올리고, 결과 크기를 작은 것부터
늘려 가며 묶기(통계 요약 없음·있음)와 검토 → 통합을 만든다. 단계마다 cgroup 누적 최대 메모리·걸린 시간·결과 크기를 남긴다.
- 중단 기준: 컨테이너가 메모리 부족으로 죽거나(memory.events oom_kill, 컨테이너 종료) 요청이 실패하면 더 큰 단계는 하지 않는다.
- 큰 묶기를 만드는 동안 다른 요청(실행 기록 조회·작은 파일 올리기)을 보내 응답과 시간을 잰다.
- 끝에(죽었으면 다시 띄운 뒤) 남은 상태: '실행 중'으로 남은 실행 기록, outputs의 *.tmp, 완료 기록과 결과 파일 수.
합성 자료만 쓴다. 계정 비밀번호는 실행 때 만들고 출력하지 않는다. 표준 라이브러리만 쓴다 (러너의 python3).
사용법: python3 output_memory.py <backend 주소> <컨테이너 이름> <합성 파일 폴더> <출력 폴더>
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

BASE, CONTAINER, FILES, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAP = {"시도": "sido", "시군구": "sigungu", "인구수": "population"}


def log(**row: object) -> None:
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
    p = subprocess.run(["docker", *args], capture_output=True, text=True)
    return p.stdout.strip()


def mem() -> dict:
    peak = docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.peak")
    cur = docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.current")
    events = dict(line.split() for line in docker("exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.events").splitlines() if line)
    return {"peak_mib": round(int(peak) / 2**20) if peak.isdigit() else None,
            "current_mib": round(int(cur) / 2**20) if cur.isdigit() else None,
            "oom_kill": int(events.get("oom_kill", 0)) if events else None}  # fmt: skip


def alive() -> bool:
    return docker("inspect", "--format", "{{.State.Running}}", CONTAINER) == "true"


def state() -> str:
    return docker("inspect", "--format", "running={{.State.Running}} OOMKilled={{.State.OOMKilled}} exit={{.State.ExitCode}}",
                  CONTAINER)  # fmt: skip


def confirm(c: Client, fid: int) -> None:
    pv = c.call("GET", f"/files/{fid}/preview?sheet=s0")[1]
    cols = {col["name"]: col["column_id"] for col in pv["columns"]}
    items = [{"sheet_id": "s0", "column_id": cols[n], "field_key": k} for n, k in MAP.items() if n in cols]
    c.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": items})


def upload(c: Client, names: list[str]) -> list[int]:
    ids: list[int] = []
    for i in range(0, len(names), 10):
        s, body = c.call("POST", "/files", files=[FILES / n for n in names[i : i + 10]])
        if s != 200:
            raise RuntimeError(f"upload {s} {str(body)[:120]}")
        for x in body:
            if x.get("error") or not x.get("id"):
                raise RuntimeError(f"upload rejected {x.get('original_name')}: {x.get('error')}")
            ids.append(x["id"])
    for fid in ids:
        confirm(c, fid)
    return ids


def bundle(c: Client, label: str, fids: list[int], rows: int, summary: bool, side: bool = False) -> bool:
    body: dict = {"file_ids": fids}
    if summary:
        body["summary"] = {"items": [{"field_key": "population", "aggregates": ["sum", "mean", "min", "max", "count"]}]}
    t = time.monotonic()
    s, plan = c.call("POST", "/bundle/plan", body)
    plan_ms = round((time.monotonic() - t) * 1000)
    if s != 200 or not isinstance(plan, dict) or not plan.get("ready"):
        log(case=label, kind="bundle", rows=rows, files=len(fids), summary=summary, plan=s,
            problems=(plan.get("problems") if isinstance(plan, dict) else str(plan))[:3] if plan else None, **mem())  # fmt: skip
        return s == 200 or s == 409  # 판정 거절은 정상 처리
    others: list[dict] = []
    stop = threading.Event()

    def poke() -> None:  # 결과를 만드는 동안 다른 요청
        other = Client()
        other.op = c.op
        while not stop.is_set():
            t0 = time.monotonic()
            st, _ = other.call("GET", "/runs", timeout=120)
            others.append({"what": "GET /runs", "status": st, "ms": round((time.monotonic() - t0) * 1000)})
            if len(others) == 1:
                t0 = time.monotonic()
                st, _ = other.call("POST", "/files", files=[FILES / "small.xlsx"], timeout=120)
                others.append({"what": "POST /files 작은 파일", "status": st, "ms": round((time.monotonic() - t0) * 1000)})
            stop.wait(2)

    th = threading.Thread(target=poke, daemon=True) if side else None
    if th:
        th.start()
    t = time.monotonic()
    s, run = c.call("POST", "/bundle/run", {**body, "expected_basis": plan["basis"]})
    ms = round((time.monotonic() - t) * 1000)
    stop.set()
    if th:
        th.join(130)
    size = None
    if s == 201:
        ds, raw = c.call("GET", f"/runs/{run['id']}/download")
        size = len(raw) if ds == 200 and isinstance(raw, bytes) else f"download {ds}"
    log(case=label, kind="bundle", rows=rows, files=len(fids), summary=summary, sheets=plan.get("sheet_count"),
        cells=rows * 5, status=s, ms=ms, plan_ms=plan_ms, bytes=size,
        detail=(None if s == 201 else str(run)[:160]), others=others or None, **mem(), state=state())  # fmt: skip
    return s == 201 and alive()


def integrate(c: Client, label: str, fids: list[int], rows: int) -> bool:
    t = time.monotonic()
    s, review = c.call("POST", "/reviews", {"file_ids": fids})
    review_ms = round((time.monotonic() - t) * 1000)
    if s != 201:
        log(case=label, kind="integrate", rows=rows, review=s, detail=str(review)[:160], **mem(), state=state())
        return False
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
        detail=(None if s == 201 else str(run if s else done)[:160]), **mem(), state=state())  # fmt: skip
    return s == 201 and alive()


def leftovers(tag: str) -> None:
    """실행 기록·결과 폴더의 남은 상태 (컨테이너가 멈췄어도 docker cp로 읽는다)."""
    tmp = OUT / f"left-{tag}"
    tmp.mkdir(exist_ok=True)
    docker("cp", f"{CONTAINER}:/app/data/moajwo.db", str(tmp / "moajwo.db"))
    docker("cp", f"{CONTAINER}:/app/data/outputs", str(tmp / "outputs"))
    runs = {}
    if (tmp / "moajwo.db").exists():
        con = sqlite3.connect(tmp / "moajwo.db")
        runs = dict(con.execute("SELECT status, count(*) FROM runs GROUP BY status").fetchall())
        con.close()
    outs = list((tmp / "outputs").glob("*")) if (tmp / "outputs").exists() else []
    log(case=f"leftover-{tag}", runs_by_status=runs, output_files=len([o for o in outs if o.suffix == ".xlsx"]),
        tmp_files=[o.name for o in outs if o.name.endswith(".tmp")], state=state())  # fmt: skip
    (tmp / "moajwo.db").unlink(missing_ok=True)  # 합성 자료지만 남기지 않는다


def main() -> None:
    c = Client()
    s, _ = c.call("POST", "/auth/setup", {"username": "ci-out", "display_name": "CI", "password": "Ci-" + secrets.token_hex(12)})
    log(case="setup", status=s, **mem())
    # 단계: 파일 10개에 나눈 결과 행 합계 (묶기 한도 300,000행). 칸 = 행 × 5 (원본 행·행 종류 + 3열)
    stages = [(10, 2_000), (10, 5_000), (10, 10_000), (10, 15_000), (10, 20_000), (10, 30_000)]
    ok = True
    for n_files, per_file in stages:
        rows = n_files * per_file
        names = [f"b{per_file}_{i}.xlsx" for i in range(n_files)]
        try:
            fids = upload(c, names)
        except RuntimeError as exc:
            log(case=f"upload {rows}", error=str(exc), **mem(), state=state())
            ok = False
            break
        ok = bundle(c, f"묶기 {rows:,}행", fids, rows, summary=False, side=per_file >= 10_000)
        if not ok:
            break
        ok = bundle(c, f"묶기+요약 {rows:,}행", fids, rows, summary=True)
        if not ok:
            break
    leftovers("after-bundle")
    if not alive():
        docker("start", CONTAINER)  # 재시작 — '실행 중'으로 남은 기록이 그대로인지 본다
        for _ in range(60):
            if docker("inspect", "--format", "{{.State.Health.Status}}", CONTAINER) == "healthy":
                break
            time.sleep(2)
        leftovers("after-restart")
        c = Client()
        log(case="note", text="재시작 뒤 새 세션 없이 측정 중단 (계정 비밀번호를 남기지 않음)")
        return
    for per_file, n_files in ((5_000, 1), (5_000, 4), (20_000, 2), (30_000, 3)):
        names = [f"b{per_file}_{i}.xlsx" for i in range(n_files)]
        try:
            fids = upload(c, names)
        except RuntimeError as exc:
            log(case="upload integrate", error=str(exc), **mem(), state=state())
            break
        if not integrate(c, f"통합 {per_file * n_files:,}행", fids, per_file * n_files):
            break
    leftovers("end")


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "output-memory.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
