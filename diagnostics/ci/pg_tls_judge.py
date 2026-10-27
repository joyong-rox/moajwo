"""PostgreSQL TLS 시험의 판정 — 실패 원인을 구분한다 (2026-10-27 2차 지시 3-2).

앞 판(a5e3cff)은 예외를 모두 'fail'로 만들고 부정 시험에서는 그 값만 비교해, 측정 도구 오류(RuntimeError)·서버 미기동·비밀번호
오류 같은 TLS와 무관한 실패도 '기대한 거절'로 세었다. 여기서는 결과를 세 갈래로 나눈다.

- 정상 연결: 연결됐고 서버가 TLS 사용(pg_stat_ssl.ssl)이라고 답함 → 기대가 '연결'일 때만 충족.
- 예상한 TLS 거절: 드라이버 오류 문구에서 기대한 원인(CA 불신·호스트 이름 불일치·만료·약한 설정·비암호 거절)이 확인됨 → 기대가
  그 원인일 때만 충족. 다른 원인이면 '원인 불일치'(검증 실패).
- 예상 밖의 실패: 인증·네트워크·시간 초과·알 수 없는 오류, 또는 psycopg 밖의 예외(측정 코드 오류) → 어느 기대도 충족하지 않음
  ('측정 불완전/도구 오류'로 따로 센다).

원인은 libpq·OpenSSL이 내는 짧은 핵심 문구로만 가린다(문장 전체 완전 일치 아님 — 판에 덜 민감). 문구가 같은 두 원인은 문맥으로 가른다:
- OpenSSL은 만료도 CA 불신도 'certificate verify failed'로만 알린다 → 호출 쪽이 서버 인증서를 따로 받아 만료를 확인한 값
  (server_cert_expired)을 주면 '만료', 아니면 'CA 불신'. 'certificate has expired' 문구가 있으면 그대로 '만료'.
- 서버의 pg_hba 거절(비암호 연결)은 서버 로캘에 따라 문구가 다르다 → 문구가 없을 때는 SQLSTATE 28000 + 비암호를 요청한 사실
  (plaintext_requested)로 가른다. TLS 실패는 SQLSTATE가 없을 수 있어 SQLSTATE 하나에만 기대지 않는다.
문구에 주소·비밀번호가 섞이지 않게 호출 쪽이 지운 뒤 넘긴다."""

from __future__ import annotations

from dataclasses import dataclass, field

# 문구로 바로 가려지는 원인 (소문자 비교). 순서가 중요하다: 만료 문구가 있으면 'verify failed'보다 먼저 본다.
CAUSE_MARKERS: dict[str, tuple[str, ...]] = {
    "expired": ("certificate has expired", "has expired"),
    "host_mismatch": ("does not match host name", "hostname mismatch", "ip address mismatch"),
    "cert_verify_failed": ("certificate verify failed", "unable to get local issuer", "self-signed certificate", "unable to verify the first certificate"),
    "weak_mode": ("weak sslmode", "may not be used with sslrootcert=system"),
    "no_encryption": ("no encryption", "rejects connection", "server does not support ssl", "ssl is not supported"),
}
UNEXPECTED_MARKERS: dict[str, tuple[str, ...]] = {
    "auth": ("password authentication failed", "authentication failed", "no password supplied", "role \"", "does not exist"),
    "network": ("connection refused", "could not connect", "timeout expired", "timed out", "connection timed out", "name or service not known", "network is unreachable"),
    "channel_binding": ("channel binding", "channel_binding"),
}
REJECTION_CAUSES = ("untrusted_ca", "host_mismatch", "expired", "weak_mode", "no_encryption")
VERDICTS = ("expected_ok", "expected_rejection", "wrong_cause", "unexpected_success", "unexpected_failure", "measurement_error")
VERDICT_LABEL = {
    "expected_ok": "정상 연결(기대대로)",
    "expected_rejection": "예상한 TLS 거절",
    "wrong_cause": "거절은 됐으나 원인이 다름 — 검증 실패",
    "unexpected_success": "거절돼야 하는데 연결됨 — 검증 실패",
    "unexpected_failure": "예상 밖의 연결 실패 — 측정 불완전",
    "measurement_error": "측정 코드 오류 — 측정 불완전",
}
VERIFY_FAILURE = ("wrong_cause", "unexpected_success")
TOOL_FAILURE = ("unexpected_failure", "measurement_error")
PG_HBA_REJECT_SQLSTATE = "28000"  # invalid_authorization_specification — pg_hba.conf가 연결을 거절할 때 (비밀번호 오류는 28P01)


@dataclass
class Outcome:
    """한 번의 연결 시도 결과. ok=True면 ssl(서버가 본 TLS 사용 여부)·tls·cipher, 아니면 error_type·error_text(비밀값 없이)."""

    ok: bool
    ssl: bool | None = None
    tls: str | None = None
    cipher: str | None = None
    error_type: str | None = None
    error_text: str = ""
    driver_error: bool = False  # psycopg(드라이버)가 낸 오류인지 — 아니면 측정 코드 오류
    sqlstate: str | None = None


@dataclass
class Verdict:
    verdict: str
    expected_met: bool
    cause: str | None  # 거절이면 가려낸 원인(없으면 None)
    note: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return VERDICT_LABEL[self.verdict]


def outcome_from_exception(exc: BaseException, scrub: tuple[str, ...] = ()) -> Outcome:
    """예외를 Outcome으로. psycopg.Error 계열만 '드라이버 오류'로 본다(그 밖은 측정 코드 오류). scrub의 글자는 문구에서 지운다."""
    try:
        import psycopg  # noqa: PLC0415 — 시험 환경에만 있는 드라이버

        driver = isinstance(exc, psycopg.Error)
    except ImportError:  # pragma: no cover — 드라이버 없이 판정만 쓸 때
        driver = False
    text = str(exc)
    for secret in scrub:
        if secret:
            text = text.replace(secret, "<지움>")
    return Outcome(
        ok=False,
        error_type=type(exc).__name__,
        error_text=text.strip()[:300],
        driver_error=driver,
        sqlstate=getattr(exc, "sqlstate", None),
    )


def classify(outcome: Outcome, *, server_cert_expired: bool = False, plaintext_requested: bool = False) -> str | None:
    """실패 원인 — REJECTION_CAUSES의 거절 원인, UNEXPECTED_MARKERS의 예상 밖 원인, 모르면 'unknown'. 연결됐으면 None.
    server_cert_expired: 호출 쪽이 서버 인증서를 따로 받아 확인한 만료 여부. plaintext_requested: sslmode=disable 등 비암호를 요청했는지."""
    if outcome.ok:
        return None
    if not outcome.driver_error:
        return "tool_error"
    text = outcome.error_text.lower()
    for cause, markers in CAUSE_MARKERS.items():
        if any(m in text for m in markers):
            if cause == "cert_verify_failed":
                return "expired" if server_cert_expired else "untrusted_ca"
            return cause
    if plaintext_requested and outcome.sqlstate == PG_HBA_REJECT_SQLSTATE:
        return "no_encryption"  # 서버 로캘이 달라 문구가 없어도 pg_hba 거절(28000)이면 비암호 거절
    for cause, markers in UNEXPECTED_MARKERS.items():
        if any(m in text for m in markers):
            return cause
    return "unknown"


def judge(expected: str, outcome: Outcome, *, server_cert_expired: bool = False, plaintext_requested: bool = False) -> Verdict:
    """expected: 'ok' 또는 REJECTION_CAUSES의 하나. 기대와 결과를 맞춰 Verdict를 낸다."""
    if expected != "ok" and expected not in REJECTION_CAUSES:
        raise ValueError(f"알 수 없는 기대: {expected}")
    cause = classify(outcome, server_cert_expired=server_cert_expired, plaintext_requested=plaintext_requested)
    if outcome.ok:
        if expected == "ok":
            if outcome.ssl is True:
                return Verdict("expected_ok", True, None)
            return Verdict("unexpected_failure", False, None, "연결됐으나 서버가 TLS 사용을 확인하지 않음(ssl != true)")
        return Verdict("unexpected_success", False, None, f"기대한 거절 원인 {expected}인데 연결됨")
    if cause == "tool_error":
        return Verdict("measurement_error", False, cause, f"드라이버 밖 예외 {outcome.error_type}")
    if cause in REJECTION_CAUSES:
        if expected == cause:
            return Verdict("expected_rejection", True, cause)
        if expected == "ok":
            return Verdict("unexpected_failure", False, cause, f"연결이 기대인데 TLS 거절({cause})")
        return Verdict("wrong_cause", False, cause, f"기대 {expected} ≠ 실제 {cause}")
    return Verdict("unexpected_failure", False, cause, f"TLS와 무관한 실패({cause}): {outcome.error_type}")


def summarize(verdicts: list[Verdict]) -> dict[str, int]:
    out = {"total": len(verdicts), "met": sum(v.expected_met for v in verdicts),
           "verify_failures": sum(v.verdict in VERIFY_FAILURE for v in verdicts),
           "tool_failures": sum(v.verdict in TOOL_FAILURE for v in verdicts)}  # fmt: skip
    for name in VERDICTS:
        out[name] = sum(v.verdict == name for v in verdicts)
    return out
