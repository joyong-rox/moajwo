"""PostgreSQL TLS 매트릭스 (pg_tls_check.sh가 띄운 일회용 서버): sslmode·sslrootcert·channel_binding 조합마다 psycopg로 붙어
결과(연결됨/거절)·TLS 판·암호 모음·오류 종류를 기록하고 기대와 대조한다. 기대와 다르면 종료 1.
출력: <out>/pg-tls-matrix.json · pg-tls-matrix.md (GITHUB_STEP_SUMMARY에도) — 주소·비밀번호·인증서 경로 없음.
환경: PGTLS_PORT, PGTLS_CA(우리 CA 파일), PGTLS_OTHER(다른 CA 파일), PGTLS_PASSWORD(일회용)."""

import json
import os
import sys
from pathlib import Path

import psycopg

OUT = Path(sys.argv[1])
PORT = int(os.environ["PGTLS_PORT"])
CA, OTHER, PW = os.environ["PGTLS_CA"], os.environ["PGTLS_OTHER"], os.environ["PGTLS_PASSWORD"]

# (이름, 연결 옵션, 기대, 뜻) — host 기본은 localhost(인증서 이름과 같음)
CASES = [
    ("sslmode=require (root 파일 없음)", {"sslmode": "require"}, "ok",
     "암호화는 되지만 서버 신원 확인이 없다 — 가로채기 서버의 인증서도 통과 (libpq 표: 도청 보호 예 · 중간자 보호 아니오)"),
    ("sslmode=require + 다른 CA 파일", {"sslmode": "require", "sslrootcert": OTHER}, "fail",
     "root 파일이 있으면 require는 verify-ca처럼 검증한다(호환 동작, 의존하지 말 것 — libpq 문서)"),
    ("sslmode=verify-ca + 우리 CA", {"sslmode": "verify-ca", "sslrootcert": CA}, "ok", "CA 서명 확인"),
    ("sslmode=verify-ca + 다른 CA", {"sslmode": "verify-ca", "sslrootcert": OTHER}, "fail", "서명한 CA가 다름"),
    ("sslmode=verify-ca + 우리 CA, host=127.0.0.1", {"sslmode": "verify-ca", "sslrootcert": CA, "host": "127.0.0.1"}, "ok",
     "verify-ca는 호스트 이름을 보지 않는다 — 같은 CA가 서명한 다른 서버 이름도 통과"),
    ("sslmode=verify-full + 우리 CA, host=localhost", {"sslmode": "verify-full", "sslrootcert": CA}, "ok", "CA 서명 + 이름 일치"),
    ("sslmode=verify-full + 우리 CA, host=127.0.0.1", {"sslmode": "verify-full", "sslrootcert": CA, "host": "127.0.0.1"}, "fail",
     "인증서 이름(localhost)과 접속 이름이 다르면 거절 — verify-full만 막는 경우"),
    ("sslmode=verify-full + sslrootcert=system", {"sslmode": "verify-full", "sslrootcert": "system"}, "fail",
     "OS 신뢰 저장소에 없는 사설 CA는 거절. 공인 CA(예: Neon의 ISRG Root X1)는 OS 저장소에 있어 통과하는 경우 — 여기서는 미검증"),
    ("sslmode=require + sslrootcert=system", {"sslmode": "require", "sslrootcert": "system"}, "fail",
     "sslrootcert=system이면 verify-full보다 약한 sslmode는 오류 (libpq 문서)"),
    ("sslmode=disable", {"sslmode": "disable"}, "fail", "서버가 비암호 연결을 거절(hostnossl reject)"),
    ("sslmode=verify-full + 우리 CA + channel_binding=require", {"sslmode": "verify-full", "sslrootcert": CA, "channel_binding": "require"},
     "ok", "SCRAM-SHA-256-PLUS 채널 결합 — 서버 인증을 한 겹 더"),
]


def attempt(params: dict) -> dict:
    kw = {"host": "localhost", "port": PORT, "user": "postgres", "password": PW, "dbname": "postgres", "connect_timeout": 10,
          "application_name": "moajwo-ci-tls", **params}  # fmt: skip
    try:
        with psycopg.connect(**kw) as con:
            ssl, version, cipher = con.execute(
                "SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
            ).fetchone()
            return {"result": "ok", "ssl": bool(ssl), "tls": version, "cipher": cipher}
    except Exception as exc:  # noqa: BLE001 — 종류와 짧은 문구만 (경로·비밀값 없음)
        text = str(exc).replace(CA, "<ca>").replace(OTHER, "<other-ca>").replace(PW, "<pw>")
        return {"result": "fail", "error": f"{type(exc).__name__}: {text.strip()[:200]}"}


rows = []
bad = 0
for name, params, expected, meaning in CASES:
    got = attempt(params)
    met = got["result"] == expected
    bad += not met
    rows.append({"case": name, "expected": expected, **got, "expected_met": met, "meaning": meaning})
    print(f"{'기대 충족' if met else '기대 어긋남'} | {name} | {got}")

libpq = psycopg.pq.version()
info = {"psycopg": psycopg.__version__, "libpq": libpq, "libpq_impl": psycopg.pq.__impl__, "cases": rows, "mismatches": bad}
(OUT / "pg-tls-matrix.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
md = [f"### PostgreSQL TLS 매트릭스 (psycopg {psycopg.__version__}, libpq {libpq} {psycopg.pq.__impl__}) — 기대 어긋남 {bad}건", "",
      "| 경우 | 기대 | 결과 | TLS/암호 모음 또는 오류 | 뜻 |", "|---|---|---|---|---|"]  # fmt: skip
for r in rows:
    detail = f"{r.get('tls')} {r.get('cipher')}" if r["result"] == "ok" else r.get("error", "")
    md.append(f"| {r['case']} | {r['expected']} | {r['result']}{'' if r['expected_met'] else ' ❌'} | {detail} | {r['meaning']} |")
(OUT / "pg-tls-matrix.md").write_text("\n".join(md) + "\n", encoding="utf-8")
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
sys.exit(1 if bad else 0)
