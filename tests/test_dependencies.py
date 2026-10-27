from datetime import UTC, datetime, timedelta

from app.dependencies import current_time


def test_the_clock_reads_now_in_utc() -> None:
    now = current_time()

    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)
