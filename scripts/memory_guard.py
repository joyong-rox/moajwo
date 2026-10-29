"""공유 PC 자원 확인 — 후보 빌드·격리 점검 전에 여유 메모리를 재고(check), 그 단계 동안의 시작·최저값을 기록한다(watch).
운영 서비스와 같은 PC·같은 Docker에서 돌기 때문이다 (docs/DEPLOY.md 11장). 운영을 멈추거나 검사를 건너뛰지 않고, 확인에 실패하거나
기준에 못 미치면 후보 준비 단계에서 멈추게 하는 것이 목적이다.

사용법:
  python scripts/memory_guard.py check <단계> <docker 이미지> <기록.tsv>
      단계 시작 기준과 비교. 종료 0 통과 · 4 기준 미달 · 3 측정 실패(측정 실패는 통과가 아니다) · 1 예상치 못한 중단
  python scripts/memory_guard.py watch <단계> <docker 이미지> <기록.tsv> [--result <결과.json>] -- <명령 ...>
      명령을 실행하는 동안 1초마다(호스트)·2초마다(Docker VM) 재서 시작·최저를 남긴다. 재지 못한 값은 '미측정'으로 적고
      지어내지 않는다 — 표본을 얻지 못한 것은 판정을 바꾸지 않는다.
      끝나면 이 실행이 만든 감시 컨테이너(고유 이름)만 지우고 목록으로 남지 않았음을 확인한다. 종료 코드는 감싼 명령의 것이고,
      감시 컨테이너가 남았거나 남았는지 확인하지 못하면 감싼 명령의 결과와 상관없이 3(도구 오류)이다 — 남은 자원부터 봐야
      하므로 정리 쪽이 먼저다 (image_smoke.py와 같은 우선순위). 감싼 명령의 종료 코드·삭제 결과·삭제 뒤 목록 조회 결과·남은 이름은
      어느 쪽이든 기록(.tsv의 summary 줄, --result 파일)과 '판정' 줄에 함께 남는다.

측정 위치 (단위 MiB = 1024×1024 바이트):
  host — Windows: GlobalMemoryStatusEx.ullAvailPhys (작업 관리자의 '사용 가능'), 리눅스: /proc/meminfo MemAvailable
  vm   — Docker 엔진이 도는 VM(Docker Desktop은 WSL2 VM, 리눅스는 그 호스트)의 /proc/meminfo MemAvailable.
         주어진 이미지로 네트워크·볼륨 없는 일회용(check)·짧은 감시용(watch) 컨테이너를 띄워 읽는다. 컨테이너의 --memory 상한은 실측이 아니다.
"""

import ctypes
import json
import os
import secrets
import subprocess
import sys
import threading
import time

os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"  # Windows: docker를 현재 폴더(저장소)에서 먼저 찾지 않게 — 단독 실행에도
TOOL = "memory_guard 2"
MIB = 1024 * 1024
# 단계를 시작해도 되는 최소 여유 (MiB). 근거와 측정값은 docs/DEPLOY.md 11장 — 바꿀 때는 그 표와 함께 바꾼다.
# host: 기준과 비교하는 값은 단계 직전의 check 값이다. 이 PC에서 그 check 값으로 단계가 OOM·재시작 없이 끝난 것을 확인한
#       가장 낮은 값(2026-10-28 15:45 빌드 866·격리 점검 552, ullAvailPhys)을 10 MiB 단위로 올렸다. 감시 첫 표본(867·479)은
#       check 뒤 감시 컨테이너를 띄우고 잰 값이라 기준과 비교하는 값이 아니다. 그보다 낮은 여유는 확인하지 않았으므로
#       시작하지 않는다 (확인한 범위 안에서만).
# vm:   격리 점검 컨테이너 상한 합(backend 1g + frontend 256m = 1280) + 256. 측정한 VM 여유는 5.3 GiB 이상이었다.
THRESHOLDS = {
    "build": {"host": 870, "vm": 1536},
    "smoke": {"host": 560, "vm": 1536},
}
# 감시 컨테이너는 watch가 끝날 때 지운다. 그래도 watch가 강제로 끝나 지우지 못한 경우를 위해 스스로 2시간 뒤 끝난다(--rm으로 사라짐).
VM_SAMPLER = "import time\nend = time.time() + 7200\nwhile time.time() < end:\n    m = open('/proc/meminfo').read().split()\n    print(int(time.time()), m[m.index('MemAvailable:') + 1], flush=True)\n    time.sleep(2)\n"
# 지운 뒤 목록에 아직 보이면(--rm 자동 삭제와 겹쳐 'Removal In Progress'인 동안 등) 다시 보기 전에 기다리는 초 — 그래도 보이면 '남음'
RECHECK = (1, 1, 1)


def host_available() -> tuple[int | None, str]:
    """돌려줌: (MiB 또는 None, 측정 위치 설명)."""
    if sys.platform == "win32":

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]  # fmt: skip

        st = MemoryStatusEx()
        st.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None, "Windows GlobalMemoryStatusEx 실패"
        return st.ullAvailPhys // MIB, "Windows 사용 가능 물리 메모리 (GlobalMemoryStatusEx.ullAvailPhys)"
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            words = f.read().split()
        return int(words[words.index("MemAvailable:") + 1]) // 1024, "호스트 /proc/meminfo MemAvailable"
    except (OSError, ValueError, IndexError) as e:
        return None, f"호스트 측정 실패 ({type(e).__name__})"


def docker(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    try:
        r = subprocess.run(["docker", *args], capture_output=True, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", f"{type(e).__name__}: {e}"
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def vm_available(image: str) -> tuple[int | None, str]:
    rc, out, err = docker("run", "--rm", "--network", "none", "--read-only", "--memory", "64m", "--entrypoint", "cat", image, "/proc/meminfo")
    if rc != 0:
        return None, f"Docker VM 측정 실패 (종료 {rc}: {err.strip()[:160]})"
    words = out.split()
    try:
        return int(words[words.index("MemAvailable:") + 1]) // 1024, "Docker VM /proc/meminfo MemAvailable"
    except (ValueError, IndexError):
        return None, "Docker VM 측정 실패 (MemAvailable 없음)"


def append_tsv(path: str, rows: list[tuple]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")


def check(stage: str, image: str, tsv: str) -> int:
    limits = THRESHOLDS.get(stage.split("-")[0])
    if limits is None:
        print(f"자원 | {stage}: 모르는 단계 — 기준 없음 (확인 실패)")
        return 3
    host, host_where = host_available()
    vm, vm_where = vm_available(image)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    append_tsv(tsv, [(now, stage, "check-host", host if host is not None else "미측정", limits["host"]),
                     (now, stage, "check-vm", vm if vm is not None else "미측정", limits["vm"])])  # fmt: skip
    if host is None or vm is None:
        print(f"자원 | {stage} 시작 전 확인 실패 — {host_where if host is None else ''} {vm_where if vm is None else ''} (측정 실패는 통과가 아님)")
        return 3
    low = [f"{name} {val} MiB < 기준 {lim} MiB" for name, val, lim in (("host", host, limits["host"]), ("vm", vm, limits["vm"])) if val < lim]
    verdict = "기준 미달 — " + " · ".join(low) if low else "통과"
    print(f"자원 | {stage} 시작 전: host {host} MiB (기준 {limits['host']}, {host_where}) · vm {vm} MiB (기준 {limits['vm']}, {vm_where}) → {verdict}")
    return 4 if low else 0


def remove_sampler(name: str) -> dict:
    """이 실행의 감시 컨테이너(고유 이름)만 지우고, 지웠다는 응답만 믿지 않고 목록으로 남지 않았음을 확인한다.
    state: removed(목록 조회 성공·그 이름 없음) · left(목록에 남음) · unknown(목록 조회 실패 — '없음'이 아니라 확인 불가).
    다른 이름(앞 실행·다른 작업)의 컨테이너는 지우지도 판정에 넣지도 않는다."""
    rrc, _, rerr = docker("rm", "-f", name, timeout=60)
    for wait in (*RECHECK, None):
        lrc, lout, lerr = docker("ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}", timeout=60)
        left = [x for x in lout.split() if x == name] if lrc == 0 else None  # 필터는 부분 일치라 이름이 정확히 같은 것만
        if lrc != 0 or not left or wait is None:
            break
        time.sleep(wait)
    return {"name": name, "state": "unknown" if lrc != 0 else ("left" if left else "removed"),
            "remove_exit": rrc, "remove_err": rerr.strip()[-200:] if rrc else "",
            "list_exit": lrc, "list_err": lerr.strip()[-200:] if lrc else "", "left": left}  # fmt: skip


def cleanup_text(c: dict) -> str:
    if c["state"] == "removed":
        return f"확인됨 (감시 컨테이너 {c['name']} 없음)"
    removed = f"삭제 종료 {c['remove_exit']}" + (f": {c['remove_err']}" if c["remove_err"] else "")
    if c["state"] == "left":
        return f"확인 실패 — 감시 컨테이너 {c['name']}가 남음 ({removed}) — 직접 지울 것"
    return f"확인 실패 — 감시 컨테이너 {c['name']}가 남았는지 확인 불가 ({removed} · 목록 조회 종료 {c['list_exit']}: {c['list_err']}) — 직접 확인할 것"


def watch(stage: str, image: str, tsv: str, cmd: list[str], result: str | None = None) -> int:
    host_samples: list[tuple[float, int]] = []
    stop = threading.Event()

    def sample_host() -> None:
        while not stop.is_set():
            v, _ = host_available()
            if v is not None:
                host_samples.append((time.time(), v))
            stop.wait(1)

    name = f"moajwo-memwatch-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}-{secrets.token_hex(3)}"
    rc_s, _, err_s = docker("run", "-d", "--rm", "--network", "none", "--read-only", "--memory", "64m", "--name", name,
                            "--entrypoint", "python", image, "-u", "-c", VM_SAMPLER)  # fmt: skip
    vm_note = "" if rc_s == 0 else f"Docker VM 감시 컨테이너를 띄우지 못함 (종료 {rc_s}: {err_s.strip()[:160]})"
    t = threading.Thread(target=sample_host, daemon=True)
    t.start()
    started = time.time()
    vm_samples: list[tuple[float, int]] = []
    try:
        try:
            rc = subprocess.run(cmd, check=False).returncode
        except OSError as e:
            print(f"자원 | {stage}: 명령을 실행하지 못함 — {e}")
            rc = 126 if isinstance(e, PermissionError) else 127  # 셸 관례: 실행 권한 없음 126 · 찾지 못함 127
    finally:  # 명령이 중단돼도(Ctrl+C 등) 감시를 멈추고 감시 컨테이너를 지운다 — 운영과 같은 Docker에 남기지 않는다
        ended = time.time()
        stop.set()
        t.join(timeout=5)
        if rc_s == 0:
            lrc, lout, _ = docker("logs", name, timeout=60)
            if lrc == 0:
                for line in lout.splitlines():  # 이 실행의 감시 컨테이너가 낸 표본만 있다 — 시각(Docker VM 시계)으로 거르지 않는다
                    parts = line.split()
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        vm_samples.append((float(parts[0]), int(parts[1]) // 1024))
            else:
                vm_note = f"Docker VM 감시 기록을 읽지 못함 (docker logs 종료 {lrc})"
        # 띄우지 못했다고 나와도(시간 초과 등) 데몬이 만들었을 수 있어, 이 실행의 고유 이름으로 늘 지우고 목록으로 확인한다
        cleanup = remove_sampler(name)
        if cleanup["state"] != "removed":  # 감싼 명령이 중단돼 아래 판정 줄이 나오지 않는 경우에도 남은 자원을 알린다
            print(f"자원 | {stage}: 감시 자원 정리 {cleanup_text(cleanup)}", flush=True)
    if rc < 0 and sys.platform != "win32":
        rc = 128 - rc  # 신호로 끝난 명령(-N) → 셸 관례 128+N (예: SIGKILL 137)
    elif sys.platform == "win32" and rc >= 2**31:
        rc -= 2**32  # Windows 상태 코드(예: Ctrl+C 0xC000013A) → 부호 있는 값으로 넘겨 셸이 원래 값을 보게
    # 감시 자원 정리를 확인하지 못하면 도구 오류(3)가 감싼 명령의 결과보다 먼저다. 감싼 명령의 종료 코드는 command_exit로 남는다
    final = rc if cleanup["state"] == "removed" else 3
    append_tsv(tsv, [(time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)), stage, "host", v, "") for ts, v in host_samples]
               + [(time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)), stage, "vm", v, "") for ts, v in vm_samples])  # fmt: skip

    def summary(samples: list[tuple[float, int]]) -> str:
        if not samples:
            return "미측정"
        return f"시작 {samples[0][1]} MiB · 최저 {min(v for _, v in samples)} MiB (표본 {len(samples)})"

    print(f"자원 | {stage} 동안: host {summary(host_samples)} · vm {summary(vm_samples)} · {round(ended - started)}초"
          + (f" · {vm_note}" if vm_note else ""), flush=True)  # fmt: skip
    record = {"tool": TOOL, "stage": stage, "seconds": round(ended - started, 1), "exit": final, "command_exit": rc, "cleanup": cleanup,
              "host": {"start": host_samples[0][1] if host_samples else None, "min": min((v for _, v in host_samples), default=None), "samples": len(host_samples)},
              "vm": {"start": vm_samples[0][1] if vm_samples else None, "min": min((v for _, v in vm_samples), default=None), "samples": len(vm_samples)},
              "note": vm_note}  # fmt: skip
    if result:  # 부르는 쪽(image_gate.sh)이 이번 실행의 판정만 읽게 — 감싼 명령의 출력과 섞이지 않는 파일로
        try:
            tmp = f"{result}.tmp-{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=1)
            os.replace(tmp, result)
        except OSError as e:
            final = record["exit"] = 3
            record["note"] = (vm_note + " · " if vm_note else "") + f"결과 파일을 쓰지 못함: {e}"
    append_tsv(tsv, [(time.strftime("%Y-%m-%dT%H:%M:%S%z"), stage, "summary", json.dumps(record, ensure_ascii=False), "")])
    print(f"자원 | {stage} 판정: 감싼 명령 종료 {rc} · 감시 자원 정리 {cleanup_text(cleanup)}"
          + (f" · {record['note']}" if record["note"] != vm_note else "") + f" → 종료 {final}", flush=True)  # fmt: skip
    return final


def main() -> int:
    sys.stdout.reconfigure(errors="replace")  # Windows 콘솔(cp949)에서 못 쓰는 글자로 멈추지 않게
    a = sys.argv[1:]
    if len(a) == 4 and a[0] == "check":
        return check(a[1], a[2], a[3])
    if len(a) >= 6 and a[0] == "watch" and "--" in a[4:]:
        i = a.index("--", 4)
        opt, cmd = a[4:i], a[i + 1 :]
        if cmd and (not opt or (len(opt) == 2 and opt[0] == "--result")):
            return watch(a[1], a[2], a[3], cmd, opt[1] if opt else None)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
