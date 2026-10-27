"""The README's measured numbers must match the code they describe."""

import re
from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.models import Base
from tests.test_seed import EXPECTED

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()


def _reported(label: str) -> int:
    match = re.search(rf"^\| {label} \| (\d+)", README, re.MULTILINE)
    assert match, f"the README has no '{label}' row"
    return int(match.group(1))


def test_the_endpoint_count_matches_the_openapi_schema(settings: Settings) -> None:
    paths = create_app(settings).openapi()["paths"]

    assert _reported("Endpoints") == sum(len(operations) for operations in paths.values())


def test_the_table_count_matches_the_models() -> None:
    assert _reported("Tables") == len(Base.metadata.tables)


def test_the_seeded_loan_count_matches_the_seed() -> None:
    assert _reported("Seeded loans") == EXPECTED.loans
