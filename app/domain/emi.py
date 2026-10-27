"""Reducing-balance EMI and amortisation schedule, in exact decimal arithmetic.

    EMI = P * r * (1 + r)^n / ((1 + r)^n - 1)

P is the principal, n the number of monthly instalments and r the monthly rate (annual
percent / 1200). Each month's interest is charged on the balance still owed, so the
interest share of the EMI falls and the principal share rises as the loan is repaid.

Amounts are rounded to the paisa, half up. The last instalment absorbs the rounding, so
the principal repaid always adds up to exactly the amount lent.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")


@dataclass(frozen=True)
class Installment:
    number: int
    due_date: date
    amount_due: Decimal
    principal: Decimal
    interest: Decimal
    closing_balance: Decimal


def to_paise(amount: Decimal) -> Decimal:
    return amount.quantize(PAISA, rounding=ROUND_HALF_UP)


def calculate_emi(principal: Decimal, annual_rate_pct: Decimal, tenure_months: int) -> Decimal:
    """The fixed monthly instalment that repays `principal` over `tenure_months`."""
    if principal <= 0 or annual_rate_pct < 0 or tenure_months < 1:
        raise ValueError("principal and tenure must be positive, and the rate non-negative")
    monthly_rate = annual_rate_pct / 1200
    if monthly_rate == 0:
        return to_paise(principal / tenure_months)
    growth = (1 + monthly_rate) ** tenure_months
    return to_paise(principal * monthly_rate * growth / (growth - 1))


def amortization_schedule(
    principal: Decimal, annual_rate_pct: Decimal, tenure_months: int, start: date
) -> list[Installment]:
    """Every instalment of the loan; the first falls due one month after `start`.

    Raises ValueError for terms so extreme that rounding the EMI to the paisa, compounded
    over the loan, adds up to more than a whole instalment, so the loan would be repaid
    before its last month (for example Rs 1,000 at 17.71% over 335 months). No product
    offers terms like these.
    """
    emi = calculate_emi(principal, annual_rate_pct, tenure_months)
    monthly_rate = annual_rate_pct / 1200
    balance = principal
    schedule = []
    for number in range(1, tenure_months + 1):
        interest = to_paise(balance * monthly_rate)
        if number == tenure_months:
            repaid = balance
        else:
            repaid = emi - interest
            if repaid >= balance:
                raise ValueError("rounding drift would repay this loan before its last month")
        balance -= repaid
        schedule.append(
            Installment(
                number=number,
                due_date=add_months(start, number),
                amount_due=repaid + interest,
                principal=repaid,
                interest=interest,
                closing_balance=balance,
            )
        )
    return schedule


def add_months(day: date, months: int) -> date:
    """The same day `months` later, clamped to shorter months: 31 Jan + 1 month = 28 Feb.

    Each due date is counted from the disbursal date rather than from the previous due
    date, so a loan disbursed on the 31st stays on the 31st after a short month.
    """
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
