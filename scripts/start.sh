#!/bin/sh
# Container entrypoint: bring the schema up to date, optionally load demo data, then serve.
set -eu

alembic upgrade head

# Demo data for local use only: docker-compose.yml sets SEED_DEMO_DATA=true.
# --if-empty makes restarts safe: the seed runs only on an empty database.
if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
    python -m app.cli seed --if-empty
fi

# exec replaces the shell, so uvicorn receives SIGTERM directly and shuts down cleanly.
# Hosts such as Render choose the port through $PORT; Compose leaves it at 8000.
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}"
