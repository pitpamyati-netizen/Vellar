"""Сделка между двумя игроками: кто что предложил и чем это кончилось.

Это существительные групповой экономики. Глаголы - кто вправе ответить, что
вправе закрыться - живут в ``domain/rules/group_offers.py``; тамошние проверки
читают эти объекты и никогда их не меняют.

:class:`Offer` - то, что предлагает один игрок. :class:`TradeRecord` - то же
предложение в том виде, в каком его держит база: то же самое плюс то, что с ним
случилось. Запись существует потому, что за предложением теперь стоит
**настоящая ценность**: предложившая сторона уже рассталась со ставкой
(Roadmap 2.3), поэтому потерянное предложение - это потерянная вещь, а терять
её в хранилище, которое само истекает, недопустимо.

Время здесь - unix-секунды, а не ``datetime``: у домена нет часов, ``now``
приходит аргументом (``Claude.md``, правило 1), и срок обязан значить одно и то
же для PostgreSQL и для адаптера в памяти.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OfferKind(StrEnum):
    """Какая сторона расстаётся с вещью.

    ``SELL`` - автор предлагает свою вещь, а платит тот, кому предложили.
    ``BUY``  - автор предлагает золото за чужую вещь.
    """

    SELL = "sell"
    BUY = "buy"


class TradeStatus(StrEnum):
    """Чем кончилась сделка. Только ``PENDING`` держит что-то в эскроу.

    ``REVERTED`` - закрытая сделка, которую откатил смотритель (``docs/keeper.md``).
    Это отдельное состояние, а не возврат в ``PENDING``: случившееся случилось, и
    журнал обязан продолжать это говорить.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVERTED = "reverted"


@dataclass(frozen=True, slots=True)
class Party:
    """Одна сторона предложения: аккаунт Telegram и персонаж за ним."""

    user_id: int
    character_id: int
    name: str


@dataclass(frozen=True, slots=True)
class Offer:
    """Объявленное предложение, ждущее ответа ровно одного человека."""

    number: int
    kind: OfferKind
    author: Party
    target: Party
    item_id: str
    item_name: str
    price: int
    quantity: int = 1
    created_at: int = 0

    @property
    def giver(self) -> Party:
        """Сторона, которая расстаётся с вещью."""
        return self.author if self.kind is OfferKind.SELL else self.target

    @property
    def payer(self) -> Party:
        """Сторона, которая расстаётся с золотом."""
        return self.target if self.kind is OfferKind.SELL else self.author


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """Одна строка журнала сделок.

    ``scope`` - группа, в которой сделано предложение, чтобы две группы никогда не
    столкнулись на коротких номерах, которые набирают игроки. ``tax``
    проставляется, когда сделка закрылась: сделка, которая не закрылась, никому
    ничего не стоила.

    ``id`` - то, как эту строку называет журнал, и больше её так не называет никто:
    короткий номер, который набирают игроки, переиспользуется, как только
    предложение закрылось, поэтому он называет стоящее предложение и не может
    назвать закрытое. Смотритель, откатывающий сделку, показывает как раз на
    закрытую - ради этого личность и заведена.
    """

    offer: Offer
    scope: str
    status: TradeStatus = TradeStatus.PENDING
    tax: int = 0
    settled_at: int | None = None
    id: int = 0

    @property
    def number(self) -> int:
        return self.offer.number

    @property
    def is_pending(self) -> bool:
        return self.status is TradeStatus.PENDING

    @property
    def is_settled(self) -> bool:
        """Двинула ли эта сделка хоть что-нибудь, а значит, можно ли её откатить."""
        return self.status is TradeStatus.ACCEPTED
