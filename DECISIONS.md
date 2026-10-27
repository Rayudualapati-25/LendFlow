# Design decisions

One entry per decision: what was chosen, the main alternative, why, and what would change
with hindsight. Entries are added in the pull request that makes the decision and revised
when later work proves them right or wrong.

## Service and tooling

### D1. Synchronous SQLAlchemy 2 behind FastAPI

- **Decision.** Path operations are plain `def` functions that use synchronous SQLAlchemy 2
  sessions over psycopg 3. FastAPI runs them in its thread pool.
- **Alternative.** `async def` endpoints with SQLAlchemy's asyncio extension and asyncpg.
- **Why.** Each request runs a few short queries and nothing else, so async adds little
  throughput here. It does add pitfalls: lazy loads raise errors in async sessions, and one
  blocking call inside `async def` stalls every request on the event loop. Synchronous
  code is easier to read, test and debug.
- **With hindsight.** No change so far. Async starts paying off once a request waits on
  slow external services, such as a credit bureau or KYC provider.

### D2. Application factory with injected settings

- **Decision.** `create_app(settings)` builds the app, its engine and its session factory.
  Uvicorn starts it with `--factory`; tests pass their own `Settings`.
- **Alternative.** A module-level `app` and engine configured from environment variables
  at import time.
- **Why.** Tests build an app pointed at the test database without patching environment
  variables, and importing a module has no side effects.
- **With hindsight.** No change so far.

### D3. Tests run against real PostgreSQL, not SQLite

- **Decision.** The suite needs a PostgreSQL 16 database (`TEST_DATABASE_URL`). CI starts
  one as a service container.
- **Alternative.** In-memory SQLite, which needs no setup.
- **Why.** The reports use PostgreSQL features (`FILTER`, window functions,
  `date_trunc`), and correctness depends on row locks and check constraints. A suite on
  SQLite could pass while production SQL fails.
- **With hindsight.** No change so far. The cost is one `docker run` (or a local
  PostgreSQL) before running tests.

### D4. uv with a committed lockfile

- **Decision.** Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.
  CI and the Docker build install with `uv sync --locked`, which fails if the lockfile is
  stale.
- **Alternative.** `pip` with a hand-maintained `requirements.txt`, or pip-tools.
- **Why.** One tool creates the virtualenv, resolves and locks, so CI, Docker and laptops
  install identical versions.
- **With hindsight.** No change so far.

### D5. Quality gates in CI: Ruff, mypy (strict) and an 80% branch-coverage floor

- **Decision.** Every push runs `ruff check`, `ruff format --check`, `mypy` in strict mode
  and `pytest` with branch coverage; coverage below 80% fails the build. Warnings are
  errors in tests, so deprecations surface immediately.
- **Alternative.** flake8 + black + isort, and coverage reported but not enforced.
- **Why.** Ruff replaces three tools with one fast one. A failing gate stops regressions;
  a number in a report only describes them.
- **With hindsight.** No change so far.

### D6. One health endpoint that checks the database

- **Decision.** `GET /health` runs `SELECT 1` and returns 503 when the database is
  unreachable. The Compose healthcheck and the CI smoke test call it.
- **Alternative.** Separate liveness (process is up) and readiness (dependencies are up)
  endpoints.
- **Why.** Docker Compose has one healthcheck per service, so one endpoint is enough.
- **With hindsight.** Under Kubernetes this should be split: restarting the API because
  the database is down does not fix the database.

### D7. Two-stage Docker image running as a non-root user

- **Decision.** A build stage installs the locked dependencies into a virtualenv; the
  runtime stage copies only that virtualenv and the code, and runs as an unprivileged
  user.
- **Alternative.** A single stage that keeps uv and pip in the final image.
- **Why.** The runtime image carries no build tooling (it dropped from 366 MB to 272 MB),
  and a compromised process does not run as root.
- **With hindsight.** No change so far.

## Data model

### D8. Logins (`users`) are separate from borrower profiles (`applicants`)

- **Decision.** `users` holds the email, password hash and role. `applicants` holds the
  borrower profile and points at exactly one applicant user. This makes seven tables, not
  the six in the plan.
- **Alternative.** Credentials on `applicants` plus a separate underwriters table, or one
  table with profile columns that are empty for underwriters.
- **Why.** Underwriters need logins but have no borrower profile. Authentication reads
  one table whatever the role, and credentials and profiles change for different reasons.
- **With hindsight.** No change so far.

### D9. One status column covers the whole loan lifecycle

- **Decision.** `applications.status` runs from `submitted` to `closed`. The `loans` row,
  the booked contract, is created at the `approved → disbursed` transition.
- **Alternative.** The usual split between origination and servicing: an application
  status that ends at approved or rejected, and a separate loan-account status.
- **Why.** The plan describes one state machine, and one column gives one place to
  enforce transitions and take the row lock.
- **With hindsight.** Once servicing grows (overdue buckets, foreclosure, restructuring),
  the loan account deserves its own status and state machine.

### D10. Money is `NUMERIC(12, 2)` in the database and `Decimal` in Python

- **Decision.** Amounts are rupees with two decimals; interest rates are percent per
  annum as `NUMERIC(5, 2)`; FOIR is `NUMERIC(12, 4)`. Floats never touch money.
- **Alternative.** Integer paise in `BIGINT`, which is also exact; or floats, which are
  not (`0.1 + 0.2 != 0.3`).
- **Why.** Exact arithmetic with rounding chosen explicitly, and values that read
  naturally in SQL reports.
- **With hindsight.** No change so far.

### D11. Enums are `VARCHAR` plus a `CHECK` constraint, not native PostgreSQL enums

- **Decision.** SQLAlchemy `Enum(native_enum=False, create_constraint=True)`, storing
  lowercase values such as `under_review`.
- **Alternative.** `CREATE TYPE ... AS ENUM`.
- **Why.** Changing a CHECK constraint is a drop and an add in one migration. Native enum
  values can be added and renamed but never removed without rebuilding the type.
- **With hindsight.** Alembic's autogenerate does not notice a changed CHECK constraint,
  so adding a status needs a hand-written migration step. Still worth it.

### D12. The database enforces the invariants too

- **Decision.** CHECK and UNIQUE constraints cover the rules that must never break:
  positive amounts, tenure of 1 to 360 months, credit score of 300 to 900, at least one
  reason code per decision, one decision and one loan per application, unique idempotency
  keys, instalment = principal + interest, and `paid_at` set exactly when an instalment
  is paid. A naming convention gives every constraint a predictable name that tests check.
- **Alternative.** Validate only in Pydantic models and service code.
- **Why.** Application code has bugs, and scripts or other services may write to the
  same tables; the database is the last line of defence. The reason-code check uses
  `cardinality(reason_codes) > 0`: `array_length` returns NULL for an empty array, and a
  CHECK that evaluates to NULL passes.
- **With hindsight.** No change so far.

### D13. A decision stores the inputs it was made on

- **Decision.** Each decision records the credit score, income, existing obligations,
  proposed EMI, FOIR, active-loan count, rules version, who decided and when.
- **Alternative.** Store only the outcome and reason codes, and recompute on demand.
- **Why.** Profiles change after a decision. Explaining it later, to an auditor or a
  customer disputing it, needs what the rules saw at the time.
- **With hindsight.** No change so far.

### D14. `repayments` has one row per instalment

- **Decision.** The whole EMI schedule is written at disbursal. Each row holds its
  principal and interest split, closing balance and paid status.
- **Alternative.** Separate schedule and payment tables, with payments allocated to
  instalments.
- **Why.** The MVP accepts only whole EMIs, so one payment always settles exactly one
  instalment.
- **With hindsight.** Partial payments, prepayments or late fees need payments as their
  own records and an allocation step.

### D15. The audit log is keyed by application, with a foreign key

- **Decision.** `audit_log` rows reference `applications.id` and record the action,
  actor, previous and new status, JSON details and time. They are written in the same
  transaction as the change they describe.
- **Alternative.** A generic `(entity_type, entity_id)` log without a foreign key.
- **Why.** Every audited event in the MVP belongs to one application's lifecycle, so a
  real foreign key keeps integrity, and an application's history is one indexed lookup.
- **With hindsight.** Auditing events outside an application (role changes, sign-ins)
  needs the generic shape. Append-only is a convention here; in production the
  application's database role would have INSERT but not UPDATE or DELETE on this table.

### D16. `BIGINT` identity primary keys

- **Decision.** `BIGINT GENERATED BY DEFAULT AS IDENTITY` on every table.
- **Alternative.** UUIDs, random (v4) or time-ordered (v7).
- **Why.** Compact, fast indexes, and IDs that are easy to read in demos and tests.
  Sequential IDs are guessable, so every endpoint checks ownership and answers 404 for
  other people's records.
- **With hindsight.** Sequential IDs also reveal volume. A public API should expose
  UUIDv7s and keep integers internal.

### D17. Tests run on a migrated database, each inside a rolled-back transaction

- **Decision.** The suite builds the schema once per run with `alembic upgrade head` on
  an empty database. Each test runs inside a transaction that is rolled back afterwards;
  a commit in the code under test only releases a SAVEPOINT. One test checks that the
  models and migrations match, another downgrades to empty and upgrades again.
- **Alternative.** `Base.metadata.create_all()` and truncating tables after each test.
- **Why.** Every run exercises the real migrations, and rollback isolates tests faster
  than truncation. Because the suite drops the schema, it refuses to run unless the
  database name ends in `_test`.
- **With hindsight.** A test that needs two real concurrent connections cannot use this
  pattern and must commit and clean up its own data.

### D18. Migrations run when the API container starts

- **Decision.** The container's start script runs `alembic upgrade head`, then `exec`s
  uvicorn so that it receives shutdown signals directly.
- **Alternative.** A separate one-off migration job that runs before the API starts.
- **Why.** `docker compose up` gives a working API with no extra step.
- **With hindsight.** With several API replicas, migrations should run once as a release
  step instead of racing in every replica.
