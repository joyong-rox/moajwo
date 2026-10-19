"""운영 health 응답 시간 비교 (소수의 직렬 요청) — localhost와 127.0.0.1, 단계별 시간과 프록시·실제 연결 주소.

monitor.py가 쓴 방식(urllib, http://localhost:8080)을 그대로 재현하고, 이름 해석·TCP 연결·첫 응답·전체 시간을 나눠 잰다.
요청 수: 주소마다 urllib 3회 + 단계별 측정 3회, 그리고 이름 해석 결과 주소마다 TCP 연결 1회 (본문 없음).
프록시 환경변수는 설정 여부만 기록한다 (값에 인증 정보가 있을 수 있음). 사용법: python health_timing.py <출력 파일(json)>
"""

import json
import os
import socket
import sys
import time
import urllib.request

PATH = "/api/health"
PORT = 8080
OUT = sys.argv[1]
res: dict = {"proxy_env_set": {k: bool(os.environ.get(k)) for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "NO_PROXY", "no_proxy")},
             "urllib_getproxies_keys": sorted(urllib.request.getproxies().keys())}  # fmt: skip

t = time.perf_counter()
infos = socket.getaddrinfo("localhost", PORT, type=socket.SOCK_STREAM)
res["getaddrinfo_localhost"] = {"seconds": round(time.perf_counter() - t, 4), "order": [f"{i[0].name} {i[4][0]}" for i in infos]}

res["tcp_connect"] = []
for fam, _, _, _, addr in infos:
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.settimeout(5)
    t = time.perf_counter()
    try:
        s.connect(addr)
        out = "연결됨"
    except OSError as e:
        out = f"{type(e).__name__} {getattr(e, 'winerror', '') or e.errno}"
    res["tcp_connect"].append({"addr": addr[0], "result": out, "seconds": round(time.perf_counter() - t, 4)})
    s.close()


def staged(host: str) -> dict:
    """이름 해석 → 연결 → 요청 → 첫 바이트 → 끝 (urllib과 같은 순서로 주소를 차례로 시도)."""
    t0 = time.perf_counter()
    cand = socket.getaddrinfo(host, PORT, type=socket.SOCK_STREAM)
    t1 = time.perf_counter()
    tried = []
    sock = None
    for fam, _, _, _, addr in cand:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(5)
        a = time.perf_counter()
        try:
            s.connect(addr)
            tried.append({"addr": addr[0], "ok": True, "seconds": round(time.perf_counter() - a, 4)})
            sock = s
            break
        except OSError as e:
            tried.append({"addr": addr[0], "ok": False, "error": type(e).__name__, "seconds": round(time.perf_counter() - a, 4)})
            s.close()
    t2 = time.perf_counter()
    sock.sendall(f"GET {PATH} HTTP/1.1\r\nHost: {host}:{PORT}\r\nConnection: close\r\n\r\n".encode())
    first = sock.recv(1)
    t3 = time.perf_counter()
    body = first
    while chunk := sock.recv(65536):
        body += chunk
    t4 = time.perf_counter()
    sock.close()
    return {"resolve": round(t1 - t0, 4), "connect": round(t2 - t1, 4), "first_byte": round(t3 - t2, 4), "total": round(t4 - t0, 4),
            "tried": tried, "status_line": body.split(b"\r\n", 1)[0].decode(errors="replace")}  # fmt: skip


for host in ("localhost", "127.0.0.1"):
    rows = []
    for _ in range(3):
        a = time.perf_counter()
        with urllib.request.urlopen(f"http://{host}:{PORT}{PATH}", timeout=10) as r:
            r.read()
            code = r.status
        rows.append({"status": code, "seconds": round(time.perf_counter() - a, 4)})
    res[f"urllib_{host}"] = rows
    res[f"staged_{host}"] = [staged(host) for _ in range(3)]

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))
