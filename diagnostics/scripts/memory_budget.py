"""파일 읽기 메모리 측정 (docs/MEMORY.md의 근거) — 합성 자료를 크기별로 만들어 파일마다 새 프로세스에서 읽고
최대 메모리(RSS)·시간·칸 수·결과를 적는다. 판정하지 않고 측정만 저장한다.

- 자료: xlsx(풀었을 때 크기 단계별), csv(파일 크기 단계별). 숫자·짧은 글자·날짜가 섞인 통계표 모양.
  text: 한 칸에 긴 글(2,000자)이 든 CSV (파일 크기 단계별) — 칸 수는 적어도 글자가 메모리를 쓰는 경우.
- 읽기: backend의 app.parsing.parse_file 그대로 (한도는 환경 변수로 넉넉히 풀어 측정 — 거절 경계는 따로 적음).
- 중단 기준: --stop-mib (기본 1200) — 한 단계의 최대 메모리가 이 값을 넘으면 더 큰 단계를 돌리지 않는다.
  공유 PC에서는 guarded_run.py 아래에서 작은 단계만 돌리고, 큰 단계는 격리 러너(GitHub Actions)에서 돌린다.
- 결과에는 원문 값이 없다 (합성 자료).
사용법: python memory_budget.py <backend 폴더> <출력 JSON> [--xlsx-mib 4,8,16] [--csv-mib 5,10] [--text-mib 5]
        [--stop-mib 1200]
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

CHILD = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
def peak_bytes():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE  # 64비트 핸들 (기본 int로 두면 호출이 실패해 0이 나온다)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError("GetProcessMemoryInfo 실패")
        return c.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
from app import parsing
base = peak_bytes()
t = time.perf_counter()
try:
    result = parsing.parse_file(__import__("pathlib").Path(sys.argv[2]), sys.argv[3])
    out = {"ok": True, "sheets": len(result.sheets), "data_rows": result.data_rows}
except parsing.ParseError as exc:
    out = {"ok": False, "error": str(exc)[:200]}
out.update({"seconds": round(time.perf_counter() - t, 2), "import_peak_mib": round(base / 2**20, 1),
            "peak_mib": round(peak_bytes() / 2**20, 1)})
print(json.dumps(out))
"""

COLUMNS = ["기준일자", "시도", "시군구", "기관코드", "인구", "세대수", "예산", "비고"]


def _row(i: int) -> list:
    return [f"2026-09-{i % 28 + 1:02d}", "서울특별시", f"시군구{i % 25}", 1100000 + i, 1000 + i, 400 + i % 97,
            i * 1.5, "합성"]  # fmt: skip


def make_xlsx(path: Path, target_mib: float) -> tuple[int, int]:
    """풀었을 때 크기가 대략 target_mib가 되게 행 수를 늘린다 (먼저 작은 표본으로 행당 크기를 잰다)."""
    import openpyxl

    def write(rows: int) -> None:
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet("시트1")
        ws.append(COLUMNS)
        for i in range(rows):
            ws.append(_row(i))
        wb.save(path)

    write(2000)
    per_row = _unpacked(path) / 2000
    rows = max(1, int(target_mib * 2**20 / per_row))
    write(rows)
    return rows, rows * len(COLUMNS)


def make_csv(path: Path, target_mib: float) -> tuple[int, int]:
    line = ",".join(str(c) for c in _row(1)) + "\n"
    rows = max(1, int(target_mib * 2**20 / len(line.encode("utf-8"))))
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(COLUMNS) + "\n")
        for i in range(rows):
            f.write(",".join(str(c) for c in _row(i)) + "\n")
    return rows, rows * len(COLUMNS)


def make_text_csv(path: Path, target_mib: float) -> tuple[int, int]:
    note = ("합성 설명 문장입니다. " * 200)[:2000]  # 한 칸 2,000자 (한 칸 한도 32,767자 아래)
    line = f"2026-09-01,서울특별시,{note}\n"
    rows = max(1, int(target_mib * 2**20 / len(line.encode("utf-8"))))
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("기준일자,시도,설명\n")
        for _ in range(rows):
            f.write(line)
    return rows, rows * 3


def _unpacked(path: Path) -> int:
    with zipfile.ZipFile(path) as z:
        return sum(i.file_size for i in z.infolist())


def measure(backend: Path, path: Path, name: str) -> dict:
    env = {**os.environ, "MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB": "100000", "MOAJWO_MAX_FILE_CELLS": "1000000000",
           "MOAJWO_MAX_UPLOAD_MIB": "100000", "PYTHONIOENCODING": "utf-8"}  # fmt: skip
    with tempfile.TemporaryDirectory() as data:
        env["MOAJWO_DATA_DIR"] = data
        p = subprocess.run([sys.executable, "-c", CHILD, str(backend), str(path), name], capture_output=True, text=True,
                           env=env, encoding="utf-8", timeout=1800)  # fmt: skip
    if p.returncode != 0:
        return {"ok": False, "error": f"exit {p.returncode}: {p.stderr[-300:]}"}
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--xlsx-mib", default="4,8,16")
    ap.add_argument("--csv-mib", default="5,10")
    ap.add_argument("--text-mib", default="")
    ap.add_argument("--stop-mib", type=float, default=1200)
    a = ap.parse_args()
    results = {"host": {"platform": platform.platform(), "python": sys.version.split()[0], "cpus": os.cpu_count()},
               "stop_mib": a.stop_mib, "cases": []}  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        for kind, sizes, make in (("xlsx", a.xlsx_mib, make_xlsx), ("csv", a.csv_mib, make_csv),
                                  ("text", a.text_mib, make_text_csv)):  # fmt: skip
            for size in [float(x) for x in sizes.split(",") if x]:
                path = Path(tmp) / f"m{size}.{'xlsx' if kind == 'xlsx' else 'csv'}"
                rows, cells = make(path, size)
                case = {"kind": kind, "target_mib": size, "file_mib": round(path.stat().st_size / 2**20, 2),
                        "unpacked_mib": round((_unpacked(path) if kind == "xlsx" else path.stat().st_size) / 2**20, 2),
                        "rows": rows, "cells": cells, **measure(a.backend, path, path.name)}  # fmt: skip
                results["cases"].append(case)
                print(json.dumps(case, ensure_ascii=False), flush=True)
                path.unlink()
                if case.get("peak_mib", 0) > a.stop_mib:
                    print(f"[stop] {kind} {size}MiB 단계 최대 {case['peak_mib']}MiB > {a.stop_mib}MiB — 더 큰 단계 생략")
                    break
    a.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    time.sleep(0)


if __name__ == "__main__":
    main()
