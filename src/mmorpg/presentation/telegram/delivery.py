"""Telegram получает сохранённые сообщения после записи игрового действия."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message

from mmorpg.application.delivery import (
    DeliveryFailedError,
    DeliveryPolicy,
    DeliveryPriority,
    DeliveryQueue,
    DeliveryStatus,
)
from mmorpg.application.operations import current_operation, json_fallback
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.cleanup import MessageReaper

logger = get_logger(__name__)
_priority: ContextVar[DeliveryPriority] = ContextVar(
    "delivery_priority", default=DeliveryPriority.REPLY
)


@contextmanager
def delivery_priority(priority: DeliveryPriority) -> Iterator[None]:
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


class DeliveryPendingError(RuntimeError):
    """Ответ сохранён и остаётся в очереди после временного отказа Telegram."""


class DeliveryWorker:
    def __init__(
        self,
        queue: DeliveryQueue,
        bot: Bot,
        *,
        sends_per_second: int = 30,
        policy: DeliveryPolicy | None = None,
        send_timeout: float = 30.0,
        poll_interval: float = 0.05,
        reaper: MessageReaper | None = None,
        recover: Callable[[], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.queue = queue
        self.bot = bot
        self.policy = policy or DeliveryPolicy(sends_per_second=sends_per_second)
        if send_timeout <= 0 or send_timeout >= self.policy.lease_seconds:
            raise ValueError("The send timeout must fit inside the delivery lease")
        self._send_timeout = send_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._reaper = reaper
        self._recover = recover
        self._task: asyncio.Task[None] | None = None
        self._dispatches: set[asyncio.Task[bool]] = set()
        self._wake = asyncio.Event()
        self.last_progress = time.monotonic()

    def healthy(self, limit: float) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and time.monotonic() - self.last_progress <= limit
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                delivered = await self.run_once()
            except Exception as error:
                # SQL может временно не отвечать. Сообщение или lease остаются
                # в базе; журнал не содержит текста ответа или ошибки Telegram.
                logger.warning("delivery_worker_unavailable", error=type(error).__name__)
                delivered = False
            if not delivered:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)

    async def run_once(self) -> bool:
        if current_operation() is not None:
            raise RuntimeError("Telegram delivery must run after the operation commits")
        if self._recover is not None:
            await self._recover()
        row = await self.queue.claim(self.bot.id, self.policy)
        self.last_progress = time.monotonic()
        if row is None:
            return False
        try:
            method = SendMessage.model_validate(row.payload)
            method.parse_mode = None
            # make_request обходит middleware: повтор и бюджет принадлежат
            # очереди, каждая настоящая попытка занимает новое место в SQL.
            async with asyncio.timeout(self._send_timeout):
                sent = await self.bot.session.make_request(
                    self.bot, method, timeout=max(1, int(self._send_timeout))
                )
        except asyncio.CancelledError:
            # При остановке неизвестно, дошёл ли запрос до Telegram. Lease
            # завершится, новый worker повторит его, иногда получится дубль.
            raise
        except TelegramRetryAfter as error:
            await self.queue.retry(
                row.key,
                row.lease_token,
                delay=float(error.retry_after),
                error=type(error).__name__,
                cooldown=True,
            )
        except TelegramEntityTooLarge as error:
            await self.queue.fail(row.key, row.lease_token, error=type(error).__name__)
        except TelegramAPIError as error:
            if isinstance(error, TelegramNetworkError | TelegramServerError):
                await self.queue.retry(
                    row.key,
                    row.lease_token,
                    delay=min(60.0, 2.0 ** min(row.attempts - 1, 6)),
                    error=type(error).__name__,
                )
            else:
                await self.queue.fail(row.key, row.lease_token, error=type(error).__name__)
        except (TimeoutError, OSError) as error:
            await self.queue.retry(
                row.key,
                row.lease_token,
                delay=min(60.0, 2.0 ** min(row.attempts - 1, 6)),
                error=type(error).__name__,
            )
        except Exception as error:
            # Нераспознаваемый/устаревший payload не задерживает весь чат.
            await self.queue.fail(row.key, row.lease_token, error=type(error).__name__)
        else:
            completed = await self.queue.complete(
                row.key, row.lease_token, sent.model_dump(mode="json", exclude_none=True)
            )
            if (
                completed
                and self._reaper is not None
                and isinstance(row.chat_id, int)
                and row.chat_id < 0
            ):
                self._reaper.schedule(self.bot.delete_message, row.chat_id, sent.message_id)
        return True

    async def deliver(self, key: str, *, wait_seconds: float = 5.0) -> Message | None:
        """Дождаться короткой очереди; при отказе оставить доставку фоновому worker."""
        self.wake()
        attempted = False
        async with asyncio.timeout(wait_seconds):
            while True:
                row = await self.queue.get(key)
                if row is None:
                    raise ValueError("Message must be enqueued before delivery")
                if row.status == DeliveryStatus.SENT:
                    return Message.model_validate(row.response, context={"bot": self.bot})
                if row.status == DeliveryStatus.FAILED:
                    raise DeliveryFailedError(key)
                if attempted and row.status == DeliveryStatus.QUEUED and row.last_error:
                    return None
                # Короткое ожидание вызывающего не обрывает сам запрос. Даже
                # если экран уже перестал ждать, сообщение и SQL ACK завершатся.
                attempt = asyncio.create_task(self.run_once())
                self._dispatches.add(attempt)
                attempt.add_done_callback(self._dispatch_finished)
                delivered = await asyncio.shield(attempt)
                attempted = True
                if not delivered:
                    if row.status == DeliveryStatus.QUEUED and row.last_error:
                        return None
                    await self._sleep(self._poll_interval)

    def _dispatch_finished(self, task: asyncio.Task[bool]) -> None:
        self._dispatches.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("delivery_attempt_unavailable", error=type(task.exception()).__name__)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        active = tuple(self._dispatches)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)


class DurableSendMiddleware(BaseRequestMiddleware):
    def __init__(self, queue: DeliveryQueue, worker: DeliveryWorker) -> None:
        self.queue = queue
        self.worker = worker

    async def __call__(
        self,
        make_request: Callable[[Bot, TelegramMethod[Any]], Awaitable[Any]],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        if not isinstance(method, SendMessage):
            return await make_request(bot, method)
        operation = current_operation()
        primary = (
            operation is not None
            and operation.command
            and method.chat_id == operation.reply_chat_id
        )
        payload = method.model_dump(
            mode="json",
            exclude_none=True,
            fallback=lambda value: None if isinstance(value, Default) else json_fallback(value),
        )
        # Reply Keyboard и линейный текст переживают рестарт без изменения.
        payload["parse_mode"] = None
        if primary:
            assert operation is not None
            operation.replies.append(
                {
                    name: value
                    for name, value in payload.items()
                    if name
                    in {"chat_id", "text", "reply_markup", "reply_parameters", "message_thread_id"}
                }
            )
        sequence = len(operation.after_commit) + 1 if operation is not None else 1
        key = (
            f"delivery:{bot.id}:{operation.id}:{sequence}"
            if operation is not None
            else f"delivery:{bot.id}:{uuid4()}"
        )
        priority = _priority.get()
        if operation is not None and not primary:
            priority = DeliveryPriority.ANNOUNCEMENT
        await self.queue.enqueue(
            key=key,
            bot_id=bot.id,
            chat_id=method.chat_id,
            payload=payload,
            priority=priority,
            operation_id=operation.id if operation is not None else None,
        )
        response = Message(
            message_id=-sequence,
            date=datetime.fromtimestamp(0, UTC),
            chat=Chat(id=method.chat_id if isinstance(method.chat_id, int) else 0, type="private"),
            text=method.text,
        )
        if operation is not None:

            async def deliver() -> None:
                try:
                    sent = await self.worker.deliver(key)
                except DeliveryFailedError, TimeoutError:
                    # Эффект уже сохранён, сообщение остаётся в очереди либо
                    # отмечено постоянным отказом. Остальные ответы продолжаются.
                    logger.warning("operation_delivery_pending", operation_id=operation.id)
                    self.worker.wake()
                    return
                if sent is not None:
                    operation.sent_messages[response.message_id] = sent.message_id

            operation.after_commit.append(deliver)
            return response
        self.worker.wake()
        if priority == DeliveryPriority.ANNOUNCEMENT:
            return response
        try:
            sent = await self.worker.deliver(key)
        except TimeoutError as error:
            raise DeliveryPendingError(key) from error
        if sent is None:
            raise DeliveryPendingError(key)
        return sent
