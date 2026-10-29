"""Management reports, written as plain SQL (CTEs and window functions) in .sql files.

The SQL lives in its own files so it can be read, reviewed and run in psql as it is.
"""

from datetime import date
from pathlib import Path

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

_SQL = Path(__file__).parent
APPROVAL_RATE_BY_MONTH = (_SQL / "approval_rate_by_month.sql").read_text()
OUTSTANDING_BY_PRODUCT = (_SQL / "outstanding_by_product.sql").read_text()
OVERDUE_BY_BUCKET = (_SQL / "overdue_by_bucket.sql").read_text()


def approval_rate_by_month(session: Session) -> list[RowMapping]:
    return list(session.execute(text(APPROVAL_RATE_BY_MONTH)).mappings())


def outstanding_by_product(session: Session) -> list[RowMapping]:
    return list(session.execute(text(OUTSTANDING_BY_PRODUCT)).mappings())


def overdue_by_bucket(session: Session, *, as_of: date) -> list[RowMapping]:
    return list(session.execute(text(OVERDUE_BY_BUCKET), {"as_of": as_of}).mappings())
