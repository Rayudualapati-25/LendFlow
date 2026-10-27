# Build stage: install the locked dependencies into a virtualenv.
FROM python:3.12-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Runtime stage: Python, the virtualenv and the application code; no build tools.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/start.sh ./scripts/start.sh
COPY app ./app

RUN useradd --system --uid 10001 lendflow
USER lendflow

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
