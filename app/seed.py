"""A year of deterministic demo data, replayed through the real services. Local use only.

Applicants apply; an underwriter reviews, decides and disburses; borrowers pay each EMI on
its due date. A small event timeline runs all of this in time order, so each decision sees
exactly the loans that existed at that moment and each audit trail runs forwards in time.
The same services as the API create every row, so the data obeys every rule.

The random seed and the end date are fixed, so every run produces the same data.
Never run this against a database that holds real customers.
"""

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from random import Random

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.domain.dates import IST
from app.domain.enums import ApplicationStatus, DecisionOutcome, InstallmentStatus, Product, Role
from app.models import Applicant, Application, Base, Decision, Loan, Repayment, User
from app.security import hash_password
from app.services import applications, decisions, loans

DEMO_PASSWORD = "lendflow-demo-password"  # noqa: S105 (documented, local demo only)
DEMO_UNDERWRITER = "underwriter@example.com"
DEMO_APPLICANT = "applicant@example.com"
DEFAULT_UNTIL = date(2026, 10, 26)
RANDOM_SEED = 2026

# Amounts (rupees) and tenures (months) to draw from, inside each product's limits.
_TERMS: dict[Product, tuple[range, tuple[int, ...]]] = {
    Product.PERSONAL: (range(20_000, 250_001, 5_000), (6, 6, 9, 12, 12, 18)),
    Product.VEHICLE: (range(150_000, 1_000_001, 25_000), (36, 48, 60, 84)),
    Product.HOME: (range(1_000_000, 5_000_001, 100_000), (180, 240, 300)),
}
_PRODUCT_WEIGHTS = (6, 3, 1)  # personal, vehicle, home


@dataclass(frozen=True)
class Summary:
    applicants: int
    applications: int
    decisions: int
    loans: int
    closed_loans: int
    installments_paid: int


def seed(session: Session, *, until: date = DEFAULT_UNTIL, applicants: int = 60) -> Summary:
    """Replay the year that ends at 18:00 in India on `until`, and count what it created."""
    rng = Random(RANDOM_SEED)  # noqa: S311 (repeatable demo data, not security)
    end = _in_india(until, hour=18)
    start = end - timedelta(days=365)
    password_hash = hash_password(DEMO_PASSWORD)

    underwriter = User(email=DEMO_UNDERWRITER, password_hash=password_hash, role=Role.UNDERWRITER)
    borrowers = [_borrower(number, password_hash, rng) for number in range(1, applicants + 1)]
    session.add_all([underwriter, *borrowers])
    session.commit()

    simulation = _Simulation(session, rng, underwriter)
    for borrower in borrowers:
        for _ in range(rng.choice((1, 1, 2, 2, 3))):
            submitted_at = start + timedelta(minutes=rng.randrange(365 * 24 * 60))
            simulation.timeline.schedule(submitted_at, partial(simulation.apply, borrower))
    simulation.timeline.run_until(end)
    return _summarise(session)


def _borrower(number: int, password_hash: str, rng: Random) -> User:
    income = Decimal(rng.randrange(25_000, 250_001, 500))
    other_emis_share = Decimal(rng.choice((0, 0, 0, 5, 10, 20, 35))) / 100
    user = User(
        email=DEMO_APPLICANT if number == 1 else f"applicant{number:02d}@example.com",
        password_hash=password_hash,
        role=Role.APPLICANT,
    )
    user.applicant = Applicant(
        full_name=f"Demo Applicant {number:02d}",
        monthly_income=income,
        existing_emi=(income * other_emis_share).quantize(Decimal(1)),
    )
    return user


def _in_india(day: date, *, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=IST)


@dataclass(order=True)
class _Event:
    at: datetime
    order: int
    action: Callable[[datetime], None] = field(compare=False)


class _Timeline:
    """Timed actions run in time order; an action may schedule later ones."""

    def __init__(self) -> None:
        self._queue: list[_Event] = []
        self._scheduled = 0

    def schedule(self, at: datetime, action: Callable[[datetime], None]) -> None:
        self._scheduled += 1  # breaks ties between equal times in scheduling order
        heapq.heappush(self._queue, _Event(at, self._scheduled, action))

    def run_until(self, end: datetime) -> None:
        while self._queue and self._queue[0].at <= end:
            event = heapq.heappop(self._queue)
            event.action(event.at)


class _Simulation:
    """Each step calls the service the API would call, then schedules the next step."""

    def __init__(self, session: Session, rng: Random, underwriter: User) -> None:
        self.session = session
        self.rng = rng
        self.underwriter = underwriter
        self.timeline = _Timeline()

    def apply(self, borrower: User, at: datetime) -> None:
        product = self.rng.choices(list(Product), weights=_PRODUCT_WEIGHTS)[0]
        amounts, tenures = _TERMS[product]
        application = applications.submit(
            self.session,
            borrower,
            product=product,
            amount=Decimal(self.rng.choice(amounts)),
            tenure_months=self.rng.choice(tenures),
            now=at,
        )
        self.timeline.schedule(at + self._within(days=4), partial(self.review, application.id))

    def review(self, application_id: int, at: datetime) -> None:
        applications.start_review(self.session, self.underwriter, application_id, now=at)
        self.timeline.schedule(at + self._within(days=3), partial(self.decide, application_id))

    def decide(self, application_id: int, at: datetime) -> None:
        credit_score = min(900, max(300, round(self.rng.gauss(725, 65))))
        decision = decisions.decide(
            self.session, self.underwriter, application_id, credit_score=credit_score, now=at
        )
        if decision.outcome == DecisionOutcome.APPROVED:
            self.timeline.schedule(
                at + self._within(days=3), partial(self.disburse, application_id)
            )

    def disburse(self, application_id: int, at: datetime) -> None:
        loan, _ = loans.disburse(
            self.session,
            self.underwriter,
            application_id,
            idempotency_key=f"seed-{application_id}",
            now=at,
        )
        borrower = loan.application.applicant.user
        # Most borrowers pay every EMI on its due date; about one in ten stops after a few.
        paid = loan.tenure_months
        if self.rng.random() < 0.1:
            paid = self.rng.randrange(1, min(5, loan.tenure_months))
        for row in loan.repayments[:paid]:
            self.timeline.schedule(
                _in_india(row.due_date, hour=10),
                partial(self.pay, borrower, loan.id, row.installment_number),
            )

    def pay(self, borrower: User, loan_id: int, installment_number: int, at: datetime) -> None:
        loans.record_repayment(
            self.session, borrower, loan_id, installment_number=installment_number, now=at
        )

    def _within(self, *, days: int) -> timedelta:
        """Somewhere between an hour and `days` days."""
        return timedelta(minutes=self.rng.randrange(60, days * 24 * 60))


def _summarise(session: Session) -> Summary:
    def count(model: type[Base], *conditions: ColumnElement[bool]) -> int:
        return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    return Summary(
        applicants=count(Applicant),
        applications=count(Application),
        decisions=count(Decision),
        loans=count(Loan),
        closed_loans=count(Application, Application.status == ApplicationStatus.CLOSED),
        installments_paid=count(Repayment, Repayment.status == InstallmentStatus.PAID),
    )
