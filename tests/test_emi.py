from datetime import date
from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import given, reject
from hypothesis import strategies as st

from app.domain.emi import add_months, amortization_schedule, calculate_emi

D = Decimal


@pytest.mark.parametrize(
    ("principal", "annual_rate_pct", "tenure_months", "expected_emi"),
    [
        (D("100000"), D("12"), 12, D("8884.88")),
        (D("1000000"), D("10"), 60, D("21247.04")),
        (D("500000"), D("0"), 10, D("50000.00")),
        (D("100"), D("0"), 3, D("33.33")),
    ],
)
def test_emi_matches_known_values(
    principal: Decimal, annual_rate_pct: Decimal, tenure_months: int, expected_emi: Decimal
) -> None:
    assert calculate_emi(principal, annual_rate_pct, tenure_months) == expected_emi


@pytest.mark.parametrize(
    ("principal", "annual_rate_pct", "tenure_months"),
    [
        (D("0"), D("12"), 12),
        (D("-1"), D("12"), 12),
        (D("1000"), D("-1"), 12),
        (D("1000"), D("12"), 0),
    ],
)
def test_emi_rejects_impossible_terms(
    principal: Decimal, annual_rate_pct: Decimal, tenure_months: int
) -> None:
    with pytest.raises(ValueError):
        calculate_emi(principal, annual_rate_pct, tenure_months)


def test_interest_is_charged_on_the_reducing_balance() -> None:
    schedule = amortization_schedule(D("100000"), D("12"), 12, start=date(2026, 1, 15))

    first, second = schedule[0], schedule[1]
    # Month 1: 1% of 1,00,000. Month 2: 1% of what is still owed after month 1.
    assert (first.interest, first.principal, first.closing_balance) == (
        D("1000.00"),
        D("7884.88"),
        D("92115.12"),
    )
    assert (second.interest, second.principal, second.closing_balance) == (
        D("921.15"),
        D("7963.73"),
        D("84151.39"),
    )
    assert all(row.interest > later.interest for row, later in pairwise(schedule))


def test_last_instalment_absorbs_the_rounding() -> None:
    schedule = amortization_schedule(D("100"), D("0"), 3, start=date(2026, 1, 15))

    assert [row.amount_due for row in schedule] == [D("33.33"), D("33.33"), D("33.34")]
    assert schedule[-1].closing_balance == D("0.00")


def test_due_dates_keep_the_disbursal_day_and_clamp_short_months() -> None:
    schedule = amortization_schedule(D("90000"), D("12"), 4, start=date(2026, 1, 31))

    assert [row.due_date for row in schedule] == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 1, 15), 1, date(2026, 2, 15)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2026, 11, 30), 3, date(2027, 2, 28)),
        (date(2026, 12, 31), 12, date(2027, 12, 31)),
    ],
)
def test_add_months(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


def test_terms_too_extreme_to_schedule_are_rejected() -> None:
    # Found by the property test below: paisa rounding of a Rs 14.87 EMI, compounded at
    # 17.71% for 335 months, adds up to more than one instalment.
    with pytest.raises(ValueError, match="before its last month"):
        amortization_schedule(D("1000"), D("17.71"), 335, start=date(2026, 1, 1))


@given(
    principal=st.decimals(min_value=D("10000"), max_value=D("50000000"), places=2),
    annual_rate_pct=st.decimals(min_value=D("0"), max_value=D("15"), places=2),
    tenure_months=st.integers(min_value=1, max_value=360),
)
def test_realistic_terms_always_produce_a_schedule(
    principal: Decimal, annual_rate_pct: Decimal, tenure_months: int
) -> None:
    schedule = amortization_schedule(principal, annual_rate_pct, tenure_months, date(2026, 1, 1))

    assert len(schedule) == tenure_months


@given(
    principal=st.decimals(min_value=D("1000"), max_value=D("50000000"), places=2),
    annual_rate_pct=st.decimals(min_value=D("0"), max_value=D("36"), places=2),
    tenure_months=st.integers(min_value=1, max_value=360),
)
def test_every_schedule_repays_exactly_the_principal(
    principal: Decimal, annual_rate_pct: Decimal, tenure_months: int
) -> None:
    emi = calculate_emi(principal, annual_rate_pct, tenure_months)
    try:
        schedule = amortization_schedule(
            principal, annual_rate_pct, tenure_months, date(2026, 1, 1)
        )
    except ValueError:
        reject()  # too extreme to schedule; see test_terms_too_extreme_to_schedule_are_rejected

    assert len(schedule) == tenure_months
    assert [row.number for row in schedule] == list(range(1, tenure_months + 1))
    assert sum(row.principal for row in schedule) == principal
    assert schedule[-1].closing_balance == 0
    for row in schedule:
        assert row.amount_due == row.principal + row.interest
        assert row.principal >= 0
        assert row.interest >= 0
        assert row.amount_due.as_tuple().exponent == -2
    assert all(row.amount_due == emi for row in schedule[:-1])
    balances = [principal] + [row.closing_balance for row in schedule]
    assert balances == sorted(balances, reverse=True)
