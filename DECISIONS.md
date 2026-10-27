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
