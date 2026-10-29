from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """The database connection alone, which is all that migrations need."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lendflow:lendflow@localhost:5432/lendflow"


class Settings(DatabaseSettings):
    """Runtime configuration for the API, read from environment variables (or `.env`)."""

    # No default on purpose: without a real signing secret the API must not start.
    # 32 characters gives the 256-bit key that HS256 needs (RFC 7518, section 3.2).
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=30, gt=0)
    # A refresh token dies after a day unused; a sign-in lasts a week at most (D53).
    refresh_token_expire_days: int = Field(default=1, gt=0)
    session_max_days: int = Field(default=7, gt=0)
    # Five failed sign-ins for one email within 15 minutes lock it until one expires (D54).
    sign_in_max_failures: int = Field(default=5, gt=0)
    sign_in_window_minutes: int = Field(default=15, gt=0)
