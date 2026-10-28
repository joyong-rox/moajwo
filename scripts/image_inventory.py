"""완성 이미지 목록 — 이미지 안의 OS·OS 패키지·Python·의존성·CA 묶음·psycopg 내장 라이브러리를 직접 읽어 JSON으로 남기고,
두 목록의 '실행 환경' 항목을 비교한다. 빌드한 이미지가 검증한 이미지와 같은 환경인지 반영 전에 확인하는 데 쓴다.

사용법 (scripts/image_gate.sh가 반영 전에 부른다 — docs/DEPLOY.md 11장):
  python scripts/image_inventory.py collect backend|frontend <이미지 ID·이름> <결과.json>
  python scripts/image_inventory.py compare <기대.json> <실제.json>   # 실행 환경 항목이 다르면 종료 코드 1

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

TOOL = "image_inventory 1"
# 비교하는 '실행 환경' 항목 — 기반 이미지·빌드 중 설치한 OS 패키지·Python 의존성·CA 묶음이 바뀌면 여기서 드러난다
ENV_KEYS = ("platform", "os_release", "machine", "os_packages", "python", "python_dists", "ca_bundle", "psycopg", "nginx")
CA_PATH = "/etc/ssl/certs/ca-certificates.crt"

BACKEND_PY = r"""
import hashlib, importlib.metadata as md, json, os, platform, re, ssl, subprocess, sysconfig
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
               "default_cafile": ssl.get_default_verify_paths().openssl_cafile}
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
    return docker("run", "--rm", "-i", "--network", "none", "--read-only", "--entrypoint", entry, image, *args,
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
    pem = docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "cat", image, CA_PATH)
    inv["ca_bundle"] = {"path": CA_PATH, **ca_info(pem)}
    return inv


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
        inv = collect(kind, sys.argv[3])
        with open(sys.argv[4], "w", encoding="utf-8") as f:
            json.dump(inv, f, ensure_ascii=False, indent=1)
        extra = f"Python {inv['python']['version']} · 의존성 {len(inv['python_dists'])}" if kind == "backend" else inv["nginx"]
        print(f"{kind} {inv['image']['id']} · {inv['platform']} · {inv['os_release']} · OS 패키지 {len(inv['os_packages'])} · {extra}"
              f" · CA {inv['ca_bundle']['loaded']}개 {inv['ca_bundle']['sha256'][:16]}")  # fmt: skip
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        with open(sys.argv[2], encoding="utf-8") as f:
            a = json.load(f)
        with open(sys.argv[3], encoding="utf-8") as f:
            b = json.load(f)
        if a.get("kind") != b.get("kind"):
            print(f"종류가 다름: {a.get('kind')} / {b.get('kind')}")
            return 2
        diffs = compare(a, b)
        for x in diffs:
            print("다름 |", x)
        print(f"요약 | {a.get('kind')} 실행 환경 항목 {'같음' if not diffs else f'다름 {len(diffs)}건'} | 기대 {a['image']['id']} | 실제 {b['image']['id']}")
        return 0 if not diffs else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
