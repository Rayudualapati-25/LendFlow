# LendFlow

A loan-origination and credit-decision API. It takes a loan from application to
disbursement and explains every approval or rejection with reason codes.

Built with FastAPI and PostgreSQL, test-first.

## Status

MVP complete: every item below is built, tested and running in CI. Measured on this
repository:

| Measure | Value |
|---|---|
| Endpoints | 20 |
| Tests | 260, with 99.7% branch coverage |
| Tables | 7 |
| Seeded loans | 74 |

Endpoints are counted from the OpenAPI schema, tables from the SQLAlchemy models, and the
seeded loans from the demo data (60 applicants, 112 applications, 108 decisions, 74 loans of
which 8 are repaid and closed). A test checks the endpoint, table and seeded-loan figures
against the code, so they cannot drift.

## MVP scope

- [x] Schema: applicants, applications, decisions, loans, repayments, audit_log
      (SQLAlchemy 2 + Alembic migrations, indexes on status and dates)
- [x] JWT sign-in with two roles: applicant and underwriter
- [x] Eligibility rules (credit score, FOIR / EMI-to-income at most 50%, existing
      loans) that return a reason code for every decision
- [x] Loan state machine: submitted → under review → approved or rejected →
      disbursed → closed; illegal transitions return 409
- [x] Reducing-balance EMI schedule using `Decimal`; idempotency key on disbursement
- [x] SQL reports with CTEs and window functions: approval rate by month,
      outstanding amount by product
- [x] pytest suite with coverage (target 80%+), Docker Compose (API + Postgres),
      GitHub Actions CI on every push
- [x] `DECISIONS.md`: each design choice, the alternative considered, and what
      would change with hindsight

## How it works

```
submitted ──review──▶ under_review ──decision──▶ approved ──disburse──▶ disbursed ──last EMI paid──▶ closed
                                          └───────────▶ rejected
```

- **Applicants** register, declare their income and other EMIs, and apply for a personal,
  vehicle or home loan. Each application quotes the product's rate and the EMI.
- **Underwriters** take an application into review, then decide it with the applicant's
  bureau credit score. Three rules run every time: credit score at least 650, FOIR (all
  monthly EMIs over income) at most 50%, and fewer than two active loans. Every decision
  stores its reason codes (`ELIGIBLE` for approvals) and the inputs the rules saw.
- **Disbursal** books the loan and its reducing-balance EMI schedule. It requires an
  `Idempotency-Key`, so a retried request returns the original loan instead of paying out
  twice. Borrowers then pay instalments in order; the last payment closes the loan.
- Every status change goes through one transition table, locks the application row and
  writes an audit entry in the same transaction. Any other move is a 409.

## API

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | anyone | API and database health |
| POST | `/auth/register` | anyone | create an applicant account |
| POST | `/auth/token` | anyone | sign in (OAuth2 password flow) for a JWT |
| GET | `/auth/me` | signed in | the current user |
| GET | `/products` | anyone | loan products with their rates and limits |
| GET, PUT | `/applicants/me` | applicant | read or save the applicant profile |
| POST | `/applications` | applicant | apply for a loan |
| GET | `/applications` | signed in | own applications, or all for underwriters |
| GET | `/applications/{id}` | owner, underwriter | one application with its EMI quote |
| POST | `/applications/{id}/review` | underwriter | start the review |
| POST | `/applications/{id}/decision` | underwriter | run the eligibility rules |
| GET | `/applications/{id}/decision` | owner, underwriter | the decision and its reason codes |
| POST | `/applications/{id}/disburse` | underwriter | book the loan (`Idempotency-Key` required) |
| GET | `/applications/{id}/audit` | underwriter | every change to the application |
| GET | `/loans` | signed in | own loans, or all for underwriters |
| GET | `/loans/{id}` | owner, underwriter | the loan and its EMI schedule |
| POST | `/loans/{id}/repayments` | owner, underwriter | pay an instalment |
| GET | `/reports/approval-rate` | underwriter | approval rate by month, rolling and month on month |
| GET | `/reports/outstanding-by-product` | underwriter | principal outstanding by product, share and rank |

Errors share one shape, `{"detail": "...", "code": "..."}`, with a stable code such as
`illegal_transition` or `idempotency_key_reused`. Money is sent as strings (`"8978.71"`).
The full schema is at `/docs` on a running stack.

## Stack

Python 3.11+ · FastAPI · PostgreSQL 16 · SQLAlchemy 2 · Alembic · Pydantic v2 ·
pytest · Docker Compose · GitHub Actions

## Quick start

```bash
docker compose up --build
```

The API listens on <http://localhost:8000>; interactive docs are at `/docs`. On first start
the container migrates the database and loads a year of demo data (60 applicants, 74 loans),
replayed through the same code as the API. Sign in from `/docs` with **Authorize**:

| Role | Email | Password |
|---|---|---|
| Underwriter | `underwriter@example.com` | `lendflow-demo-password` |
| Applicant | `applicant@example.com` | `lendflow-demo-password` |

The demo applicant's first request (₹2,10,000 over 6 months) was refused with
`FOIR_ABOVE_LIMIT`; the same amount over 12 months was approved and disbursed. These
credentials exist only in the local demo data. `python scripts/smoke_test.py` checks a running
stack end to end.

Create a real underwriter account with `docker compose exec api python -m app.cli
create-underwriter EMAIL`; it prompts for the password without echoing it.

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

```
app/
  domain/      pure business rules: EMI, eligibility, state machine, products
  services/    one function per use case, one transaction each
  routers/     HTTP endpoints
  reports/     the two SQL reports, as .sql files
  models.py    SQLAlchemy tables
  seed.py      demo data, replayed through the services
  cli.py       operator commands
migrations/    Alembic
scripts/       container start-up and the end-to-end smoke test
tests/         pytest: unit, API, concurrency, property-based and report tests
```

Design choices and their trade-offs are recorded in [DECISIONS.md](DECISIONS.md).

## Done when

- [x] `docker compose up` starts the API and the database (checked by CI on every push)
- [x] CI is green on every push
- [x] This README reports measured numbers: endpoints, tests and coverage, tables,
      seeded loans
