"""격리 backend 한 프로세스를 시험용 제어 장치와 함께 띄운다 (keyjoin_requests.py가 쓴다 — 제품 코드는 바꾸지 않는다).

제어 파일은 KJ_CTL 폴더에 시나리오 쪽이 만들고 지운다. 장치는 요청 번호(request_id)가 걸린 요청에만 작동한다.
- armed-plan-<rid> (내용: 모일 요청 수 N): POST /keyjoin/run의 계획 다시 판정(build_plan) 직전에 N개가 모일 때까지 기다린다
  — 서로 다른 프로세스의 같은 요청을 sleep 운이 아니라 같은 지점에 세운다.
- hold-plan-<rid>: 그 지점에서 파일이 지워질 때까지 기다린다 (같은 프로세스의 둘째 요청 시험).
- hold-prepare-<rid>: 결과 파일을 다 쓴 뒤 완료 기록(runlease.prepare) 직전에 파일이 지워질 때까지 기다린다 (응답 유실 흉내).
- kill-<rid>: 그 지점에서 프로세스를 바로 끝낸다(os._exit — 서버가 죽은 경우). killed-<rid>에 PID를 남긴다.
- fail-<rid>: 그 지점에서 OSError를 낸다 (시작 뒤 실패).
각 장치가 걸리면 passed-<지점>-<rid>-<pid> 파일을 남긴다 (시나리오가 순서를 확인).
사용법: python keyjoin_hook_server.py <backend 폴더> <포트>
"""

import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
CTL = Path(os.environ["KJ_CTL"])
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import uvicorn  # noqa: E402
from app import keyjoin_api, runlease  # noqa: E402


def _mark(stage: str, rid: str) -> None:
    (CTL / f"passed-{stage}-{rid}-{os.getpid()}-{time.monotonic_ns()}").write_text("x", encoding="utf-8")


def _wait_gone(path: Path, limit_s: float = 180) -> None:
    deadline = time.monotonic() + limit_s
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)


def _gate(rid: str | None) -> None:
    if not rid:
        return
    armed = CTL / f"armed-plan-{rid}"
    if armed.exists():
        n = int(armed.read_text(encoding="utf-8").strip() or "2")
        (CTL / f"arrived-plan-{rid}-{os.getpid()}-{time.monotonic_ns()}").write_text("x", encoding="utf-8")
        deadline = time.monotonic() + 180
        while len(list(CTL.glob(f"arrived-plan-{rid}-*"))) < n and time.monotonic() < deadline:
            time.sleep(0.005)
    if (CTL / f"hold-plan-{rid}").exists():
        _mark("plan-held", rid)
        _wait_gone(CTL / f"hold-plan-{rid}")
    _mark("plan", rid)


_real_build = keyjoin_api.build_plan


def _build_plan(db, user, req, keep_values=False):  # noqa: ANN001, ANN202 — 원래 함수와 같은 인자
    if keep_values:
        _gate(getattr(req, "request_id", None))
    return _real_build(db, user, req, keep_values=keep_values)


keyjoin_api.build_plan = _build_plan
_real_prepare = runlease.prepare


def _prepare(db, run, tmp, summary, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    try:
        rid = json.loads(summary).get("request_id")
    except (TypeError, ValueError):
        rid = None
    if rid:
        if (CTL / f"kill-{rid}").exists():
            (CTL / f"killed-{rid}").write_text(str(os.getpid()), encoding="utf-8")
            os._exit(137)
        if (CTL / f"fail-{rid}").exists():
            _mark("fail", rid)
            raise OSError(28, "injected for the keyjoin request test")
        if (CTL / f"hold-prepare-{rid}").exists():
            _mark("prepare-held", rid)
            _wait_gone(CTL / f"hold-prepare-{rid}")
        _mark("prepare", rid)
    return _real_prepare(db, run, tmp, summary, *args, **kwargs)


runlease.prepare = _prepare

from app.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
