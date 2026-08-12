"""Configuration behaviour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mmorpg.config import AppEnv, Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"_env_file": None}
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_local_is_the_default_environment() -> None:
    settings = _settings()
    assert settings.app_env is AppEnv.LOCAL
    assert settings.uses_external_storage is False


def test_dev_and_prod_use_external_storage() -> None:
    assert _settings(app_env="dev").uses_external_storage is True
    assert _settings(app_env="prod", webhook_secret="s3cret").uses_external_storage is True


def test_prod_requires_a_webhook_secret() -> None:
    with pytest.raises(ValidationError, match="webhook_secret"):
        _settings(app_env="prod")


def test_pool_bounds_are_validated() -> None:
    with pytest.raises(ValidationError, match="postgres_pool_max"):
        _settings(postgres_pool_min=10, postgres_pool_max=5)


def test_cycle_seconds_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _settings(cycle_seconds=0)


def test_webhook_url_joins_base_and_path() -> None:
    settings = _settings(
        app_env="prod",
        webhook_secret="s3cret",
        webhook_base_url="https://vellar.example/",
        webhook_path="/telegram/webhook",
    )
    assert settings.webhook_url == "https://vellar.example/telegram/webhook"


def test_the_concurrency_ceiling_is_translated_for_aiogram() -> None:
    """aiogram spells "no limit" as None; the setting spells it 0."""
    assert _settings().concurrency_limit == 100
    assert _settings(update_concurrency_limit=250).concurrency_limit == 250
    assert _settings(update_concurrency_limit=0).concurrency_limit is None


def test_the_heartbeat_tolerates_two_missed_beats() -> None:
    """One missed beat is a busy host; three in a row is a wedged loop."""
    assert _settings(heartbeat_seconds=10.0).heartbeat_stale_after == 30.0


def test_settings_are_immutable() -> None:
    settings = _settings()
    with pytest.raises(ValidationError):
        settings.world_seed = "other"  # type: ignore[misc]
