# Design decisions

One entry per decision: what was chosen, the main alternative, why, and what would change
with hindsight. Entries are added in the pull request that makes the decision and revised
when later work proves them right or wrong.

Contents: [Service and tooling](#service-and-tooling) (D1–D7) ·
[Data model](#data-model) (D8–D18) · [Authentication](#authentication) (D19–D24, D53–D54) ·
[Business rules](#business-rules) (D25–D27) · [Applications](#applications) (D28–D32) ·
[Lifecycle](#lifecycle) (D33–D35) · [Credit decisions](#credit-decisions) (D36–D40) ·
[Disbursal and repayment](#disbursal-and-repayment) (D41–D43, D50–D52) ·
[Reports](#reports) (D44–D46, D49) · [Demo data and operations](#demo-data-and-operations)
(D47–D48)

## Service and tooling

### D1. Synchronous SQLAlchemy 2 behind FastAPI

- **Decision.** Path operations are plain `def` functions that use synchronous SQLAlchemy 2
  sessions over psycopg 3. FastAPI runs them in its thread pool.
- **Alternative.** `async def` endpoints with SQLAlchemy's asyncio extension and asyncpg.
- **Why.** Each request runs a few short queries and nothing else, so async adds little
  throughput here. It does add pitfalls: lazy loads raise errors in async sessions, and one
  blocking call inside `async def` stalls every request on the event loop. Synchronous
  code is easier to read, test and debug.
- **With hindsight.** No change. Synchronous sessions also kept the row locks simple: a
  request holds one connection for its whole transaction, which the concurrency tests
  rely on. Async starts paying off once a request waits on slow external services, such
  as a credit bureau or KYC provider.

### D2. Application factory with injected settings

- **Decision.** `create_app(settings)` builds the app, its engine and its session factory.
  Uvicorn starts it with `--factory`; tests pass their own `Settings`.
- **Alternative.** A module-level `app` and engine configured from environment variables
  at import time.
- **Why.** Tests build an app pointed at the test database without patching environment
  variables, and importing a module has no side effects.
- **With hindsight.** No change. Tests also use it to add their own dependency overrides,
  such as a frozen clock (D40).

### D3. Tests run against real PostgreSQL, not SQLite

- **Decision.** The suite needs a PostgreSQL 16 database (`TEST_DATABASE_URL`). CI starts
  one as a service container.
- **Alternative.** In-memory SQLite, which needs no setup.
- **Why.** The reports use PostgreSQL features (`FILTER`, window functions,
  `date_trunc`), and correctness depends on row locks and check constraints. A suite on
  SQLite could pass while production SQL fails.
- **With hindsight.** Worth it. The constraint tests, the two-connection locking tests,
  and the reports' `FILTER`, `generate_series` and `AT TIME ZONE` (the IST month boundary
  in D45) could not have been tested on SQLite. The cost is one `docker run`, or a local
  PostgreSQL, before running tests.

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
- **With hindsight.** No change. Treating warnings as errors paid off on the first day:
  Starlette's test client had moved from `httpx` to `httpx2`, and the build said so at
  once instead of breaking later.

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
- **With hindsight.** Prepayments became their own records (D50) without an allocation
  step, because they repay principal only. Partial EMIs and late fees would still need
  payments as their own records, allocated across what is due.

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
- **With hindsight.** An access token cannot be revoked before it expires. Refresh
  tokens stored server-side and rotated on use (D53) now carry the sign-in, so access
  tokens stay short and stateless, and logout takes effect within their 30 minutes.

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
  trade-off when there is no email-verification step. Failed sign-ins are now limited
  per email (D54), and unknown emails are limited the same way, so the limit leaks
  nothing either.

### D23. Applicants register themselves; underwriters cannot

- **Decision.** `POST /auth/register` always creates an applicant. Request bodies forbid
  unknown fields, so sending `"role": "underwriter"` is a 422. Underwriter accounts are
  created with an operator command (D48).
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

### D53. Refresh tokens are opaque, stored as digests and rotated; reuse ends the sign-in

- **Decision.** Signing in also returns a refresh token: 256 random bits
  (`secrets.token_urlsafe`), stored only as a SHA-256 digest. `POST /auth/refresh`
  exchanges it for a new access token and the next refresh token, and each one works
  once. All the tokens of one sign-in share a `family_id`. Presenting a token that was
  already exchanged revokes the whole family (401 `refresh_token_reused`): two parties
  hold it, and the server cannot tell the client from the thief
  ([RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html), section 4.14.2). A refresh
  token dies after a day unused, and a sign-in lasts at most seven days however often it
  is refreshed: an idle and an absolute timeout, as OWASP recommends for every session.
  `POST /auth/logout` revokes the family and answers 204 even for unknown tokens. The
  token's row is locked while it is exchanged, so two requests with one token take
  turns; a test on two connections shows the second one ending the sign-in.
- **Alternative.** Refresh tokens as JWTs; longer-lived access tokens; or RFC 6749's
  `grant_type=refresh_token` on the token endpoint.
- **Why.** A refresh token has to be revocable, so it lives in the database anyway, and
  an opaque random string carries nothing to forge or decode. A fast digest is enough
  because the token is random; passwords need Argon2 only because people choose them.
  Rotation turns a stolen token into a detected event instead of a silent second
  session. A separate JSON endpoint leaves the OAuth2 password form, and Swagger's
  Authorize button, unchanged.
- **With hindsight.** Strict reuse detection signs a user out when one client refreshes
  twice in parallel (two browser tabs); accepting the previous token for a few seconds
  is the usual fix. Logout does not end access tokens already issued: they lapse within
  30 minutes, which is why they are short. A `sid` claim checked against revoked
  sign-ins would end them at once, for one more lookup per request.

### D54. Five failed sign-ins for an email in 15 minutes lock it, counted in the database

- **Decision.** Each failed sign-in is stored with the email as typed (normalised) and
  its time. While an email has five failures in the last 15 minutes, every sign-in for
  it is refused with 429 `too_many_sign_in_attempts` and a `Retry-After` header, even with
  the right password, until the oldest of them is 15 minutes old. Refused attempts are
  not counted, so they do not extend the lock; a successful sign-in clears the email's
  failures; failures older than the window are deleted as new ones arrive. Attempts for
  one email take turns behind a transaction-scoped advisory lock on a hash of the email
  (`pg_advisory_xact_lock`), so parallel guesses cannot all count the same four failures.
  A test on two connections shows the second guess waiting and then being refused, and
  fails if the lock is removed. Unknown emails are limited exactly like real ones.
- **Alternative.** An in-memory or Redis rate limiter keyed by client IP, such as
  slowapi; a lockout flag on the user row; a CAPTCHA after a few failures.
- **Why.** The threat here is guessing one account's password, and a count per email
  stops it wherever the guesses come from. The database is shared by every API instance,
  unlike process memory, and is already there. A flag on `users` would exist only for
  real accounts, so a 429 would reveal them, undoing D22. A sliding window lets the owner
  back in within 15 minutes without asking support to unlock the account.
- **With hindsight.** Anyone who knows a customer's email can lock them out for 15
  minutes at a time, the usual price of limits per account. Password spraying (one guess
  each against many accounts) is not slowed at all; that needs a limit per client IP at
  the edge, where the real IP is known. At high volume the failures belong in Redis, with
  keys that expire on their own.

## Business rules

### D25. Business rules are pure functions in `app/domain`

- **Decision.** The EMI maths, and later the eligibility rules and state machine, live in
  `app/domain` as plain functions on plain values: no database, no HTTP, no clock. The
  services in `app/services` load data, call them and save the results.
- **Alternative.** Rules inside the ORM models or the route handlers.
- **Why.** Pure rules are unit-tested in microseconds without a database, can be reused
  from scripts, and can be read in isolation.
- **With hindsight.** No change. Purity is what made the property-based tests (D27, D38)
  cheap enough to run thousands of generated cases.

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
  onto the application, and disbursal copies it onto the loan.
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

- **Decision.** Each transition has its own endpoint: `review`, `decision` and `disburse`
  under `POST /applications/{id}/`, and closure happens when the last instalment is paid
  through `POST /loans/{id}/repayments`. No endpoint lets a client set `status`, and
  request bodies that try get a 422.
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

## Credit decisions

### D36. The credit score arrives with the decision, from the underwriter

- **Decision.** `POST /applications/{id}/decision` takes the bureau score (CIBIL or
  similar) that the underwriter pulled. Applicants never supply a score.
- **Alternative.** A credit-score field on the applicant's profile, or a live bureau
  integration.
- **Why.** A self-reported score cannot be trusted, and a bureau integration is out of
  scope. In a manual process, pulling the report is the underwriter's step. The decision
  stores the score it used.
- **With hindsight.** In production a bureau client sits behind an interface, with a
  fake in tests, and the endpoint takes no score at all.

### D37. Every rule runs and every decision carries reason codes

- **Decision.** Three rules run together: credit score at least 650, FOIR at most 50%,
  and fewer than two active LendFlow loans. A rejection lists every failed rule in a
  fixed order; an approval carries `ELIGIBLE`. A CHECK constraint (D12) means no decision
  row can exist without a code, and each decision records the rules version it ran under.
  Applicants can read their own decision and its reasons.
- **Alternative.** Stop at the first failed rule, or reject with one generic message.
- **Why.** The applicant learns everything to fix at once, and stable codes let a client
  show its own wording. Indian lenders have long been expected to tell applicants the
  main reasons for a rejection: RBI's Fair Practices Code for Lenders, extended to all
  loan categories in 2007
  ([circular](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=3315)).
- **With hindsight.** A real credit policy has more rules (age, employment,
  loan-to-value for home and vehicle loans) and per-product thresholds. The stored rules
  version is what makes changing them safe.

### D38. "FOIR at most 50%" is compared exactly; the stored FOIR is rounded up

- **Decision.** The rule compares `existing EMIs + new EMI <= income x 0.50` without
  rounding. The FOIR stored and shown is rounded up to four places.
- **Alternative.** Round the ratio first and compare the rounded value.
- **Why.** Rounding before comparing would let 50.004% pass as 50.00%. Rounding the
  shown value up means anything refused for FOIR shows at least 0.5001, so a FOIR of
  0.5000 never appears next to a FOIR refusal. A property test checks that the shown FOIR
  exceeds 0.50 exactly when the FOIR rule failed.
- **With hindsight.** No change so far.

### D39. Existing obligations include the applicant's other LendFlow loans

- **Decision.** Existing EMIs are the declared EMIs to other lenders plus the EMIs of the
  applicant's LendFlow loans that are approved or disbursed; approved ones count because
  the lender is already committed to them. A decision locks the applicant's row as well
  as the application's.
- **Alternative.** Count only the declared EMIs, or only disbursed loans.
- **Why.** An applicant with two approvals in flight must not look debt-free to a third
  decision. Without the applicant lock, two of their applications decided at the same
  moment could both pass the active-loan limit.
- **With hindsight.** A bureau report lists loans across all lenders, which would
  replace the self-declared figure.

### D40. Time is an input

- **Decision.** Services take `now` as a parameter; the API supplies it from a
  `current_time` dependency that tests override. Every state change and its audit entry
  take their timestamps from it, not from the database's `now()`. Database connections use
  the UTC time zone, so timestamps come back in UTC whatever the server's setting.
- **Alternative.** Call `datetime.now()` inside services and freeze time in tests with a
  library such as freezegun.
- **Why.** Explicit inputs keep services deterministic, and the seed script can backdate
  decisions and disbursals without patching anything.
- **With hindsight.** The first version left submission, review and audit times to the
  database's `now()`. Planning a seed that replays a year of history showed that a mix of
  two clocks produces audit trails out of order with the decisions they explain, so every
  state change now takes the injected clock.

## Disbursal and repayment

### D41. An idempotency key on disbursal, stored on the loan

- **Decision.** `POST /applications/{id}/disburse` needs an `Idempotency-Key` header
  (400 without one). The key is stored on the loan under a unique index. A retry with the
  same key returns the original loan (201 with `Idempotent-Replayed: true`); the same key
  on another application is a 422; a new key on an already-disbursed application is a 409.
  Two mechanisms make this hold under concurrency: the row lock (D35) makes a retry wait
  for the first request and then find its loan, and the unique index stops one key booking
  loans on two applications. Each has a test with two real connections.
- **Alternative.** A generic table of keys, request fingerprints and stored responses for
  every POST; or no key at all, relying on approved to disbursed being one-way.
- **Why.** Disbursal pays money out. A client retrying after a timeout must not pay twice
  and should learn what the first attempt did; without a key, the status check alone would
  turn the retry into a confusing 409. Only this endpoint needs the guarantee, so a column
  on the booked loan is enough. Status codes follow the IETF draft on the
  `Idempotency-Key` header; the replay header follows Stripe.
- **With hindsight.** A generic key store pays off once several endpoints need one, such as
  payments arriving from a gateway, and its keys should expire after a set period.

### D42. A repayment names the instalment it settles

- **Decision.** `POST /loans/{id}/repayments` with `{"installment_number": n}`.
  Instalments are paid in order (409 `installment_out_of_order` otherwise) and once (409
  `installment_already_paid`). Paying the last one moves the loan to closed. The borrower
  pays their own instalments; an underwriter can record a payment received another way.
- **Alternative.** "Pay the next instalment" without naming it, or free-form amounts
  allocated to whatever is due.
- **Why.** Naming the instalment makes a retried payment a harmless 409 instead of a second
  charge: idempotent by design without another key. Whole-EMI payments match D14.
- **With hindsight.** Overdue tracking (D49) and prepayment (D50) fitted without changing
  this: days past due come from due dates and payment times, and a prepayment is its own
  record with its own key. Partial EMIs would still need an allocation step, and a
  payment gateway's callbacks would carry their own idempotency keys.

### D43. Timestamps are UTC; due dates are Indian business dates

- **Decision.** All timestamps are stored in UTC. The EMI schedule starts from the
  disbursal date in India Standard Time (UTC+05:30), a fixed offset because India has no
  daylight saving time.
- **Alternative.** Use the UTC date for due dates.
- **Why.** A loan disbursed at 23:00 UTC on 31 October was disbursed on 1 November for the
  borrower; the UTC date would schedule every EMI a day early.
- **With hindsight.** A lender working across countries would keep a business time zone per
  entity or product rather than a constant.

### D50. Prepayments are their own records, and only the unpaid schedule is rebuilt

- **Decision.** `POST /loans/{id}/prepayments` repays part of the principal ahead of time,
  with a choice: `"reduce": "emi"` keeps the end date with a lower EMI, `"reduce": "tenure"`
  keeps the EMI and ends sooner. It is allowed once every EMI due so far is paid (409
  `installment_due` otherwise) and must leave something owed (422 `prepayment_too_large`;
  foreclose instead, D52). A `prepayments` row records the principal, the interest (D51),
  who paid and when. The unpaid instalments are rewritten in place; paid ones never change.
  The loan's `emi` and `tenure_months` now describe the plan in force, while the
  application keeps the terms as approved. Outstanding principal, in the loan API and in
  both reports, is the principal minus the principal of paid instalments and of
  prepayments; the overdue report counts a prepayment from the day it was received.
- **Alternative.** A general payments table with an allocation step (D14's hindsight); one
  option only, as many lenders default to a shorter tenure; or versioned schedules.
- **Why.** A prepayment repays principal only, so nothing needs allocating across
  instalments, and one row per prepayment is enough. Offering both options shows the
  trade-off a borrower actually faces. On the Rs 1,00,000 test loan, prepaying Rs 20,000
  after three EMIs leaves Rs 3,334.32 of interest to pay if the EMI is lowered, and
  Rs 2,517.97 if the EMI is kept and the loan ends two months sooner. The audit entry
  records the EMI and the instalments left, before and after.
- **With hindsight.** Lowering the EMI fails when very little is left over many months:
  Rs 896 over 352 months gives an EMI so small that paisa rounding repays it early. The
  API answers 422 `remainder_too_small` and suggests keeping the EMI; a minimum
  prepayment amount would avoid the case. Versioned schedules would make the plan before a
  prepayment queryable rather than only recorded in the audit log.

### D51. Interest only for the days the prepaid principal was owed, and no prepayment fee

- **Decision.** Principal paid during an EMI period (from the last due date, or the
  disbursal date, to the next due date) carries interest for the days of that period that
  have passed: principal x monthly rate x days passed / days in the period. The rebuilt
  schedule then charges each future month's interest on the lower balance. No prepayment
  or foreclosure fee is charged.
- **Alternative.** Credit the prepayment at the next due date, charging the whole month's
  interest on the old balance; a daily rate (annual / 365) for part-periods; or a fee.
- **Why.** RBI's April 2024 circular on charging of interest names "charging interest for
  the entire month" on a loan repaid during the month as an unfair practice: interest is
  due only for the period the loan was outstanding
  ([RBI/2024-25/30](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12678)).
  Spreading the monthly rate over the period's days keeps the schedule consistent: a whole
  period still costs exactly one month's interest, whether it has 28 days or 31. RBI's
  2025 directions bar pre-payment charges on floating-rate loans to individuals sanctioned
  from 1 January 2026, and require any other charge to be disclosed upfront
  ([RBI/2025-26/64](https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12878)).
  LendFlow's rates are fixed at submission, but it charges nothing.
- **With hindsight.** Interest accrued daily on the actual balance, as most lenders now
  compute it, would make part-periods fall out naturally instead of being a special case.
  A fee schedule per product, disclosed in the sanction letter, is what a real lender
  with fixed-rate products would add.

### D52. Foreclosure is quoted, then confirmed; every payment ahead takes an idempotency key

- **Decision.** `GET /loans/{id}/foreclosure` quotes today's amount: the principal owed
  plus interest for the days of the current EMI period (D51). `POST /loans/{id}/foreclosure`
  sends that amount back. A different amount, such as yesterday's quote, is a 409
  `foreclosure_amount_changed` and nothing is paid. Foreclosing deletes the unpaid
  instalments, records a `foreclosure` prepayment and moves the application from disbursed
  to closed through the transition table, with its audit entry. Both endpoints that pay
  ahead require an `Idempotency-Key`, now a shared dependency that disbursal uses too. The
  key is stored on the prepayment under a unique index: a retry is replayed (200 with
  `Idempotent-Replayed: true`), and the same key for another loan, kind or amount is a 422.
  Both lock the application row first, so prepayments, repayments and foreclosure on one
  loan take turns. Tests on two real connections show a second Rs 60,000 prepayment
  waiting and then being refused because only Rs 40,000 is left, and one key used on two
  loans at once prepaying only one of them.
- **Alternative.** Foreclose for whatever is owed when the request arrives; or make
  prepayments idempotent by naming something, as repayments name their instalment (D42).
- **Why.** Interest grows daily, so the amount paid must be the amount the borrower was
  shown. A prepayment has no instalment to name, so a retry after a timeout would pay
  twice without a key. Closing through the normal transition reuses the lock, the audit
  entry and the 409 rules.
- **With hindsight.** With three endpoints keyed, a generic idempotency store (D41's
  hindsight) is closer to paying for itself, especially once payments arrive from a
  gateway. Foreclosure needs any EMI already due to be paid first; a real lender would
  quote the overdue EMIs and the principal as one amount.

## Reports

### D44. Reports are plain SQL files, not ORM queries

- **Decision.** Each report is a `.sql` file in `app/reports/`, built from CTEs and window
  functions, run with `text()` and validated into Pydantic rows.
- **Alternative.** SQLAlchemy query builders, or fetching rows and aggregating in Python.
- **Why.** Reviewers and analysts read SQL, the files run unchanged in `psql`, and window
  functions and `FILTER` read more clearly as SQL than through a builder. The database
  aggregates instead of shipping every row to Python.
- **With hindsight.** As reports multiply, a read replica or materialised views would keep
  heavy queries off the primary database. Not needed at this size.

### D45. Months are Indian calendar months, and every month is listed

- **Decision.** Decisions are grouped by month in `Asia/Kolkata`. A calendar CTE built
  with `generate_series` lists every month from the first decision to the last, including
  months with none, before the window functions run.
- **Alternative.** Group by UTC month and list only the months that have decisions.
- **Why.** A decision made at 00:30 on 1 April in India belongs to April for the business;
  a test pins that case and fails if the report switches to UTC. Without the calendar, a
  "3-month" rolling window would silently cover more than three months after a quiet month,
  and "previous month" would compare months that are not adjacent.
- **With hindsight.** At high volume, a daily summary table would avoid rescanning every
  decision.

### D46. Outstanding is principal minus principal repaid, on disbursed loans

- **Decision.** For each loan whose application is `disbursed`, outstanding is the
  principal minus the principal part of its paid instalments and of any prepayments
  (D50). Products are compared with `SUM() OVER ()` for their share of the book and
  `RANK()`.
- **Alternative.** Take the closing balance of the last paid instalment, or include
  interest still due.
- **Why.** Summing repaid principal does not depend on payment order and matches the loan
  API's `outstanding_principal`. Principal is what a lender means by its outstanding book,
  and closed loans owe nothing.
- **With hindsight.** The next report a lender asks for is overdue loans grouped by days
  past due; D49 adds it with the same definition of outstanding.

### D49. Overdue loans are bucketed by RBI's day-end rule, as of any past day

- **Decision.** `GET /reports/overdue-by-bucket` places each loan on the book at a day-end
  in India into RBI's buckets for term loans: current, SMA-0 (1-30 days past due), SMA-1
  (31-60), SMA-2 (61-90) and NPA (over 90). Days past due count from the loan's oldest
  instalment still unpaid at the end of the day, with its due date as day 1. The SQL
  rebuilds the book from timestamps, using only loans disbursed and payments made by that
  day-end, so any past day can be reported and a later payment does not rewrite it.
  `as_of` defaults to yesterday in India, the latest day-end; today or later is a 422
  `day_not_ended`. Each bucket shows its share of the book and the share in that bucket or
  a worse one, a running window sum whose SMA-1 row is the portfolio at risk over 30 days
  (PAR30).
- **Alternative.** A nightly job that stores each loan's days past due, as core banking
  systems do; or counting the due date itself as day 0.
- **Why.** Computing from payment times needs no batch job and no stored state that can
  drift, and it answers "what did the book look like at month-end?" exactly. The day
  count follows RBI's worked example: dues of 31 March 2021 left unpaid become SMA-1 at
  the 30 April day-end, SMA-2 on 30 May and NPA on 29 June
  ([RBI/2021-2022/125](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12194)).
  A test replays those dates, and the SQL is checked against a loan-by-loan count in
  Python over the seeded book.
- **With hindsight.** This is a days-past-due view, not full asset classification. RBI
  classifies the borrower rather than each loan, and an NPA is upgraded only when all
  arrears are paid; here a loan moves bucket as soon as its oldest arrears are paid. At
  scale, a daily snapshot table would make month-end reporting cheap, and regulatory
  returns are built from such snapshots. A collections worklist, one row per overdue loan,
  oldest first, is the natural next endpoint.

## Demo data and operations

### D47. Demo data is a year replayed through the real services

- **Decision.** `python -m app.cli seed` replays a year on a small event timeline.
  Applications arrive at random times; the underwriter reviews them, decides with a
  randomly drawn credit score and disburses the approved ones; borrowers pay each EMI on
  its due date, and about one in ten stops after a few payments. Every step calls the same
  service the API uses. A fixed random seed and end date (26 October 2026) make every run
  identical, and a test pins the result: 60 applicants, 112 applications, 74 loans. Docker
  Compose runs it on first start (`SEED_DEMO_DATA=true`, with `--if-empty`).
- **Alternative.** SQL fixtures, or generated rows inserted in bulk.
- **Why.** Rows inserted directly could break rules the services enforce: a loan with no
  approved decision, a schedule that does not add up, an audit trail out of order. Through
  the services, the seed is one more large integration test. The timeline keeps history
  consistent: each decision sees exactly the loans that were active at that moment.
- **With hindsight.** The first calibration produced almost no closed loans, because short
  personal loans with large amounts failed the FOIR rule, which was the rules doing their
  job; amounts were then sized to tenures to give a believable book. A year takes about five
  seconds to replay; load testing at volume would need bulk generation instead.

### D48. Underwriters are created with an operator command

- **Decision.** `python -m app.cli create-underwriter EMAIL` reads the password from
  standard input and applies the same email and password rules as registration.
- **Alternative.** An admin-only HTTP endpoint, or inserting a row with SQL.
- **Why.** No HTTP request can ever create an underwriter (D23). Reading the password from
  standard input keeps it out of shell history and process listings.
- **With hindsight.** In production, staff accounts belong in an identity provider (single
  sign-on with role claims) rather than in local passwords.
