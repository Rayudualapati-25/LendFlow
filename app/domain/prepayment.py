"""Interest on principal paid ahead of the EMI schedule.

The schedule charges interest per instalment period, from one due date to the next, at the
monthly rate. Principal paid during a period was owed only for the days of it that have
passed, so it carries interest for those days alone. RBI's April 2024 circular on charging
of interest named the opposite, a whole month's interest on a loan repaid mid-month, as an
unfair practice.
"""

from datetime import date
from decimal import Decimal

from app.domain.emi import to_paise


def days_owed(*, period_start: date, period_end: date, on: date) -> int:
    """Days of the period that have passed by `on`; none before the period starts (when
    the previous EMI was paid early)."""
    return min(max((on - period_start).days, 0), (period_end - period_start).days)


def accrued_interest(
    principal: Decimal,
    annual_rate_pct: Decimal,
    *,
    period_start: date,
    period_end: date,
    on: date,
) -> Decimal:
    """Interest on `principal` for the days of the period that have passed by `on`.

    The period's interest accrues evenly over its days, so a whole period costs exactly the
    schedule's monthly interest, whether the month has 28 days or 31.
    """
    days = days_owed(period_start=period_start, period_end=period_end, on=on)
    days_in_period = (period_end - period_start).days
    return to_paise(principal * annual_rate_pct / 1200 * days / days_in_period)
