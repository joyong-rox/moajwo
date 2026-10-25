"""결과 실행(묶기·통합)을 맡은 서버 프로세스와 그 생존 근거, 결과 파일을 확정하는 순서.

멈춘 실행 정리(app/recovery.py)가 살아 있는 실행을 건드리지 않고, 완성된 결과 파일을 지우지 않게 한다.

담당과 생존 근거
- worker = "<결과 폴더 식별자>-<프로세스 식별자>". 결과 폴더 식별자(outputs/.disk-id)는 그 폴더를 함께 쓰는
  프로세스끼리 같다 (로컬 Docker 볼륨은 재시작해도 같고, Render는 인스턴스마다 디스크가 새것이라 다르다).
- 프로세스 잠금: 실행을 처음 맡을 때 outputs/.workers/<프로세스 식별자>.lock에 운영체제 배타 잠금(POSIX flock,
  Windows msvcrt)을 걸고 프로세스가 끝날 때까지 쥔다. 프로세스가 죽으면(메모리 부족 종료·강제 종료 포함) 운영체제가
  푼다. 같은 폴더의 다른 프로세스는 이 잠금을 얻으면 '죽음 확정', 얻지 못하면 '살아 있음 확정'으로 본다.
  잠금이 프로세스 사이에서 듣지 않는 파일 시스템이면(자기 시험 실패) 잠금 파일을 두지 않는다 — 근거 없음이 된다.
- 생존 신호: 실행을 만드는 동안만 HEARTBEAT_S초마다 heartbeat_at(UTC)을 기록한다. 요청 연결 풀이 바빠도 막히지
  않게 전용 연결을 쓰고, 실행 하나는 MAX_RUN_S초까지만 보낸다. 실행이 없으면 DB에 아무것도 보내지 않는다 (외부 DB
  휴면 유지). 이 시각은 다른 서버(다른 디스크)의 실행을 '결과 확인 필요'로 둘지 정할 때만 쓴다 — 시각만으로
  '실패'로 정하지 않는다.
- 이 프로세스 안의 잠금(threading)은 생존 신호를 보낼 실행 목록만 지킨다. 다른 프로세스의 실행을 지키는 것은 DB의
  조건부 갱신과 운영체제 잠금이다.

결과를 확정하는 순서 (묶기·직접 통합·자동화 통합 공통)
1. begin (commit①): running·담당·계획한 최종 경로. 자동화가 만든 '검토 대기' 행은 그 상태일 때만 바꾼다.
2. 결과를 연결된 임시 파일 "<최종 이름>.<프로세스 식별자>.tmp"에 쓴다.
3. prepare (commit②): 임시 파일을 fsync하고 sha256을 구한 뒤, 담당·상태가 그대로일 때만 sha256·요약·변경 기록을
   함께 기록한다 (status는 그대로).
4. finish (commit③): 담당·상태·sha256이 그대로일 때만 done으로 바꾸는 UPDATE로 행(SQLite는 DB 쓰기) 잠금을 잡은
   채 임시 파일을 최종 이름으로 옮기고 commit한다 — 정리와 이름 바꾸기가 겹치지 않는다.
- 이름을 바꾸기 전 실패(fail): 조건부로 failed를 기록한 뒤에만 내 임시 파일을 지운다. 그 사이 다른 곳이 결과를
  정했으면 그 상태대로 답한다.
- 이름을 바꾼 뒤 실패(commit 오류·응답 유실): 최종 파일을 지우지 않고 다시 읽어 판정한다 — done이면 성공, 아직이면
  done을 다시 시도, 판정할 수 없으면 RunUncertain (결과 파일은 있고 기록만 미확정 — 정리가 근거로 확정한다).
- 조건부 갱신은 메모리의 객체를 바꾸지 않고(synchronize_session=False), 판정할 값은 DB에서 다시 읽는다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Row, create_engine, delete, insert, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.pool import NullPool

from app import db as app_db
from app.config import DATA_DIR
from app.models import IntegrationRun, RunChange
from app.timefmt import KST

logger = logging.getLogger(__name__)

OUTPUT_DIR = DATA_DIR / "outputs"
WORKERS_DIR = OUTPUT_DIR / ".workers"
LIVE = ("running", "check")  # 담당이 아직 결과를 확정할 수 있는 상태 (check = 정리가 결과를 확정하지 못함)
HEARTBEAT_S = 15
MAX_RUN_S = 3600  # 실행 하나에 생존 신호를 보내는 최대 시간 — 멈춘 담당이 외부 DB를 계속 깨우지 않게
_HEX12 = re.compile(r"^[0-9a-f]{12}$")
UNCERTAIN = (
    "결과 파일은 저장했지만 완료 기록을 확인하지 못했습니다 — 실행 기록에서 이 실행(#{run_id})의 상태를 확인하세요. "
    "서버가 결과 파일과 기록을 맞춰 보고 확정합니다."
)
UNRECORDED = (
    "결과를 만들지 못했지만 실패 기록을 남기지 못했습니다 — 실행 기록에서 이 실행(#{run_id})의 상태를 확인하세요. "
    "서버가 남은 파일과 기록을 맞춰 보고 확정합니다."
)


class RunUncertain(Exception):
    """결과를 확정하지 못함 (기록 미확정) — 다시 실행하기 전에 실행 기록에서 확인할 일. API는 503으로 알린다."""

    def __init__(self, run_id: int, template: str = UNCERTAIN) -> None:
        super().__init__(template.format(run_id=run_id))
        self.run_id = run_id


class RunError(Exception):
    """확정 실패 (기록됨) — 묶기·통합이 각자의 오류(409)로 바꿔 알린다."""


class RunSettled(RunError):
    """이 실행을 다른 곳(정리·관리자)이 이미 실패로 정함 — 그 이유로 알린다 (다시 기록하지 않는다)."""


class RunDone(Exception):
    """이 실행을 다른 곳(정리)이 이미 done으로 확정함 — 성공으로 답한다."""


# ---------------------------------------------------------------- 담당 식별자·프로세스 잠금

if os.name == "nt":
    import msvcrt

    def _lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:  # 다른 핸들이 쥐고 있음
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:  # 다른 열린 파일이 쥐고 있음 (그 밖의 OSError는 잠금을 못 씀 — 호출한 쪽이 판단)
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


_mutex = threading.Lock()
_me: dict[str, Any] = {}


def disk_id() -> str:
    """결과 폴더 식별자 — 처음 쓰는 프로세스가 만든다. 다 쓴 임시 파일을 link로 게시해, 동시에 뜬 프로세스가 반쯤 쓴
    값을 읽지 않는다."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    marker = OUTPUT_DIR / ".disk-id"
    if not marker.exists():
        tmp = OUTPUT_DIR / f".disk-id.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(uuid.uuid4().hex[:12].encode("ascii"))
        try:
            os.link(tmp, marker)
        except FileExistsError:  # 다른 프로세스가 먼저 게시함
            pass
        except OSError:  # link를 못 쓰는 파일 시스템 — 없을 때만 배타 생성으로 (늦은 쪽은 먼저 쓴 값을 읽는다)
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(tmp.read_bytes())
        finally:
            tmp.unlink(missing_ok=True)
    for _ in range(100):  # 배타 생성 직후 내용을 쓰기 전이면 잠깐 비어 있을 수 있다 (link 경로에서는 없음)
        value = marker.read_bytes().decode("ascii", "replace").strip()
        if value:
            break
        time.sleep(0.01)
    if not _HEX12.match(value):
        raise RuntimeError("결과 폴더 식별자(outputs/.disk-id)의 형식이 맞지 않습니다 — 정리하지 않습니다")
    return value


def _lock_path(proc: str) -> Path:
    return WORKERS_DIR / f"{proc}.lock"


def _hold_lock(proc: str) -> int | None:
    """이 프로세스의 잠금 파일을 만들고 평생 쥔다. 다른 핸들로는 잠기지 않는지 스스로 시험해, 잠금이 듣지 않으면
    파일을 지우고 None (다른 프로세스는 이 프로세스의 생존을 확정하지 못한다)."""
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    path = _lock_path(proc)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    ok = False
    try:
        if _lock(fd):
            probe = os.open(path, os.O_RDWR)
            try:
                ok = not _lock(probe)
                if not ok:
                    _unlock(probe)
            finally:
                os.close(probe)
    except OSError:
        ok = False
    if ok:
        return fd
    os.close(fd)
    path.unlink(missing_ok=True)
    logger.warning("worker lock does not work across processes here — liveness falls back to heartbeats")
    return None


def identity() -> dict[str, Any]:
    """이 프로세스의 담당 식별자 (처음 쓸 때 만든다 — fork로 pid가 바뀌면 새로 만든다)."""
    with _mutex:
        if _me.get("pid") != os.getpid():
            disk, proc = disk_id(), uuid.uuid4().hex[:12]
            fd = _hold_lock(proc)
            _me.clear()
            _me.update(pid=os.getpid(), disk=disk, proc=proc, worker=f"{disk}-{proc}", fd=fd, started=time.monotonic())
        return _me


def worker() -> str:
    return identity()["worker"]


def owner_state(worker_id: str | None) -> str:
    """'me' / 'alive' / 'dead' / 'unknown'. 같은 결과 폴더를 쓰는 담당만 그 잠금으로 확정한다 — 다른 디스크·이전 판
    (worker 없음)·잠금 파일 없음은 'unknown'."""
    me = identity()
    if not worker_id:
        return "unknown"
    if worker_id == me["worker"]:
        return "me"
    disk, _, proc = worker_id.partition("-")
    if disk != me["disk"] or not _HEX12.match(proc):
        return "unknown"
    try:
        fd = os.open(_lock_path(proc), os.O_RDWR)
    except OSError:
        return "unknown"
    try:
        if _lock(fd):
            _unlock(fd)
            return "dead"
        return "alive"
    except OSError:
        return "unknown"
    finally:
        os.close(fd)


def forget_dead_workers(keep: set[str], min_age_s: float = 60) -> int:
    """죽음이 확정된(잠금을 얻을 수 있는) 프로세스의 잠금 파일을 지운다. 막 만든 파일은 그 프로세스가 아직 잠그기 전일
    수 있어 두고, keep(아직 '실행 중'인 행의 담당)의 파일은 남긴다 — 그 파일이 '끝났다'는 근거라, 지우면 다음 차례에
    근거 없음('결과 확인 필요')으로 잘못 정한다 (recovery가 부른다)."""
    removed = 0
    me = identity()
    for path in WORKERS_DIR.glob("*.lock") if WORKERS_DIR.is_dir() else []:
        proc = path.stem
        if proc == me["proc"] or proc in keep or not _HEX12.match(proc):
            continue
        try:
            if time.time() - path.stat().st_mtime < min_age_s:
                continue
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        try:
            dead = _lock(fd)
            if dead:
                _unlock(fd)
        except OSError:
            dead = False
        finally:
            os.close(fd)
        if dead:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


# ---------------------------------------------------------------- 생존 신호

_beat_cv = threading.Condition()
_active: dict[int, float] = {}  # 실행 번호 → 맡은 시각 (monotonic)
_beat: dict[str, Any] = {"thread": None, "engine": None}


def track(run_id: int) -> None:
    """이 프로세스가 지금 만드는 실행 — 끝날 때까지 생존 신호를 보낸다 (스레드는 목록이 비면 멈춘다)."""
    with _beat_cv:
        _active[run_id] = time.monotonic()
        if _beat["thread"] is None:  # 멈추는 판단과 같은 잠금 안에서 본다 — 방금 넣은 실행이 신호 없이 남지 않게
            _beat["thread"] = threading.Thread(target=_beat_loop, name="run-heartbeat", daemon=True)
            _beat["thread"].start()


def untrack(run_id: int) -> None:
    with _beat_cv:
        _active.pop(run_id, None)
        _beat_cv.notify_all()


def active() -> set[int]:
    with _beat_cv:
        return set(_active)


def _beat_loop() -> None:
    while True:
        with _beat_cv:
            if not _active:
                _beat["thread"] = None
                return
            _beat_cv.wait(HEARTBEAT_S)
            now = time.monotonic()
            ids = [run_id for run_id, since in _active.items() if now - since < MAX_RUN_S]
        if ids:
            try:
                send_heartbeat(ids)
            except Exception as exc:  # noqa: BLE001 — 신호 실패는 기록만 하고 다음 차례에 다시 (실행은 막지 않음)
                logger.warning("run heartbeat failed: %s", type(exc).__name__)


def _engine() -> Engine:
    """생존 신호 전용 연결 (요청 연결 풀과 따로, 쓸 때만 연결 — 남는 연결이 외부 DB를 깨워 두지 않게)."""
    with _beat_cv:
        if _beat["engine"] is None:
            url = app_db.engine.url
            if url.get_backend_name() == "sqlite":
                args: dict[str, Any] = {"check_same_thread": False}
            else:  # 외부 PostgreSQL (psycopg 3): 앱 연결과 같은 접속 제한·준비된 문장 끔
                args = {"connect_timeout": 15, "application_name": "moajwo-heartbeat", "prepare_threshold": None}
            _beat["engine"] = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args=args)
        return _beat["engine"]


def send_heartbeat(run_ids: list[int]) -> None:
    eng = _engine()
    with eng.begin() as con:
        if eng.dialect.name == "postgresql":  # 담당·정리가 잠근 행 뒤에서 오래 기다리지 않게
            con.execute(text("SET LOCAL lock_timeout = '2s'"))
        con.execute(
            update(IntegrationRun)
            .where(IntegrationRun.id.in_(run_ids), IntegrationRun.worker == worker(), IntegrationRun.status.in_(LIVE))
            .values(heartbeat_at=datetime.now(UTC))
        )


# ---------------------------------------------------------------- 결과 파일 확정 순서


def temp_path(target: Path) -> Path:
    """최종 결과 파일에 연결된 이 프로세스의 임시 파일 (실행 번호는 최종 이름에, 담당은 프로세스 식별자로)."""
    return target.with_name(f"{target.name}.{identity()['proc']}.tmp")


def new_temp(target: Path) -> Path:
    """임시 파일을 비어 있는 새 파일로 만든다 (0600 — 결과에는 원본 값이 들어 있다)."""
    tmp = temp_path(target)
    tmp.unlink(missing_ok=True)  # 같은 프로세스가 같은 실행을 다시 만드는 경우만 (이름에 실행 번호·프로세스가 있음)
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    return tmp


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync(path: Path, flags: int = os.O_RDWR) -> None:
    fd = os.open(path, flags)  # Windows는 쓰기로 연 파일만 디스크로 내보낸다
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":  # Windows는 디렉터리를 열어 fsync할 수 없다 (이름 바꾸기는 NTFS 기록에 남는다)
        return
    try:
        _fsync(path, os.O_RDONLY)
    except OSError:
        logger.warning("could not fsync the output folder")


def read_state(db: Session, run_id: int) -> Row[Any] | None:
    """DB에 지금 기록된 값 (메모리의 객체가 아니라 열을 바로 읽는다)."""
    return db.execute(
        select(IntegrationRun.status, IntegrationRun.worker, IntegrationRun.output_sha256, IntegrationRun.error).where(
            IntegrationRun.id == run_id
        )
    ).one_or_none()


def _sync(run: IntegrationRun, **values: Any) -> None:
    for key, value in values.items():
        set_committed_value(run, key, value)


def begin(db: Session, run: IntegrationRun, path_for: Callable[[int], Path], from_status: str | None = None) -> Path:
    """commit①: running·담당·계획한 최종 경로를 기록하고 생존 신호를 시작한다. from_status가 있으면(자동화가 만든
    '검토 대기' 행을 이어 쓰는 경우) DB의 상태가 그대로일 때만 바꾸고, 아니면 RunError."""
    me = worker()
    run.status = "running"
    run.worker = me
    run.heartbeat_at = datetime.now(UTC)
    run.output_sha256 = None
    run.error = None
    if run.id is None:
        db.add(run)
        db.flush()  # 결과 파일 이름에 실행 번호를 쓴다
    target = path_for(run.id)
    run.output_path = target.relative_to(DATA_DIR).as_posix()
    if from_status is not None:
        res = db.execute(
            update(IntegrationRun)
            .where(IntegrationRun.id == run.id, IntegrationRun.status == from_status)
            .values(status="running")
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            db.rollback()
            raise RunError("이 실행은 이미 다른 요청이 만들기 시작했습니다 — 실행 기록을 확인하세요.")
    track(run.id)
    try:
        db.commit()
    except Exception:
        untrack(run.id)
        raise
    return target


def prepare(
    db: Session, run: IntegrationRun, tmp: Path, summary: str, change_count: int = 0, changes: list[dict] | None = None
) -> str:
    """commit②: 임시 파일을 fsync하고 sha256을 구한 뒤, 담당·상태가 그대로일 때만 sha256·요약·변경 기록을 함께 기록한다.
    응답을 잃었으면 다시 읽어 내 sha256이 기록됐는지로 판정한다."""
    _fsync(tmp)
    sha = file_sha256(tmp)
    res = db.execute(
        update(IntegrationRun)
        .where(IntegrationRun.id == run.id, IntegrationRun.worker == run.worker, IntegrationRun.status.in_(LIVE))
        .values(output_sha256=sha, summary=summary, change_count=change_count, heartbeat_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        db.rollback()
        _settled_elsewhere(db, run)
    if changes:
        db.execute(insert(RunChange), changes)
    try:
        db.commit()
    except Exception:
        db.rollback()
        state = read_state(db, run.id)  # DB에 닿지 못하면 그대로 던진다 — 호출한 쪽의 fail이 판단
        db.rollback()
        if state is None or state.worker != run.worker or state.output_sha256 != sha:
            raise
    _sync(run, output_sha256=sha, summary=summary, change_count=change_count)
    return sha


def _settled_elsewhere(db: Session, run: IntegrationRun) -> None:
    """조건부 갱신이 0행: 다른 곳(정리·관리자)이 결과를 정했다. 그 상태대로 알린다 (파일은 건드리지 않는다)."""
    state = read_state(db, run.id)
    db.rollback()
    if state is not None and state.status == "done":
        raise RunDone()
    if state is not None and state.status == "failed":
        raise RunSettled(state.error or "이 실행은 이미 실패로 기록됐습니다.")
    raise RunUncertain(run.id, UNRECORDED)


def finish(db: Session, run: IntegrationRun, tmp: Path, target: Path) -> None:
    """commit③: 담당·상태·sha256이 그대로일 때만 done으로 바꾸는 UPDATE의 잠금을 쥔 채 이름을 바꾸고 commit한다.
    이름을 바꾼 뒤의 오류는 최종 파일을 지우지 않고 다시 읽어 판정한다 (응답 유실이면 성공, 판정 불가면
    RunUncertain)."""
    if target.exists():  # Windows의 os.replace는 쓸 수 있는 대상을 조용히 덮으므로 먼저 본다
        raise RunError(f"같은 이름의 결과 파일이 이미 있습니다: {target.name}")
    sha, done_at = run.output_sha256, datetime.now(KST)
    if _mark_done(db, run, sha, done_at) != 1:
        db.rollback()
        _settled_elsewhere(db, run)
    try:
        os.replace(tmp, target)  # 같은 폴더 안 이동 — 원자적
    except OSError:
        db.rollback()
        raise
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:  # 권한만 못 바꿈 — 결과는 그대로 쓸 수 있다
        logger.warning("run %s: could not make the result read-only", run.id)
    _fsync_dir(target.parent)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 반영됐을 수 있다: 최종 파일을 두고 다시 읽는다
        _after_rename(db, run, sha, done_at, exc)
    _sync(run, status="done", finished_at=done_at, error=None)


def _mark_done(db: Session, run: IntegrationRun, sha: str | None, done_at: datetime) -> int:
    res = db.execute(
        update(IntegrationRun)
        .where(
            IntegrationRun.id == run.id,
            IntegrationRun.worker == run.worker,
            IntegrationRun.status.in_(LIVE),
            IntegrationRun.output_sha256 == sha,
        )
        .values(status="done", finished_at=done_at, error=None, heartbeat_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return res.rowcount


def _after_rename(db: Session, run: IntegrationRun, sha: str | None, done_at: datetime, exc: BaseException) -> None:
    logger.warning("run %s: completion commit failed after the rename (%s) — checking", run.id, type(exc).__name__)
    try:
        db.rollback()
        state = read_state(db, run.id)
        if state is not None and state.status == "done" and state.worker == run.worker:
            db.rollback()
            return  # 반영됐는데 응답만 잃음
        if state is not None and state.status in LIVE and state.worker == run.worker and state.output_sha256 == sha:
            if _mark_done(db, run, sha, done_at) == 1:
                db.commit()
                return
        db.rollback()
    except Exception as again:  # noqa: BLE001 — DB에 닿지 못함: 결과 파일은 두고 정리가 확정한다
        raise RunUncertain(run.id) from again
    raise RunUncertain(run.id) from exc


def fail(db: Session, run: IntegrationRun, tmp: Path | None, message: str, start_summary: str) -> None:
    """이름을 바꾸기 전 실패: 담당·상태가 그대로일 때만 failed(요약은 시작 때 값, 변경 기록 없음)를 기록하고,
    그 commit이 된 뒤에만 내 임시 파일을 지운다. 기록하지 못하면(DB 오류) 파일을 두고 RunUncertain — 정리가 근거로
    확정한다. 0행이면 다른 곳이 정한 상태대로 알린다 (RunDone / RunSettled / RunUncertain)."""
    try:
        db.rollback()
        res = db.execute(
            update(IntegrationRun)
            .where(IntegrationRun.id == run.id, IntegrationRun.worker == run.worker, IntegrationRun.status.in_(LIVE))
            .values(
                status="failed",
                error=message[:1000],
                finished_at=datetime.now(KST),
                output_path=None,
                output_sha256=None,
                summary=start_summary,
                change_count=0,
            )
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 1:
            db.execute(delete(RunChange).where(RunChange.run_id == run.id))
            db.commit()
        else:
            db.rollback()
            _settled_elsewhere(db, run)
    except RunSettled:  # 다른 곳이 실패로 정함 — 내 임시 파일은 쓸 곳이 없다
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise
    except (RunDone, RunUncertain):
        raise
    except Exception as exc:  # noqa: BLE001 — 실패 기록을 남기지 못함 (DB 오류 등): 파일은 두고 정리가 확정한다
        raise RunUncertain(run.id, UNRECORDED) from exc
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    _sync(run, status="failed", error=message[:1000], output_path=None, output_sha256=None, change_count=0)
