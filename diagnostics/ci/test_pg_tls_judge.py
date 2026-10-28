"""pg_tls_judge의 방어 시험 (서버 없이) — 2026-10-27 2차 지시 3-2.

앞 판의 오판: 측정 오류(RuntimeError)·비밀번호 오류·연결 거부도 '거절 기대' 시험을 통과시켰다(backups/review-20261027b/pg-tls/
before-fix-judge/). 여기서는 그런 실패가 성공으로 세어지지 않고, 각 부정 시험은 기대한 원인일 때만 충족이며, 정상 연결은 서버가
TLS 사용을 확인할 때만 충족임을 못 박는다. 실행: python -m pytest diagnostics/ci/test_pg_tls_judge.py (저장소 루트)."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pg_tls_judge import (  # noqa: E402
    REJECTION_CAUSES,
    Outcome,
    classify,
    judge,
    outcome_from_exception,
    summarize,
)

VERIFY_FAILED = 'connection failed: connection to server at "127.0.0.1", port 55433 failed: SSL error: certificate verify failed'
MESSAGES = {
    "untrusted_ca": VERIFY_FAILED,
    "host_mismatch": 'connection failed: ... failed: server certificate for "localhost" (and 1 other name) does not match host name "127.0.0.1"',
    "expired": "connection failed: ... failed: SSL error: certificate has expired",
    "weak_mode": 'connection is bad: weak sslmode "require" may not be used with sslrootcert=system (use "verify-full")',
    "no_encryption": 'connection failed: ... FATAL:  pg_hba.conf rejects connection for host "127.0.0.1", user "postgres", database "postgres", no encryption',
}


def _driver_error(text: str, sqlstate: str | None = None) -> Outcome:
    exc = psycopg.OperationalError(text)
    if sqlstate:
        exc.sqlstate = sqlstate  # type: ignore[attr-defined]
    return outcome_from_exception(exc)


@pytest.mark.parametrize("cause", REJECTION_CAUSES)
def test_each_rejection_cause_is_recognised_and_only_matches_its_own_expectation(cause: str) -> None:
    outcome = _driver_error(MESSAGES[cause])
    assert classify(outcome) == cause
    assert judge(cause, outcome).expected_met is True
    for other in REJECTION_CAUSES:
        if other != cause:
            v = judge(other, outcome)
            assert v.expected_met is False and v.verdict == "wrong_cause", (other, v)
    ok_expected = judge("ok", outcome)
    assert ok_expected.expected_met is False and ok_expected.verdict == "unexpected_failure"


def test_verify_failed_is_expiry_only_when_the_server_certificate_was_independently_found_expired() -> None:
    """OpenSSL은 만료도 CA 불신도 'certificate verify failed'로만 알린다 — 호출 쪽이 서버 인증서를 받아 확인한 값으로 가른다."""
    outcome = _driver_error(VERIFY_FAILED)
    assert classify(outcome) == "untrusted_ca" and classify(outcome, server_cert_expired=True) == "expired"
    assert judge("expired", outcome).verdict == "wrong_cause"  # 만료 확인 없이는 만료로 세지 않는다
    assert judge("expired", outcome, server_cert_expired=True).expected_met is True
    assert judge("untrusted_ca", outcome, server_cert_expired=True).verdict == "wrong_cause"


def test_plaintext_rejection_is_recognised_by_sqlstate_when_the_server_speaks_another_language() -> None:
    localized = _driver_error("connection failed: FATAL:  (서버 로캘의 문구 — pg_hba 거절)", sqlstate="28000")
    assert classify(localized) == "unknown"  # 문구만으로는 모름
    assert classify(localized, plaintext_requested=True) == "no_encryption"
    assert judge("no_encryption", localized, plaintext_requested=True).expected_met is True
    # 같은 SQLSTATE라도 비암호를 요청하지 않았으면(예: 다른 pg_hba 거절) 비암호 거절로 세지 않는다
    assert judge("no_encryption", localized).verdict == "unexpected_failure"
    # 비밀번호 오류(28P01)는 비암호 요청이어도 비암호 거절이 아니다
    wrong_pw = _driver_error("connection failed: FATAL:  password authentication failed for user", sqlstate="28P01")
    assert judge("no_encryption", wrong_pw, plaintext_requested=True).verdict == "unexpected_failure"


def test_a_measurement_error_never_counts_as_an_expected_rejection() -> None:
    """RuntimeError 같은 드라이버 밖 예외 — 앞 판은 '거절 기대' 시험을 통과시켰다."""
    outcome = outcome_from_exception(RuntimeError("측정 도구 오류"))
    assert outcome.driver_error is False and classify(outcome) == "tool_error"
    for expected in (*REJECTION_CAUSES, "ok"):
        v = judge(expected, outcome)
        assert v.expected_met is False and v.verdict == "measurement_error", (expected, v)
    s = summarize([judge(c, outcome) for c in REJECTION_CAUSES])
    assert s["met"] == 0 and s["tool_failures"] == len(REJECTION_CAUSES) and s["verify_failures"] == 0


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ('connection failed: FATAL:  password authentication failed for user "postgres"', "auth"),
        ("connection failed: connection to server at \"127.0.0.1\", port 1 failed: Connection refused", "network"),
        ("connection failed: connection timeout expired", "network"),
        ("connection failed: something nobody listed", "unknown"),
    ],
)
def test_authentication_network_and_unknown_failures_do_not_pass_a_certificate_test(text: str, cause: str) -> None:
    outcome = _driver_error(text)
    assert classify(outcome) == cause
    for expected in REJECTION_CAUSES:
        v = judge(expected, outcome)
        assert v.expected_met is False and v.verdict == "unexpected_failure", (expected, v)
    assert judge("ok", outcome).verdict == "unexpected_failure"


def test_a_normal_connection_counts_only_when_the_server_confirms_tls() -> None:
    assert judge("ok", Outcome(ok=True, ssl=True, tls="TLSv1.3", cipher="x")).expected_met is True
    plain = judge("ok", Outcome(ok=True, ssl=False))
    assert plain.expected_met is False and plain.verdict == "unexpected_failure"
    for cause in REJECTION_CAUSES:  # 거절돼야 하는데 연결됨 → 검증 실패
        v = judge(cause, Outcome(ok=True, ssl=True))
        assert v.expected_met is False and v.verdict == "unexpected_success"


def test_summary_separates_verification_failures_from_tool_failures() -> None:
    verdicts = [
        judge("untrusted_ca", _driver_error(MESSAGES["untrusted_ca"])),  # 충족
        judge("host_mismatch", _driver_error(MESSAGES["untrusted_ca"])),  # 원인 다름 → 검증 실패
        judge("expired", Outcome(ok=True, ssl=True)),  # 연결됨 → 검증 실패
        judge("untrusted_ca", outcome_from_exception(ValueError("x"))),  # 측정 오류
        judge("ok", _driver_error("connection refused")),  # 예상 밖 실패
    ]
    s = summarize(verdicts)
    assert (s["total"], s["met"], s["verify_failures"], s["tool_failures"]) == (5, 1, 2, 2)


def test_secrets_are_scrubbed_from_the_error_text() -> None:
    exc = psycopg.OperationalError("connection to server at host hidden.example failed: password 'pw-secret' rejected")
    outcome = outcome_from_exception(exc, scrub=("pw-secret", "hidden.example"))
    assert "pw-secret" not in outcome.error_text and "hidden.example" not in outcome.error_text


def test_unknown_expectation_is_an_error() -> None:
    with pytest.raises(ValueError):
        judge("maybe", Outcome(ok=True, ssl=True))
