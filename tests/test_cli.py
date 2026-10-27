import io

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main, run
from app.domain.enums import Role
from app.models import User
from app.security import verify_password
from app.seed import Summary
from tests.conftest import TEST_DATABASE_URL
from tests.factories import make_user


def test_create_underwriter_reads_the_password_from_stdin(db_session: Session) -> None:
    stdout = io.StringIO()

    code = run(
        ["create-underwriter", "Priya@Example.com"],
        db_session,
        stdin=io.StringIO("a strong passphrase\n"),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    user = db_session.scalars(select(User).where(User.email == "priya@example.com")).one()
    assert user.role == Role.UNDERWRITER
    assert verify_password("a strong passphrase", user.password_hash)
    assert "Created underwriter priya@example.com" in stdout.getvalue()


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("not-an-email", "a strong passphrase", "email"),
        ("priya@example.com", "short", "password"),
    ],
)
def test_create_underwriter_validates_like_registration(
    db_session: Session, email: str, password: str, message: str
) -> None:
    stderr = io.StringIO()

    code = run(
        ["create-underwriter", email],
        db_session,
        stdin=io.StringIO(password),
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 2
    assert message in stderr.getvalue()


def test_create_underwriter_refuses_a_taken_email(db_session: Session) -> None:
    make_user(db_session, email="taken@example.com")
    stderr = io.StringIO()

    code = run(
        ["create-underwriter", "taken@example.com"],
        db_session,
        stdin=io.StringIO("a strong passphrase"),
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert "already registered" in stderr.getvalue()


def test_seed_if_empty_leaves_an_existing_database_alone(db_session: Session) -> None:
    make_user(db_session)
    stdout = io.StringIO()

    code = run(
        ["seed", "--if-empty"],
        db_session,
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert "already has data" in stdout.getvalue()
    assert len(db_session.scalars(select(User)).all()) == 1


def test_main_connects_with_the_environment_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A validation failure exercises the real wiring without writing to the database.
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr("sys.argv", ["app.cli", "create-underwriter", "not-an-email"])
    monkeypatch.setattr("sys.stdin", io.StringIO("a strong passphrase"))

    assert main() == 2
    assert "email" in capsys.readouterr().err


def test_seed_reports_what_it_created(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed itself is tested in test_seed.py; here only the command's wiring and output.
    summary = Summary(
        applicants=2, applications=3, decisions=3, loans=1, closed_loans=0, installments_paid=4
    )
    monkeypatch.setattr("app.cli.seed", lambda session, until: summary)
    stdout = io.StringIO()

    code = run(["seed"], db_session, stdin=io.StringIO(), stdout=stdout, stderr=io.StringIO())

    assert code == 0
    assert stdout.getvalue() == (
        "Seeded 2 applicants, 3 applications, 3 decisions and 1 loans "
        "(0 closed, 4 instalments paid).\n"
    )


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_create_underwriter_prompts_without_echo_at_a_terminal(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("getpass.getpass", lambda prompt: "a strong passphrase")

    code = run(
        ["create-underwriter", "ops@example.com"],
        db_session,
        stdin=_Terminal(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    user = db_session.scalars(select(User).where(User.email == "ops@example.com")).one()
    assert verify_password("a strong passphrase", user.password_hash)
