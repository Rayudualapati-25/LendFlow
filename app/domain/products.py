"""The product catalogue: the fixed rate and the limits each loan product is offered on."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.enums import Product
from app.errors import TermsOutsideProduct


@dataclass(frozen=True)
class ProductTerms:
    product: Product
    annual_rate_pct: Decimal
    min_amount: Decimal
    max_amount: Decimal
    min_tenure_months: int
    max_tenure_months: int


# Every product stays inside the envelope the EMI property tests prove safe:
# at most 15% a year, at least Rs 10,000, at most 360 months.
CATALOGUE: dict[Product, ProductTerms] = {
    terms.product: terms
    for terms in (
        ProductTerms(
            Product.PERSONAL, Decimal("14.00"), Decimal("10000.00"), Decimal("2500000.00"), 6, 60
        ),
        ProductTerms(
            Product.VEHICLE, Decimal("10.50"), Decimal("50000.00"), Decimal("5000000.00"), 12, 84
        ),
        ProductTerms(
            Product.HOME, Decimal("8.75"), Decimal("500000.00"), Decimal("20000000.00"), 60, 360
        ),
    )
}


def check_terms(product: Product, amount: Decimal, tenure_months: int) -> ProductTerms:
    """The product's terms, if the amount and tenure are within its limits."""
    terms = CATALOGUE[product]
    name = f"{product.value.capitalize()} loans"
    if not terms.min_amount <= amount <= terms.max_amount:
        raise TermsOutsideProduct(
            f"{name} are for {format_inr(terms.min_amount)} to {format_inr(terms.max_amount)}"
        )
    if not terms.min_tenure_months <= tenure_months <= terms.max_tenure_months:
        raise TermsOutsideProduct(
            f"{name} run for {terms.min_tenure_months} to {terms.max_tenure_months} months"
        )
    return terms


def format_inr(amount: Decimal) -> str:
    """Rupees with Indian digit grouping: thousands, then lakhs and crores (₹25,00,000.00)."""
    rupees, paise = f"{amount:.2f}".split(".")
    head, groups = rupees[:-3], [rupees[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return f"₹{','.join(groups)}.{paise}"
