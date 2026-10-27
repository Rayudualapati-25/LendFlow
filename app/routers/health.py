from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionDep

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]


@router.get("/health", responses={503: {"model": Health}})
def health(session: SessionDep, response: Response) -> Health:
    """Report whether the API can reach its database. Used by the Compose healthcheck."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database="unavailable")
    return Health(status="ok", database="ok")
