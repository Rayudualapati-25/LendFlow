from datetime import UTC, date, datetime

import pytest

from app.domain.dates import business_date


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 3, 31, 18, 29, tzinfo=UTC), date(2026, 3, 31)),
        (datetime(2026, 3, 31, 18, 30, tzinfo=UTC), date(2026, 4, 1)),
    ],
)
def test_business_dates_follow_india_standard_time(moment: datetime, expected: date) -> None:
    assert business_date(moment) == expected
