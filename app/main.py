from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.errors import DomainError, handle_domain_error
from app.routers import auth, health


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or Settings()  # type: ignore[call-arg]  # values come from the env
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
    # Starlette types handlers as taking any Exception; this one only ever gets a DomainError.
    app.add_exception_handler(DomainError, handle_domain_error)  # type: ignore[arg-type]

    app.include_router(health.router)
    app.include_router(auth.router)
    return app
