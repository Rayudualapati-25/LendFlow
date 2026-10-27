#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
set -eu

alembic upgrade head

# exec replaces the shell, so uvicorn receives SIGTERM directly and shuts down cleanly.
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
