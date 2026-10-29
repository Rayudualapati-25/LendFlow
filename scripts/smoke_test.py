"""Check a running stack end to end: health, demo sign-in, seeded loans and both reports.

    python scripts/smoke_test.py [http://localhost:8000]

Uses only the standard library, so it runs on any machine with Python 3.
"""

import json
import sys
import urllib.parse
import urllib.request
from typing import Any

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
DEMO_PASSWORD = "lendflow-demo-password"  # noqa: S105 (the documented local demo password)


def call(path: str, *, token: str = "", form: dict[str, str] | None = None) -> Any:
    data = urllib.parse.urlencode(form).encode() if form else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(BASE + path, data=data, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)


def sign_in(email: str) -> str:
    token: str = call("/auth/token", form={"username": email, "password": DEMO_PASSWORD})[
        "access_token"
    ]
    return token


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"smoke test failed: {message}")


def main() -> None:
    check(call("/health") == {"status": "ok", "database": "ok"}, "API is not healthy")
    underwriter = sign_in("underwriter@example.com")
    months = call("/reports/approval-rate", token=underwriter)
    products = call("/reports/outstanding-by-product", token=underwriter)
    overdue = call("/reports/overdue-by-bucket", token=underwriter)
    loans = call("/loans", token=sign_in("applicant@example.com"))

    check(len(months) >= 12, f"expected a year of decisions, got {len(months)} months")
    check(
        {row["product"] for row in products} == {"personal", "vehicle", "home"},
        f"expected outstanding loans for every product, got {products}",
    )
    active = sum(row["active_loans"] for row in products)
    # Nothing is paid after the demo year ends, so every active loan is on yesterday's book.
    on_book = sum(row["loans"] for row in overdue["buckets"])
    check(on_book == active, f"overdue report has {on_book} loans, expected {active}")
    check(len(loans) >= 1, "the demo applicant should have a loan")
    print(
        f"OK: {len(months)} months of decisions, {active} active loans "
        f"(overdue report as of {overdue['as_of']} agrees), demo login works"
    )


if __name__ == "__main__":
    main()
