"""공유 PC 보호: 명령 하나를 실행하면서 사용 가능 메모리를 2초마다 보고, 바닥값 아래로 연속해서 떨어지면 그 명령(자식 포함)을 멈춘다.

- 시작 전: 사용 가능 메모리가 시작 기준 이상이 될 때까지 최대 대기 시간만큼 기다리고, 그래도 모자라면 실행하지 않는다 (종료 98).
- 실행 중: 바닥값 아래가 연속 N번이면 `taskkill /T /F`로 멈추고 종료 99 — 측정이 끝나지 않은 것이므로 결과를 통과로 세지 않는다.
- 메모리 측정은 monitor.py와 같은 GlobalMemoryStatusEx(ullAvailPhys).
사용법: python guarded_run.py [--start-mb 900] [--floor-mb 400] [--hits 2] [--wait-s 600] -- <명령 ...>   (run_logged.py 안에서 쓴다)
"""

import ctypes
import subprocess
import sys
import time


class MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]  # fmt: skip


def avail_mb() -> int:
    s = MemoryStatus()
    s.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys / 1024 / 1024)


def main() -> int:
    i = sys.argv.index("--")
    opts = dict(zip(sys.argv[1:i:2], sys.argv[2:i:2], strict=False))
    start, floor = int(opts.get("--start-mb", 900)), int(opts.get("--floor-mb", 400))
    hits, wait = int(opts.get("--hits", 2)), float(opts.get("--wait-s", 600))
    cmd = sys.argv[i + 1 :]
    t0 = time.time()
    while avail_mb() < start:
        if time.time() - t0 > wait:
            print(f"[guard] 시작 안 함: 사용 가능 메모리 {avail_mb()}MB < 시작 기준 {start}MB ({int(wait)}초 대기)", file=sys.stderr, flush=True)
            return 98
        time.sleep(5)
    print(f"[guard] 시작: 사용 가능 {avail_mb()}MB (대기 {int(time.time() - t0)}초) · 바닥 {floor}MB×{hits}", file=sys.stderr, flush=True)
    p = subprocess.Popen(cmd)
    low, lowest = 0, avail_mb()
    while p.poll() is None:
        time.sleep(2)
        m = avail_mb()
        lowest = min(lowest, m)
        low = low + 1 if m < floor else 0
        if low >= hits:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            p.wait()
            print(f"[guard] 멈춤: 사용 가능 메모리 {m}MB가 바닥 {floor}MB 아래 {hits}번 연속 — 측정 미완료 (통과로 세지 않음)", file=sys.stderr, flush=True)
            return 99
    print(f"[guard] 끝: 종료 {p.returncode} · 실행 중 최저 {lowest}MB", file=sys.stderr, flush=True)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
