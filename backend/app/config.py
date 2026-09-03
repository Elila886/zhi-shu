import logging
from ipaddress import IPv4Network, IPv6Network, ip_network
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
    hybrid_dense_k: int = 8
    hybrid_bm25_k: int = 8
    hybrid_final_k: int = 3
    hybrid_rrf_k: int = 60
    hybrid_dense_weight: float = 1.0
    hybrid_bm25_weight: float = 1.0
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
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_key_prefix: str = "zhishu:traffic"
    rate_limit_window_seconds: int = 60
    rate_limit_ordinary_requests: int = 60
    rate_limit_agent_requests: int = 10
    rate_limit_upload_requests: int = 5
    rate_limit_signup_requests: int = 5
    rate_limit_login_ip_requests: int = 10
    rate_limit_login_account_requests: int = 5
    rate_limit_refresh_session_requests: int = 30
    rate_limit_refresh_ip_requests: int = 60
    rate_limit_public_config_requests: int = 60
    rate_limit_failure_retry_after_seconds: int = 5
    trusted_proxy_cidrs: list[str] = ["127.0.0.1/32", "::1/128"]

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
        if not self.redis_url:
            raise ValueError("REDIS_URL must not be empty")
        positive_rate_settings = (
            "rate_limit_window_seconds", "rate_limit_ordinary_requests", "rate_limit_agent_requests",
            "rate_limit_upload_requests", "rate_limit_signup_requests", "rate_limit_login_ip_requests",
            "rate_limit_login_account_requests", "rate_limit_refresh_session_requests",
            "rate_limit_refresh_ip_requests", "rate_limit_public_config_requests",
            "rate_limit_failure_retry_after_seconds",
        )
        for name in positive_rate_settings:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be greater than zero")
        positive_hybrid_settings = (
            "hybrid_dense_k",
            "hybrid_bm25_k",
            "hybrid_final_k",
            "hybrid_rrf_k",
        )
        for name in positive_hybrid_settings:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be greater than zero")
        if self.hybrid_dense_weight < 0 or self.hybrid_bm25_weight < 0:
            raise ValueError("HYBRID_DENSE_WEIGHT and HYBRID_BM25_WEIGHT must be greater than or equal to zero")
        if self.hybrid_dense_weight == 0 and self.hybrid_bm25_weight == 0:
            raise ValueError("HYBRID_DENSE_WEIGHT and HYBRID_BM25_WEIGHT cannot both be zero")
        if not self.rate_limit_key_prefix.strip():
            raise ValueError("RATE_LIMIT_KEY_PREFIX must not be empty")
        for cidr in self.trusted_proxy_cidrs:
            try:
                ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid trusted proxy CIDR: {cidr!r}") from exc
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

    @property
    def trusted_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return tuple(ip_network(cidr, strict=False) for cidr in self.trusted_proxy_cidrs)

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="allow")


settings = Settings()  # type: ignore
