"""Запрос, который был в воздухе, когда пропала связь.

Пул asyncpg переподключается сам: соединение, которое он держал и которое умерло
в простое, выбрасывается на следующем ``acquire``, а на его место открывается
свежее. Чего пул *не* делает — не выполняет запрос заново, поэтому PostgreSQL,
перезапустившийся между двумя обновлениями, стоил одному игроку действия: тот
читал «Что-то пошло не так» о базе, которая здорова уже секунду.

:class:`ReconnectingPool` стоит между хранилищами и пулом и этот вызов
повторяет. Где он повторять перестаёт — весь смысл модуля, и черта проведена там,
где кончается уверенность (``docs/adr/0009-repeating-a-lost-query.md``):

- **соединение не было получено** — не отправлено ничего, PostgreSQL о запросе не
  слышал, поэтому повторить можно что угодно, запись в том числе;
- **соединение получено, и запрос упал на нём** — ``SELECT`` выполняется заново,
  потому что прочитать дважды значит прочитать то же самое, а ``UPDATE`` — нет:
  сервер мог его закрепить и потерять только ответ на обратном пути, и повтор
  забрал бы золото дважды.

Пул обёрнут, а не унаследован: хранилища по-прежнему зовут ``fetch``,
``fetchrow``, ``fetchval`` и ``execute`` ровно как раньше и обо всём этом не
знают ничего.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy

if TYPE_CHECKING:  # pragma: no cover - только для типов
    import asyncpg

logger = get_logger(__name__)


def lost_connection(error: BaseException) -> bool:
    """Обрыв ли это связи, а не сломанный запрос.

    Ошибка разбора или нарушенное ограничение - это ответ PostgreSQL, и повтор дал бы
    тот же ответ, только медленнее.
    """
    import asyncpg

    if isinstance(
        error,
        asyncpg.exceptions.PostgresConnectionError
        | asyncpg.exceptions.CannotConnectNowError
        | asyncpg.exceptions.TooManyConnectionsError
        | asyncpg.exceptions.AdminShutdownError
        | asyncpg.exceptions.CrashShutdownError,
    ):
        return True
    if isinstance(error, asyncpg.exceptions.InterfaceError):
        # Тот же класс покрывает и обычное неверное обращение («не столько аргументов»),
        # а это ошибка в запросе, и всплыть она обязана сразу.
        return "closed" in str(error).lower()
    # ConnectionResetError и его родня - это OSError, а вышедший срок команды -
    # TimeoutError. И то и другое значит, что ответ не пришёл.
    return isinstance(error, OSError | TimeoutError)


def repeatable(query: str) -> bool:
    """Одно ли и то же - выполнить этот запрос дважды и выполнить его один раз."""
    head = query.lstrip()
    while head.startswith("--"):
        _, _, head = head.partition("\n")
        head = head.lstrip()
    return head[:6].upper() == "SELECT"


def _head(query: str) -> str:
    """Первая строка запроса - достаточно, чтобы узнать его в журнале."""
    return " ".join(query.split())[:60]


class ReconnectingPool:
    """Пул asyncpg с одной добавленной привычкой: он делает пропавший вызов заново."""

    __slots__ = ("_policy", "_pool")

    def __init__(self, pool: asyncpg.Pool, policy: RetryPolicy) -> None:
        self._pool = pool
        self._policy = policy

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetch", query, args, kwargs)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchrow", query, args, kwargs)

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchval", query, args, kwargs)

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("execute", query, args, kwargs)

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        """Собственное соединение - тому, кто ведёт свою транзакцию.

        Выдаётся голым: транзакцию повторяют целиком или не повторяют вовсе, а эта
        обёртка не знает, где она началась.
        """
        return self._pool.acquire(*args, **kwargs)

    async def close(self) -> None:
        await self._pool.close()

    async def _run(
        self, method: str, query: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        repeats = 0
        while True:
            connected = False
            try:
                async with self._pool.acquire() as connection:
                    connected = True
                    result = await getattr(connection, method)(query, *args, **kwargs)
            except Exception as error:
                if not lost_connection(error):
                    raise
                # До того как соединение получено, не отправлено ничего, поэтому
                # безопасно повторить даже запись; после - только чтение.
                may_repeat = not connected or repeatable(query)
                if not may_repeat or repeats >= self._policy.attempts:
                    logger.error(
                        "postgres_call_lost",
                        query=_head(query),
                        repeats=repeats,
                        repeated=may_repeat,
                        error=type(error).__name__,
                    )
                    raise
                repeats += 1
                wait = self._policy.pause(repeats)
                logger.warning(
                    "postgres_repeating",
                    query=_head(query),
                    attempt=repeats,
                    wait=wait,
                    error=type(error).__name__,
                )
                await asyncio.sleep(wait)
            else:
                if repeats:
                    logger.info("postgres_recovered", query=_head(query), repeats=repeats)
                return result
