"""완성 이미지 목록 — 이미지 안의 OS·OS 패키지·Python·의존성·CA 묶음·psycopg 내장 라이브러리를 직접 읽어 JSON으로 남기고,
두 목록의 '실행 환경' 항목을 비교한다. 빌드한 이미지가 검증한 이미지와 같은 환경인지 반영 전에 확인하는 데 쓴다.

사용법 (scripts/image_gate.sh가 반영 전에 부른다 — docs/DEPLOY.md 11장):
  python scripts/image_inventory.py collect backend|frontend <이미지 ID·이름> <결과.json>
  python scripts/image_inventory.py compare <기대.json> <실제.json>
종료 코드: 0 같음 · 4 실행 환경이 다름 · 3 입력 오류(도구 판·종류가 다르거나, 종류별 필수 항목이 없거나 자료형이 틀림 — 비교하지 않음.
또는 같은 이미지 ID인데 실행 환경 항목이 다름 — 두 목록 중 하나는 그 이미지에서 모은 것이 아니라는 모순) · 2 사용법 · 1 예상치 못한
중단(처리하지 못한 예외 — 판정 아님). collect도 모은 목록이 필수 항목을 갖추지 못하면 종료 3이다.
이미지 ID가 같다는 사실만으로는 통과도 거절도 하지 않는다 — ID가 같아도 검사·비교는 그대로 한다. 검증 목록의 출처는 ID가 아니라 승인한
트리에 커밋된 고정 경로(image_gate.sh·deploy.sh가 확인)와 CI 출처 기록(verified)으로 본다. ID의 뜻은 Docker 저장 방식마다 다르다
(containerd 저장소는 매니페스트 digest, 기존 저장소는 설정 digest — image.id_meaning에 기록).

이미지는 일회용 컨테이너(--rm)로 네트워크 없이(--network none), 볼륨 없이, 읽기 전용 루트로 읽기만 한다.
운영 컨테이너·볼륨은 건드리지 않는다. 앱 코드·정적 파일의 해시(app_tree·html_tree)는 소스마다 다르므로 기록만 하고 비교하지 않는다.
"""

import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time

os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"  # Windows: docker를 현재 폴더(저장소)에서 먼저 찾지 않게 — 단독 실행에도
TOOL = "image_inventory 1"
# 비교하는 '실행 환경' 항목 — 기반 이미지·빌드 중 설치한 OS 패키지·Python 의존성·CA 묶음이 바뀌면 여기서 드러난다
ENV_KEYS = ("platform", "os_release", "machine", "os_packages", "python", "python_dists", "ca_bundle", "psycopg", "nginx")
# 종류별로 반드시 있어야 하는 실행 환경 항목 — 나머지 ENV_KEYS는 그 종류에 있으면 안 된다 (다른 종류의 목록이 섞이지 않게)
REQUIRED = {
    "backend": ("platform", "os_release", "machine", "os_packages", "python", "python_dists", "ca_bundle", "psycopg"),
    "frontend": ("platform", "os_release", "machine", "os_packages", "ca_bundle", "nginx"),
}
CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")  # 같은 이미지인지 문자열로 비교하므로 표기를 하나로 (대문자·공백 거절)
LIMITS = ("--memory", "256m", "--memory-swap", "256m", "--cpus", "1")  # 목록을 읽는 일회용 컨테이너의 상한 (운영과 같은 PC)

BACKEND_PY = r"""
import hashlib, importlib.metadata as md, json, os, platform, pyexpat, re, sqlite3, ssl, subprocess, sysconfig
o = {}
rel = {}
for line in open("/etc/os-release", encoding="utf-8"):
    k, _, v = line.strip().partition("=")
    if k:
        rel[k] = v.strip('"')
o["os_release"] = {"id": rel.get("ID"), "version_id": rel.get("VERSION_ID"), "codename": rel.get("VERSION_CODENAME"),
                   "detail": open("/etc/debian_version", encoding="utf-8").read().strip()}
o["machine"] = platform.machine()
fmt = "${Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n"
pk = {}
for line in subprocess.run(["dpkg-query", "-W", "-f=" + fmt], capture_output=True, text=True, check=True).stdout.splitlines():
    n, v, a, st = line.split("\t")
    if st.startswith("ii"):
        pk[n if n not in pk else n + ":" + a] = v
o["os_packages"] = dict(sorted(pk.items()))
o["python"] = {"version": platform.python_version(), "openssl": ssl.OPENSSL_VERSION,
               "default_cafile": ssl.get_default_verify_paths().openssl_cafile,
               "expat": pyexpat.EXPAT_VERSION, "sqlite": sqlite3.sqlite_version}  # xlsx XML 해석기 · 운영 DB 엔진
d = {}
for dist in md.distributions():
    d[re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower()] = dist.version
o["python_dists"] = dict(sorted(d.items()))
try:
    import psycopg
    from psycopg import pq
    libs = {}
    libdir = os.path.join(sysconfig.get_paths()["purelib"], "psycopg_binary.libs")
    if os.path.isdir(libdir):
        for f in sorted(os.listdir(libdir)):
            b = open(os.path.join(libdir, f), "rb").read()
            m = re.search(rb"OpenSSL \d+\.\d+\.\d+[a-z]?(?: +\d+ \w+ \d{4})?", b)
            libs[f] = {"sha256": hashlib.sha256(b).hexdigest(), "openssl_string": m.group(0).decode() if m else None}
    o["psycopg"] = {"version": psycopg.__version__, "impl": pq.__impl__, "libpq_version": pq.version(), "bundled_libs": libs}
except Exception as e:
    o["psycopg"] = {"error": type(e).__name__ + ": " + str(e)[:200]}
h, n = hashlib.sha256(), 0
for root, dirs, files in os.walk("/app"):
    dirs[:] = sorted(x for x in dirs if os.path.join(root, x) != "/app/data")
    for f in sorted(files):
        p = os.path.join(root, f)
        h.update(p.encode() + b"\0" + hashlib.sha256(open(p, "rb").read()).digest())
        n += 1
o["app_tree"] = {"files": n, "sha256": h.hexdigest()}
print(json.dumps(o))
"""

FRONTEND_SH = r"""
echo "@@release"; cat /etc/alpine-release
echo "@@osrel"; cat /etc/os-release
echo "@@machine"; uname -m
echo "@@apk"; grep -E '^(P|V):' /lib/apk/db/installed
echo "@@nginx"; nginx -v 2>&1
echo "@@html"; cd /usr/share/nginx/html && find . -type f -exec sha256sum {} + | sort -k2
echo "@@end"
"""


def docker(*args: str, stdin: bytes | None = None) -> bytes:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")  # Windows Git Bash가 /etc/... 인자를 경로로 바꾸지 않게
    r = subprocess.run(["docker", *args], input=stdin, capture_output=True, env=env, timeout=600, check=False)
    if r.returncode != 0:
        raise SystemExit(f"docker {' '.join(args[:3])} 실패 (코드 {r.returncode}): {r.stderr.decode('utf-8', 'replace')[:500]}")
    return r.stdout


def run_in(image: str, entry: str, args: list[str], script: str) -> str:
    # 일회용 컨테이너: 네트워크 없음, 볼륨 없음, 읽기 전용 루트
    return docker("run", "--rm", "-i", "--network", "none", "--read-only", *LIMITS, "--entrypoint", entry, image, *args,
                  stdin=script.encode()).decode("utf-8", "replace")  # fmt: skip


def ca_info(pem: bytes) -> dict:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=pem.decode("ascii", "replace"))
    names = []
    for c in ctx.get_ca_certs():
        subj = [kv for rdn in c.get("subject", ()) for kv in rdn]
        names.append(next((v for k, v in subj if k == "commonName"), None) or next((v for k, v in subj if k == "organizationalUnitName"), "?"))
    return {"sha256": hashlib.sha256(pem).hexdigest(), "pem_blocks": pem.count(b"-----BEGIN CERTIFICATE-----"), "loaded": len(names),
            "emsign_emudhra": sorted(n for n in names if re.search("emsign|emudhra", n, re.I))}  # fmt: skip


def image_meta(image: str) -> tuple[dict, dict]:
    d = json.loads(docker("image", "inspect", image))[0]
    store = json.loads(docker("info", "--format", "{{json .DriverStatus}}") or b"null")
    containerd = bool(store) and ["driver-type", "io.containerd.snapshotter.v1"] in store
    meta = {"ref": image, "id": d["Id"], "id_meaning": "manifest digest (containerd 저장소)" if containerd else "config digest (기존 저장소)",
            "created": d.get("Created"), "repo_tags": d.get("RepoTags"), "repo_digests": d.get("RepoDigests"),
            "diff_ids": (d.get("RootFS") or {}).get("Layers", []), "docker_store": store}  # fmt: skip
    return meta, {"os": d.get("Os"), "arch": d.get("Architecture"), "variant": d.get("Variant")}


def collect(kind: str, image: str) -> dict:
    meta, plat = image_meta(image)
    inv = {"tool": TOOL, "kind": kind, "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "image": meta, "platform": plat}
    if kind == "backend":
        inv.update(json.loads(run_in(image, "python", ["-I", "-"], BACKEND_PY)))
    else:
        sec, cur = {}, None
        for line in run_in(image, "sh", ["-s"], FRONTEND_SH).splitlines():
            if line.startswith("@@"):
                cur = line[2:]
                sec[cur] = []
            elif cur:
                sec[cur].append(line)
        if "end" not in sec:
            raise SystemExit("frontend 목록 출력이 끝까지 오지 않음 (@@end 없음)")
        rel = {k: v.strip('"') for k, v in (x.split("=", 1) for x in sec["osrel"] if "=" in x)}
        inv["os_release"] = {"id": rel.get("ID"), "version_id": rel.get("VERSION_ID"), "detail": sec["release"][0].strip()}
        inv["machine"] = sec["machine"][0].strip()
        pk, name = {}, None
        for x in sec["apk"]:
            if x.startswith("P:"):
                name = x[2:]
            elif x.startswith("V:") and name:
                pk[name] = x[2:]
        inv["os_packages"] = dict(sorted(pk.items()))
        inv["nginx"] = " ".join(sec["nginx"]).strip()
        inv["html_tree"] = {"files": len(sec["html"]), "sha256": hashlib.sha256("\n".join(sec["html"]).encode()).hexdigest()}
    pem = docker("run", "--rm", "--network", "none", "--read-only", *LIMITS, "--entrypoint", "cat", image, CA_PATH)
    inv["ca_bundle"] = {"path": CA_PATH, **ca_info(pem)}
    return inv


def _no_dup_keys(pairs: list) -> dict:
    """JSON 객체의 중복 키를 거부한다 — 기본 json은 마지막 값만 남겨 다른 값이 숨을 수 있다."""
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"중복 키 {k!r}")
        d[k] = v
    return d


def _str(v: object) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _str_map(v: object) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(_str(k) and _str(x) for k, x in v.items())


def validate(inv: object, label: str, expected: bool = False) -> list[str]:
    """비교 전 입력 검사 — 도구 판, 종류, 종류별 필수 항목과 자료형. expected=True(검증 목록)이면 CI 출처 기록(verified의
    ci_run·commit)도 있어야 한다. 문제 목록을 돌려준다 (비어 있으면 통과)."""
    if not isinstance(inv, dict):
        return [f"{label}: JSON 객체가 아님 ({type(inv).__name__})"]
    errs: list[str] = []
    if inv.get("tool") != TOOL:
        errs.append(f"{label}: 도구 판이 다름 ({inv.get('tool')!r}, 이 도구는 {TOOL!r})")
    kind = inv.get("kind")
    if not isinstance(kind, str) or kind not in REQUIRED:
        return errs + [f"{label}: 종류가 backend·frontend가 아님 ({kind!r})"]
    iid = inv["image"].get("id") if isinstance(inv.get("image"), dict) else None
    if not (isinstance(iid, str) and IMAGE_ID.fullmatch(iid)):
        errs.append(f"{label}: image.id가 sha256:<소문자 16진수 64자리>가 아님 ({iid!r})")
    if expected:
        v = inv.get("verified")
        if not (isinstance(v, dict) and _str(v.get("ci_run")) and isinstance(v.get("commit"), str) and HEX40.fullmatch(v["commit"])):
            errs.append(f"{label}: 검증 목록의 출처 기록(verified.ci_run·commit)이 없음 — CI에서 검증한 이미지에서 만든 목록만 쓴다")
    errs += [f"{label}: 필수 항목 없음 — {k}" for k in REQUIRED[kind] if k not in inv]
    errs += [f"{label}: {kind}에 없어야 할 항목 — {k}" for k in ENV_KEYS if k in inv and k not in REQUIRED[kind]]

    def need(cond: bool, what: str) -> None:
        if not cond:
            errs.append(f"{label}: 형식 오류 — {what}")

    p, r, ca = inv.get("platform"), inv.get("os_release"), inv.get("ca_bundle")
    if "platform" in inv:
        need(isinstance(p, dict) and _str(p.get("os")) and _str(p.get("arch")) and (p.get("variant") is None or _str(p.get("variant"))),
             "platform.os·arch·variant")  # fmt: skip
    if "os_release" in inv:
        keys = ("id", "version_id", "detail") + (("codename",) if kind == "backend" else ())
        need(isinstance(r, dict) and all(_str(r.get(k)) for k in keys), "os_release." + "·".join(keys))
    if "machine" in inv:
        need(_str(inv["machine"]), "machine")
    if "os_packages" in inv:
        need(_str_map(inv["os_packages"]), "os_packages (이름→판, 비어 있지 않음)")
    if "ca_bundle" in inv:
        need(isinstance(ca, dict) and _str(ca.get("path")) and isinstance(ca.get("sha256"), str) and HEX64.fullmatch(ca["sha256"]) is not None
             and type(ca.get("pem_blocks")) is int and ca["pem_blocks"] > 0 and type(ca.get("loaded")) is int and ca["loaded"] > 0
             and isinstance(ca.get("emsign_emudhra"), list) and all(_str(x) for x in ca["emsign_emudhra"]),
             "ca_bundle.path·sha256·pem_blocks·loaded·emsign_emudhra")  # fmt: skip
    if kind == "backend":
        py, ps = inv.get("python"), inv.get("psycopg")
        if "python" in inv:
            need(isinstance(py, dict) and all(_str(py.get(k)) for k in ("version", "openssl", "default_cafile", "expat", "sqlite")),
                 "python.version·openssl·default_cafile·expat·sqlite")  # fmt: skip
        if "python_dists" in inv:
            need(_str_map(inv["python_dists"]), "python_dists (이름→판, 비어 있지 않음)")
        if "psycopg" in inv:
            libs = ps.get("bundled_libs") if isinstance(ps, dict) else None
            need(isinstance(ps, dict) and "error" not in ps and _str(ps.get("version")) and _str(ps.get("impl"))
                 and type(ps.get("libpq_version")) is int and ps["libpq_version"] > 0 and isinstance(libs, dict) and len(libs) > 0
                 and all(_str(k) and isinstance(v, dict) and isinstance(v.get("sha256"), str) and HEX64.fullmatch(v["sha256"])
                         and (v.get("openssl_string") is None or _str(v.get("openssl_string"))) for k, v in libs.items()),
                 "psycopg.version·impl·libpq_version·bundled_libs")  # fmt: skip
    elif "nginx" in inv:
        need(_str(inv["nginx"]) and "nginx/" in inv["nginx"], "nginx 판")
    return errs


def compare(a: dict, b: dict) -> list[str]:
    diffs = []
    for k in ENV_KEYS:
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            for kk in sorted(set(va) | set(vb)):
                if va.get(kk) != vb.get(kk):
                    diffs.append(f"{k}.{kk}: {json.dumps(va.get(kk), ensure_ascii=False)} → {json.dumps(vb.get(kk), ensure_ascii=False)}")
        elif va != vb:
            diffs.append(f"{k}: {json.dumps(va, ensure_ascii=False)} → {json.dumps(vb, ensure_ascii=False)}")
    return diffs


def main() -> int:
    sys.stdout.reconfigure(errors="replace")  # Windows 콘솔(cp949)에서 못 쓰는 글자로 멈추지 않게
    if len(sys.argv) == 5 and sys.argv[1] == "collect" and sys.argv[2] in ("backend", "frontend"):
        kind = sys.argv[2]
        if os.path.exists(sys.argv[4]):  # 앞 실행의 목록이 이번 결과로 읽히지 않게
            os.remove(sys.argv[4])
        inv = collect(kind, sys.argv[3])
        errs = validate(inv, "모은 목록")
        out = sys.argv[4] if not errs else sys.argv[4] + ".invalid.json"  # 불완전한 목록은 정식 이름으로 남기지 않는다 (진단용으로만)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(inv, f, ensure_ascii=False, indent=1)
        if errs:
            for e in errs:
                print("입력 오류 |", e)
            print(f"요약 | {kind} 목록을 다 모으지 못함 — 입력 오류 {len(errs)}건 ({out})")
            return 3
        extra = f"Python {inv['python']['version']} · 의존성 {len(inv['python_dists'])}" if kind == "backend" else inv["nginx"]
        print(f"{kind} {inv['image']['id']} · {inv['platform']} · {inv['os_release']} · OS 패키지 {len(inv['os_packages'])} · {extra}"
              f" · CA {inv['ca_bundle']['loaded']}개 {inv['ca_bundle']['sha256'][:16]}")  # fmt: skip
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        docs = []
        for label, path in (("기대", sys.argv[2]), ("실제", sys.argv[3])):
            try:
                with open(path, encoding="utf-8") as f:
                    docs.append(json.load(f, object_pairs_hook=_no_dup_keys))
            except (OSError, ValueError) as e:
                print(f"입력 오류 | {label}: 읽을 수 없음 — {type(e).__name__}: {e}")
                print("요약 | 비교하지 않음 — 입력 오류")
                return 3
        a, b = docs
        errs = validate(a, "기대", expected=True) + validate(b, "실제")
        if not errs and a["kind"] != b["kind"]:
            errs.append(f"종류가 다름: 기대 {a['kind']} / 실제 {b['kind']}")
        if errs:
            for e in errs:
                print("입력 오류 |", e)
            print(f"요약 | 비교하지 않음 — 입력 오류 {len(errs)}건 (필수 항목이 없거나 형식이 틀린 목록은 같다고 보지 않는다)")
            return 3
        diffs = compare(a, b)
        for x in diffs:
            print("다름 |", x)
        if a["image"]["id"] == b["image"]["id"]:  # 같은 이미지라면 실행 환경이 다를 수 없다 — 다르면 목록 하나가 그 이미지의 것이 아님
            if diffs:
                print(f"입력 오류 | 같은 이미지 ID({a['image']['id']})인데 실행 환경 항목이 다름 — 두 목록 중 하나는 그 이미지에서 모은 것이 아님")
                print(f"요약 | 비교 결과를 받지 않음 — 같은 이미지 ID인데 다름 {len(diffs)}건 (모순, 입력 오류)")
                return 3
            print(f"참고 | 기대 목록과 후보가 같은 이미지 ID({a['image']['id']}) — ID로 출처를 판단하지 않고 항목을 그대로 비교함")
        print(f"요약 | {a.get('kind')} 실행 환경 항목 {'같음' if not diffs else f'다름 {len(diffs)}건'} | 기대 {a['image']['id']} | 실제 {b['image']['id']}")
        return 0 if not diffs else 4
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
