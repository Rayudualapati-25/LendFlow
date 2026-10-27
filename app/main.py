from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.routers import health


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or Settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="LendFlow",
        summary="Loan origination and credit decisions with reason codes.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    app.include_router(health.router)
    return app
