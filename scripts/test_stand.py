"""Безопасные адреса отдельного стенда для интеграционных испытаний."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit

from mmorpg.config import Settings


def _address(dsn: str, *, kind: str) -> tuple[str, int, str]:
    """Вернуть узел, порт и базу, не включая пароль в сообщения об ошибках."""
    try:
        url = urlsplit(dsn)
        port = url.port
        host = url.hostname
    except ValueError as error:
        raise ValueError(f"Invalid {kind} test address") from error
    schemes = {"postgresql", "postgres"} if kind == "PostgreSQL" else {"redis", "rediss"}
    if url.scheme not in schemes or not host or not port or url.query or url.fragment:
        raise ValueError(f"Invalid {kind} test address")
    host = "loopback" if host in {"localhost", "127.0.0.1", "::1"} else host
    return host, port, unquote(url.path.lstrip("/"))


def validate_test_addresses(
    test_postgres: str, test_redis: str, app_postgres: str, app_redis: str
) -> None:
    """Не дать испытаниям обратиться к хранилищам обычной игры."""
    postgres_host, postgres_port, database = _address(test_postgres, kind="PostgreSQL")
    redis_host, redis_port, redis_database = _address(test_redis, kind="Redis")
    if not database.endswith("_test"):
        raise ValueError("Test PostgreSQL database name must end with '_test'")
    if not redis_database.isdecimal():
        raise ValueError("Test Redis address must select a numeric database")
    app_pg_host, app_pg_port, _ = _address(app_postgres, kind="PostgreSQL")
    app_redis_host, app_redis_port, _ = _address(app_redis, kind="Redis")
    if (postgres_host, postgres_port) == (app_pg_host, app_pg_port):
        raise ValueError("Test PostgreSQL must use a separate server port or host")
    if (redis_host, redis_port) == (app_redis_host, app_redis_port):
        raise ValueError("Test Redis must use a separate server port or host")


def load_test_settings() -> Settings:
    """Взять только явные тестовые адреса; не подставлять адреса из .env."""
    test_postgres = os.environ.get("VELLAR_TEST_POSTGRES_DSN", "")
    test_redis = os.environ.get("VELLAR_TEST_REDIS_DSN", "")
    if not test_postgres or not test_redis:
        raise ValueError("Set VELLAR_TEST_POSTGRES_DSN and VELLAR_TEST_REDIS_DSN")
    app_settings = Settings(app_env="dev", bot_token="0:test")  # type: ignore[call-arg]
    default_postgres = Settings.model_fields["postgres_dsn"].default
    default_redis = Settings.model_fields["redis_dsn"].default
    assert isinstance(default_postgres, str) and isinstance(default_redis, str)
    validate_test_addresses(
        test_postgres,
        test_redis,
        app_settings.postgres_dsn or default_postgres,
        app_settings.redis_dsn or default_redis,
    )
    validate_test_addresses(test_postgres, test_redis, default_postgres, default_redis)
    return Settings(  # type: ignore[call-arg]
        app_env="dev",
        bot_token="0:test",
        postgres_dsn=test_postgres,
        redis_dsn=test_redis,
        _env_file=None,
    )


if __name__ == "__main__":
    load_test_settings()
    print("Test storage addresses are isolated from the application settings.")
