"""PostgreSQL TLS 매트릭스 (pg_tls_check.sh가 띄운 일회용 서버): sslmode·sslrootcert·channel_binding 조합마다 psycopg로 붙어
결과를 pg_tls_judge로 판정한다 — 정상 연결 / 예상한 TLS 거절(원인 일치) / 원인 불일치·거절돼야 하는데 연결됨(검증 실패) /
예상 밖 실패·측정 오류(측정 불완전). 한 건이라도 충족이 아니면 종료 1.
출력: <out>/pg-tls-matrix-<phase>.json · .md (GITHUB_STEP_SUMMARY에도) — 주소·비밀번호·인증서 경로 없음.
환경: PGTLS_PORT, PGTLS_CA(우리 CA 파일), PGTLS_OTHER(다른 CA 파일), PGTLS_PASSWORD(일회용), PGTLS_PHASE(valid|expired —
서버 인증서가 유효한 판 / 만료된 판)."""

from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pg_tls_judge import VERIFY_FAILURE, Outcome, Verdict, judge, outcome_from_exception, summarize  # noqa: E402

OUT = Path(sys.argv[1])
PORT = int(os.environ["PGTLS_PORT"])
CA, OTHER, PW = os.environ["PGTLS_CA"], os.environ["PGTLS_OTHER"], os.environ["PGTLS_PASSWORD"]
PHASE = os.environ.get("PGTLS_PHASE", "valid")

# (이름, 연결 옵션, 기대('ok' 또는 거절 원인), 뜻) — host 기본은 localhost(인증서 이름과 같음)
VALID_CASES = [
    ("sslmode=require (root 파일 없음)", {"sslmode": "require"}, "ok",
     "암호화는 되지만 서버 신원 확인이 없다 — 가로채기 서버의 인증서도 통과 (libpq 표: 도청 보호 예 · 중간자 보호 아니오)"),
    ("sslmode=require + 다른 CA 파일", {"sslmode": "require", "sslrootcert": OTHER}, "untrusted_ca",
     "root 파일이 있으면 require는 verify-ca처럼 검증한다(호환 동작, 의존하지 말 것 — libpq 문서)"),
    ("sslmode=verify-ca + 우리 CA", {"sslmode": "verify-ca", "sslrootcert": CA}, "ok", "CA 서명 확인"),
    ("sslmode=verify-ca + 다른 CA", {"sslmode": "verify-ca", "sslrootcert": OTHER}, "untrusted_ca", "서명한 CA가 다름"),
    ("sslmode=verify-ca + 우리 CA, host=127.0.0.1", {"sslmode": "verify-ca", "sslrootcert": CA, "host": "127.0.0.1"}, "ok",
     "verify-ca는 호스트 이름을 보지 않는다 — 같은 CA가 서명한 다른 서버 이름도 통과"),
    ("sslmode=verify-full + 우리 CA, host=localhost", {"sslmode": "verify-full", "sslrootcert": CA}, "ok", "CA 서명 + 이름 일치"),
    ("sslmode=verify-full + 우리 CA, host=127.0.0.1", {"sslmode": "verify-full", "sslrootcert": CA, "host": "127.0.0.1"}, "host_mismatch",
     "인증서 이름(localhost)과 접속 이름이 다르면 거절 — verify-full만 막는 경우"),
    ("sslmode=verify-full + sslrootcert=system", {"sslmode": "verify-full", "sslrootcert": "system"}, "untrusted_ca",
     "OS 신뢰 저장소에 없는 사설 CA는 거절. 공인 CA(예: Neon의 ISRG Root X1)는 OS 저장소에 있어 통과하는 경우 — 여기서는 미검증"),
    ("sslmode=require + sslrootcert=system", {"sslmode": "require", "sslrootcert": "system"}, "weak_mode",
     "sslrootcert=system이면 verify-full보다 약한 sslmode는 오류 (libpq 문서)"),
    ("sslmode=disable", {"sslmode": "disable"}, "no_encryption", "서버가 비암호 연결을 거절(hostnossl reject)"),
    ("sslmode=verify-full + 우리 CA + channel_binding=require", {"sslmode": "verify-full", "sslrootcert": CA, "channel_binding": "require"},
     "ok", "SCRAM-SHA-256-PLUS 채널 결합 — 서버 인증을 한 겹 더"),
]
# 서버 인증서가 만료된 판 (같은 CA가 서명, 이름 localhost, 유효 기간이 지난 인증서 — 시스템 시각은 바꾸지 않음)
EXPIRED_CASES = [
    ("[만료] sslmode=verify-full + 우리 CA", {"sslmode": "verify-full", "sslrootcert": CA}, "expired", "만료된 서버 인증서는 거절"),
    ("[만료] sslmode=verify-ca + 우리 CA", {"sslmode": "verify-ca", "sslrootcert": CA}, "expired", "verify-ca도 유효 기간은 본다"),
    ("[만료] sslmode=require + 우리 CA 파일", {"sslmode": "require", "sslrootcert": CA}, "expired", "root 파일이 있으면 require도 verify-ca처럼 — 만료 거절"),
    ("[만료] sslmode=require (root 파일 없음)", {"sslmode": "require"}, "ok",
     "**만료된 인증서도 연결된다** — require는 인증서를 검증하지 않는다는 증거(암호화만)"),
]
CASES = EXPIRED_CASES if PHASE == "expired" else VALID_CASES


def served_certificate() -> dict:
    """서버가 TLS 핸드셰이크에서 보내는 인증서를 psycopg와 무관하게 받아(PostgreSQL SSLRequest 뒤 검증 없는 TLS) openssl로 유효 기간을
    읽는다 — 'certificate verify failed'가 만료 때문인지 CA 불신 때문인지는 이 독립 확인으로 가른다. 실패하면 error만 남긴다."""
    try:
        with socket.create_connection(("localhost", PORT), timeout=10) as sock:
            sock.sendall(struct.pack("!II", 8, 80877103))  # SSLRequest
            if sock.recv(1) != b"S":
                return {"error": "server declined SSLRequest"}
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(sock, server_hostname="localhost") as tls:
                der = tls.getpeercert(binary_form=True)
        pem = ssl.DER_cert_to_PEM_cert(der)
        dates = subprocess.run(["openssl", "x509", "-noout", "-subject", "-dates"], input=pem, capture_output=True, text=True, timeout=30)
        expired = subprocess.run(["openssl", "x509", "-noout", "-checkend", "0"], input=pem, capture_output=True, text=True, timeout=30)
        info = dict(line.split("=", 1) for line in dates.stdout.strip().splitlines() if "=" in line)
        return {"subject": info.get("subject", "").strip(), "not_before": info.get("notBefore"), "not_after": info.get("notAfter"),
                "expired": expired.returncode != 0}  # -checkend 0: 만료면 종료 1
    except Exception as exc:  # noqa: BLE001 — 독립 확인 실패는 '측정 불완전'으로 드러나게 둔다
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def attempt(params: dict) -> Outcome:
    kw = {"host": "localhost", "port": PORT, "user": "postgres", "password": PW, "dbname": "postgres", "connect_timeout": 10,
          "application_name": "moajwo-ci-tls", **params}  # fmt: skip
    try:
        with psycopg.connect(**kw) as con:
            ssl, version, cipher = con.execute(
                "SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
            ).fetchone()
            return Outcome(ok=True, ssl=bool(ssl), tls=version, cipher=cipher)
    except Exception as exc:  # noqa: BLE001 — 판정기가 드라이버 오류와 측정 오류를 나눈다
        return outcome_from_exception(exc, scrub=(PW, CA, OTHER))


SERVED = served_certificate()
SERVER_EXPIRED = bool(SERVED.get("expired"))
print(f"서버 인증서(독립 확인): {SERVED}")
if PHASE == "expired" and not SERVER_EXPIRED:
    print("[측정 불완전] 만료 판인데 서버 인증서가 만료로 확인되지 않음 — 아래 만료 기대는 충족될 수 없다")
rows: list[dict] = []
verdicts: list[Verdict] = []
for name, params, expected, meaning in CASES:
    got = attempt(params)
    v = judge(expected, got, server_cert_expired=SERVER_EXPIRED, plaintext_requested=params.get("sslmode") == "disable")
    verdicts.append(v)
    detail = f"{got.tls} {got.cipher}" if got.ok else f"{got.error_type}: {got.error_text[:160]}"
    rows.append({"case": name, "expected": expected, "connected": got.ok, "ssl": got.ssl, "cause": v.cause, "verdict": v.verdict,
                 "verdict_label": v.label, "expected_met": v.expected_met, "note": v.note, "detail": detail, "meaning": meaning})  # fmt: skip
    print(f"{'기대 충족' if v.expected_met else '기대 어긋남'} | {name} | {v.label} | {detail}")

s = summarize(verdicts)
libpq = psycopg.pq.version()
info = {"phase": PHASE, "psycopg": psycopg.__version__, "libpq": libpq, "libpq_impl": psycopg.pq.__impl__, "served_certificate": SERVED,
        "summary": s, "cases": rows}  # fmt: skip
(OUT / f"pg-tls-matrix-{PHASE}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
md = [f"### PostgreSQL TLS 매트릭스 [{PHASE}] (psycopg {psycopg.__version__}, libpq {libpq} {psycopg.pq.__impl__}) — "
      f"충족 {s['met']}/{s['total']} · 검증 실패 {s['verify_failures']} · 측정 불완전/도구 오류 {s['tool_failures']}", "",
      f"서버 인증서(독립 확인): {SERVED.get('subject')} · {SERVED.get('not_before')} ~ {SERVED.get('not_after')} · 만료 {SERVED.get('expired')}{' · ' + SERVED['error'] if SERVED.get('error') else ''}", "",
      "| 경우 | 기대 | 판정 | 원인·TLS | 뜻 |", "|---|---|---|---|---|"]  # fmt: skip
for r in rows:
    mark = "" if r["expected_met"] else (" ❌검증" if r["verdict"] in VERIFY_FAILURE else " ⚠측정")
    md.append(f"| {r['case']} | {r['expected']} | {r['verdict_label']}{mark} | {r['cause'] or ''} {r['detail']} | {r['meaning']} |")
(OUT / f"pg-tls-matrix-{PHASE}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
print(f"요약 [{PHASE}] | 충족 {s['met']}/{s['total']} | 검증 실패 {s['verify_failures']} | 측정 불완전 {s['tool_failures']}")
sys.exit(0 if s["met"] == s["total"] else 1)
