"""화면에 보낼 시각을 한국 시간(KST, +09:00)으로 맞춘다 — 응답에는 항상 시간대(+09:00)를 붙여 보낸다.

SQLite는 시각을 시간대 없이 돌려주므로 저장할 때 쓴 기준으로 해석한다.
- DB 기본값(func.now())과 datetime.now(UTC)로 저장한 시각은 UTC → utc_to_kst
- datetime.now(KST)로 저장한 시각(통합 실행 시작·종료, 자동화 수정 시각)은 KST 벽시계 → stored_kst
화면(frontend runFormat.formatTime)은 시간대를 읽어 Asia/Seoul로 보여 준다.
"""

from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


def stored_kst(value: datetime | None) -> datetime | None:
    """datetime.now(KST)로 저장해 시간대 없이 돌아온 값에 KST를 붙인다 (시각은 바꾸지 않음)."""
    if value is None:
        return None
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


def utc_to_kst(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KST)
