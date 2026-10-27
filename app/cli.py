"""Operator commands. Run them inside the API container or a configured virtualenv:

python -m app.cli seed [--if-empty] [--until YYYY-MM-DD]
python -m app.cli create-underwriter EMAIL      (prompts for the password, or reads stdin)
"""

import argparse
import getpass
import sys
from collections.abc import Sequence
from datetime import date
from typing import TextIO

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DatabaseSettings
from app.db import make_engine
from app.domain.enums import Role
from app.errors import EmailAlreadyRegistered
from app.models import User
from app.schemas import RegisterRequest
from app.seed import DEFAULT_UNTIL, seed
from app.services import users


def main() -> int:
    engine = make_engine(DatabaseSettings().database_url)
    with Session(engine, expire_on_commit=False) as session:
        return run(sys.argv[1:], session, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)


def run(
    argv: Sequence[str], session: Session, *, stdin: TextIO, stdout: TextIO, stderr: TextIO
) -> int:
    """Run one command and return the process exit code."""
    args = _parser().parse_args(argv)
    if args.command == "seed":
        return _seed(session, until=args.until, if_empty=args.if_empty, stdout=stdout)
    return _create_underwriter(
        session, args.email, _read_password(stdin), stdout=stdout, stderr=stderr
    )


def _read_password(stdin: TextIO) -> str:
    """Prompt without echo at a terminal; otherwise read one line, so it can be piped in."""
    if stdin.isatty():
        return getpass.getpass("Password: ")
    return stdin.readline().rstrip("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed_command = commands.add_parser("seed", help="load a year of demo data (local use only)")
    seed_command.add_argument(
        "--if-empty", action="store_true", help="do nothing if any user already exists"
    )
    seed_command.add_argument(
        "--until",
        type=date.fromisoformat,
        default=DEFAULT_UNTIL,
        help="last day of the demo year (default: %(default)s)",
    )
    create = commands.add_parser("create-underwriter", help="create an underwriter login")
    create.add_argument("email")
    return parser


def _seed(session: Session, *, until: date, if_empty: bool, stdout: TextIO) -> int:
    if if_empty and session.scalar(select(User.id).limit(1)) is not None:
        print("The database already has data; not seeding.", file=stdout)
        return 0
    summary = seed(session, until=until)
    print(
        f"Seeded {summary.applicants} applicants, {summary.applications} applications, "
        f"{summary.decisions} decisions and {summary.loans} loans "
        f"({summary.closed_loans} closed, {summary.installments_paid} instalments paid).",
        file=stdout,
    )
    return 0


def _create_underwriter(
    session: Session, email: str, password: str, *, stdout: TextIO, stderr: TextIO
) -> int:
    try:
        credentials = RegisterRequest(email=email, password=password)
    except ValidationError as error:
        for problem in error.errors():
            print(f"{problem['loc'][0]}: {problem['msg']}", file=stderr)
        return 2
    try:
        user = users.create_user(
            session, email=credentials.email, password=credentials.password, role=Role.UNDERWRITER
        )
    except EmailAlreadyRegistered as error:
        print(error.message, file=stderr)
        return 1
    print(f"Created underwriter {user.email} (id {user.id}).", file=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
