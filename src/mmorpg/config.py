"""Application configuration.

Settings are read once at startup from the environment (and from a local ``.env``
file when present) and are then treated as immutable. Nothing in the codebase may
read ``os.environ`` directly; everything goes through :class:`Settings`.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTENT_DIR = PROJECT_ROOT / "content"


class AppEnv(StrEnum):
    """Deployment environment.

    ``LOCAL`` swaps PostgreSQL and Redis for in-memory adapters so the bot can be
    run and played without any external services. See
    ``docs/adr/0005-in-memory-adapters.md``.
    """

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    """Root configuration object."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False

    bot_token: SecretStr = SecretStr("")

    webhook_base_url: str = "https://example.com"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: SecretStr = SecretStr("")
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    world_seed: str = "vellar-prime"
    cycle_seconds: int = Field(default=21_600, gt=0)

    postgres_dsn: str = "postgresql://vellar:vellar@localhost:5432/vellar"
    postgres_pool_min: int = Field(default=5, ge=1)
    postgres_pool_max: int = Field(default=20, ge=1)

    redis_dsn: str = "redis://localhost:6379/0"

    content_dir: Path = DEFAULT_CONTENT_DIR

    # Updates slower than this many seconds are reported by the slow callback
    # detector; see docs/architecture.md, "Latency budget".
    slow_callback_seconds: float = Field(default=0.1, gt=0.0)

    @model_validator(mode="after")
    def _check_pool_bounds(self) -> Settings:
        if self.postgres_pool_max < self.postgres_pool_min:
            msg = "postgres_pool_max must be greater than or equal to postgres_pool_min"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_prod_requirements(self) -> Settings:
        if self.app_env is AppEnv.PROD and not self.webhook_secret.get_secret_value():
            msg = "webhook_secret is required when app_env is 'prod'"
            raise ValueError(msg)
        return self

    @property
    def uses_external_storage(self) -> bool:
        """Whether PostgreSQL and Redis adapters should be used."""
        return self.app_env is not AppEnv.LOCAL

    @property
    def webhook_url(self) -> str:
        """Public URL Telegram will deliver updates to."""
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"


def load_settings() -> Settings:
    """Build the settings object. Call once, at startup."""
    return Settings()
