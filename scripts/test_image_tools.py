"""반영 전 이미지 검증 도구의 부정 입력·명령 실패 시험 (실제 Docker를 쓰지 않는다 — docs/DEPLOY.md 11장).

사용법: python scripts/test_image_tools.py [--scripts <scripts 폴더>] [--json <결과.json>]
  --scripts: 시험할 판의 scripts 폴더 (기본: 이 파일이 있는 폴더). 다른 판(예: 수정 전 커밋)을 같은 조건으로 시험할 때 쓴다.
  종료 0: 모든 사례가 기대와 같음. '기대와 다름'은 통과·막힘(또는 점검 자원을 남김)이 다른 것과, 막히긴 했지만 종료 코드
  구분(0 통과 · 4 측정한 실패 · 3 도구 오류)이 기대와 다른 것을 나눠 센다.

  1. image_smoke.py — main()을 프로세스 안에서 돌리고 subprocess.run을 가짜 docker로 바꾼다(실제 docker에 닿지 않음).
     정상 대조군과 빈·일부·중복·형식 오류 결과, 로그 조회 실패·빈 로그, 자원 삭제·목록 조회 실패.
  2. image_inventory.py compare — 최소·끊긴 JSON, 필수 항목이 양쪽에 다 없음, 자료형 오류, 도구 판·종류 다름, 실제 목록 같음·다름,
     같은 이미지(같은 ID) 비교, 같은 ID인데 내용이 다름(모순), 후보에서 모은 목록을 검증 목록 자리에 씀(출처 기록 없음).
  3. image_gate.sh — 임시 git 저장소에 이 판의 image_gate.sh를 커밋하고 목록·점검 도구는 대역으로 바꿔(docker 호출 없음)
     종료 0인데 결과가 없거나 실패를 말하는 경우, PYTHON·DEPLOY_EXPECTED_DIR 환경 변수, 자원 기준 미달, 검증 목록의 커밋하지 않은
     변경, 감시 컨테이너 정리 실패. 자원 도구는 이 판의 진짜 memory_guard.py를 docker 호출만 가짜로 바꿔 돌린다.
  4. memory_guard.py — main()을 프로세스 안에서 돌리고 호스트 측정과 subprocess.run(docker·감싼 명령)을 가짜로 바꾼다.
     기준 이상·미달·측정 실패(통과가 아님), watch가 명령 종료 코드를 그대로 돌려주고 중단(Ctrl+C)에도 감시 컨테이너를 지우는지,
     감시 컨테이너 삭제 실패·남음·목록 조회 실패는 명령 결과와 상관없이 3이고 두 결과가 기록에 남는지, 표본을 못 얻은 것은 판정을
     바꾸지 않는지. 이 파일이 없는 판(수정 전)에서는 건너뛴다.
  통과·막힘이 맞아도 남아야 할 근거(gate.result의 두 사유, watch 기록의 명령 종료 코드·정리 결과)가 없으면 '구분' 쪽으로 센다.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 1. image_smoke.py (가짜 docker)


def child_items(mod, scenario: str) -> object:
    """컨테이너 점검 결과 — 새 판은 CHECKS_IN의 id, 옛 판은 CHECK_PY의 점검 이름."""
    if hasattr(mod, "CHECKS_IN"):
        items = [{"id": cid, "item": name, "ok": True, "detail": {}} for cid, name in mod.CHECKS_IN]
    else:
        names = re.findall(r'rec\("([^"]+)"', mod.CHECK_PY)
        items = [{"item": n, "ok": True, "detail": {}} for n in names]
    if scenario == "empty":
        return []
    if scenario == "partial":
        return items[:1]
    if scenario == "duplicate":
        return items[:-1] + [dict(items[0])]
    if scenario == "ok_string":
        items[0]["ok"] = "false"
    if scenario == "ok_missing":
        del items[0]["ok"]
    if scenario == "unknown_id":
        items.append({"id": "made.up", "item": "없는 점검", "ok": True, "detail": {}})
    if scenario == "not_list":
        return {"checks": items}
    if scenario in ("one_failed", "one_failed_rm_fail"):
        items[0]["ok"] = False
    if scenario == "child_step_error":  # 컨테이너 안 TLS 단계가 예외로 중단 — 그 단계 점검은 사유와 함께 실패 (옛 판은 이름으로 찾음)
        for x in items:
            if str(x.get("id", x.get("item", ""))).startswith(("ca.", "tls.", "CA", "TLS")):
                x["ok"] = False
                x["detail"] = {"not_run": True, "reason": "FileNotFoundError: [Errno 2] No such file or directory: 'openssl'"}
    if scenario == "unicode_sep":  # 값 안의 줄 구분 문자 (U+2028) — 결과 줄이 끊기면 안 됨
        items[0]["detail"] = {"body": "앞 뒤 끝\u0085"}
    return items


class FakeDocker:
    """image_smoke.py가 부르는 docker 명령만 흉내 낸다. 모르는 명령은 기록하고 실패로 돌려준다."""

    def __init__(self, mod, scenario: str):
        self.mod, self.s = mod, scenario
        self.containers: set[str] = set()
        self.volumes = {"moajwo_moajwo-data"}
        self.networks = {"bridge", "moajwo_default"}
        self.unknown: list[str] = []
        self.meta: dict[str, dict] = {}  # 컨테이너별 네트워크·볼륨 (격리 확인 조회용)
        self.removed = False  # 정리를 시작했는지 (목록 조회 실패를 '삭제 뒤'에만 일으키려고)
        self.called: set[str] = set()  # 실패를 넣는 자리 중 실제로 불린 것 (CMD_INJECT — 주입이 닿았는지)
        self.health_calls = 0
        if scenario == "leftover":  # 앞 실행이 남긴 점검 컨테이너
            self.containers.add("moajwo-smoke-20261028000000-1-abc123-backend")

    def __call__(self, args, input=None, capture_output=False, env=None, timeout=None, check=False, **kw):  # noqa: A002, ANN001, ARG002
        a = list(args)
        if not a or a[0] != "docker":
            raise AssertionError(f"docker가 아닌 명령: {a}")
        rc, out, err = self.handle(a[1:], input)
        return subprocess.CompletedProcess(a, rc, out.encode(), err.encode())

    def handle(self, a: list[str], _stdin: bytes | None) -> tuple[int, str, str]:
        s = self.s
        if a[:2] == ["image", "inspect"]:
            return 0, f"sha256:{'b' if 'backend' in a[2] else 'f'}" + "0" * 63, ""
        if a[:2] == ["network", "create"]:
            self.networks.add(a[-1])
            return 0, a[-1], ""
        if a[:2] == ["volume", "create"]:
            self.volumes.add(a[-1])
            return 0, a[-1], ""
        if a[:2] == ["run", "-d"]:
            name = a[a.index("--name") + 1]
            self.containers.add(name)
            vol = next((x.split(":")[0] for i, x in enumerate(a) if i and a[i - 1] == "-v"), None)
            self.meta[name] = {"net": a[a.index("--network") + 1], "vol": vol}
            if s == "anon_volume" and name.endswith("-backend"):  # 이미지의 VOLUME 선언 — docker run이 익명 볼륨을 만든다
                anon = "a" * 64
                self.volumes.add(anon)
                self.meta[name]["anon"] = anon
            return 0, "c" * 64, ""
        if a[:2] == ["network", "inspect"]:
            return 0, "false" if s == "not_internal" else "true", ""
        if a[0] == "inspect":
            fmt = a[a.index("--format") + 1]
            if "PortBindings" in fmt:
                m = self.meta.get(a[1], {})
                ports = '{"8000/tcp":[{"HostIp":"127.0.0.1","HostPort":"18000"}]}' if s == "port_published" else "{}"
                mounts = (f"volume:{m['vol']}:/app/data;" if m.get("vol") else "") + (f"volume:{m['anon']}:/var/cache;" if m.get("anon") else "")
                return 0, f"{ports}|{mounts}|{m.get('net', '')};", ""
            if "Health" in fmt and "State.Status" in fmt:
                self.health_calls += 1
                if self.health_calls > 1:
                    self.called.add("health_repeat")
                if s == "health_then_timeout" and self.health_calls > 1:  # 첫 조회는 됐고 그다음부터 docker가 응답하지 않음
                    raise subprocess.TimeoutExpired(["docker", "inspect"], 60)
                return 0, "running starting 0" if s == "health_then_timeout" else "running healthy 0", ""
            if "Health" in fmt:  # 옛 판의 상태 조회 형식 — 같은 시나리오를 흉내 낸다
                self.health_calls += 1
                if self.health_calls > 1:
                    self.called.add("health_repeat")
                if s == "health_then_timeout" and self.health_calls > 1:
                    raise subprocess.TimeoutExpired(["docker", "inspect"], 60)
                return 0, "starting" if s == "health_then_timeout" else "healthy", ""
            return 0, "running 0", ""
        if a[0] == "exec" and "wget" in a:
            self.called.add("wget")
            if s == "front_daemon_err":  # docker 쪽 연결 실패 — frontend에 닿지 못함
                return 1, "", 'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/exec": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.'
            return 0, "", ""
        if a[:2] == ["exec", "-i"]:
            self.called.add("exec_i")
            if s == "exec_fail":
                return 1, "", "OCI runtime exec failed"
            return 0, "점검 중…\n@@RESULT " + json.dumps(child_items(self.mod, s), ensure_ascii=False) + "\n", ""
        if a[0] == "logs":
            backend = a[1].endswith("-backend")
            self.called.update(("logs", "logs_backend") if backend else ("logs",))
            if s == "logs_fail" and backend:
                return 17, "", "Error response from daemon: can not get logs"
            if s == "logs_empty":
                return 0, "", ""
            mark = "INFO:     Application startup complete.\n" if backend else "/docker-entrypoint.sh: Configuration complete; ready for start up\n"
            if s == "log_join" and backend:  # 표준출력 끝에 줄바꿈이 없고 표준오류가 ERROR 줄로 시작
                return 0, 'INFO:     127.0.0.1:1 - "GET /health HTTP/1.1" 200 OK', "ERROR:    something went wrong\n" + mark
            return 0, "", mark
        if a[:2] == ["rm", "-f"]:  # 새 판: rm -f -v <이름>, 옛 판: rm -f <이름>
            self.removed = True
            self.called.add("rm")
            name = a[-1]
            if s in ("rm_fail", "one_failed_rm_fail"):
                return 1, "", "Error response from daemon: removal in progress"
            self.containers.discard(name)
            if "-v" in a and self.meta.get(name, {}).get("anon"):  # -v: 그 컨테이너의 익명 볼륨도 지움
                self.volumes.discard(self.meta[name]["anon"])
            return 0, name, ""
        if a[:2] == ["volume", "rm"]:
            self.called.add("volume_rm")
            if s == "volume_rm_fail":
                return 1, "", "Error response from daemon: volume is in use"
            self.volumes.discard(a[2])
            return 0, a[2], ""
        if a[:2] == ["network", "rm"]:
            self.called.add("network_rm")
            if s == "network_rm_fail":
                return 1, "", "Error response from daemon: network has active endpoints"
            self.networks.discard(a[2])
            return 0, a[2], ""
        if a[:2] == ["ps", "-a"]:  # 옛 판: --filter name=<tag>, 새 판: 전부 이름만 — 삭제 뒤 조회 실패는 두 꼴 모두에 넣는다
            if self.removed:
                self.called.add("list_after_rm")
            if s == "list_fail" and self.removed:
                return 1, "", "Cannot connect to the Docker daemon"
            names = sorted(self.containers | {"moajwo-backend-1", "moajwo-frontend-1"})
            if "--filter" in a:
                tag = a[a.index("--filter") + 1].split("=", 1)[1]
                names = [n for n in names if tag in n]
            return 0, "\n".join(names), ""
        if a[:2] == ["volume", "ls"]:
            if self.removed:
                self.called.add("list_after_rm")
            if s == "list_fail" and self.removed:
                return 1, "", "Cannot connect to the Docker daemon"
            names = sorted(self.volumes)
            if "--filter" in a:
                tag = a[a.index("--filter") + 1].split("=", 1)[1]
                names = [n for n in names if tag in n]
            return 0, "\n".join(names), ""
        if a[:2] == ["network", "ls"]:
            if self.removed:
                self.called.update(("list_after_rm", "network_ls_after_rm"))
            if s in ("list_fail", "network_ls_fail") and self.removed:
                return 1, "", "Cannot connect to the Docker daemon"
            return 0, "\n".join(sorted(self.networks)), ""
        self.unknown.append(" ".join(a))
        return 1, "", "가짜 docker가 모르는 명령"


SMOKE_CASES = [
    ("normal", True, "정상 대조군 — 필수 점검이 모두 한 번, ok=true"),
    ("empty", False, "컨테이너 결과가 빈 목록"),
    ("partial", False, "필수 점검 하나만"),
    ("duplicate", False, "하나를 다른 점검의 중복으로 바꿔 개수만 맞춤"),
    ("ok_string", False, "ok가 문자열 'false'"),
    ("ok_missing", False, "ok 없음"),
    ("unknown_id", False, "모르는 점검이 하나 더 있음"),
    ("not_list", False, "결과가 목록이 아님"),
    ("one_failed", False, "점검 하나가 ok=false (제품 실패)"),
    ("exec_fail", False, "컨테이너 점검 실행이 종료 1"),
    ("logs_fail", False, "backend docker logs가 종료 17"),
    ("logs_empty", False, "docker logs가 종료 0이지만 빈 로그"),
    ("rm_fail", False, "컨테이너 삭제 실패(남음)"),
    ("volume_rm_fail", False, "볼륨 삭제 실패(남음)"),
    ("network_rm_fail", False, "네트워크 삭제 실패(남음)"),
    ("network_ls_fail", False, "삭제 뒤 네트워크 목록 조회 실패"),
    ("list_fail", False, "삭제 뒤 목록 조회가 모두 실패"),
    ("not_internal", False, "점검 네트워크가 내부 네트워크가 아님 (격리 확인)"),
    ("port_published", False, "점검 backend에 호스트 포트가 연결됨 (격리 확인)"),
    ("leftover", False, "앞 실행이 남긴 점검 컨테이너가 있음 (지우지 않고 멈춤)"),
    # 2026-10-28 수정본 검토에서 나온 경우 — 막히는 것과 함께 종료 코드 구분(3 도구 오류 · 4 측정한 실패)을 본다
    ("front_daemon_err", False, "frontend 응답 조회가 docker 쪽 연결 실패 (frontend의 실패가 아님)"),
    ("health_then_timeout", False, "backend 상태를 한 번 읽은 뒤 docker inspect가 계속 시간 초과"),
    ("one_failed_rm_fail", False, "점검 하나 실패 + 컨테이너 삭제 실패 (정리 확인이 먼저)"),
    ("child_step_error", False, "컨테이너 안 TLS 단계가 예외로 중단 (그 단계 점검은 사유와 함께 실패 — 대부분 후보 쪽 원인)"),
    ("unicode_sep", True, "점검 값에 U+2028·U+2029·U+0085 (결과 줄이 끊기지 않아야 함)"),
    ("log_join", False, "backend 로그 표준출력이 줄바꿈 없이 끝나고 표준오류가 ERROR 줄로 시작"),
    ("anon_volume", False, "후보 backend 이미지에 VOLUME 선언 (익명 볼륨 — 격리 확인에서 멈추고 익명 볼륨까지 지움)"),
    ("print_fail", False, "모든 점검이 통과인데 화면 출력이 실패 (통과 JSON을 남기지 않아야 함)"),
    ("write_fail", False, "모든 점검이 통과인데 결과 JSON 쓰기가 실패 (통과 JSON이 없어야 함)"),
]
# 종료 코드 기대 (적힌 사례만): 0 통과 · 4 측정한 점검 실패 · 3 도구 오류. 정리 실패·확인 불가가 있으면 3이 먼저
SMOKE_RC = {"normal": 0, "empty": 3, "partial": 3, "duplicate": 3, "ok_string": 3, "ok_missing": 3, "unknown_id": 3, "not_list": 3,
            "one_failed": 4, "exec_fail": 3, "logs_fail": 3, "logs_empty": 4, "rm_fail": 3, "volume_rm_fail": 3, "network_rm_fail": 3,
            "network_ls_fail": 3, "list_fail": 3, "not_internal": 3, "port_published": 3, "leftover": 3,
            "front_daemon_err": 3, "health_then_timeout": 3, "one_failed_rm_fail": 3, "child_step_error": 4, "unicode_sep": 0,
            "log_join": 4, "anon_volume": 3, "print_fail": 3, "write_fail": 3}  # fmt: skip
# docker 명령 실패를 넣는 사례 → 그 실패를 넣는 자리. 그 명령을 부르지 않는 판에는 주입이 닿지 않는다 — 옛 판이면 '해당 없음',
# 새 판(CHECKS_IN 있음)이면 시험이 그 경우를 확인하지 못한 것이라 기대와 다름으로 센다. 격리·포트·익명 볼륨·앞 실행 잔여처럼
# docker 쪽 상태를 바꾸는 사례는 도구가 조회하든 않든 그 상태가 있으므로 여기에 넣지 않는다 (조회하지 않으면 그대로 결함)
CMD_INJECT = {"exec_fail": "exec_i", "logs_fail": "logs_backend", "logs_empty": "logs", "log_join": "logs_backend", "rm_fail": "rm",
              "one_failed_rm_fail": "rm", "volume_rm_fail": "volume_rm", "network_rm_fail": "network_rm",
              "network_ls_fail": "network_ls_after_rm", "list_fail": "list_after_rm", "front_daemon_err": "wget",
              "health_then_timeout": "health_repeat"}  # fmt: skip
SMOKE_LEAK_OK = {"rm_fail", "volume_rm_fail", "network_rm_fail", "one_failed_rm_fail", "leftover"}  # 삭제 실패를 일부러 일으킨 사례


class _BrokenOut(io.TextIOWrapper):
    """쓰기마다 실패하는 표준출력 (출력 실패 시험)."""

    def write(self, s: str) -> int:
        raise OSError(5, "가짜 출력 실패")


def _fail_write(path: str, data: dict) -> None:
    raise OSError(28, "가짜 쓰기 실패 (디스크 가득)")


def run_smoke(scripts: Path, scenario: str, tmp: Path) -> dict:
    mod = load(scripts / "image_smoke.py", f"image_smoke_{scenario}")
    fake = FakeDocker(mod, scenario)
    out = tmp / f"smoke-{scenario}.json"
    clock = [0.0]
    saved = (subprocess.run, time.sleep, time.monotonic, sys.argv, sys.stdout)
    buf = (_BrokenOut if scenario == "print_fail" else io.TextIOWrapper)(io.BytesIO(), encoding="utf-8")
    if scenario == "write_fail":
        if not hasattr(mod, "write_atomic"):  # 옛 판은 결과 파일 쓰기를 따로 부르지 않아 실패를 주입할 곳이 없다
            return {"rc": None, "passed": None, "na": "주입할 곳 없음 (write_atomic 없음)", "summary": "", "leak": []}
        mod.write_atomic = _fail_write
    try:
        subprocess.run = fake
        time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
        time.monotonic = lambda: clock[0]
        sys.argv = ["image_smoke.py", "img-backend", "img-frontend", str(out)]
        sys.stdout = buf
        try:
            rc = mod.main()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # noqa: BLE001 — 판정에 기록
            rc = f"예외 {type(e).__name__}: {e}"
    finally:
        subprocess.run, time.sleep, time.monotonic, sys.argv, sys.stdout = saved
    buf.flush()
    text = buf.buffer.getvalue().decode("utf-8", "replace")
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    summary = next((x for x in text.splitlines() if x.startswith("요약")), "")
    passed = rc == 0 or (data or {}).get("ok") is True  # 실패로 끝났는데 통과 JSON(ok true)이 남으면 통과로 센다 (기대와 다름)
    point = CMD_INJECT.get(scenario)
    if point and point not in fake.called:
        if not hasattr(mod, "CHECKS_IN"):  # 옛 판: 그 명령을 부르지 않아 주입할 곳이 없다
            return {"rc": rc, "passed": None, "na": f"주입할 곳 없음 (이 판은 삭제 뒤 {point}에 해당하는 docker 명령을 부르지 않음)",
                    "summary": summary[:160], "leak": []}  # fmt: skip
        summary = f"[시험이 확인하지 못함] 주입 자리 {point}가 불리지 않음 · {summary}"
        passed = True  # 실패를 넣었는데 닿지 않았다 — 막혔다고 볼 근거가 없으므로 통과로 센다 (기대와 다름)
    left = sorted({c for c in fake.containers if c.startswith("moajwo-smoke-")} | (fake.volumes - {"moajwo_moajwo-data"})
                  | {n for n in fake.networks if n.startswith("moajwo-smoke-")})  # fmt: skip
    return {"rc": rc, "passed": passed, "summary": summary[:160], "status": (data or {}).get("status"),
            "json_ok": (data or {}).get("ok"), "unknown_docker": fake.unknown,
            "leak": [] if scenario in SMOKE_LEAK_OK else left}  # fmt: skip


# ---------------------------------------------------------------- 2. image_inventory.py compare


def compare_cases(repo_root: Path) -> list[tuple[str, bool, str, object, object]]:
    exp = {k: json.loads((repo_root / "deploy" / "expected-images" / f"{k}.json").read_text(encoding="utf-8")) for k in ("backend", "frontend")}
    b, f = exp["backend"], exp["frontend"]

    def candidate(doc: dict) -> dict:  # 같은 실행 환경의 다른 빌드 (이미지 ID가 다르고 출처 기록 없음 — collect 결과와 같은 꼴)
        c = {k: v for k, v in json.loads(json.dumps(doc)).items() if k != "verified"}
        c["image"]["id"] = "sha256:" + "c" * 64
        return c

    def same_image(doc: dict) -> dict:  # 검증 목록과 같은 이미지(같은 ID)에서 새로 모은 목록 — 출처 기록 없음
        return {k: v for k, v in json.loads(json.dumps(doc)).items() if k != "verified"}

    cb, cf = candidate(b), candidate(f)
    sb, sf = same_image(b), same_image(f)
    same_id_changed = same_image(b)
    same_id_changed["os_packages"]["tzdata"] = same_id_changed["os_packages"]["tzdata"] + "~시험"
    changed = candidate(b)
    changed["os_packages"]["tzdata"] = changed["os_packages"]["tzdata"] + "~시험"
    no_verified = {k: v for k, v in b.items() if k != "verified"}
    dup = json.dumps(cb, ensure_ascii=False)[:-1] + ', "machine": "aarch64"}'  # 같은 키가 두 번 — 마지막 값만 남으면 다른 값이 숨는다
    no_dists = {k: v for k, v in b.items() if k != "python_dists"}
    as_list = dict(b, os_packages=sorted(b["os_packages"]))
    pe = dict(b, psycopg={"error": "ImportError: 시험"})
    tool = dict(b, tool="image_inventory 0")
    respelled = json.loads(json.dumps(cb))  # 후보 자신의 목록 + 지어낸 출처 기록 + ID 끝에 공백 (같은 이미지인데 문자열만 다름)
    respelled["verified"] = {"ci_run": "x", "commit": "0" * 40}
    respelled["image"]["id"] += " "
    kind_list = dict(cb, kind=["backend"])
    variant_b = dict(b, platform=dict(b["platform"], variant={}))  # 검증 목록과 후보(다른 빌드) 모두 같은 잘못된 값
    variant_c = dict(cb, platform=dict(cb["platform"], variant={}))
    minimal_b = {"kind": "backend", "image": {"id": "x"}}
    minimal_f = {"kind": "frontend", "image": {"id": "x"}}
    return [
        ("same_backend", True, "검증 목록 backend = 같은 환경의 다른 빌드", b, cb),
        ("same_frontend", True, "검증 목록 frontend = 같은 환경의 다른 빌드 (Python 항목 없음)", f, cf),
        ("differ", False, "OS 패키지 판 하나 다름", b, changed),
        ("same_image_backend", True, "같은 이미지 비교 — 검증 목록 backend와 같은 ID·같은 실행 환경 (ID만으로 거절하지 않음)", b, sb),
        ("same_image_frontend", True, "같은 이미지 비교 — 검증 목록 frontend와 같은 ID·같은 실행 환경", f, sf),
        ("same_id_contradiction", False, "같은 이미지 ID인데 OS 패키지 판 하나 다름 (모순 — ID가 같아도 비교함)", b, same_id_changed),
        ("self_list_no_provenance", False, "후보에서 모은 목록을 그대로 검증 목록 자리에 씀 (출처 기록 없음 — 출처 보호)", cb, cb),
        ("no_verified", False, "검증 목록에 CI 출처 기록(verified)이 없음", no_verified, cb),
        ("dup_key", False, "후보 목록 JSON에 같은 키가 두 번 (machine)", b, dup),
        ("broken_json", False, "후보 목록 JSON이 중간에 끊김 (불완전한 JSON)", b, json.dumps(cb, ensure_ascii=False)[:200]),
        ("minimal_backend", False, "kind·image.id만 있는 같은 JSON 두 개 (backend)", minimal_b, minimal_b),
        ("minimal_frontend", False, "kind·image.id만 있는 같은 JSON 두 개 (frontend)", minimal_f, minimal_f),
        ("both_missing_dists", False, "양쪽 다 python_dists 없음", no_dists, no_dists),
        ("list_type", False, "os_packages가 양쪽 다 목록(자료형 오류)", as_list, as_list),
        ("psycopg_error", False, "psycopg가 양쪽 다 오류 기록", pe, pe),
        ("tool_mismatch", False, "도구 판이 다른 목록", tool, tool),
        ("kind_mismatch", False, "backend 목록과 frontend 목록", b, f),
        ("self_respelled", False, "후보 자신의 목록 + 지어낸 verified + 이미지 ID 끝 공백", respelled, cb),
        ("kind_list", False, "kind가 문자열이 아님 (목록) — 예외가 아니라 입력 오류", b, kind_list),
        ("nested_types", False, "양쪽 다 platform.variant가 객체 (안쪽 항목 자료형)", variant_b, variant_c),
    ]


# 종료 코드 기대: 0 같음 · 4 실행 환경이 다름 · 3 입력 오류(도구 판·종류·필수 항목·형식·출처 기록·중복 키·같은 ID인데 다름)
COMPARE_RC = {"same_backend": 0, "same_frontend": 0, "differ": 4, "same_image_backend": 0, "same_image_frontend": 0,
              "same_id_contradiction": 3, "self_list_no_provenance": 3, "no_verified": 3, "dup_key": 3, "broken_json": 3,
              "minimal_backend": 3, "minimal_frontend": 3, "both_missing_dists": 3, "list_type": 3, "psycopg_error": 3,
              "tool_mismatch": 3, "kind_mismatch": 3, "self_respelled": 3, "kind_list": 3, "nested_types": 3}  # fmt: skip


def run_compare(scripts: Path, a: object, b: object, tmp: Path, name: str) -> dict:
    pa, pb = tmp / f"cmp-{name}-a.json", tmp / f"cmp-{name}-b.json"
    pa.write_text(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False), encoding="utf-8")
    pb.write_text(b if isinstance(b, str) else json.dumps(b, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(scripts / "image_inventory.py"), "compare", str(pa), str(pb)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)  # fmt: skip
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return {"rc": r.returncode, "passed": r.returncode == 0, "summary": last[:160]}


# ---------------------------------------------------------------- 3. image_gate.sh (대역 도구)

STUB_INV = r"""import json, os, sys
mode = os.environ.get("GATE_STUB", "ok")
if sys.argv[1] == "collect":
    if mode == "collect_empty":
        sys.exit(0)
    json.dump({"kind": sys.argv[2], "marker": "built"}, open(sys.argv[4], "w"))
    print("collect", sys.argv[2])
    sys.exit(0)
exp = json.load(open(sys.argv[2])); act = json.load(open(sys.argv[3]))
if mode == "compare_crash":
    raise RuntimeError("대역 비교 중단")
if mode == "compare_silent":
    sys.exit(0)
same = exp.get("marker") == act.get("marker")
print(f"요약 | {act['kind']} 실행 환경 항목 {'같음' if same else '다름 1건'} | 기대 x | 실제 y")
sys.exit(0 if same else (4 if os.environ.get("GATE_NEW_CODES") else 1))
"""

STUB_MEM = r'''"""자원 확인 대역 — 시험할 판의 진짜 memory_guard.py(GATE_REAL_MEM)를 그대로 돌리고 docker 호출과 호스트 측정만 가짜로 바꾼다.
watch가 감싼 명령(대역 격리 점검)은 진짜로 실행한다. docker 호출은 모두 여기서 답하므로 실제 docker에 닿지 않는다.
GATE_WATCH: rm_fail(감시 컨테이너 삭제 실패 — 남음) · left(삭제 응답은 성공인데 목록에 남음) · list_fail(삭제 뒤 목록 조회 실패)."""
import importlib.util
import os
import subprocess
import sys

mode, fault = os.environ.get("GATE_STUB", "ok"), os.environ.get("GATE_WATCH", "")
spec = importlib.util.spec_from_file_location("memory_guard_under_test", os.environ["GATE_REAL_MEM"])
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)
real_run = subprocess.run
made, gone = [], []


def cp(args, rc, out="", err=""):
    return subprocess.CompletedProcess(args, rc, out.encode("utf-8"), err.encode("utf-8"))


def fake(args, *a, **kw):
    if not args or args[0] != "docker":
        return real_run(args, *a, **kw)
    d = list(args[1:])
    if d[:2] == ["run", "-d"]:
        made.append(d[d.index("--name") + 1])
        return cp(args, 0, "cid\n")
    if d[:1] == ["run"]:
        return cp(args, 0, "MemTotal: 8000000 kB\nMemAvailable: 5120000 kB\n")
    if d[:1] == ["logs"]:
        return cp(args, 0, "1700000000 5120000\n")
    if d[:2] == ["rm", "-f"]:
        if fault == "rm_fail":
            return cp(args, 1, err="Error response from daemon: (대역) 삭제 실패")
        gone.append(d[2])
        return cp(args, 0, d[2] + "\n")
    if d[:2] == ["ps", "-a"]:
        if fault == "list_fail":
            return cp(args, 1, err="Error response from daemon: (대역) 목록 조회 실패")
        return cp(args, 0, "".join(n + "\n" for n in made if fault == "left" or n not in gone))
    return cp(args, 1, err="모르는 docker 명령 (대역)")


subprocess.run = fake
mg.host_available = lambda: (500 if mode == "mem_low" else 900, "대역 호스트 측정")
if hasattr(mg, "RECHECK"):
    mg.RECHECK = ()  # 남아 보일 때 다시 보기 전의 기다림 — 대역은 기다리지 않음
sys.exit(mg.main())
'''


def stub_smoke(real: Path) -> str:
    """대역 점검 — 진짜 파일의 상수(TOOL·REQUIRED)는 그대로 두고 main만 바꾼다 (검증 스크립트가 상수를 읽으므로).
    진짜 파일 끝의 실행 블록(실제 docker를 부르는 main)은 잘라 낸다 — 남아 있으면 대역이 아니다."""
    src = real.read_text(encoding="utf-8")
    head, sep, _ = src.rpartition('if __name__ == "__main__":')
    if not sep:
        raise SystemExit(f"{real}에 실행 블록이 없어 대역을 만들 수 없음")
    return (
        head
        + r"""

def _stub_main():
    import json, os, sys
    mode = os.environ.get("GATE_STUB", "ok")
    b, f, out = sys.argv[1:4]
    req = globals().get("REQUIRED") or []
    rows = [{"id": c, "item": n, "ok": True, "detail": None, "measured": True} for c, n in req]
    d = {"tool": globals().get("TOOL"), "images": {"backend": b, "frontend": f}, "status": "pass", "ok": True,
         "required": len(req), "passed": len(req), "failed": [], "unmeasured": [], "errors": [], "checks": rows}
    if mode == "smoke_no_json":
        sys.exit(0)
    if mode == "smoke_json_fail":
        d.update(status="fail", ok=False)
    if mode == "smoke_wrong_ids":
        d["images"] = {"backend": "sha256:other", "frontend": f}
    if mode == "smoke_fail":
        d.update(status="fail", ok=False)
        json.dump(d, open(out, "w"))
        sys.exit(4 if os.environ.get("GATE_NEW_CODES") else 1)
    json.dump(d, open(out, "w"))
    sys.exit(0)

if __name__ == "__main__":
    _stub_main()
"""
    )


GATE_CASES = [
    ("normal", True, "모든 대역이 통과", {}),
    ("compare_differ", False, "실행 환경이 다름", {"GATE_STUB": "compare_differ"}),
    ("compare_crash", False, "목록 대조 도구가 예외로 중단", {"GATE_STUB": "compare_crash"}),
    ("compare_silent", False, "대조가 종료 0인데 '같음' 요약이 없음", {"GATE_STUB": "compare_silent"}),
    ("collect_empty", False, "목록 수집이 종료 0인데 파일이 없음", {"GATE_STUB": "collect_empty"}),
    ("smoke_fail", False, "격리 점검 실패", {"GATE_STUB": "smoke_fail"}),
    ("smoke_no_json", False, "격리 점검이 종료 0인데 결과 JSON이 없음", {"GATE_STUB": "smoke_no_json"}),
    ("smoke_json_fail", False, "격리 점검이 종료 0인데 JSON은 실패", {"GATE_STUB": "smoke_json_fail"}),
    ("smoke_wrong_ids", False, "격리 점검 JSON의 이미지 ID가 다름", {"GATE_STUB": "smoke_wrong_ids"}),
    ("python_env_true", False, "PYTHON=true + 실제로는 대조 실패 (환경 변수로 인터프리터 바꾸기)", {"GATE_STUB": "compare_differ", "PYTHON": "true"}),
    ("expected_dir_env", False, "DEPLOY_EXPECTED_DIR가 '같음'이 되는 목록을 가리킴 (저장소 목록은 다름)", {"GATE_STUB": "ok", "EXPECTED_ENV": "1"}),
    ("mem_low", False, "격리 점검 전 자원 기준 미달", {"GATE_STUB": "mem_low"}),
    ("result_unwritable", False, "통과 기록 gate.result를 쓸 수 없음 (그 이름이 폴더)", {}),
    ("bash_env", False, "BASH_ENV가 있음 (셸 시작 파일이 검증 명령을 바꿀 수 있음)", {}),
    ("expected_modified", False, "검증 목록을 커밋하지 않고 후보에서 모은 목록으로 덮어씀 (무단 변경)", {}),
    ("watch_rm_fail", False, "격리 점검 통과 + 감시 컨테이너 삭제 실패·남음 (진짜 watch)", {"GATE_WATCH": "rm_fail"}),
    ("watch_left", False, "격리 점검 통과 + 삭제 응답은 성공인데 감시 컨테이너가 목록에 남음", {"GATE_WATCH": "left"}),
    ("watch_list_fail", False, "격리 점검 통과 + 삭제 뒤 목록 조회 실패 (남았는지 확인 불가)", {"GATE_WATCH": "list_fail"}),
    ("watch_rm_fail_smoke_fail", False, "격리 점검 실패 + 감시 컨테이너 삭제 실패 — 두 사유 모두", {"GATE_STUB": "smoke_fail", "GATE_WATCH": "rm_fail"}),
]
# 막힐 때 gate.result의 reason에 있어야 하는 조각 — 막혔어도 사유를 잃으면 '구분' 쪽으로 센다
GATE_REASON = {"expected_modified": ["HEAD에 커밋된 내용과 다르"], "watch_rm_fail": ["감시 자원 정리 확인 실패", "남음"],
               "watch_left": ["감시 자원 정리 확인 실패", "남음"], "watch_list_fail": ["감시 자원 정리 확인 실패", "확인 불가"],
               "watch_rm_fail_smoke_fail": ["격리 기동 점검 실패 (측정한 점검이 실패)", "감시 자원 정리 확인 실패"]}  # fmt: skip


def run_gate(scripts: Path, case: str, env_extra: dict, tmp: Path) -> dict:
    root = tmp / f"gate-{case}"
    (root / "scripts").mkdir(parents=True)
    (root / "deploy" / "expected-images").mkdir(parents=True)
    shutil.copy(scripts / "image_gate.sh", root / "scripts" / "image_gate.sh")
    (root / "scripts" / "image_inventory.py").write_text(STUB_INV, encoding="utf-8")
    (root / "scripts" / "memory_guard.py").write_text(STUB_MEM, encoding="utf-8")
    (root / "scripts" / "image_smoke.py").write_text(stub_smoke(scripts / "image_smoke.py"), encoding="utf-8")
    marker = "other" if case in ("compare_differ", "python_env_true", "expected_dir_env", "expected_modified") else "built"
    if case == "result_unwritable":
        (root / "out" / "gate.result").mkdir(parents=True)
    for k in ("backend", "frontend"):
        (root / "deploy" / "expected-images" / f"{k}.json").write_text(json.dumps({"kind": k, "marker": marker}), encoding="utf-8")
    git = ["git", "-c", "core.autocrlf=false", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "gate stub"]):  # 운영처럼 검증 목록·도구가 커밋된 저장소
        subprocess.run(git + cmd, cwd=root, check=True, capture_output=True)
    if case == "expected_modified":  # 커밋한 목록(다름)을 후보에서 모은 목록(같음)으로 덮어씀 — 커밋하지 않음
        (root / "deploy" / "expected-images" / "backend.json").write_text(json.dumps({"kind": "backend", "marker": "built"}), encoding="utf-8")
        (root / "deploy" / "expected-images" / "frontend.json").write_text(json.dumps({"kind": "frontend", "marker": "built"}), encoding="utf-8")
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    env["GATE_REAL_MEM"] = str(scripts / "memory_guard.py")
    env.pop("GATE_WATCH", None)
    if env_extra.get("GATE_WATCH"):
        env["GATE_WATCH"] = env_extra["GATE_WATCH"]
    env.pop("PYTHON", None)
    env.pop("DEPLOY_EXPECTED_DIR", None)
    if "python_env_true" == case:
        env["PYTHON"] = env_extra["PYTHON"]
    if env_extra.get("EXPECTED_ENV"):
        alt = tmp / f"gate-{case}-alt"
        alt.mkdir()
        for k in ("backend", "frontend"):
            (alt / f"{k}.json").write_text(json.dumps({"kind": k, "marker": "built"}), encoding="utf-8")
        env["DEPLOY_EXPECTED_DIR"] = str(alt)
    env["GATE_STUB"] = env_extra.get("GATE_STUB", "ok")
    env.pop("BASH_ENV", None)
    if case == "bash_env":  # 내용은 무해(true)해도 검증은 멈춰야 한다
        benv = root / "benv.sh"
        benv.write_text("true\n", encoding="utf-8")
        env["BASH_ENV"] = str(benv)
    if "find_python()" in (scripts / "image_gate.sh").read_text(encoding="utf-8"):
        env["GATE_NEW_CODES"] = "1"  # 새 판의 종료 코드 약속(부정 판정 4)을 대역도 따른다
    bash = shutil.which("bash") or "bash"
    r = subprocess.run([bash, "scripts/image_gate.sh", "sha256:bbb", "sha256:fff", "out"], cwd=root, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False)  # fmt: skip
    res = root / "out" / "gate.result"
    lines = [x for x in r.stdout.splitlines() if x.startswith("[검증")]
    record = res.read_text(encoding="utf-8").splitlines() if res.is_file() else []
    reason = next((x[len("reason=") :] for x in record if x.startswith("reason=")), "")
    out = {"rc": r.returncode, "passed": r.returncode == 0 or "status=pass" in record,  # 막혔어도 통과 기록이 남으면 통과로 본다
           "summary": (lines[-1] if lines else r.stdout.strip()[-160:])[:160], "gate_result": record[:1] or None}  # fmt: skip
    missing = [w for w in GATE_REASON.get(case, []) if w not in reason]
    if r.returncode != 0 and missing:
        out["evidence_ok"] = False
        out["summary"] = f"gate.result 사유에 없음 {missing} | {out['summary']}"[:200]
    return out


# ---------------------------------------------------------------- 실행


# ---------------------------------------------------------------- 4. memory_guard.py (가짜 호스트 측정·가짜 docker)

MEM_CASES = [
    ("check_ok", True, "check build — host 900·vm 5000 MiB (기준 이상)"),
    ("check_host_low", False, "check build — host 800 MiB < 기준 870"),
    ("check_vm_low", False, "check smoke — vm 1000 MiB < 기준 1536"),
    ("check_host_fail", False, "check — 호스트 측정 실패 (통과가 아님)"),
    ("check_vm_fail", False, "check — Docker VM 측정 컨테이너 실패 (통과가 아님)"),
    ("check_vm_garbage", False, "check — VM 출력에 MemAvailable 없음 (통과가 아님)"),
    ("check_unknown_stage", False, "check — 기준이 없는 단계 이름"),
    ("watch_ok", True, "watch — 명령 종료 0, 감시 컨테이너 지움"),
    ("watch_cmd_fail", False, "watch — 명령 종료 2를 그대로 돌려줌, 감시 컨테이너 지움"),
    ("watch_interrupt", False, "watch — 명령 도중 Ctrl+C(KeyboardInterrupt)에도 감시 컨테이너 지움"),
    ("watch_sampler_fail", True, "watch — 감시 컨테이너를 못 띄워도 명령은 돌고 VM은 '미측정'으로 적음"),
    ("watch_sampler_timeout", True, "watch — 감시 컨테이너 docker run이 시간 초과(데몬은 만들었음)여도 이름으로 지움"),
    ("watch_signal", False, "watch — 명령이 신호·Windows 상태 코드로 끝남 (셸이 보는 값 그대로)"),
    ("watch_permission", False, "watch — 명령 실행 권한 없음 (126)"),
    ("watch_vm_clock_skew", True, "watch — Docker VM 시계가 100초 어긋나도 VM 표본을 버리지 않음"),
    ("watch_logs_fail", True, "watch — 감시 기록(docker logs)을 못 읽음: VM '미측정'일 뿐 판정은 그대로 (표본 부족 ≠ 정리 실패)"),
    ("watch_removal_in_progress", True, "watch — 삭제가 '이미 진행 중' 오류여도 다시 본 목록에서 없어졌으면 정리 확인"),
    ("watch_rm_fail", False, "watch — 명령 종료 0 + 감시 컨테이너 삭제 실패·목록에 남음 → 3"),
    ("watch_left", False, "watch — 명령 종료 0 + 삭제 응답은 성공인데 목록에 남음 → 3"),
    ("watch_list_fail", False, "watch — 명령 종료 0 + 삭제 뒤 목록 조회 실패 (남았는지 확인 불가) → 3"),
    ("watch_cmd_fail_rm_fail", False, "watch — 명령 종료 2 + 감시 컨테이너 삭제 실패 → 3, 기록에 명령 종료 2와 남은 이름"),
    ("watch_result_file", True, "watch --result — 이번 실행의 판정 기록(명령 종료 코드·정리 결과)을 따로 남김"),
]


# watch는 감시 자원 정리를 확인하면 감싼 명령의 종료 코드를, 확인하지 못하면 3을 돌려준다
MEM_RC = {"check_ok": 0, "check_host_low": 4, "check_vm_low": 4, "check_host_fail": 3, "check_vm_fail": 3, "check_vm_garbage": 3,
          "check_unknown_stage": 3, "watch_ok": 0, "watch_cmd_fail": 2, "watch_sampler_fail": 0, "watch_sampler_timeout": 0,
          "watch_signal": -1073741510 if sys.platform == "win32" else 137, "watch_permission": 126, "watch_vm_clock_skew": 0,
          "watch_logs_fail": 0, "watch_removal_in_progress": 0, "watch_rm_fail": 3, "watch_left": 3, "watch_list_fail": 3,
          "watch_cmd_fail_rm_fail": 3, "watch_result_file": 0}  # fmt: skip
MEM_LEAK_OK = {"watch_rm_fail", "watch_cmd_fail_rm_fail"}  # 삭제 실패를 일부러 일으킨 사례 — 남은 감시 컨테이너는 기대한 것
# 기록(.tsv summary·--result)에 남아야 하는 (감싼 명령의 종료 코드, 정리 상태)
MEM_RECORD = {"watch_rm_fail": (0, "left"), "watch_left": (0, "left"), "watch_list_fail": (0, "unknown"),
              "watch_cmd_fail_rm_fail": (2, "left"), "watch_result_file": (0, "removed")}  # fmt: skip


def _cp(args: list[str], rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, rc, out.encode("utf-8"), err.encode("utf-8"))


class FakeMemDocker:
    """memory_guard.py가 부르는 docker 명령과 watch가 감싼 명령을 흉내 낸다. 띄운·지운 감시 컨테이너를 기억한다."""

    def __init__(self, scenario: str):
        self.s = scenario
        self.started: list[str] = []
        self.removed: list[str] = []
        self.listed = 0

    def __call__(self, args, input=None, capture_output=False, env=None, timeout=None, check=False, **kw):  # noqa: A002, ANN001, ARG002
        a = list(args)
        if a[0] != "docker":  # watch가 감싼 명령
            if self.s == "watch_interrupt":
                raise KeyboardInterrupt
            if self.s == "watch_permission":
                raise PermissionError(13, "액세스 거부 (가짜)")
            if self.s == "watch_signal":  # Windows: Ctrl+C 상태 코드(부호 없는 값), 리눅스: SIGKILL(-9)
                return _cp(a, 3221225786 if sys.platform == "win32" else -9)
            return _cp(a, 2 if self.s in ("watch_cmd_fail", "watch_cmd_fail_rm_fail") else 0)
        d = a[1:]
        if d[:2] == ["run", "-d"]:  # watch의 VM 감시 컨테이너
            if self.s == "watch_sampler_fail":
                return _cp(a, 125, err="docker: Error response from daemon: (가짜)")
            self.started.append(d[d.index("--name") + 1])
            if self.s == "watch_sampler_timeout":  # CLI는 시간 초과로 끊겼지만 데몬은 만들었다
                raise subprocess.TimeoutExpired(a, 120)
            return _cp(a, 0, "cid\n")
        if d[:1] == ["run"]:  # check의 VM 측정 (cat /proc/meminfo)
            if self.s == "check_vm_fail":
                return _cp(a, 125, err="docker: Error response from daemon: No such image (가짜)")
            if self.s == "check_vm_garbage":
                return _cp(a, 0, "garbage\n")
            vm = 1000 if self.s == "check_vm_low" else 5000
            return _cp(a, 0, f"MemTotal: 8000000 kB\nMemAvailable: {vm * 1024} kB\n")
        if d[:1] == ["logs"]:
            if self.s == "watch_logs_fail":
                return _cp(a, 1, err="Error response from daemon: (가짜) 기록을 읽지 못함")
            return _cp(a, 0, f"{int(time.time()) + (100 if self.s == 'watch_vm_clock_skew' else 0)} {5000 * 1024}\n")
        if d[:2] == ["ps", "-a"]:  # 삭제 뒤 확인: 띄우고 지우지 않은 감시 컨테이너
            self.listed += 1
            if self.s == "watch_list_fail":
                return _cp(a, 1, err="Error response from daemon: (가짜) 목록 조회 실패")
            if self.s == "watch_left" or (self.s == "watch_removal_in_progress" and self.listed == 1):  # 지웠다는데 보임
                return _cp(a, 0, "".join(n + "\n" for n in self.started))
            return _cp(a, 0, "".join(n + "\n" for n in self.started if n not in self.removed))
        if d[:2] == ["rm", "-f"]:
            if d[2] not in self.started:
                return _cp(a, 1, err=f"Error response from daemon: No such container: {d[2]}")
            if self.s in ("watch_rm_fail", "watch_cmd_fail_rm_fail"):
                return _cp(a, 1, err="Error response from daemon: (가짜) 삭제 실패")
            self.removed.append(d[2])
            if self.s == "watch_removal_in_progress":  # --rm 자동 삭제와 겹침 — 오류지만 곧 없어진다
                return _cp(a, 1, err=f"Error response from daemon: removal of container {d[2]} is already in progress")
            return _cp(a, 0, d[2] + "\n")
        return _cp(a, 1, err="모르는 명령 (가짜)")


def run_mem(scripts: Path, scenario: str, tmp: Path) -> dict:
    path = scripts / "memory_guard.py"
    if not path.exists():
        raise FileNotFoundError(path)
    mod = load(path, f"memory_guard_{scenario}")
    fake = FakeMemDocker(scenario)
    host = {"check_host_low": 800, "check_host_fail": None}.get(scenario, 900)
    stage = {"check_vm_low": "smoke", "check_unknown_stage": "deploy"}.get(scenario, "build")
    tsv = tmp / f"mem-{scenario}.tsv"
    result = tmp / f"mem-{scenario}.watch.json"
    if scenario == "watch_result_file" and getattr(mod, "TOOL", "") == "memory_guard 1":
        return {"rc": None, "passed": None, "na": "이 판의 watch에는 --result가 없음 (판정 기록 파일은 새 동작)"}
    saved = (subprocess.run, sys.argv, sys.stdout, mod.host_available)
    buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    if hasattr(mod, "RECHECK"):
        mod.RECHECK = (0, 0, 0)  # 남아 보이면 다시 보는 동작은 그대로, 기다림만 없앰
    try:
        subprocess.run = fake
        mod.host_available = lambda: (host, "가짜 호스트 측정")
        if scenario.startswith("check"):
            sys.argv = ["memory_guard.py", "check", stage, "img", str(tsv)]
        elif scenario == "watch_result_file":
            sys.argv = ["memory_guard.py", "watch", "build-backend", "img", str(tsv), "--result", str(result), "--", "fake-build", "backend"]
        else:
            sys.argv = ["memory_guard.py", "watch", "build-backend", "img", str(tsv), "--", "fake-build", "backend"]
        sys.stdout = buf
        try:
            rc = mod.main()
        except BaseException as e:  # noqa: BLE001 — KeyboardInterrupt도 판정에 기록
            rc = f"예외 {type(e).__name__}"
    finally:
        subprocess.run, sys.argv, sys.stdout, mod.host_available = saved
    buf.flush()
    text = buf.buffer.getvalue().decode("utf-8", "replace")
    res_lines = [x for x in text.splitlines() if x.startswith("자원")]
    line = next((x for x in res_lines if " 동안: " in x), res_lines[0] if res_lines else "")
    verdict = next((x for x in res_lines if " 판정: " in x), "")
    leak = sorted(set(fake.started) - set(fake.removed))  # 띄우고 지우지 않은 감시 컨테이너 — 있으면 기대와 다름
    passed = rc == 0 and (scenario != "watch_vm_clock_skew" or "vm 시작" in line)  # 시계가 어긋나도 VM 표본을 남겨야 함
    out = {"rc": rc, "passed": passed, "summary": (verdict or line)[:200], "leak": [] if scenario in MEM_LEAK_OK else leak}
    if scenario in MEM_RECORD:  # 정리 실패가 명령 결과를 덮지 않고 둘 다 기록에 남는지 (.tsv summary, --result 파일)
        want_cmd, want_state = MEM_RECORD[scenario]
        rows = [x.split("\t") for x in tsv.read_text(encoding="utf-8").splitlines()] if tsv.exists() else []
        rec = next((json.loads(r[3]) for r in reversed(rows) if len(r) > 3 and r[2] == "summary"), {})
        c = rec.get("cleanup") if isinstance(rec.get("cleanup"), dict) else {}
        problems = []
        if rec.get("command_exit") != want_cmd or c.get("state") != want_state:
            problems.append(f"기록 command_exit {rec.get('command_exit')!r}·cleanup {c.get('state')!r} (기대 {want_cmd}·{want_state})")
        if want_state == "left" and not (c.get("left") and all(str(n).startswith("moajwo-memwatch-") for n in c["left"])):
            problems.append("남은 감시 컨테이너 이름이 기록에 없음")
        if scenario == "watch_result_file" and (not result.exists() or json.loads(result.read_text(encoding="utf-8")) != rec):
            problems.append("--result 파일이 없거나 .tsv 기록과 다름")
        if problems:
            out["evidence_ok"] = False
            out["summary"] = ("; ".join(problems) + " | " + out["summary"])[:240]
    return out


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts", default=str(HERE))
    ap.add_argument("--json")
    ns = ap.parse_args()
    scripts = Path(ns.scripts).resolve()
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, want, what in SMOKE_CASES:
            r = run_smoke(scripts, name, tmp)
            rows.append({"tool": "image_smoke", "case": name, "what": what, "want_pass": want, "want_rc": SMOKE_RC.get(name), **r})
        for name, want, what, a, b in compare_cases(REPO):
            r = run_compare(scripts, a, b, tmp, name)
            rows.append({"tool": "image_inventory compare", "case": name, "what": what, "want_pass": want, "want_rc": COMPARE_RC.get(name), **r})
        with contextlib.suppress(FileNotFoundError):
            for name, want, what, env_extra in GATE_CASES:
                r = run_gate(scripts, name, env_extra, tmp)
                rows.append({"tool": "image_gate", "case": name, "what": what, "want_pass": want, **r})
        try:
            for name, want, what in MEM_CASES:
                r = run_mem(scripts, name, tmp)
                rows.append({"tool": "memory_guard", "case": name, "what": what, "want_pass": want, "want_rc": MEM_RC.get(name), **r})
        except FileNotFoundError:
            print("건너뜀 | memory_guard.py 없음 (이 판에는 자원 확인 도구가 없음)")
    bad = bad_pass = bad_rc = na = 0
    for r in rows:
        if r.get("na"):  # 이 판에는 해당하지 않는 사례 — 세지 않고 표시만
            na += 1
            print(f"해당 없음 | {r['tool']} | {r['case']} | {r['na']}")
            continue
        pass_ok = r["passed"] == r["want_pass"] and not r.get("leak")  # 점검·감시 자원을 남기면 종료 코드와 상관없이 기대와 다름
        rc_ok = (r.get("want_rc") is None or r["rc"] == r["want_rc"]) and r.get("evidence_ok", True)  # 근거(사유·기록)를 잃은 것도 '구분'
        ok = pass_ok and rc_ok
        bad += not ok
        bad_pass += not pass_ok
        bad_rc += pass_ok and not rc_ok
        want = f"기대 {'통과' if r['want_pass'] else '막힘'}" + (f"·종료 {r['want_rc']}" if r.get("want_rc") is not None else "")
        leak = f" · 남은 자원 {r['leak']}" if r.get("leak") else ""
        print(f"{'맞음' if ok else '틀림'} | {r['tool']} | {r['case']} | {want} | 실제 {'통과' if r['passed'] else '막힘'} (종료 {r['rc']}){leak} | {r.get('summary', '')}")
    print(f"요약 | {scripts} | 사례 {len(rows) - na} · 기대와 다름 {bad} (통과·막힘·자원 남김 {bad_pass} · 막혔지만 종료 코드·근거 구분만 {bad_rc})"
          + (f" · 해당 없음 {na}" if na else ""))  # fmt: skip
    if ns.json:
        Path(ns.json).write_text(json.dumps({"scripts": str(scripts), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
