"""Настройки приложения.

Настройки читаются один раз на старте из окружения (и из локального файла
``.env``, если он есть), после чего считаются неизменными. Ничто в коде не
читает ``os.environ`` напрямую: всё идёт через :class:`Settings`.
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
# Временный каталог, а не дерево проекта: в контейнере бот работает от пользователя,
# которому под /app не принадлежит ничего.
DEFAULT_HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "vellar-heartbeat"
#: ``GROUP_ID=*``: отвечать в любой группе, где бот состоит. См. ``Settings``.
ANY_GROUP = "*"


class AppEnv(StrEnum):
    """Где игра запущена.

    ``LOCAL`` подменяет PostgreSQL и Redis адаптерами в памяти, и бота можно
    запустить и играть без единой внешней службы. См.
    ``docs/adr/0005-in-memory-adapters.md``.

    ``SOLO`` оставляет PostgreSQL и убирает только Redis: мир лежит на диске и
    переживает перезапуск, а экран, на котором стоит игрок, живёт в процессе,
    который его обслуживает. Одна машина, один процесс, без контейнеров - см.
    ``docs/adr/0010-a-machine-without-containers.md``.
    """

    LOCAL = "local"
    SOLO = "solo"
    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    """Корневой объект настроек."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False

    # --- Что игра записывает о себе (``mmorpg.logging``) ---
    # Куда ложатся файлы журнала. Относительный путь считается от корня проекта, пустой
    # означает только stdout - именно это нужно контейнеру: там журнал принадлежит
    # демону, который его собирает, а процессу не принадлежит ничего, во что он вправе
    # писать.
    log_dir: str = "logs"
    # Сколько держится повседневный журнал. Неделя - это срок, за который приходит
    # жалоба игрока: на «вчера у меня пропало золото» ответить можно, на «где-то весной»
    # - нет, а насыщенный день на сотню игроков - это несколько мегабайт.
    log_retention_days: int = Field(default=7, ge=1)
    # И сколько держится важная половина - отказы, предупреждения, каждое движение
    # золота, каждый закрытый аккаунт. ``0`` значит «не удалять никогда»: по этому файлу
    # разбирают спор о пропавшем кошельке, и растёт он килобайтами там, где другой
    # растёт мегабайтами.
    log_important_retention_days: int = Field(default=0, ge=0)

    bot_token: SecretStr = SecretStr("")

    # Из какого рабочего дерева всё это запущено. Штампует Start.bat - в образ для
    # стека, в окружение для solo, - и пишется в журнал на старте, чтобы на вопрос
    # «крутится ли бот с моей последней правкой» отвечала не память.
    vellar_build: str = "unknown"

    webhook_base_url: str = "https://example.com"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: SecretStr = SecretStr("")
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    # --- Сообщество ---
    # Публичный канал, куда игра пишет, и группа, где разговаривают игроки. Оба
    # принимают либо числовой id чата (-100...), либо @username. Пусто - значит, этой
    # части просто нет: локальный запуск без канала обязан играться.
    channel_id: str = ""
    # ``*`` значит «в любой группе, куда бота добавили». Это настройка для проб, а не
    # для боя: она существует, чтобы групповую половину игры можно было попробовать в ту
    # минуту, когда бота добавили в чат, не разыскивая сперва числовой id. С настоящим
    # id всё прочее по-прежнему пропускается мимо.
    group_id: str = ""
    # Показывается игроку как «где найти остальных»; пусто - строки нет.
    channel_url: str = ""
    group_url: str = ""

    # --- Смотрители ---
    # Telegram-id, от которых начинается право смотрителя, через запятую. Держатся в
    # окружении, а не в базе, нарочно: это право переживает стёртую таблицу, и выдать
    # его себе изнутри игры нельзя. Все остальные держат право потому, что один из этих
    # id им его выдал, и хранится оно уже на их аккаунте (``docs/keeper.md``).
    admin_ids: str = ""

    world_seed: str = "vellar-prime"
    # Мир больше не переворачивается по часам: локация держит свою карту, пока её не
    # вычистят. Со сроком остались две вещи, и обе короткие, потому что обе существуют,
    # чтобы дать повод вернуться, а не заставить ждать: прилавок в лавке и откат на
    # сборе сырья.
    shop_rotation_seconds: int = Field(default=1_800, gt=0)
    gather_cooldown_seconds: int = Field(default=900, gt=0)

    postgres_dsn: str = "postgresql://vellar:vellar@localhost:5432/vellar"
    postgres_pool_min: int = Field(default=5, ge=1)
    postgres_pool_max: int = Field(default=20, ge=1)

    redis_dsn: str = "redis://localhost:6379/0"

    # --- Связь, оборвавшаяся на ходу (docs/architecture.md) ---
    # Упавшее соединение пул заменит сам; здесь речь о вызове, который был в воздухе,
    # когда оно упало. Сколько раз он повторяется и какие паузы между повторами - пауза
    # удваивается до потолка. Ничто, что уже могло изменить мир, не повторяется, см.
    # docs/adr/0009-repeating-a-lost-query.md.
    reconnect_attempts: int = Field(default=5, ge=1)
    reconnect_delay_seconds: float = Field(default=0.2, gt=0.0)
    reconnect_max_delay_seconds: float = Field(default=5.0, gt=0.0)
    # Сколько старт ждёт ответа от PostgreSQL, Redis и Telegram, прежде чем сдаться.
    # Стек, который поднимается разом, поднимается не по порядку.
    startup_wait_seconds: float = Field(default=60.0, ge=0.0)

    content_dir: Path = DEFAULT_CONTENT_DIR

    # Обновления медленнее этого числа секунд отмечает детектор медленных колбэков; см.
    # docs/architecture.md, «Бюджет задержки».
    slow_callback_seconds: float = Field(default=0.1, gt=0.0)
    # Детектору нужен режим отладки asyncio, а он проставляет время каждому колбэку и
    # держит живыми истоки корутин. Это инструмент разработки, поэтому он выключен там,
    # где подключены живые игроки, и настройка оставлена пустой: место, где о ней
    # забыли, - это всегда место, где сидят игроки, а перила ценой в пропускную
    # способность просят, а не наследуют.
    slow_callback_detector: bool | None = None

    # Как часто игра записывает, что успела сделать (``mmorpg.metrics``). Строка в
    # минуту: достаточно часто, чтобы увидеть десять плохих минут, достаточно редко,
    # чтобы журнал за день оставался читаемым.
    metrics_seconds: float = Field(default=60.0, gt=0.0)

    # Сколько отправок в секунду бот себе позволяет. Telegram считает около тридцати, и
    # на бота целиком; за перебор отвечают «подожди», а для того, кто слушает экранного
    # диктора, это ответ, который не пришёл.
    telegram_sends_per_second: int = Field(default=30, ge=1)

    # Потолок одновременно обрабатываемых обновлений. Наплыв игроков не поставит в
    # очередь больше одновременной работы, чем вывезет пул PostgreSQL; 0 снимает
    # потолок. См. docs/architecture.md, «Ёмкость».
    update_concurrency_limit: int = Field(default=100, ge=0)

    # Сердцебиение: живой цикл событий трогает этот файл каждые ``heartbeat_seconds``, и
    # проверка контейнера падает, как только файл протух. См. ``mmorpg.health``.
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
    def log_path(self) -> Path | None:
        """Куда ложатся файлы журнала, или ``None`` - только stdout."""
        stripped = self.log_dir.strip()
        if not stripped:
            return None
        directory = Path(stripped)
        return directory if directory.is_absolute() else PROJECT_ROOT / directory

    @property
    def watching_slow_callbacks(self) -> bool:
        """Включён ли режим отладки asyncio. Локально - да, в остальном - по просьбе."""
        if self.slow_callback_detector is None:
            return self.app_env is AppEnv.LOCAL
        return self.slow_callback_detector

    @property
    def broadcasts_enabled(self) -> bool:
        """Настроен ли канал игры. Без него бродкаст ничего не делает."""
        return bool(self.channel_id.strip())

    @property
    def group_chat_enabled(self) -> bool:
        """Отвечает ли игра на команды в группах."""
        return bool(self.group_id.strip())

    @property
    def any_group_allowed(self) -> bool:
        """Отвечает ли бот в любой группе, куда его добавили."""
        return self.group_id.strip() == ANY_GROUP

    @property
    def admins(self) -> frozenset[int]:
        """Telegram-id, от которых идёт право. Всё неразобранное правом не считается."""
        ids: set[int] = set()
        for part in self.admin_ids.replace(";", ",").split(","):
            stripped = part.strip()
            if stripped.lstrip("-").isdigit():
                ids.add(int(stripped))
        return frozenset(ids)

    def is_admin(self, telegram_id: int) -> bool:
        """Идёт ли право этого аккаунта из окружения, а не из игры.

        Такой аккаунт - смотритель всегда, права его изнутри игры не лишить, и только
        он может выдать право кому-то ещё.
        """
        return telegram_id in self.admins

    @property
    def uses_postgres(self) -> bool:
        """Лежит ли мир в PostgreSQL, а не в памяти."""
        return self.app_env is not AppEnv.LOCAL

    @property
    def uses_redis(self) -> bool:
        """Лежит ли короткоживущее состояние в Redis, а не в памяти.

        ``SOLO`` говорит «нет»: Redis держит здесь место игрока, начатый бой и карту
        локации, и все три написаны так, чтобы их потеря была безопасной
        (``Claude.md``, правило 8). Один процесс, обслуживающий одну машину, удержит их
        сам, а это на одну службу меньше.
        """
        return self.app_env in (AppEnv.DEV, AppEnv.PROD)

    @property
    def webhook_url(self) -> str:
        """Публичный адрес, на который Telegram будет слать обновления."""
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def heartbeat_stale_after(self) -> float:
        """Возраст сердцебиения, после которого цикл событий считается вставшим.

        Три удара: один пропущенный - это медленный диск или занятая машина, три подряд
        - уже нет.
        """
        return self.heartbeat_seconds * 3

    @property
    def concurrency_limit(self) -> int | None:
        """Потолок обновлений в том виде, в каком его хочет aiogram: ``None`` - без потолка."""
        return self.update_concurrency_limit or None


def load_settings() -> Settings:
    """Собрать настройки. Вызывается один раз, на старте."""
    return Settings()
