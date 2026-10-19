"""진단 도구를 돌리는 동안 공유 PC와 운영을 지켜보는 감시 (운영에는 health GET만 보낸다).

10초마다: 호스트 사용 가능 메모리, 운영 웹 /api/health 응답 시간·상태. 경고 기준을 넘으면 WARN 줄을 남긴다.
멈춤 파일이 생기거나 제한 시간이 지나면 끝난다.
사용법: python monitor.py <기록 파일(jsonl)> <멈춤 파일> [분, 기본 240]
"""

import ctypes
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROD_HEALTH = "http://localhost:8080/api/health"
MIN_AVAIL_MB = 600  # 이보다 적으면 경고 (도구를 멈출지 판단)
MAX_HEALTH_S = 2.0


class MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]  # fmt: skip


def avail_mb() -> int:
    s = MemoryStatus()
    s.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys / 1024 / 1024)


def health() -> tuple[str, float]:
    t = time.time()
    try:
        with urllib.request.urlopen(PROD_HEALTH, timeout=5) as r:
            return str(r.status), round(time.time() - t, 3)
    except Exception as e:  # noqa: BLE001 — 상태를 기록만 한다
        return type(e).__name__, round(time.time() - t, 3)


out, stop = Path(sys.argv[1]), Path(sys.argv[2])
end = time.time() + 60 * (float(sys.argv[3]) if len(sys.argv) > 3 else 240)
with out.open("a", encoding="utf-8") as f:
    while time.time() < end and not stop.exists():
        mem = avail_mb()
        code, secs = health()
        warn = mem < MIN_AVAIL_MB or code != "200" or secs > MAX_HEALTH_S
        row = {"t": datetime.now().strftime("%H:%M:%S"), "avail_mb": mem, "prod_health": code, "secs": secs, "warn": warn}
        f.write(json.dumps(row) + "\n")
        f.flush()
        if warn:
            print("WARN", json.dumps(row), flush=True)
        time.sleep(10)
