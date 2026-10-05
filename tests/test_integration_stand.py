"""Опасные адреса хранилищ отвергаются до первого подключения."""

import pytest
from scripts.test_stand import load_test_settings, validate_test_addresses

APP_POSTGRES = "postgresql://user:secret@localhost:5432/vellar"
APP_REDIS = "redis://localhost:6379/0"
TEST_POSTGRES = "postgresql://test:secret@localhost:55432/vellar_test"
TEST_REDIS = "redis://localhost:56379/0"


def test_accepts_separate_test_servers() -> None:
    validate_test_addresses(TEST_POSTGRES, TEST_REDIS, APP_POSTGRES, APP_REDIS)


@pytest.mark.parametrize(
    ("postgres", "redis"),
    [
        (APP_POSTGRES, TEST_REDIS),
        ("postgresql://user:secret@127.0.0.1:5432/vellar_test", TEST_REDIS),
        ("postgresql://test:secret@localhost:55432/vellar", TEST_REDIS),
        (TEST_POSTGRES, APP_REDIS),
        (TEST_POSTGRES, "redis://127.0.0.1:6379/1"),
        (TEST_POSTGRES, "redis://localhost:56379/not-a-db"),
    ],
)
def test_rejects_game_or_ambiguous_storage(postgres: str, redis: str) -> None:
    with pytest.raises(ValueError, match="Test"):
        validate_test_addresses(postgres, redis, APP_POSTGRES, APP_REDIS)


def test_empty_application_addresses_do_not_replace_explicit_test_addresses(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "")
    monkeypatch.setenv("REDIS_DSN", "")
    monkeypatch.setenv("VELLAR_TEST_POSTGRES_DSN", TEST_POSTGRES)
    monkeypatch.setenv("VELLAR_TEST_REDIS_DSN", TEST_REDIS)
    settings = load_test_settings()
    assert settings.postgres_dsn == TEST_POSTGRES
    assert settings.redis_dsn == TEST_REDIS


def test_missing_test_addresses_fail_before_any_connection(monkeypatch) -> None:
    monkeypatch.delenv("VELLAR_TEST_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("VELLAR_TEST_REDIS_DSN", raising=False)
    with pytest.raises(ValueError, match="VELLAR_TEST_POSTGRES_DSN"):
        load_test_settings()
