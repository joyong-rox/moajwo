"""결과 파일 생성(묶기·통합)의 메모리 — 격리 러너 전용. 운영 PC·운영 Docker 엔진에서 돌리지 않는다.

512MiB 컨테이너(Render 무료와 같은 크기, docs/MEMORY.md의 Render 읽기 한도)에서 두 부분을 잰다.
A. 재현: 결과 칸 예산을 끈 컨테이너(MOAJWO_MAX_OUTPUT_CELLS 아주 큼)에서 묶기 20만 행 → 메모리 부족으로 죽는지, 다시 띄우면
   '실행 중' 기록이 '실패'와 이유로 바뀌고 쓰다 만 임시 파일이 지워지는지 (app/recovery.py).
B. Render 권장 예산(30만 칸): 한도 아래·경계·위의 묶기, 통계 요약을 넣은 묶기('서로 다른 기록' 표시), 큰 묶기 중 다른 요청
   (큰 xlsx 올리기·실행 기록 조회), 검토 → 통합(한도 아래·위). 단계마다 cgroup 누적 최대 메모리·시간·결과 크기.
D. (사례마다 새 컨테이너, 예산 30만 칸) D1 묶기+묶기 동시 · D2 묶기+통합 동시 · D3 긴 글자(CSV 고유 글자, 공백이 붙은
   공유 문자열 — Excel 방식으로 직접 만든 xlsx) · D4 넓은 표 묶기·통합 · D5 큰 통계 요약(한도 안 실행, 한도 넘는 계획)
   · D6 분석 Excel 내려받기(하나·둘 동시) · D7 작은 xlsx의 큰 병합 범위 · D3L 로컬 기본 한도의 공유 긴 글자
   · D8 결과 만들기 차례 2개에서 묶기 둘 + 큰 xlsx 올리기를 동시에.
E. 최종 Render 권장값(결과 글자 3,200만·결과 차례 1개·읽기 차례 1개)에서 남은 경계: E1 칸마다 다른 긴 한글 여러 파일(묶기·
   통합 허용·초과 거절·거절 뒤 다음 정상 요청) · E2 결과 한 건(통합, 예산 바로 아래) + 큰 xlsx 올리기를 같은 순간에 · E3A~C
   읽기 차례를 기다리는 요청 — 파일 셋을 읽는 검토 생성(A)·큰 업로드(B)를 따로, 그리고 겹쳐(C) 잰다.
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
        if files is not None and all(f.suffix == ".csv" for f in files):
            data = data.replace(f"Content-Type: {XLSX}".encode(), b"Content-Type: text/csv")
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
    events = dict(line.split() for line in ev.splitlines() if len(line.split()) == 2)  # 컨테이너가 멈추면 오류 문구
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


def upload(c: Client, names: list[str], confirm: bool = True, mapping: dict[str, str] | None = None) -> list[int]:
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
        items = [{"sheet_id": "s0", "column_id": cols[n], "field_key": k} for n, k in (mapping or MAP).items() if n in cols]
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


def part_a(c: Client, files: int = 10) -> None:
    fids = upload(c, names("b20000", files))
    bundle(c, f"{PART} 묶기 {files * 20000:,}행 (예산 끔)", fids, files * 20000)
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


STATS = {"시도": "sido", "시군구": "sigungu", "인구수(명)": "population", "남자(명)": "population_male",
         "여자(명)": "population_female", "세대수(세대)": "households", "예산(천원)": "budget", "지출(천원)": "expenditure",
         "집행률(%)": "execution_rate"}  # fmt: skip
WIDE = {"시도": "sido", "시군구": "sigungu"}
LONG = {"시도": "sido", "시군구": "sigungu"}


def _plan(c: Client, fids: list[int], items: list[str] | None = None) -> tuple[int, dict, int]:
    body: dict = {"file_ids": fids}
    if items:
        links = [{"file_id": f, "sheet_id": "s0", "rows_are_records": True} for f in fids]
        body["summary"] = {"items": [{"field_key": k, "aggregates": ["sum", "count"], "links": links} for k in items]}
    t = time.monotonic()
    s, plan = c.call("POST", "/bundle/plan", body)
    return s, (plan if isinstance(plan, dict) else {"raw": str(plan)[:160]}), round((time.monotonic() - t) * 1000)


def _review_ready(c: Client, fids: list[int]) -> int | None:
    s, review = c.call("POST", "/reviews", {"file_ids": fids})
    if s != 201:
        log(case="review failed", status=s, detail=str(review)[:160])
        return None
    rid = review["id"]
    for it in review.get("items", []):
        if it.get("category") == "pending" and it.get("decision") is None:
            payload = it.get("payload") or {}
            cands = payload.get("candidates") or []
            if it.get("kind") == "mapping":  # 제안한 표준 항목만 — 다른 후보를 고르면 매핑이 바뀌어 '다시 검사'가 된다
                choice = payload.get("field_key")
            else:
                choice = cands[0].get("code") if cands else None
            ok = choice or it.get("kind") == "pii"
            c.call("POST", f"/reviews/{rid}/items/{it['id']}/decision",
                   {"decision": "approved", "choice": choice} if ok else {"decision": "rejected"})  # fmt: skip
    cs, done = c.call("POST", f"/reviews/{rid}/complete")
    if cs != 200:
        log(case="complete failed", status=cs, detail=str(done)[:160])
        return None
    return rid


def _together(jobs: dict[str, tuple[str, str, object]]) -> dict[str, dict]:
    """여러 요청을 같은 순간에 보낸다 (각자 다른 연결, 같은 계정). 결과: 상태·시간·요약."""
    gate = threading.Barrier(len(jobs))
    out: dict[str, dict] = {}

    def go(name: str, method: str, path: str, body: object) -> None:
        cl = Client()
        cl.op = MAIN.op
        gate.wait()
        t = time.monotonic()
        if method == "UPLOAD":  # body = 올릴 파일 목록
            s, res = cl.call("POST", path, files=body, timeout=300)
            if s == 200 and isinstance(res, list) and res and res[0].get("error"):
                s, res = 422, res[0]["error"]
        else:
            s, res = cl.call(method, path, body)
        out[name] = {"status": s, "ms": round((time.monotonic() - t) * 1000),
                     "detail": None if s in (200, 201) else str(res)[:200],
                     "run": res.get("id") if isinstance(res, dict) else None}  # fmt: skip

    ths = [threading.Thread(target=go, args=(n, *j)) for n, j in jobs.items()]
    for th in ths:
        th.start()
    for th in ths:
        th.join(900)
    return out


def part_d1(c: Client) -> None:
    """묶기 + 묶기를 동시에 — 각각 한도(30만 칸) 가까이 (60,000행 × 5칸)."""
    a, b = upload(c, names("b6000", 10)), upload(c, names("b5000", 10))
    (sa, pa, _), (sb, pb, _) = _plan(c, a), _plan(c, b)
    log(case="D1 준비", plans=[sa, sb], ready=[pa.get("ready"), pb.get("ready")], **mem())
    res = _together({"묶기 60,000행": ("POST", "/bundle/run", {"file_ids": a, "expected_basis": pa.get("basis")}),
                     "묶기 50,000행": ("POST", "/bundle/run", {"file_ids": b, "expected_basis": pb.get("basis")})})  # fmt: skip
    log(case="D1 묶기+묶기 동시 (300,000칸 + 250,000칸)", results=res, **mem(), state=state())


def part_d2(c: Client) -> None:
    """묶기(60,000행 = 30만 칸) + 통합(37,000행 ≈ 29.6만 칸)을 동시에."""
    a = upload(c, names("b6000", 10))
    sa, pa, _ = _plan(c, a)
    rid = _review_ready(c, upload(c, ["i37000_0.xlsx"]))
    log(case="D2 준비", plan=sa, ready=pa.get("ready"), review=rid, **mem())
    if rid is None:
        return
    res = _together({"묶기 60,000행": ("POST", "/bundle/run", {"file_ids": a, "expected_basis": pa.get("basis")}),
                     "통합 37,000행": ("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})})  # fmt: skip
    log(case="D2 묶기+통합 동시 (300,000칸 + 약 296,000칸)", results=res, **mem(), state=state())


def part_d3(c: Client) -> None:
    """긴 글자: (a) CSV 5개 × 1,000행 × 3,000자(고유) 묶기 (b) 공백이 붙은 32,000자 공유 문자열을 N칸이 가리키는 작은 xlsx."""
    t = time.monotonic()
    fids = upload(c, [f"lt_{i}.csv" for i in range(5)], mapping=LONG)
    log(case="D3a 긴 글자 CSV 올리기 (5 × 1,000행 × 3,000자)", ms=round((time.monotonic() - t) * 1000), **mem(), state=state())
    s, plan, plan_ms = _plan(c, fids)
    if s == 200 and plan.get("ready"):
        t = time.monotonic()
        rs, run = c.call("POST", "/bundle/run", {"file_ids": fids, "expected_basis": plan["basis"]})
        log(case="D3a 긴 글자 CSV 묶기", status=rs, ms=round((time.monotonic() - t) * 1000), plan_ms=plan_ms,
            detail=None if rs == 201 else str(run)[:160], **mem(), state=state())  # fmt: skip
    else:
        log(case="D3a 묶기 계획", status=s, ready=plan.get("ready"), problems=(plan.get("problems") or [])[:2], **mem())
    _shared_text(c, (499, 500, 2000, 4000, 8000))


def part_d3l(c: Client) -> None:
    """로컬·폐쇄망 기본 한도(글자 6,400만)에서 공유 긴 글자 — 한도 바로 아래(1,999칸)·위."""
    _shared_text(c, (1999, 2000, 4000, 8000))


def _shared_text(c: Client, sizes: tuple[int, ...]) -> None:
    for n in sizes:  # 같은 32,000자(앞뒤 공백)를 n칸이 공유 문자열로 가리킴 (파일은 수 KB)
        if not alive():
            return
        t = time.monotonic()
        try:
            fid = upload(c, [f"sst_{n}.xlsx"], mapping=LONG)
        except RuntimeError as exc:
            log(case=f"D3b 공유 긴 글자 {n}칸 올리기", error=str(exc)[:160], **mem(), state=state())
            continue
        log(case=f"D3b 공유 긴 글자 {n}칸 올리기", ms=round((time.monotonic() - t) * 1000), **mem(), state=state())
        s, plan, plan_ms = _plan(c, fid)
        if s == 200 and plan.get("ready"):
            t = time.monotonic()
            rs, run = c.call("POST", "/bundle/run", {"file_ids": fid, "expected_basis": plan["basis"]})
            log(case=f"D3b 공유 긴 글자 {n}칸 묶기", status=rs, ms=round((time.monotonic() - t) * 1000),
                detail=None if rs == 201 else str(run)[:160], **mem(), state=state())  # fmt: skip
        else:
            log(case=f"D3b 공유 긴 글자 {n}칸 계획", status=s, ready=plan.get("ready"), **mem(), state=state())


def part_d4(c: Client) -> None:
    """넓은 표: 1,000열 × 290행 (약 29만 칸) 묶기, 같은 파일 통합(해당 없음 열 포함)."""
    fid = upload(c, ["w_wide.xlsx"], mapping=WIDE)
    log(case="D4 넓은 표 올리기", **mem(), state=state())
    s, plan, plan_ms = _plan(c, fid)
    if s == 200 and plan.get("ready"):
        t = time.monotonic()
        rs, run = c.call("POST", "/bundle/run", {"file_ids": fid, "expected_basis": plan["basis"]})
        log(case="D4 넓은 표 묶기 (1,000열 × 290행)", status=rs, ms=round((time.monotonic() - t) * 1000), plan_ms=plan_ms,
            detail=None if rs == 201 else str(run)[:160], **mem(), state=state())  # fmt: skip
    else:
        log(case="D4 묶기 계획", status=s, ready=plan.get("ready"), problems=(plan.get("problems") or [])[:2], **mem())
    if not alive():
        return
    rid = _review_ready(c, fid)
    if rid is not None:
        t = time.monotonic()
        s, run = c.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
        log(case="D4 넓은 표 통합 (해당 없음 열 포함)", status=s, ms=round((time.monotonic() - t) * 1000),
            detail=None if s == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip


def part_d5(c: Client) -> None:
    """큰 통계 요약: (a) 한도 안 묶기 1파일 27,000행 × 요약 항목 7개 (b) 한도 넘는 묶기 4파일 × 27,000행 × 7개의 '계획'."""
    items = ["population", "population_male", "population_female", "households", "budget", "expenditure"]
    one = upload(c, ["s7_0.xlsx"], mapping=STATS)
    s, plan, plan_ms = _plan(c, one, items)
    log(case="D5a 계획 (1파일 27,000행 × 요약 6)", status=s, ready=plan.get("ready"), plan_ms=plan_ms,
        problems=(plan.get("problems") or [])[:2], **mem(), state=state())  # fmt: skip
    if s == 200 and plan.get("ready") and alive():
        t = time.monotonic()
        body = {"file_ids": one, "expected_basis": plan["basis"],
                "summary": {"items": [{"field_key": k, "aggregates": ["sum", "count"],
                                       "links": [{"file_id": one[0], "sheet_id": "s0", "rows_are_records": True}]} for k in items]}}  # fmt: skip
        rs, run = c.call("POST", "/bundle/run", body)
        log(case="D5a 묶기+요약 6", status=rs, ms=round((time.monotonic() - t) * 1000),
            detail=None if rs == 201 else str(run)[:160], **mem(), state=state())  # fmt: skip
    if not alive():
        return
    four = one + upload(c, [f"s7_{i}.xlsx" for i in (1, 2, 3)], mapping=STATS)
    s, plan, plan_ms = _plan(c, four, items)
    log(case="D5b 계획 (4파일 × 27,000행 × 요약 6 — 칸 한도 넘음)", status=s, ready=plan.get("ready"), plan_ms=plan_ms,
        problems=(plan.get("problems") or [])[:2], **mem(), state=state())  # fmt: skip


def part_d6(c: Client) -> None:
    """데이터 분석 Excel 내려받기: 30,000행 (시군구 5,000개로 묶음, 숫자 아닌 값 5,000행) — 미리보기·실행·내려받기."""
    fid = upload(c, ["an_30k.xlsx"])
    body = {"field_key": "population", "targets": [{"file_id": fid[0], "sheet_id": "s0", "rows_are_records": True}],
            "group_by": ["sigungu"]}  # fmt: skip
    t = time.monotonic()
    s, pv = c.call("POST", "/analysis/preview", body)
    log(case="D6 분석 미리보기", status=s, ms=round((time.monotonic() - t) * 1000),
        detail=None if s == 200 else str(pv)[:160], **mem(), state=state())  # fmt: skip
    if s != 200 or not alive():
        return
    t = time.monotonic()
    rs, run = c.call("POST", "/analysis/run", {**body, "expected_fingerprint": pv["fingerprint"]})
    log(case="D6 분석 실행", status=rs, ms=round((time.monotonic() - t) * 1000),
        detail=None if rs == 201 else str(run)[:160], **mem(), state=state())  # fmt: skip
    if rs != 201 or not alive():
        return
    t = time.monotonic()
    ds, raw = c.call("GET", f"/analysis/runs/{run['id']}/download")
    log(case="D6 분석 Excel 내려받기", status=ds, ms=round((time.monotonic() - t) * 1000),
        bytes=len(raw) if isinstance(raw, bytes) else None, **mem(), state=state())  # fmt: skip
    if ds == 200 and alive():
        res = _together({"내려받기 1": ("GET", f"/analysis/runs/{run['id']}/download", None),
                         "내려받기 2": ("GET", f"/analysis/runs/{run['id']}/download", None)})  # fmt: skip
        log(case="D6 분석 Excel 내려받기 둘 동시", results=res, **mem(), state=state())


def part_d7(c: Client) -> None:
    """병합 범위: 몇 KB xlsx의 큰 병합 하나 (A3:Z<n>) — 칸 수 한도(Render 40만) 안·밖."""
    for name in ("merge_300k.xlsx", "merge_1m.xlsx"):
        if not alive():
            return
        t = time.monotonic()
        try:
            upload(c, [name], confirm=False)
            log(case=f"D7 큰 병합 올리기 {name}", status="ok", ms=round((time.monotonic() - t) * 1000), **mem(), state=state())
        except RuntimeError as exc:
            log(case=f"D7 큰 병합 올리기 {name}", error=str(exc)[:200], ms=round((time.monotonic() - t) * 1000),
                **mem(), state=state())  # fmt: skip


def part_d8(c: Client) -> None:
    """결과 만들기 차례 2개(MOAJWO_OUTPUT_SLOTS=2): 한도 가까운 묶기 둘(30만·25만 칸) + 큰 xlsx(풀었을 때 14.8MiB) 올리기를
    같은 순간에 — 차례를 1개로 둔 근거(결과 둘이 겹친 데에 읽기 하나가 더 겹칠 때)를 잰다. 수정 전 이미지에는 차례가 없다."""
    a, b = upload(c, names("b6000", 10)), upload(c, names("b5000", 10))
    (sa, pa, _), (sb, pb, _) = _plan(c, a), _plan(c, b)
    log(case="D8 준비", plans=[sa, sb], ready=[pa.get("ready"), pb.get("ready")], **mem())
    res = _together({"묶기 60,000행": ("POST", "/bundle/run", {"file_ids": a, "expected_basis": pa.get("basis")}),
                     "묶기 50,000행": ("POST", "/bundle/run", {"file_ids": b, "expected_basis": pb.get("basis")}),
                     "큰 xlsx 올리기": ("UPLOAD", "/files", [FILES / "x_big.xlsx"])})  # fmt: skip
    log(case="D8 묶기+묶기+큰 xlsx 올리기 동시 (차례 2개)", results=res, **mem(), state=state())


def part_d9(c: Client) -> None:
    """긴 글자 파일 넷(각 499칸 × 32,000자 ≈ 1,597만 자 — 파일 하나의 글자 한도 바로 아래)을 한 결과로: 묶기, 그다음 통합.
    결과 칸은 몇천 개라 칸 수 예산에 걸리지 않는다 — 결과 하나의 글자 양을 따로 막아야 하는지 잰다."""
    fids = upload(c, [f"sst499_{k}.xlsx" for k in range(4)], mapping=LONG)
    log(case="D9 올리기 (4 × 499칸 × 32,000자)", **mem(), state=state())
    s, plan, plan_ms = _plan(c, fids)
    if s == 200 and plan.get("ready") and alive():
        t = time.monotonic()
        rs, run = c.call("POST", "/bundle/run", {"file_ids": fids, "expected_basis": plan["basis"]})
        log(case="D9 묶기 (약 6,390만 자)", status=rs, ms=round((time.monotonic() - t) * 1000), plan_ms=plan_ms,
            detail=None if rs == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip
    else:
        log(case="D9 묶기 계획", status=s, ready=plan.get("ready"), problems=(plan.get("problems") or [])[:2], **mem(),
            state=state())  # fmt: skip
    if not alive():
        return
    rid = _review_ready(c, fids)
    if rid is not None and alive():
        t = time.monotonic()
        s, run = c.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
        log(case="D9 통합 (약 6,390만 자)", status=s, ms=round((time.monotonic() - t) * 1000),
            detail=None if s == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip


def part_d10(c: Client, n: int) -> None:
    """긴 글자 파일 n개(각 약 1,597만 자)를 통합만 — 새 컨테이너에서 결과 하나의 글자 양에 따른 메모리."""
    fids = upload(c, [f"sst499_{k}.xlsx" for k in range(n)], mapping=LONG)
    log(case=f"D10 올리기 {n}개", **mem(), state=state())
    rid = _review_ready(c, fids)
    if rid is None or not alive():
        return
    t = time.monotonic()
    s, run = c.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
    log(case=f"D10 통합 {n}개 (약 {n * 1597}만 자)", status=s, ms=round((time.monotonic() - t) * 1000),
        detail=None if s == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip


def part_d11(c: Client, n: int) -> None:
    """긴 글자 파일 n개를 묶기만 — 새 컨테이너."""
    fids = upload(c, [f"sst499_{k}.xlsx" for k in range(n)], mapping=LONG)
    log(case=f"D11 올리기 {n}개", **mem(), state=state())
    s, plan, plan_ms = _plan(c, fids)
    if s != 200 or not plan.get("ready") or not alive():
        log(case=f"D11 묶기 계획 {n}개", status=s, ready=plan.get("ready"), problems=(plan.get("problems") or [])[:2],
            **mem(), state=state())  # fmt: skip
        return
    t = time.monotonic()
    rs, run = c.call("POST", "/bundle/run", {"file_ids": fids, "expected_basis": plan["basis"]})
    log(case=f"D11 묶기 {n}개 (약 {n * 1597}만 자)", status=rs, ms=round((time.monotonic() - t) * 1000), plan_ms=plan_ms,
        detail=None if rs == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip


def part_d12(c: Client, name: str = "sst499_0.xlsx") -> None:
    """긴 글자 파일 하나(기본: 결과 글자 예산 바로 아래 — 통합이 약 4,790만 자로 셈)의 통합과 큰 xlsx 올리기를 같은
    순간에 — 결과 만들기 차례 하나 + 읽기 차례 하나가 겹치는, 차례가 허용하는 가장 큰 조합."""
    fids = upload(c, [name], mapping=LONG)
    rid = _review_ready(c, fids)
    log(case="D12 준비", review=rid, **mem(), state=state())
    if rid is None or not alive():
        return
    res = _together({"통합 1개 (약 4,790만 자)": ("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True}),
                     "큰 xlsx 올리기": ("UPLOAD", "/files", [FILES / "x_big.xlsx"])})  # fmt: skip
    log(case="D12 긴 글자 통합 + 큰 xlsx 올리기 동시", results=res, **mem(), state=state())


def _small_ok(c: Client, tag: str) -> None:
    """거절·실패 뒤 다음 정상 요청: 작은 파일 하나 묶기."""
    fid = upload(c, ["b5000_0.xlsx"])
    s = bundle(c, f"{tag} 뒤 다음 정상 요청 (묶기 5,000행)", fid, 5000)
    log(case=f"{tag} 다음 정상 요청", status=s, **mem(), state=state())


def part_e1(c: Client) -> None:
    """칸마다 다른 긴 한글(공유 문자열 없음) 파일 7개(각 1,125칸 × 약 4,100자 ≈ 461만 자): 묶기·통합 6개(≈ 2,770만 자 — 예산
    3,200만 안)는 허용, 7개(≈ 3,230만 자)는 예산을 넘어 실행 기록을 만들기 전에 거절. 거절 뒤 다음 정상 요청."""
    fids = upload(c, [f"dk_{k}.xlsx" for k in range(7)], mapping=LONG)
    log(case="E1 올리기 7개 (칸마다 다른 긴 한글, 각 약 461만 자)", files=len(fids), **mem(), state=state())
    for n in (6, 7):
        s, plan, plan_ms = _plan(c, fids[:n])
        if s == 200 and plan.get("ready") and alive():
            t = time.monotonic()
            rs, run = c.call("POST", "/bundle/run", {"file_ids": fids[:n], "expected_basis": plan["basis"]})
            log(case=f"E1 묶기 {n}개", status=rs, ms=round((time.monotonic() - t) * 1000), plan_ms=plan_ms,
                detail=None if rs == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip
        else:
            log(case=f"E1 묶기 계획 {n}개", status=s, ready=plan.get("ready"), problems=(plan.get("problems") or [])[:2],
                **mem(), state=state())  # fmt: skip
        if not alive():
            return
    _small_ok(c, "E1 묶기")
    for n in (6, 7):
        rid = _review_ready(c, fids[:n])
        log(case=f"E1 검토 {n}개", review=rid, **mem(), state=state())
        if rid is None or not alive():
            return
        t = time.monotonic()
        s, run = c.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
        log(case=f"E1 통합 {n}개", status=s, ms=round((time.monotonic() - t) * 1000),
            detail=None if s == 201 else str(run)[:200], **mem(), state=state())  # fmt: skip
        if not alive():
            return
    _small_ok(c, "E1 통합")


def part_e2(c: Client) -> None:
    """결과 한 건 + 큰 업로드를 최종 권장값으로: 긴 글자 파일(330칸 × 32,002자 — 통합 글자 예산 3,200만 바로 아래)의 통합과
    큰 xlsx(풀었을 때 14.8MiB) 올리기를 같은 순간에 (결과 차례 하나 + 읽기 차례 하나)."""
    part_d12(c, "sst330_0.xlsx")
    _small_ok(c, "E2")


def _three(c: Client) -> list[int]:
    return upload(c, [f"dk_{k}.xlsx" for k in range(3)], mapping=LONG)


def part_e3a(c: Client) -> None:
    """E3 기준 A: 파일 셋(칸마다 다른 긴 한글)을 읽는 검토 생성만 — 이 요청 혼자의 최대."""
    fids = _three(c)
    log(case="E3A 준비", **mem(), state=state())
    t = time.monotonic()
    s, review = c.call("POST", "/reviews", {"file_ids": fids})
    log(case="E3A 검토 생성 (파일 셋)", status=s, ms=round((time.monotonic() - t) * 1000), **mem(), state=state())


def part_e3b(c: Client) -> None:
    """E3 기준 B: 큰 xlsx 올리기만 — 이 요청 혼자의 최대."""
    log(case="E3B 준비", **mem(), state=state())
    t = time.monotonic()
    s, body = c.call("POST", "/files", files=[FILES / "x_big.xlsx"])
    ok = s == 200 and isinstance(body, list) and not body[0].get("error")
    log(case="E3B 큰 xlsx 올리기", status=s, ok=ok, ms=round((time.monotonic() - t) * 1000), **mem(), state=state())


def part_e3c(c: Client) -> None:
    """E3 겹침: 검토 생성(파일 셋 — 파일마다 읽기 차례를 따로 얻음)과 큰 xlsx 올리기를 같은 순간에. 읽기 차례가 하나라 한쪽이
    기다리는데, 검토 생성은 먼저 읽은 파일의 결과를 쥔 채 다음 파일의 차례를 기다릴 수 있다 — A·B 혼자의 최대와 비교한다.
    두 요청만 겹친다 (무제한 동시 요청으로 넓히지 않음)."""
    fids = _three(c)
    log(case="E3C 준비", **mem(), state=state())
    res = _together({"검토 생성 (파일 셋)": ("POST", "/reviews", {"file_ids": fids}),
                     "큰 xlsx 올리기": ("UPLOAD", "/files", [FILES / "x_big.xlsx"])})  # fmt: skip
    log(case="E3C 검토 생성 + 큰 xlsx 올리기 동시", results=res, **mem(), state=state())
    _small_ok(c, "E3C")


MAIN = Client()


def main() -> None:
    c = MAIN
    s, _ = c.call("POST", "/auth/setup", {"username": "ci-out", "display_name": "CI", "password": PW})
    log(case="setup", status=s, **mem())
    try:
        if PART.startswith("D12_"):  # D12_<칸 수><차례 글자>: 그 크기 파일 하나로 D12
            n = "".join(ch for ch in PART.split("_")[1] if ch.isdigit())
            part_d12(c, "sst499_0.xlsx" if n == "499" else f"sst{n}_0.xlsx")
            raise _Done
        if PART.startswith(("D10", "D11")):  # D10_n·D11_n: 파일 n개 (D10L_n·D11L_n: 로컬 기본 한도, 1GiB)
            (part_d10 if PART.startswith("D10") else part_d11)(c, int(PART.split("_")[1]))
            raise _Done
        {"A": part_a, "B": part_b, "C": part_c, "D1": part_d1, "D2": part_d2, "D3": part_d3, "D4": part_d4,
         "D5": part_d5, "D6": part_d6, "D7": part_d7, "D3L": part_d3l, "D8": part_d8, "D9": part_d9,
         "A3": lambda cl: part_a(cl, 15), "D12": part_d12, "E1": part_e1, "E2": part_e2, "E3A": part_e3a,
         "E3B": part_e3b, "E3C": part_e3c}[PART](c)  # fmt: skip
    except _Done:
        pass
    except RuntimeError as exc:
        log(case="stopped", error=str(exc), **mem(), state=state())
    leftovers("end")


class _Done(Exception):
    """D10_n·D11_n을 돌렸다 (아래 표를 건너뜀)."""


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / f"output-memory-{PART}.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
