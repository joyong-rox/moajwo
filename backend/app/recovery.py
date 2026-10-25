"""멈춘 결과 실행 정리 — 결과 파일을 만드는 도중 담당 프로세스가 끝난(메모리 부족 종료·강제 재시작·처리기가 놓음)
실행을, 근거가 있을 때만 확정한다 (담당·잠금·확정 순서는 app/runlease.py).

- 살아 있는지: 같은 결과 폴더를 쓰는 담당은 그 프로세스 잠금으로 확정한다 (얻으면 죽음, 못 얻으면 살아 있음). 다른
  디스크(다른 서버, 디스크가 초기화된 같은 서버)·이전 판(담당 없음)·잠금 파일 없음은 근거가 없다 — 그런 실행은 마지막
  생존 신호가 STALE_S초보다 오래됐을 때 '결과 확인 필요'(check)로만 두고 파일을 건드리지 않는다. 시작 시각만으로
  판단하지 않는다. 이전 판 실행은 이 프로세스가 뜬 지 LEGACY_GRACE_S초 뒤에만 본다 (무중단 배포에서 이전 판 서버가
  아직 돌릴 수 있음).
- 가로채기: 실행마다 한 트랜잭션. 조건부 UPDATE(상태·담당이 그대로일 때만)로 행 잠금(SQLite는 쓰기 잠금)을 잡은 뒤
  잠근 행의 값을 다시 읽어 판정한다 — 조회와 갱신 사이에 완료된 실행·done·다른 기록은 바꾸지 않는다.
- 판정 (죽음이 확정된 담당 — 파일은 이 서버의 결과 폴더에서만 본다. 해시는 잠금 밖에서 먼저 구한다):
  | 준비(sha256) | 임시 파일 (먼저 봄) | 최종 파일 | 결과 |
  | 없음 | 있음/없음 | 없음 | failed — 결과 파일은 저장되지 않음, 그 실행의 임시 파일만 삭제 |
  | 없음 | - | 있음 | check (예상 밖 파일 — 지우지 않음) |
  | 있음 | - | 있음·해시 같음 | done |
  | 있음 | 있음·해시 같음 | 없음 | done — 임시 파일을 최종 이름으로 옮김 |
  | 있음 | - | 있음·해시 다름 | check (내려받지 않게, 지우지 않음) |
  | 있음 | 있음·해시 다름 | 없음 | failed + 그 임시 파일 삭제 |
  | 있음 | 없음 | 없음 | failed — 결과 파일이 이 서버의 결과 폴더에 없음 |
  failed는 요약을 시작 때 값으로, 변경 수 0, 변경 기록 삭제. 파일을 보다 오류가 나면 판정하지 않고 다음 차례로.
- 언제: 기동 때 한 번, 그리고 판정할 근거가 생길 때만 도는 일회성 타이머 (다른 디스크 실행은 마지막 생존 신호 +
  STALE_S, 이전 판 실행은 기동 + LEGACY_GRACE_S, 이 프로세스가 놓은 실행·죽은 담당은 곧). 실행 기록 목록(GET /runs)은
  이미 읽은 행으로 타이머만 예약한다 (쓰기·추가 조회 없음). 후보가 없으면 타이머도 없다 (외부 DB 휴면을 깨우지 않음).
- 이전 판 임시 파일(tmp*.xlsx.tmp — 실행과 연결 없음)·기록 없는 결과 파일은 자동으로 지우지 않는다 (status가 알림).
- 별도 절차 (CLI, docs/DEPLOY.md '결과 확인 필요 실행'):
    python -m app.recovery status              실행 중·결과 확인 필요 실행, 연결 없는 파일
    python -m app.recovery recover             위 판정을 한 번 (기동 정리와 같음 — 서버를 멈춘 롤백 전에 쓴다).
                                               살아 있는 담당은 그대로, 근거 없는 실행(다른 디스크·잠금 파일 없음)은
                                               신호가 STALE_S 넘었을 때만 '결과 확인 필요'로(실패·파일 변경 없음),
                                               이전 판 실행은 그대로(이 명령은 막 뜬 프로세스라). 종료 0 = 담당이 있는
                                               실행 중 행 없음(downgrade 가능), 2 = 남음(blocking — next_s초 뒤 다시),
                                               1 = 오류
    python -m app.recovery resolve RUN_ID --failed   결과 확인 필요 → 실패 (조건부, 파일은 지우지 않고 경로만 알림)
  세 명령 모두 먼저 이 DB의 리비전(alembic_version)과 runs 표의 0020 열(담당·생존 신호)을 읽기만 하고 맞춰 본다.
  - 0020_run_worker_lease + 열 있음: 위 동작.
  - 0019_analysis_runs + 열 없음(예: 반영 전 운영 DB): status는 실행 기록의 상태별 건수만 보여 주고(종료 0),
    recover·resolve는 아무것도 바꾸지 않고 종료 1.
  - 그 밖(리비전 없음·여러 개·이 판이 다루지 않는 리비전·리비전과 열이 맞지 않음·runs 표 없음): status를 포함해
    진단만 알리고 종료 1 — 어느 판이라고 짐작하지 않고 아무것도 바꾸지 않는다. DB를 읽지 못해도 종료 1.
  진단·오류에는 리비전 이름·열 이름·오류 종류만 (주소·SQL·값 없음).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.orm import Session

from app import db as app_db
from app import runlease
from app.config import DATA_DIR
from app.models import IntegrationRun, RunChange
from app.runlease import OUTPUT_DIR, file_sha256, owner_state
from app.timefmt import KST

logger = logging.getLogger(__name__)

# 근거 없는 실행을 '결과 확인 필요'로 둘 때까지 생존 신호가 없어야 하는 시간 (Render 겹침 최대 약 6분보다 김)
STALE_S = 600
LEGACY_GRACE_S = 1800  # 이전 판 실행을 볼 때까지 기다리는 시간 (Render 헬스체크 대기 15분 + 종료 지연 최대 6분 + 여유)
SOON_S = 5
RETRY_S = (30, 60, 120, 240, 480)  # 기동 정리가 DB에 닿지 못했을 때 다시 볼 간격 (그 뒤에는 다음 기동·목록 요청)

FAILED_UNSAVED = (
    "결과를 만드는 도중 이 실행을 맡은 서버 프로세스가 끝났습니다 — 결과 파일은 저장되지 않았습니다. 다시 실행하세요."
)
FAILED_MISSING = (
    "결과를 확정하기 전에 이 실행을 맡은 서버 프로세스가 끝났고, 결과 파일이 이 서버의 결과 폴더에 없습니다 — "
    "다시 실행하세요."
)
FAILED_BROKEN = (
    "결과를 확정하기 전에 이 실행을 맡은 서버 프로세스가 끝났고, 남은 임시 파일이 기록한 내용과 달라 쓰지 않았습니다 — "
    "다시 실행하세요."
)
CHECK_UNKNOWN = (
    "이 실행을 맡은 서버 프로세스의 생존 신호가 끊겼습니다. 이 서버에서는 그 결과 파일을 확인할 수 없어"
    "(다른 서버였거나 디스크가 초기화됨) 결과를 확정하지 않았습니다 — 관리자 확인이 필요합니다."
)
CHECK_LEGACY = (
    "이전 판 서버에서 시작한 실행이라 끝났는지 확인할 수 없어 결과를 확정하지 않았습니다 — 관리자 확인이 필요합니다."
)
CHECK_MISMATCH = "결과 파일이 남아 있지만 기록한 내용과 달라 내려받지 않게 두었습니다 — 관리자 확인이 필요합니다."
CHECK_UNEXPECTED = "완료를 기록하기 전의 실행인데 결과 파일이 남아 있습니다 — 관리자 확인이 필요합니다."
RECOVERED = "담당 서버 프로세스가 끝난 뒤 정리에서 결과 파일(해시 일치)로 완료를 확인했습니다."


class _Changed(Exception):
    """가로채는 사이 그 실행이 바뀌었다 (완료·생존 신호·다른 정리) — 손대지 않는다."""


def _aware(value: datetime | None) -> datetime | None:
    """heartbeat_at은 UTC로 저장한다 — SQLite는 시간대 없이 돌려주므로 UTC를 붙인다."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _kst(value: datetime | None) -> datetime:
    return (_aware(value) or datetime.now(UTC)).astimezone(KST)


def _start_summary(summary: str | None) -> str:
    """실패로 돌릴 때 요약을 시작 때 값으로 (묶기는 방식·요약 여부, 통합은 빈 요약 — 결과 내용은 남기지 않는다)."""
    try:
        s = json.loads(summary or "{}")
    except ValueError:
        return "{}"
    if isinstance(s, dict) and s.get("mode") == "sheets":
        return json.dumps({"mode": "sheets", "stats_summary": s.get("stats_summary")})
    return "{}"


def _file_state(path: Path | None, sha: str | None) -> str:
    """'none' / 'same' / 'different' (sha가 없으면 있기만 하면 'different')."""
    if path is None or not path.is_file():
        return "none"
    return "same" if sha is not None and file_sha256(path) == sha else "different"


def _paths(output_path: str | None, worker_id: str | None) -> tuple[Path | None, Path | None]:
    if not output_path:
        return None, None
    target = (DATA_DIR / output_path).resolve()
    if not target.is_relative_to(OUTPUT_DIR.resolve()):
        return None, None
    proc = (worker_id or "").partition("-")[2]
    return target, (target.with_name(f"{target.name}.{proc}.tmp") if proc else None)


def _claim(db: Session, run_id: int, worker_id: str | None) -> Any:
    """가로채기: 상태·담당이 그대로일 때만 행을 잠그고(값은 그대로) 잠근 행의 값을 다시 읽는다."""
    if db.get_bind().dialect.name == "postgresql":  # 담당이 확정 중이면(행 잠금) 오래 기다리지 않고 다음 차례로
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
    owner = IntegrationRun.worker.is_(None) if worker_id is None else IntegrationRun.worker == worker_id
    res = db.execute(
        update(IntegrationRun)
        .where(IntegrationRun.id == run_id, IntegrationRun.status == "running", owner)
        .values(heartbeat_at=IntegrationRun.heartbeat_at)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise _Changed()
    return db.execute(
        select(
            IntegrationRun.output_path,
            IntegrationRun.output_sha256,
            IntegrationRun.summary,
            IntegrationRun.heartbeat_at,
        ).where(IntegrationRun.id == run_id)
    ).one()


def _set(db: Session, run_id: int, **values: Any) -> None:
    db.execute(
        update(IntegrationRun)
        .where(IntegrationRun.id == run_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def _mark_check(run_id: int, worker_id: str | None, message: str) -> str:
    with app_db.SessionLocal() as db:
        _claim(db, run_id, worker_id)
        _set(db, run_id, status="check", error=message)
        db.commit()
    logger.warning("run %s: result not confirmed — marked check", run_id)
    return "check"


def _settle_dead(run_id: int, worker_id: str | None) -> str:
    """죽음이 확정된 담당의 실행을 파일 근거로 판정한다 (위 표)."""
    with app_db.SessionLocal() as db:
        seen = db.execute(
            select(IntegrationRun.output_path, IntegrationRun.output_sha256).where(IntegrationRun.id == run_id)
        ).one()
    target, tmp = _paths(seen.output_path, worker_id)
    # 근거는 잠금 밖에서 먼저 (큰 파일 해시가 쓰기 잠금을 오래 쥐지 않게). 임시 파일을 먼저 본다 — 이름 바꾸기는
    # 임시 → 최종 원자 이동이라 이 순서면 둘 다 없다고 잘못 보지 않는다
    tmp_state, final_state = _file_state(tmp, seen.output_sha256), _file_state(target, seen.output_sha256)
    with app_db.SessionLocal() as db:
        cur = _claim(db, run_id, worker_id)
        if (cur.output_path, cur.output_sha256) != (seen.output_path, seen.output_sha256):
            raise _Changed()  # 근거를 본 뒤 준비가 기록됐다 — 다음 차례에 다시 본다
        ended = _kst(cur.heartbeat_at)
        prepared = cur.output_sha256 is not None
        if not prepared and final_state == "none":
            outcome, message = "failed", FAILED_UNSAVED
        elif not prepared or final_state == "different":
            outcome, message = "check", CHECK_UNEXPECTED if not prepared else CHECK_MISMATCH
        elif final_state == "same" or tmp_state == "same":
            outcome, message = "done", None
        else:
            outcome, message = "failed", FAILED_BROKEN if tmp_state == "different" else FAILED_MISSING
        if outcome == "done":
            if final_state != "same":  # 잠금을 쥔 채 옮긴다 — commit 전에 멈추면 다음 차례에 최종 파일로 done
                assert tmp is not None and target is not None
                os.replace(tmp, target)
                os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            summary = json.loads(cur.summary or "{}")
            summary["recovered"] = RECOVERED
            done = json.dumps(summary, ensure_ascii=False)
            _set(db, run_id, status="done", error=None, finished_at=ended, summary=done)
        elif outcome == "failed":
            if target is not None and target.is_file():  # 그 사이 최종 파일이 생겼으면 실패로 정하지 않는다
                raise _Changed()
            _set(db, run_id, status="failed", error=message, finished_at=ended, output_path=None, output_sha256=None,
                 summary=_start_summary(cur.summary), change_count=0)  # fmt: skip
            db.execute(delete(RunChange).where(RunChange.run_id == run_id))
        else:
            _set(db, run_id, status="check", error=message)
        db.commit()
    if outcome == "failed" and tmp is not None and tmp_state != "none":
        tmp.unlink(missing_ok=True)  # 그 실행에 연결된 임시 파일만 — 실패를 기록한 뒤에
    logger.warning("run %s: worker gone — settled as %s", run_id, outcome)
    return outcome


def recover(now: datetime | None = None) -> dict[str, Any]:
    """running 실행을 판정한다. 반환: 건수, complete(오류 없이 모든 후보를 보았음 — 기다리는 후보는 남을 수 있음),
    next_s(다시 볼 때까지 초 — 없으면 None)."""
    now = now or datetime.now(UTC)
    report: dict[str, Any] = {"failed": 0, "done": 0, "check": 0, "alive": 0, "waiting": 0, "errors": 0}
    waits: list[float] = []
    with app_db.SessionLocal() as db:
        rows = db.execute(
            select(IntegrationRun.id, IntegrationRun.worker, IntegrationRun.heartbeat_at).where(
                IntegrationRun.status == "running"
            )
        ).all()
    mine = runlease.active()
    uptime = time.monotonic() - runlease.identity()["started"]
    for row in rows:
        try:
            state = owner_state(row.worker)
            if state == "me" and row.id in mine or state == "alive":
                report["alive"] += 1
                continue
            if state in ("me", "dead"):
                report[_settle_dead(row.id, row.worker)] += 1
                continue
            if row.worker is None:  # 이전 판 — 무중단 배포의 이전 서버가 아직 돌릴 수 있어 기동 뒤 한동안 기다린다
                if uptime < LEGACY_GRACE_S:
                    report["waiting"] += 1
                    waits.append(LEGACY_GRACE_S - uptime + 1)
                    continue
                report[_mark_check(row.id, None, CHECK_LEGACY)] += 1
                continue
            beat = _aware(row.heartbeat_at)
            quiet = (now - beat).total_seconds() if beat is not None else STALE_S
            if quiet < STALE_S:
                report["waiting"] += 1
                waits.append(STALE_S - quiet + 1)
                continue
            report[_mark_check(row.id, row.worker, CHECK_UNKNOWN)] += 1
        except _Changed:
            waits.append(SOON_S)
        except Exception as exc:  # noqa: BLE001 — 그 실행만 두고 다음으로 (DB 오류·파일 오류)
            report["errors"] += 1
            waits.append(RETRY_S[0])
            logger.warning("run %s: recovery skipped (%s)", row.id, type(exc).__name__)
    report["complete"] = report["errors"] == 0
    report["next_s"] = min(waits) if waits else None
    try:  # 정리 뒤에도 실행 중인 행: 담당이 있는 것(downgrade를 막음)과 이전 판 것, 그리고 남길 잠금 파일
        with app_db.SessionLocal() as db:
            left = list(db.scalars(select(IntegrationRun.worker).where(IntegrationRun.status == "running")))
    except Exception:  # noqa: BLE001 — 남은 행을 모르면 잠금 파일을 지우지 않는다
        report["blocking"] = report["legacy"] = None
        return report
    report["blocking"] = sum(w is not None for w in left)
    report["legacy"] = sum(w is None for w in left)
    try:
        runlease.forget_dead_workers(keep={w.partition("-")[2] for w in left if w})
    except OSError:
        pass
    return report


# ---------------------------------------------------------------- 언제 도는가 (일회성 타이머)

_timer_lock = threading.Lock()
_timer: dict[str, Any] = {"due": None, "timer": None, "retries": 0}


def schedule(delay_s: float) -> None:
    """delay_s초 뒤 한 번 정리한다 (이미 더 이른 예약이 있으면 그대로)."""
    delay_s = max(1.0, float(delay_s))
    due = time.monotonic() + delay_s
    with _timer_lock:
        if _timer["due"] is not None and _timer["due"] <= due:
            return
        if _timer["timer"] is not None:
            _timer["timer"].cancel()
        timer = threading.Timer(delay_s, _scheduled)
        timer.daemon = True
        _timer.update(due=due, timer=timer)
        timer.start()


def _scheduled() -> None:
    with _timer_lock:
        _timer.update(due=None, timer=None)
    try:
        report = recover()
    except Exception as exc:  # noqa: BLE001 — DB에 닿지 못함: 정해 둔 간격으로 몇 번만 다시 본다
        tries = _timer["retries"]
        _timer["retries"] = tries + 1
        logger.warning("scheduled recovery incomplete: %s", type(exc).__name__)
        if tries < len(RETRY_S):
            schedule(RETRY_S[tries])
        return
    _timer["retries"] = 0
    _log(report, "scheduled")
    if report["next_s"] is not None:
        schedule(report["next_s"])


def _log(report: dict[str, Any], when: str) -> None:
    counts = " ".join(f"{k}={report[k]}" for k in ("failed", "done", "check", "alive", "waiting", "errors"))
    if report["complete"]:
        logger.info("%s recovery complete: %s", when, counts)
    else:
        logger.warning("%s recovery incomplete: %s", when, counts)


def startup() -> dict[str, Any] | None:
    """기동 때 한 번 (lifespan). 어떤 오류도 기동을 막지 않는다 — 정리하지 못했으면 '미완료'로 남기고 다시 볼 예약."""
    try:
        report = recover()
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup recovery incomplete: %s — will retry", type(exc).__name__)
        _timer["retries"] = 1
        schedule(RETRY_S[0])
        return None
    _log(report, "startup")
    if report["next_s"] is not None:
        schedule(report["next_s"])
    return report


def notice(runs: list[IntegrationRun]) -> None:
    """실행 기록 목록에서 이미 읽은 행만 보고 정리를 예약한다 (쓰기·추가 조회 없음 — 판정은 타이머가 따로 한다)."""
    try:
        mine = runlease.active()
        for run in runs:
            if run.status != "running":
                continue
            state = owner_state(run.worker)
            if state == "dead" or state == "me" and run.id not in mine:
                schedule(SOON_S)
            elif state == "unknown":
                beat = _aware(run.heartbeat_at)
                quiet = (datetime.now(UTC) - beat).total_seconds() if beat is not None else STALE_S
                schedule(SOON_S if run.worker is not None and quiet >= STALE_S else max(SOON_S, STALE_S - quiet + 1))
    except Exception as exc:  # noqa: BLE001 — 목록 응답은 막지 않는다
        logger.warning("recovery notice skipped: %s", type(exc).__name__)


# ---------------------------------------------------------------- 별도 절차 (CLI)


def _local_files() -> bool:
    """결과 파일을 이 프로세스의 결과 폴더에서 볼 수 있는가 — 외부 DB 모드에서 다른 PC로 돌리면 서버의 폴더가 아니다."""
    return app_db.engine.url.get_backend_name() == "sqlite"


def status() -> dict[str, Any]:
    with app_db.SessionLocal() as db:
        rows = db.execute(
            select(IntegrationRun.id, IntegrationRun.status, IntegrationRun.worker, IntegrationRun.heartbeat_at,
                   IntegrationRun.output_path, IntegrationRun.error)
            .where(IntegrationRun.status.in_(("running", "check")))
            .order_by(IntegrationRun.id)
        ).all()  # fmt: skip
        known = set(db.scalars(select(IntegrationRun.output_path).where(IntegrationRun.output_path.is_not(None))))
        total = db.scalar(select(func.count(IntegrationRun.id)))
    now = datetime.now(UTC)
    out: dict[str, Any] = {"runs_total": total, "running": [], "check": []}
    for r in rows:
        beat = _aware(r.heartbeat_at)
        item = {"id": r.id, "owner": owner_state(r.worker) if r.worker else "이전 판(담당 없음)",
                "quiet_s": round((now - beat).total_seconds()) if beat else None,
                "file": Path(r.output_path).name if r.output_path else None}  # fmt: skip
        if r.status == "check":
            item["reason"] = r.error
        out[r.status].append(item)
    if _local_files() and OUTPUT_DIR.is_dir():
        names = {Path(p).name for p in known}
        out["legacy_temp_files"] = sorted(p.name for p in OUTPUT_DIR.glob("tmp*.xlsx.tmp"))
        out["unlinked_result_files"] = sorted(p.name for p in OUTPUT_DIR.glob("*.xlsx") if p.name not in names)
    else:
        out["files"] = "이 DB의 결과 파일은 이 PC의 폴더에 없습니다 — 파일 확인은 건너뜀 (DB만 봄)"
    return out


def resolve_failed(run_id: int) -> dict[str, Any]:
    """'결과 확인 필요' 실행을 실패로 (조건부 — 그 상태일 때만). 파일은 지우지 않고 경로만 알린다."""
    with app_db.SessionLocal() as db:
        row = db.execute(
            select(IntegrationRun.output_path, IntegrationRun.summary).where(
                IntegrationRun.id == run_id, IntegrationRun.status == "check"
            )
        ).one_or_none()
        if row is None:
            return {"id": run_id, "changed": False, "reason": "결과 확인 필요 상태가 아닙니다 — 바꾸지 않았습니다"}
        res = db.execute(
            update(IntegrationRun)
            .where(IntegrationRun.id == run_id, IntegrationRun.status == "check")
            .values(status="failed", finished_at=datetime.now(KST), summary=_start_summary(row.summary), change_count=0,
                    error="관리자가 확인해 실패로 정리했습니다 (결과 확인 필요였음) — 다시 실행하세요.")
            .execution_options(synchronize_session=False)
        )  # fmt: skip
        if res.rowcount != 1:
            db.rollback()
            return {"id": run_id, "changed": False, "reason": "그 사이 상태가 바뀌었습니다 — 바꾸지 않았습니다"}
        db.execute(delete(RunChange).where(RunChange.run_id == run_id))
        db.commit()
    return {"id": run_id, "changed": True, "left_file": row.output_path}


LEASE_REVISION = "0020_run_worker_lease"  # 담당·생존 신호 열이 있는 판 — status·recover·resolve
LEGACY_REVISION = "0019_analysis_runs"  # 그 바로 앞 판 — status는 상태별 건수만, 바꾸는 명령은 거절
LEASE_COLUMNS = frozenset({"worker", "heartbeat_at"})


def schema() -> dict[str, Any]:
    """리비전(alembic_version의 모든 행)과 runs 표의 0020 열 — 읽기만 한다."""
    with app_db.engine.connect() as c:
        found = inspect(c)
        has_runs = found.has_table("runs")
        columns = {col["name"] for col in found.get_columns("runs")} if has_runs else set()
        revisions = (
            [r for (r,) in c.execute(text("SELECT version_num FROM alembic_version"))]
            if found.has_table("alembic_version")
            else []
        )
    return {"revisions": revisions, "runs_table": has_runs, "lease_columns": sorted(LEASE_COLUMNS & columns)}


def schema_problem(info: dict[str, Any]) -> tuple[str | None, str | None]:
    """(판, 문제): 판은 'lease'(0020 + 열) · 'legacy'(0019 + 열 없음), 문제는 그 밖일 때의 진단 (원문 없이)."""
    revisions, have = info["revisions"], set(info["lease_columns"])
    if not revisions:
        return None, "리비전 기록(alembic_version)이 없습니다"
    if len(revisions) > 1:
        return None, f"리비전이 {len(revisions)}개입니다 ({', '.join(revisions)})"
    revision = revisions[0]
    if revision not in (LEASE_REVISION, LEGACY_REVISION):
        supported = f"{LEGACY_REVISION}·{LEASE_REVISION}"
        return None, f"이 판의 복구 명령이 다루지 않는 리비전({revision})입니다 (다루는 리비전: {supported})"
    if not info["runs_table"]:
        return None, f"리비전은 {revision}인데 실행 기록 표(runs)가 없습니다"
    if revision == LEASE_REVISION and have != LEASE_COLUMNS:
        found = "일부만 있습니다" if have else "없습니다"
        return None, f"리비전은 {LEASE_REVISION}인데 담당·생존 신호 열(runs.worker·heartbeat_at)이 {found}"
    if revision == LEGACY_REVISION and have:
        return None, f"리비전은 {LEGACY_REVISION}인데 0020의 담당·생존 신호 열({', '.join(sorted(have))})이 있습니다"
    return ("lease" if revision == LEASE_REVISION else "legacy"), None


def legacy_status(revision: str) -> dict[str, Any]:
    """0019 DB: 실행 기록의 상태별 건수만 (읽기만 — 담당·생존 신호가 없어 정리 대상인지 판단하지 않는다)."""
    with app_db.engine.connect() as c:
        rows = c.execute(text("SELECT status, count(*) FROM runs GROUP BY status ORDER BY status")).all()
    return {
        "revision": revision,
        "lease_columns": False,
        "runs_by_status": {status: count for status, count in rows},
        "note": f"{LEGACY_REVISION} DB입니다 — 담당·생존 신호 열이 없어 실행 기록의 상태별 건수만 보여 줍니다 "
        f"(읽기만 함). recover·resolve는 {LEASE_REVISION} DB에서만 씁니다.",
    }


def _print(out: dict[str, Any]) -> None:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    print(json.dumps(out, ensure_ascii=encoding != "utf8", indent=1, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.recovery", description="멈춘 결과 실행 확인·정리 (docs/DEPLOY.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="실행 중·결과 확인 필요 실행과 연결 없는 파일")
    sub.add_parser("recover", help="담당 프로세스가 끝난 실행을 근거대로 한 번 정리 (기동 정리와 같음)")
    r = sub.add_parser("resolve", help="결과 확인 필요 실행을 실패로")
    r.add_argument("run_id", type=int)
    r.add_argument("--failed", action="store_true", required=True)
    a = ap.parse_args(argv)
    try:  # 다른 판 DB에 명령을 잘못 써도 바꾸지 않게 리비전과 열부터 맞춰 본다 (읽기만)
        info = schema()
        kind, problem = schema_problem(info)
        if kind == "legacy" and a.cmd == "status":
            _print(legacy_status(LEGACY_REVISION))
            return 0
    except Exception as exc:  # noqa: BLE001 — DB에 닿지 못함·DB가 아닌 파일 등. 종류만 (주소·SQL 없이)
        _print({"error": f"DB를 확인하지 못했습니다 ({type(exc).__name__}) — 아무것도 바꾸지 않았습니다."})
        return 1
    revision = info["revisions"][0] if len(info["revisions"]) == 1 else None
    if problem is not None:
        error = (
            f"{problem} — 마이그레이션이 끝나지 않았거나 다른 판·손으로 바뀐 DB일 수 있어 {a.cmd}를 하지 않았습니다 "
            "(아무것도 바꾸지 않음). 백업한 뒤 'alembic current'와 runs 표 구조를 확인하세요 (docs/DEPLOY.md)."
        )
        _print({"revisions": info["revisions"], "lease_columns": info["lease_columns"], "error": error})
        return 1
    if kind == "legacy":
        error = (
            f"이 DB는 {LEGACY_REVISION}입니다 — {a.cmd}는 {LEASE_REVISION} DB에서만 씁니다 (담당·생존 신호 열이 없음). "
            "아무것도 바꾸지 않았습니다 (docs/DEPLOY.md)."
        )
        _print({"revision": revision, "error": error})
        return 1
    if a.cmd == "status":
        out, ok = {"revision": revision, **status()}, True
    elif a.cmd == "recover":  # 0: 담당이 있는 실행 중 행 없음, 2: 남음(살아 있거나 근거를 기다림), 1: 오류
        out = recover()
        if out["complete"] and out["blocking"]:
            _print(out)
            return 2
        ok = out["complete"] and out["blocking"] == 0
    else:
        out = resolve_failed(a.run_id)
        ok = out["changed"]
    _print(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
