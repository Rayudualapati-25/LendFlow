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
  pattern. The row-locking test (D35) commits its own rows and deletes them afterwards.

### D18. Migrations run when the API container starts

- **Decision.** The container's start script runs `alembic upgrade head`, then `exec`s
  uvicorn so that it receives shutdown signals directly.
- **Alternative.** A separate one-off migration job that runs before the API starts.
- **Why.** `docker compose up` gives a working API with no extra step.
- **With hindsight.** With several API replicas, migrations should run once as a release
  step instead of racing in every replica.

## Authentication

### D19. Stateless JWT access tokens, 30 minutes, no refresh tokens

- **Decision.** `POST /auth/token` (the OAuth2 password flow, so Swagger's Authorize
  button works) returns an HS256-signed JWT carrying `sub`, `role`, `iat` and `exp`.
- **Alternative.** Server-side sessions in a table behind a cookie, or short access tokens
  paired with refresh tokens.
- **Why.** The plan asks for JWT, and a bearer token suits an API whose callers are other
  services or a single-page app. A short expiry limits what a leaked token can do.
- **With hindsight.** A token cannot be revoked before it expires (logout, compromised
  account). That needs a denylist of token IDs, or very short access tokens with
  refresh tokens stored server-side and rotated on use.

### D20. The token names the user; the role comes from the database on every request

- **Decision.** Each request loads the user by the token's `sub` and checks the role
  stored in the database. The `role` claim is informational, for clients.
- **Alternative.** Trust the role claim and skip the query.
- **Why.** A deleted user or a changed role takes effect immediately, for the price of
  one primary-key lookup.
- **With hindsight.** No change so far.

### D21. Argon2id password hashes, through pwdlib

- **Decision.** `pwdlib`'s recommended hasher: Argon2id at 64 MiB memory, 3 iterations,
  4 lanes.
- **Alternative.** bcrypt through passlib.
- **Why.** Argon2id is memory-hard and OWASP's first recommendation. passlib is no longer
  maintained, and bcrypt only uses the first 72 bytes of a password. pwdlib is what
  FastAPI's security tutorial uses.
- **With hindsight.** Hashing takes about 75 ms by design, so the tests hash their shared
  password once per run instead of once per user.

### D22. Sign-in does not reveal which accounts exist

- **Decision.** A wrong password and an unknown email get the same 401 and take about
  the same time: an unknown email is still checked against a dummy hash. Tokens must use
  HS256 (pinned, so `alg: none` is refused) and carry `sub`, `iat` and `exp`. The signing
  secret has no default and must be at least 32 characters, or the API will not start.
- **Alternative.** Return early for unknown emails, which is faster but lets an attacker
  learn which emails have accounts by timing the response.
- **Why.** Account enumeration is the first step of credential stuffing.
- **With hindsight.** Registration still reveals a taken email with a 409, the usual
  trade-off when there is no email-verification step. Rate limiting on `/auth/token` is
  missing and would be the next addition.

### D23. Applicants register themselves; underwriters cannot

- **Decision.** `POST /auth/register` always creates an applicant. Request bodies forbid
  unknown fields, so sending `"role": "underwriter"` is a 422. Underwriter accounts are
  provisioned outside the API, by an operator.
- **Alternative.** A role field on registration guarded by a check, or an admin endpoint.
- **Why.** Self-service privilege escalation is impossible by construction rather than
  by a check that could be forgotten.
- **With hindsight.** No change so far.

### D24. Business errors are exceptions with a status and a stable code

- **Decision.** Services raise `DomainError` subclasses, such as `EmailAlreadyRegistered`
  (409, `email_already_registered`). One exception handler renders every such error as
  `{"detail": ..., "code": ...}`.
- **Alternative.** Raise FastAPI's `HTTPException` inside services, or return result
  objects.
- **Why.** Services do not depend on FastAPI, so the seed script can call them. Clients
  get a machine-readable code, and every error has the same shape.
- **With hindsight.** The HTTP status sits on the exception class, a small leak of HTTP
  into the service layer. A mapping table in the API layer would be purer but is more
  code for no practical gain at this size.

## Business rules

### D25. Business rules are pure functions in `app/domain`

- **Decision.** The EMI maths, and later the eligibility rules and state machine, live in
  `app/domain` as plain functions on plain values: no database, no HTTP, no clock. The
  services in `app/services` load data, call them and save the results.
- **Alternative.** Rules inside the ORM models or the route handlers.
- **Why.** Pure rules are unit-tested in microseconds without a database, can be reused
  from scripts, and can be read in isolation.
- **With hindsight.** No change so far.

### D26. EMI rounded to the paisa; interest on the real balance; last instalment adjusts

- **Decision.** The EMI uses the standard reducing-balance formula, rounded half-up to
  the paisa. Each month, interest is the outstanding balance times the monthly rate
  (rounded to the paisa) and the rest of the EMI repays principal. The last instalment
  repays whatever is left. Due dates count from the disbursal date, clamped to short
  months (disbursed on 31 January: due 28 February, 31 March, 30 April).
- **Alternative.** Round the EMI up to the whole rupee, as many lenders do, or derive
  every row from the exact closed-form balance so rounding cannot accumulate.
- **Why.** Paisa precision matches what published EMI calculators show (Rs 10 lakh at
  10% for 60 months is Rs 21,247.04), and every interest figure stays explainable as
  "rate times what you owed".
- **With hindsight.** Property-based testing showed that the rounding drift compounds.
  For Rs 1,000 at 17.71% over 335 months it exceeds a whole EMI, so the loan would be
  repaid before its last month. The schedule now rejects such terms with a clear error. A
  second property test shows that any term up to 15% p.a. on Rs 10,000 or more over up to
  360 months schedules correctly; at that worst corner the final instalment differs from
  the EMI by at most Rs 37.84.

### D27. Property-based tests for money arithmetic

- **Decision.** Hypothesis generates (principal, rate, tenure) combinations and checks
  invariants: principal repaid adds up exactly to the amount lent, the balance ends at
  zero, every instalment is principal plus interest, all but the last equal the EMI, and
  the balance never increases.
- **Alternative.** Hand-picked examples only.
- **Why.** Examples check what the author thought of; invariants over generated inputs
  check what they did not. That is how the drift case in D26 was found.
- **With hindsight.** No change. Hand-checked examples are kept as well, because an
  invariant cannot tell you the EMI itself is right.

## Applications

### D28. A product catalogue in code; the rate is locked in at submission

- **Decision.** Three products (personal, vehicle, home), each with a fixed rate and
  amount and tenure limits, defined as a constant. Submitting copies the product's rate
  onto the application, and disbursal will copy it onto the loan.
- **Alternative.** A products table that operations staff can edit without a deploy, and
  risk-based pricing per applicant.
- **Why.** Rates change rarely and need sign-off, so a reviewed code change is an
  acceptable control at this size. Copying the rate means an applicant keeps the rate
  they were quoted, even if the catalogue changes while they wait.
- **With hindsight.** Real lenders reprice often and price by risk. That needs a table
  of effective-dated rates and pricing rules.

### D29. Money crosses the API as strings

- **Decision.** Requests accept amounts as numbers or strings, with at most two decimal
  places. Responses always send strings such as `"100000.00"`, which is Pydantic's
  default for `Decimal`.
- **Alternative.** Plain JSON numbers.
- **Why.** JavaScript and many JSON libraries read numbers into binary floats, which
  cannot hold most decimal amounts exactly. A string arrives exactly as sent.
- **With hindsight.** No change so far.

### D30. Other people's records answer 404, not 403

- **Decision.** An applicant asking for someone else's application gets the same 404
  `application_not_found` as for an ID that does not exist.
- **Alternative.** 403 Forbidden.
- **Why.** A 403 confirms that the record exists. With sequential IDs (D16), that would
  let anyone count the lender's applications.
- **With hindsight.** No change so far.

### D31. Offset pagination, newest first

- **Decision.** List endpoints take `limit` (1 to 100, default 50) and `offset`, sorted
  by creation time with the ID as a tie-breaker.
- **Alternative.** Keyset (cursor) pagination on `(created_at, id)`.
- **Why.** Simple to implement and use, and pages are small.
- **With hindsight.** Offset pages shift when new rows arrive and slow down as the offset
  grows. Keyset pagination over the existing `(status, created_at)` index fixes both.

### D32. Income and other loans are self-declared

- **Decision.** The applicant's profile (`PUT /applicants/me`) states monthly income and
  EMIs paid to other lenders. Nothing is verified.
- **Alternative.** Verify income from bank statements or payslips before deciding.
- **Why.** Verification needs integrations that are out of scope for this MVP.
- **With hindsight.** In India the natural next step is the RBI's Account Aggregator
  framework, which lets a lender fetch bank statements with the customer's consent.

## Lifecycle

### D33. Status changes are actions, not a writable field

- **Decision.** Each transition has its own endpoint: `POST /applications/{id}/review`
  now, with decision and disbursal endpoints to follow. No endpoint lets a client set
  `status`, and request bodies that try get a 422.
- **Alternative.** `PATCH /applications/{id}` with `{"status": "approved"}`.
- **Why.** Each transition has its own role, rules and side effects: deciding runs the
  eligibility rules, disbursing books a loan. A generic status setter would let a client
  skip them.
- **With hindsight.** No change so far.

### D34. One transition table, checked in one place

- **Decision.** `ALLOWED_TRANSITIONS` maps each status to the statuses it may move to,
  and `check_transition` is the only gate. Every status change goes through
  `lifecycle.move`, which checks, writes the audit entry and sets the status. Tests cover
  all 36 status pairs (5 legal, 31 illegal) against a list written independently of the
  table.
- **Alternative.** If/else checks inside each endpoint, or a state-machine library.
- **Why.** The whole lifecycle reads in six lines, and an endpoint cannot forget a check
  because it never sets the status itself. A library would add a dependency for a table.
- **With hindsight.** No change so far.

### D35. Transitions lock the application row

- **Decision.** Every transition loads the application with `SELECT ... FOR UPDATE`,
  refreshing any copy the session already holds, then checks the move, audits it and
  commits. A test with two real connections shows a second underwriter waiting for the
  first and then getting 409; with `FOR UPDATE` removed, that test fails.
- **Alternative.** Optimistic concurrency, using a version column and
  `UPDATE ... WHERE version = n`; or no protection at all.
- **Why.** Without it, two underwriters could act on the same application at once and
  approve or disburse it twice. For short transactions on one row, a pessimistic lock is
  the simplest thing to reason about.
- **With hindsight.** Optimistic locking suits long-lived edits, such as a form left
  open in a browser, because it holds no lock while the user thinks.
