from datetime import UTC, datetime

from app.config import Settings
from app.models import User
from app.security import create_access_token


def auth_headers(user: User, settings: Settings) -> dict[str, str]:
    """Bearer headers for `user`, minted directly instead of through the sign-in endpoint."""
    token = create_access_token(user, settings, now=datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}
