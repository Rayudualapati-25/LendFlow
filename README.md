# LendFlow

A loan-origination and credit-decision API. It takes a loan from application to
disbursement and explains every approval or rejection with reason codes.

Built with FastAPI and PostgreSQL, test-first.

## Status

In progress. This README is the build plan; items are ticked as they land.

## MVP scope

- [ ] Schema: applicants, applications, decisions, loans, repayments, audit_log
      (SQLAlchemy 2 + Alembic migrations, indexes on status and dates)
- [ ] JWT sign-in with two roles: applicant and underwriter
- [ ] Eligibility rules (credit score, FOIR / EMI-to-income at most 50%, existing
      loans) that return a reason code for every decision
- [ ] Loan state machine: submitted → under review → approved or rejected →
      disbursed → closed; illegal transitions return 409
- [ ] Reducing-balance EMI schedule using `Decimal`; idempotency key on disbursement
- [ ] SQL reports with CTEs and window functions: approval rate by month,
      outstanding amount by product
- [ ] pytest suite with coverage (target 80%+), Docker Compose (API + Postgres),
      GitHub Actions CI on every push
- [ ] `DECISIONS.md`: each design choice, the alternative considered, and what
      would change with hindsight

## Stack

Python 3.11+ · FastAPI · PostgreSQL 16 · SQLAlchemy 2 · Alembic · Pydantic v2 ·
pytest · Docker Compose · GitHub Actions

## Quick start

```bash
docker compose up --build
```

The API listens on <http://localhost:8000>; interactive docs are at `/docs`.

## Development

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and PostgreSQL 16 for the tests.

```bash
uv sync                                   # virtualenv with runtime and dev dependencies
docker run -d --name lendflow-test-db -p 5432:5432 \
  -e POSTGRES_USER=lendflow -e POSTGRES_PASSWORD=lendflow \
  -e POSTGRES_DB=lendflow_test postgres:16-alpine
uv run pytest --cov                       # tests with branch coverage (80% floor)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The tests use `TEST_DATABASE_URL` (see `.env.example`) and default to the container above.
Design choices and their trade-offs are recorded in [DECISIONS.md](DECISIONS.md).

## Done when

- `docker compose up` starts the API and the database
- CI is green on every push
- This README reports measured numbers: endpoints, tests and coverage, tables,
  seeded loans
