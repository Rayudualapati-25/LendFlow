from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import make_engine
from app.errors import DomainError
from app.routers import applicants, applications, auth, health, loans, products, reports


async def handle_domain_error(_: Request, error: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code, content={"detail": error.message, "code": error.code}
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or Settings()  # type: ignore[call-arg]  # values come from the env
    engine = make_engine(settings.database_url)

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

    for router in (health, auth, products, applicants, applications, loans, reports):
        app.include_router(router.router)
    return app
