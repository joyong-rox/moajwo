"""결과 만들기 차례 — 결과 파일(묶기·통합)·통계 요약 평가·데이터 분석 계산과 분석 Excel처럼 메모리를 크게 쓰는 일을
한 프로세스에서 동시에 OUTPUT_SLOTS개까지만 한다 (docs/MEMORY.md '결과 만들기 차례').

- 결과 칸 예산(MAX_OUTPUT_CELLS)은 요청 하나의 크기만 막는다. 이 차례가 없으면 한도 가까운 결과 둘이 동시에 만들어져
  512MiB 서버가 메모리 부족으로 죽을 수 있다 (같은 조건 측정 — docs/MEMORY.md).
- 차례가 없으면 기다리지 않고 바로 거절한다(OutputBusy → 503). 실행 기록을 만들기 전, 무거운 계산 전에 거절하므로 남는
  기록·파일이 없고, 기다리는 요청이 DB 연결·작업 스레드를 쥐지 않는다. 다시 누르는 것은 사용자가 정한다.
- 같은 스레드가 다시 잡으면(묶기 계획 → 만들기, 통합 → 자동화) 차례를 하나로 센다.
- 프로세스 안의 차례다 — 다른 프로세스(worker 여럿·다른 서버)와 나누지 않는다. 기본 이미지는 프로세스 1개이고, worker를
  늘리면 이 한도가 worker 수만큼 곱해진다 (docs/MEMORY.md).
- 끝나면(정상·예외·DB 오류·요청 취소로 인한 예외 모두) finally에서 돌려준다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import OUTPUT_SLOTS

_SLOTS = threading.BoundedSemaphore(OUTPUT_SLOTS)
_held = threading.local()

BUSY = (
    "다른 결과(통합·묶기·통계 요약·데이터 분석)를 만드는 중이라 이 요청은 시작하지 않았습니다 — 실행 기록·파일은 "
    "만들지 않았습니다. 그 작업이 끝난 뒤 다시 누르세요."
)


class OutputBusy(Exception):
    """결과 만들기 차례가 없음 — 시작하지 않았다 (다시 눌러도 됨)."""

    def __init__(self) -> None:
        super().__init__(BUSY)


@contextmanager
def output_slot() -> Iterator[None]:
    depth = getattr(_held, "depth", 0)
    if depth:  # 같은 스레드가 이미 쥐고 있음 — 다시 잡지 않는다 (BoundedSemaphore를 두 번 잡으면 자기 자신을 기다림)
        _held.depth = depth + 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    if not _SLOTS.acquire(blocking=False):
        raise OutputBusy()
    _held.depth = 1
    try:
        yield
    finally:
        _held.depth = 0
        _SLOTS.release()
