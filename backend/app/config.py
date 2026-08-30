import logging
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from loguru import logger
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

LOGS_DIR = BASE_DIR / "logs"
if not LOGS_DIR.exists():
    LOGS_DIR.mkdir()

logging.basicConfig(
    level=logging.INFO,  # For displaying the default model calling logs
)

logger.add(
    sink=LOGS_DIR / "api_{time:YYYYMMDD}.log",
    level="INFO",
    rotation="00:00",
    retention="7 days",
    compression="zip",
)


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    tavily_api_key: str
    model_provider: str
    model_names: list[str]
    model_base_url: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    embeddings_model_name: str
    embeddings_base_url: str | None = None
    token_bearer_url: str
    jwt_secret: str
    jwt_algorithm: str
    access_token_expiry_mins: int
    refresh_token_expiry_days: int
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str
    pgvector_collection_name: str
    super_admin_emails: list[str] = []
    frontend_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:8501",
        "http://localhost:8502",
    ]
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    refresh_cookie_name: str = "zhishu_refresh"
    admin_refresh_cookie_name: str = "zhishu_admin_refresh"

    @model_validator(mode="after")
    def validate_browser_security(self):
        if not self.frontend_origins:
            raise ValueError("FRONTEND_ORIGINS must contain at least one explicit origin")
        normalized: list[str] = []
        for origin in self.frontend_origins:
            value = origin.strip()
            parsed = urlsplit(value)
            invalid_port = False
            try:
                parsed.port
            except ValueError:
                invalid_port = True
            if (
                value == "*"
                or not value
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.hostname is None
                or invalid_port
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"Invalid credentialed CORS origin: {origin!r}")
            if value not in normalized:
                normalized.append(value)
        self.frontend_origins = normalized
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @property
    def database_uri(self) -> str:
        """Generate PostgreSQL connection string for sqlalchemy."""
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"

    @property
    def checkpointer_uri(self) -> str:
        """Generate PostgreSQL connection string for checkpointer."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}?sslmode=disable"

    @property
    def pgvector_connection(self) -> str:
        """Generate PostgreSQL connection string for PGVector."""
        return f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="allow")


settings = Settings()  # type: ignore
