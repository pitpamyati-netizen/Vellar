"""Application configuration.

Settings are read once at startup from the environment (and from a local ``.env``
file when present) and are then treated as immutable. Nothing in the codebase may
read ``os.environ`` directly; everything goes through :class:`Settings`.
"""

from __future__ import annotations

import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTENT_DIR = PROJECT_ROOT / "content"
# The temporary directory, not the project tree: the container runs the bot as a
# non-root user that owns nothing under /app.
DEFAULT_HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "vellar-heartbeat"
#: ``GROUP_ID=*``: answer in any group the bot is a member of. See ``Settings``.
ANY_GROUP = "*"


class AppEnv(StrEnum):
    """Deployment environment.

    ``LOCAL`` swaps PostgreSQL and Redis for in-memory adapters so the bot can be
    run and played without any external services. See
    ``docs/adr/0005-in-memory-adapters.md``.

    ``SOLO`` keeps PostgreSQL and drops only Redis: the world is on disk and
    survives a restart, while the screen a player stands on lives in the process
    that serves them. One machine, one process, no containers - see
    ``docs/adr/0010-a-machine-without-containers.md``.
    """

    LOCAL = "local"
    SOLO = "solo"
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

    # Which working tree this is running from. Stamped by Start.bat - into the
    # image for the stack, into the environment for a solo run - and logged on
    # startup, so "is the bot running my latest change" has an answer that does
    # not depend on memory.
    vellar_build: str = "unknown"

    webhook_base_url: str = "https://example.com"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: SecretStr = SecretStr("")
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    # --- Community ---
    # The public channel the game posts to, and the group players talk in. Both
    # accept either a numeric chat id (-100...) or an @username. Empty means the
    # feature is simply off: a local run has no channel and must still play.
    channel_id: str = ""
    # ``*`` means "whatever group the bot has been added to". That is a testing
    # setting, not a production one: it exists so the group half of the game can
    # be played the minute somebody adds the bot to a chat, without first hunting
    # down a numeric id (Roadmap, "Риски"). With a real id set, every other chat
    # is ignored exactly as before.
    group_id: str = ""
    # Shown to players as "where to find the others"; empty hides the line.
    channel_url: str = ""
    group_url: str = ""

    # --- Keepers ---
    # Telegram ids the keeper right starts from, comma separated. Kept in the
    # environment and not in the database on purpose: a right that outlives a
    # wiped table, and one nobody can grant themselves from inside the game.
    # Everybody else who holds the right holds it because one of these ids handed
    # it to them, and it is stored on their account instead (``docs/keeper.md``).
    admin_ids: str = ""

    world_seed: str = "vellar-prime"
    # The world no longer turns over on a clock: a location holds its map until it
    # is cleared out. Two things are still timed, and both are short, because both
    # exist to give a player a reason to come back rather than to make them wait:
    # the shelf in a shop, and the cooldown on gathering raw stuff.
    shop_rotation_seconds: int = Field(default=1_800, gt=0)
    gather_cooldown_seconds: int = Field(default=900, gt=0)

    postgres_dsn: str = "postgresql://vellar:vellar@localhost:5432/vellar"
    postgres_pool_min: int = Field(default=5, ge=1)
    postgres_pool_max: int = Field(default=20, ge=1)

    redis_dsn: str = "redis://localhost:6379/0"

    # --- A link that broke while the game was running (docs/architecture.md) ---
    # A dropped connection is replaced by the pool itself; what these govern is
    # the call that was in the air when it dropped. How many times it is repeated,
    # and how long the waits between repeats are - the wait doubles up to the
    # ceiling. Nothing that may have already changed the world is repeated, see
    # docs/adr/0009-repeating-a-lost-query.md.
    reconnect_attempts: int = Field(default=5, ge=1)
    reconnect_delay_seconds: float = Field(default=0.2, gt=0.0)
    reconnect_max_delay_seconds: float = Field(default=5.0, gt=0.0)
    # How long startup waits for PostgreSQL, Redis and Telegram to answer before
    # giving up. A stack that comes up together does not come up in order.
    startup_wait_seconds: float = Field(default=60.0, ge=0.0)

    content_dir: Path = DEFAULT_CONTENT_DIR

    # Updates slower than this many seconds are reported by the slow callback
    # detector; see docs/architecture.md, "Latency budget".
    slow_callback_seconds: float = Field(default=0.1, gt=0.0)
    # That detector needs asyncio debug mode, which timestamps every callback and
    # keeps coroutine origins alive. It is a development tool, so it is off
    # wherever real players are connected and the setting is left unset: the
    # place it was forgotten in is always the place players are, and a guard
    # rail that costs throughput must be asked for rather than inherited.
    slow_callback_detector: bool | None = None

    # How often the game writes down what it served (``mmorpg.metrics``). One
    # line a minute: often enough to see the ten bad minutes, rare enough that
    # the log stays readable for a day of play.
    metrics_seconds: float = Field(default=60.0, gt=0.0)

    # Sends per second the bot allows itself. Telegram counts about thirty, for
    # the bot as a whole; going over is answered with "wait", which for a player
    # listening to a screen reader is an answer that never came.
    telegram_sends_per_second: int = Field(default=30, ge=1)

    # Ceiling on updates handled at the same time. A burst of players cannot
    # queue more concurrent work than the PostgreSQL pool can serve; 0 lifts the
    # ceiling. See docs/architecture.md, "Capacity".
    update_concurrency_limit: int = Field(default=100, ge=0)

    # Liveness heartbeat: the running event loop touches this file every
    # ``heartbeat_seconds``, and the container healthcheck fails once the file
    # goes stale. See ``mmorpg.health``.
    heartbeat_path: Path = DEFAULT_HEARTBEAT_PATH
    heartbeat_seconds: float = Field(default=10.0, gt=0.0)

    @model_validator(mode="after")
    def _check_pool_bounds(self) -> Settings:
        if self.postgres_pool_max < self.postgres_pool_min:
            msg = "postgres_pool_max must be greater than or equal to postgres_pool_min"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_retry_bounds(self) -> Settings:
        if self.reconnect_max_delay_seconds < self.reconnect_delay_seconds:
            msg = "reconnect_max_delay_seconds must be greater than or equal to the first delay"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_prod_requirements(self) -> Settings:
        if self.app_env is AppEnv.PROD and not self.webhook_secret.get_secret_value():
            msg = "webhook_secret is required when app_env is 'prod'"
            raise ValueError(msg)
        return self

    @property
    def watching_slow_callbacks(self) -> bool:
        """Whether asyncio debug mode is on. Local by default, elsewhere on request."""
        if self.slow_callback_detector is None:
            return self.app_env is AppEnv.LOCAL
        return self.slow_callback_detector

    @property
    def broadcasts_enabled(self) -> bool:
        """Whether the game channel is configured. Broadcasts are a no-op if not."""
        return bool(self.channel_id.strip())

    @property
    def group_chat_enabled(self) -> bool:
        """Whether public group commands are answered."""
        return bool(self.group_id.strip())

    @property
    def any_group_allowed(self) -> bool:
        """Whether the bot answers in every group it has been added to."""
        return self.group_id.strip() == ANY_GROUP

    @property
    def admins(self) -> frozenset[int]:
        """Telegram ids the right comes from. Anything unparsable is simply not one."""
        ids: set[int] = set()
        for part in self.admin_ids.replace(";", ",").split(","):
            stripped = part.strip()
            if stripped.lstrip("-").isdigit():
                ids.add(int(stripped))
        return frozenset(ids)

    def is_admin(self, telegram_id: int) -> bool:
        """Whether this account's right comes from the environment rather than the game.

        Such an account is a keeper always, cannot be stripped of it from inside
        the game, and is the only one that can hand the right to somebody else.
        """
        return telegram_id in self.admins

    @property
    def uses_postgres(self) -> bool:
        """Whether the world is kept in PostgreSQL rather than in memory."""
        return self.app_env is not AppEnv.LOCAL

    @property
    def uses_redis(self) -> bool:
        """Whether the short-lived state is kept in Redis rather than in memory.

        ``SOLO`` says no: what Redis holds here is where a player is standing, the
        fight they are in the middle of and the map of a location, and all three
        are already written to be survivable losses (``Claude.md``, rule 8). One
        process serving one machine can hold them itself, and that is one service
        fewer to install.
        """
        return self.app_env in (AppEnv.DEV, AppEnv.PROD)

    @property
    def webhook_url(self) -> str:
        """Public URL Telegram will deliver updates to."""
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def heartbeat_stale_after(self) -> float:
        """Heartbeat age that means the event loop is wedged.

        Three beats: one missed beat is a slow disk or a busy host, three in a
        row is not.
        """
        return self.heartbeat_seconds * 3

    @property
    def concurrency_limit(self) -> int | None:
        """The update ceiling in the form aiogram wants: ``None`` for no limit."""
        return self.update_concurrency_limit or None


def load_settings() -> Settings:
    """Build the settings object. Call once, at startup."""
    return Settings()
