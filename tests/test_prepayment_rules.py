from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.emi import add_months, amortization_schedule, calculate_emi, schedule_at_emi
from app.domain.prepayment import accrued_interest

D = Decimal
START = date(2026, 1, 10)


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (date(2026, 4, 1), D("0.00")),  # before the period: the last EMI was paid early
        (date(2026, 4, 10), D("0.00")),  # the day the period starts
        (date(2026, 4, 25), D("116.67")),  # 15 of 30 days: half a month at 14% on 20,000
        (date(2026, 5, 9), D("225.56")),  # 29 of 30 days
    ],
)
def test_interest_accrues_only_for_the_days_of_the_period_that_have_passed(
    on: date, expected: Decimal
) -> None:
    interest = accrued_interest(
        D("20000"), D("14"), period_start=date(2026, 4, 10), period_end=date(2026, 5, 10), on=on
    )

    assert interest == expected


def test_a_whole_period_costs_exactly_the_schedules_monthly_interest() -> None:
    # 28 days in February: still exactly one month of interest, as on the EMI schedule.
    interest = accrued_interest(
        D("20000"),
        D("14"),
        period_start=date(2026, 1, 31),
        period_end=date(2026, 2, 28),
        on=date(2026, 2, 28),
    )

    assert interest == D("233.33")


def test_a_schedule_can_continue_from_a_later_instalment() -> None:
    whole = amortization_schedule(D("100000"), D("14"), 12, start=START)

    rest = amortization_schedule(whole[3].closing_balance, D("14"), 8, start=START, first_number=5)

    assert [row.number for row in rest] == list(range(5, 13))
    assert [row.due_date for row in rest] == [row.due_date for row in whole[4:]]
    assert sum(row.principal for row in rest) == whole[3].closing_balance


def test_keeping_the_emi_after_a_prepayment_finishes_the_loan_sooner() -> None:
    emi = calculate_emi(D("100000"), D("14"), 12)  # 8,978.71
    balance = D("56289.39")  # after three EMIs and a 20,000 prepayment

    rest = schedule_at_emi(balance, D("14"), emi, start=START, first_number=4, max_installments=9)

    assert [row.amount_due for row in rest] == [emi] * 6 + [D("4935.10")]
    assert [row.due_date for row in rest] == [add_months(START, n) for n in range(4, 11)]
    assert sum(row.principal for row in rest) == balance
    assert rest[-1].closing_balance == 0


def test_keeping_the_emi_never_runs_past_the_original_last_month() -> None:
    # Without the cap this would take a fourth month; the third and last allowed one
    # pays everything left instead.
    rest = schedule_at_emi(
        D("30000"), D("12"), D("10000"), start=START, first_number=1, max_installments=3
    )

    assert [row.amount_due for row in rest] == [D("10000.00"), D("10000.00"), D("10608.03")]
    assert rest[-1].closing_balance == 0


def test_an_emi_that_does_not_cover_the_interest_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not cover the interest"):
        schedule_at_emi(
            D("100000"), D("14"), D("1000"), start=START, first_number=1, max_installments=12
        )


@given(
    principal=st.integers(min_value=10_000, max_value=20_000_000).map(D),
    rate=st.sampled_from([D("14.00"), D("10.50"), D("8.75")]),
    tenure=st.integers(min_value=6, max_value=360),
    paid_share=st.floats(min_value=0, max_value=0.9),
    prepaid_share=st.integers(min_value=1, max_value=99),
)
def test_keeping_the_emi_repays_exactly_what_is_left_within_the_original_term(
    principal: Decimal, rate: Decimal, tenure: int, paid_share: float, prepaid_share: int
) -> None:
    whole = amortization_schedule(principal, rate, tenure, start=START)
    paid = int(tenure * paid_share)
    owed = whole[paid - 1].closing_balance if paid else principal
    balance = (owed * (100 - prepaid_share) / 100).quantize(D("0.01"))

    rest = schedule_at_emi(
        balance,
        rate,
        calculate_emi(principal, rate, tenure),
        start=START,
        first_number=paid + 1,
        max_installments=tenure - paid,
    )

    assert 1 <= len(rest) <= tenure - paid
    assert sum(row.principal for row in rest) == balance
    assert rest[-1].closing_balance == 0
    assert all(row.principal > 0 and row.interest >= 0 for row in rest)
