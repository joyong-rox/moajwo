"""화면에 보낼 시각을 한국 시간(KST, +09:00)으로 맞춘다.

DB 기본값(func.now())과 datetime.now(UTC)로 저장한 시각은 UTC다. SQLite는 시간대 없이 돌려주므로 UTC로 보고 바꾼다.
"""

from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


def utc_to_kst(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KST)
