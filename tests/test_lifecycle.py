"""One loan through the whole API: sign-up to closure, as an applicant and an underwriter."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import Role
from tests.factories import make_user
from tests.helpers import auth_headers

PASSWORD = "a long enough passphrase"


def test_a_loan_from_application_to_closure(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    client.post("/auth/register", json={"email": "meera@example.com", "password": PASSWORD})
    token = client.post(
        "/auth/token", data={"username": "meera@example.com", "password": PASSWORD}
    ).json()["access_token"]
    borrower = {"Authorization": f"Bearer {token}"}
    underwriter = auth_headers(make_user(db_session, role=Role.UNDERWRITER), settings)

    client.put(
        "/applicants/me",
        json={"full_name": "Meera Iyer", "monthly_income": "90000", "existing_emi": "0"},
        headers=borrower,
    )
    application = client.post(
        "/applications",
        json={"product": "personal", "amount": "60000", "tenure_months": 6},
        headers=borrower,
    ).json()
    path = f"/applications/{application['id']}"

    assert client.post(f"{path}/review", headers=underwriter).json()["status"] == "under_review"
    decision = client.post(f"{path}/decision", json={"credit_score": 780}, headers=underwriter)
    assert decision.json()["reason_codes"] == ["ELIGIBLE"]
    loan = client.post(
        f"{path}/disburse", headers=underwriter | {"Idempotency-Key": "meera-1"}
    ).json()
    for number in range(1, 7):
        loan = client.post(
            f"/loans/{loan['id']}/repayments", json={"installment_number": number}, headers=borrower
        ).json()

    assert loan["status"] == "closed"
    assert client.get(path, headers=borrower).json()["status"] == "closed"
    trail = client.get(f"{path}/audit", headers=underwriter).json()
    assert [(entry["action"], entry["to_status"]) for entry in trail] == [
        ("application.submitted", "submitted"),
        ("application.review_started", "under_review"),
        ("application.approved", "approved"),
        ("loan.disbursed", "disbursed"),
        *[("repayment.received", None)] * 6,
        ("loan.closed", "closed"),
    ]
