from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.eligibility import RULES_VERSION, Facts, assess
from app.domain.emi import calculate_emi
from app.domain.enums import ApplicationStatus, DecisionOutcome
from app.errors import DecisionNotFound
from app.models import Applicant, Application, Decision, User
from app.services import applications
from app.services.lifecycle import lock_application, move

# Loans that already commit the applicant to monthly EMIs.
ACTIVE = (ApplicationStatus.APPROVED, ApplicationStatus.DISBURSED)


def decide(
    session: Session, underwriter: User, application_id: int, *, credit_score: int, now: datetime
) -> Decision:
    """Run the eligibility rules and approve or reject (under_review -> approved | rejected).

    The credit score is the bureau score the underwriter pulled for this applicant.
    """
    application = lock_application(session, application_id)
    # The rules read the applicant's other loans, so decisions for one applicant take turns:
    # two applications decided at once could otherwise both slip under the loan limit.
    applicant = session.scalars(
        select(Applicant)
        .where(Applicant.id == application.applicant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    active_loans = session.scalars(
        select(Application).where(
            Application.applicant_id == applicant.id,
            Application.id != application.id,
            Application.status.in_(ACTIVE),
        )
    ).all()

    lendflow_emis = sum((_emi(loan) for loan in active_loans), Decimal(0))
    facts = Facts(
        credit_score=credit_score,
        monthly_income=applicant.monthly_income,
        existing_emi=applicant.existing_emi + lendflow_emis,
        proposed_emi=_emi(application),
        active_loans=len(active_loans),
    )
    assessment = assess(facts)
    reason_codes = [str(code) for code in assessment.reason_codes]
    outcome = DecisionOutcome.APPROVED if assessment.approved else DecisionOutcome.REJECTED

    move(
        session,
        application,
        ApplicationStatus(outcome.value),
        actor=underwriter,
        action=f"application.{outcome.value}",
        details={"reason_codes": reason_codes},
    )
    decision = Decision(
        application=application,
        outcome=outcome,
        reason_codes=reason_codes,
        credit_score=facts.credit_score,
        monthly_income=facts.monthly_income,
        existing_emi=facts.existing_emi,
        proposed_emi=facts.proposed_emi,
        foir=assessment.foir,
        active_loans=facts.active_loans,
        rules_version=RULES_VERSION,
        decided_by_id=underwriter.id,
        decided_at=now,
    )
    session.add(decision)
    session.commit()
    return decision


def get_visible_to(session: Session, user: User, application_id: int) -> Decision:
    """The decision on an application this user may see; 404 if there is none yet."""
    application = applications.get_visible_to(session, user, application_id)
    if application.decision is None:
        raise DecisionNotFound(f"Application {application_id} has not been decided yet")
    return application.decision


def _emi(application: Application) -> Decimal:
    return calculate_emi(application.amount, application.annual_rate_pct, application.tenure_months)
