"""Business dates. Timestamps are stored in UTC; due dates follow the calendar in India."""

from datetime import date, datetime, timedelta, timezone

# India has no daylight saving time, so a fixed offset is exact and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def business_date(moment: datetime) -> date:
    """The date in India at `moment`: 23:00 UTC on 31 October is 1 November there."""
    return moment.astimezone(IST).date()


def last_completed_day(moment: datetime) -> date:
    """The latest day in India whose day-end has passed at `moment`: yesterday there."""
    return business_date(moment) - timedelta(days=1)
