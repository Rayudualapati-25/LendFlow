from datetime import date
from decimal import Decimal

import pytest

from app.domain.emi import amortization_schedule
from app.domain.enums import Product
from app.domain.products import CATALOGUE, check_terms, format_inr
from app.errors import TermsOutsideProduct

D = Decimal


def test_every_product_has_terms() -> None:
    assert set(CATALOGUE) == set(Product)


@pytest.mark.parametrize("product", list(Product))
def test_every_product_can_be_scheduled_at_its_extremes(product: Product) -> None:
    terms = CATALOGUE[product]
    corners = [
        (terms.min_amount, terms.min_tenure_months),
        (terms.min_amount, terms.max_tenure_months),
        (terms.max_amount, terms.min_tenure_months),
        (terms.max_amount, terms.max_tenure_months),
    ]

    for amount, tenure in corners:
        schedule = amortization_schedule(amount, terms.annual_rate_pct, tenure, date(2026, 1, 1))
        assert len(schedule) == tenure


def test_limits_are_inclusive() -> None:
    terms = CATALOGUE[Product.PERSONAL]

    check_terms(Product.PERSONAL, terms.min_amount, terms.min_tenure_months)
    check_terms(Product.PERSONAL, terms.max_amount, terms.max_tenure_months)


@pytest.mark.parametrize(
    ("amount", "tenure_months", "message"),
    [
        (D("9999.99"), 12, "Personal loans are for ₹10,000.00 to ₹25,00,000.00"),
        (D("2500000.01"), 12, "Personal loans are for ₹10,000.00 to ₹25,00,000.00"),
        (D("100000"), 5, "Personal loans run for 6 to 60 months"),
        (D("100000"), 61, "Personal loans run for 6 to 60 months"),
    ],
)
def test_terms_outside_the_product_are_refused(
    amount: Decimal, tenure_months: int, message: str
) -> None:
    with pytest.raises(TermsOutsideProduct, match=message):
        check_terms(Product.PERSONAL, amount, tenure_months)


@pytest.mark.parametrize(
    ("amount", "formatted"),
    [
        (D("999"), "₹999.00"),
        (D("1000"), "₹1,000.00"),
        (D("150000.5"), "₹1,50,000.50"),
        (D("20000000"), "₹2,00,00,000.00"),
    ],
)
def test_rupees_use_indian_digit_grouping(amount: Decimal, formatted: str) -> None:
    assert format_inr(amount) == formatted
