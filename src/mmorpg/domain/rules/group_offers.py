"""Предложения в группе: что один игрок вправе предложить другому и когда это
действительно может закрыться.

Грамматика живёт в ``group_commands``, существительные - в ``entities/trade``, а
этот модуль о последствиях. Всё здесь держится на трёх правилах:

- **отвечает только тот, кому предложили**: предложение называет одного
  человека, и чужому, нажавшему кнопку, достаётся отказ, а не товар
  (``Narrative.md``, раздел 9);
- **автор ставит своё вперёд**: объявленное предложение вынимает вещь из сумки
  продавца или золото из кошелька покупателя и держит это, пока не ответят
  (Roadmap 2.3). Поэтому предложение - обещание, которое автор уже не нарушит
  по случайности: истратить то, что он выложил на стол, нельзя;
- **тот, кому предложили, не ставит ничего, пока не согласился**: забрать
  золото у того, кто не соглашался, было бы воровством, поэтому его сторона
  читается в тот момент, когда он отвечает, и это единственное, что закрытию
  ещё остаётся проверить.

Часов здесь нет. ``now`` - unix-время, которое передаёт вызывающий, и это то,
что позволяет проверять срок, не дожидаясь пяти минут (``Claude.md``,
правило 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.entities.trade import Offer, OfferKind, Party
from mmorpg.domain.rules.group_commands import GroupIntent, normalise

# Пять минут, из Narrative.md: достаточно, чтобы прочитать сообщение вслух и подумать, и
# достаточно мало, чтобы забытое предложение нельзя было принять назавтра.
OFFER_TTL_SECONDS = 300
MAX_OFFER_NUMBER = 999

# Уборка, возвращающая ставки, идёт на минуту позже срока нарочно: игроку,
# нажавшему «Принять» мгновением позже нужного, надо сказать, что предложение
# вышло по сроку, а не что его не было вовсе, - а такой ответ существует, пока
# существует строка.
SWEEP_GRACE_SECONDS = 60

OFFER_KIND_FOR_INTENT: dict[GroupIntent, OfferKind] = {
    GroupIntent.SELL: OfferKind.SELL,
    GroupIntent.BUY: OfferKind.BUY,
}


class Refusal(StrEnum):
    """Почему бот сказал «нет». Слова живут в слое представления."""

    SELF = "self"
    NO_CHARACTER = "no_character"
    TARGET_HAS_NO_CHARACTER = "target_has_no_character"
    UNKNOWN_ITEM = "unknown_item"
    AMBIGUOUS_ITEM = "ambiguous_item"
    AUTHOR_LACKS_ITEM = "author_lacks_item"
    TARGET_LACKS_ITEM = "target_lacks_item"
    AUTHOR_LACKS_GOLD = "author_lacks_gold"
    TARGET_LACKS_GOLD = "target_lacks_gold"
    NOT_YOURS = "not_yours"
    UNKNOWN_OFFER = "unknown_offer"
    EXPIRED = "expired"
    TOO_MANY_COMMANDS = "too_many_commands"
    TOO_MANY_OFFERS = "too_many_offers"
    PROFILE_HIDDEN = "profile_hidden"
    BLOCKED_BY_TARGET = "blocked_by_target"
    BLOCKED_TARGET = "blocked_target"


def is_expired(offer: Offer, now: int, *, ttl: int = OFFER_TTL_SECONDS) -> bool:
    return now - offer.created_at >= ttl


def sweep_before(now: int) -> int:
    """Момент, когда предложение перестаёт быть отвечаемым *и* перестаёт помниться.

    Всё объявленное в это мгновение или раньше и вышло по сроку, и отстояло свою
    отсрочку, поэтому ставка возвращается автору. Одно выражение, потому что уборка
    теперь случается не в одном месте: перед командой в группе и один раз на старте
    игры (``application.services.group_trade``).
    """
    return now - OFFER_TTL_SECONDS - SWEEP_GRACE_SECONDS


def answerable_by(offer: Offer, user_id: int) -> bool:
    """Отвечает только тот, кому предложили. Уйти вправе оба, согласиться - один."""
    return user_id == offer.target.user_id


def next_number(previous: int) -> int:
    """Номера предложений короткие, потому что их набирают руками: от 1 до 999 и по кругу."""
    return previous % MAX_OFFER_NUMBER + 1


# --- escrow ----------------------------------------------------------


def stakes_item(offer: Offer) -> bool:
    """Вынуло ли объявление этого предложения вещь из сумки автора."""
    return offer.kind is OfferKind.SELL


def stakes_gold(offer: Offer) -> bool:
    """Вынуло ли объявление этого предложения золото из кошелька автора."""
    return offer.kind is OfferKind.BUY


# --- checks ----------------------------------------------------------


def check_proposal(
    *,
    kind: OfferKind,
    author: Party,
    target: Party,
    giver_holds: int,
    quantity: int,
    price: int,
    author_gold: int,
) -> Refusal | None:
    """Можно ли вообще объявить это предложение.

    Вещь проверяется у той стороны, которая должна её отдать, - потому что так же её
    и называли. Золото **автора** проверяется тоже, и только автора: покупатель
    ставит свои деньги в ту минуту, когда предлагает, а чтение чужого кошелька
    ответило бы на вопрос, которого никто не задавал, и выдало бы остаток всей
    группе.
    """
    if author.user_id == target.user_id:
        return Refusal.SELF
    if giver_holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM if kind is OfferKind.SELL else Refusal.TARGET_LACKS_ITEM
    if kind is OfferKind.BUY and author_gold < price:
        return Refusal.AUTHOR_LACKS_GOLD
    return None


def check_settlement(
    offer: Offer, *, target_holds: int, target_gold: int, now: int
) -> Refusal | None:
    """Можно ли исполнить предложение прямо сейчас.

    Читается только тот, кому предложили: сторона автора лежит в эскроу с той
    минуты, как предложение объявили, поэтому измениться мог лишь кошелёк - или
    сумка - того, кто отвечает.
    """
    if is_expired(offer, now):
        return Refusal.EXPIRED
    if offer.kind is OfferKind.SELL and target_gold < offer.price:
        return Refusal.TARGET_LACKS_GOLD
    if offer.kind is OfferKind.BUY and target_holds < offer.quantity:
        return Refusal.TARGET_LACKS_ITEM
    return None


def check_gift(*, author: Party, target: Party, holds: int, quantity: int) -> Refusal | None:
    """Передача не просит ничего взамен, поэтому ей не нужно подтверждение - только наличие."""
    if author.user_id == target.user_id:
        return Refusal.SELF
    if holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM
    return None


def check_contact(*, blocked_by_target: bool, blocks_target: bool) -> Refusal | None:
    """Имеют ли эти двое дело друг с другом вообще (Roadmap 2.5).

    Блокировка работает в обе стороны нарочно. Кто бы ни провёл черту, пара закрыта:
    односторонняя блокировка позволила бы блокирующему и дальше слать предложения
    тому, кто уже сказал «нет», а чёрный список существует ровно затем, чтобы этого
    не было.
    """
    if blocked_by_target:
        return Refusal.BLOCKED_BY_TARGET
    if blocks_target:
        return Refusal.BLOCKED_TARGET
    return None


def check_profile(*, visible: bool) -> Refusal | None:
    """Закрытый профиль - это отказ, а не молчание: спросившему ответ причитается."""
    return None if visible else Refusal.PROFILE_HIDDEN


def check_gold_gift(*, author: Party, target: Party, purse: int, amount: int) -> Refusal | None:
    if author.user_id == target.user_id:
        return Refusal.SELF
    if purse < amount:
        return Refusal.AUTHOR_LACKS_GOLD
    return None


# --- как игрок называет товар ---------------------------------------


@dataclass(frozen=True, slots=True)
class ItemOption:
    """Один из тех, кого игрок мог иметь в виду."""

    item_id: str
    name: str


def match_items(query: str, catalogue: Sequence[ItemOption]) -> tuple[ItemOption, ...]:
    """Свести набранное имя вещи с тем, что у человека и правда есть.

    Игроки не набирают идентификаторы, они набирают «кожаная броня», а иногда просто
    «броня». Поэтому сверка идёт от строгой к вольной и останавливается на первой
    ступени, что-то нашедшей: точное имя сильнее начала, начало сильнее вхождения.
    Вернуть несколько кандидатов — не отказ, это значит «скажите, какая именно», а
    угадывание двинуло бы не тот товар.
    """
    wanted = normalise(query)
    if not wanted:
        return ()

    exact = [option for option in catalogue if normalise(option.name) == wanted]
    if exact:
        return tuple(exact)

    # Идентификатор тоже работает - для тех, кто читает файлы содержимого.
    by_id = [option for option in catalogue if option.item_id.casefold() == wanted]
    if by_id:
        return tuple(by_id)

    starts = [option for option in catalogue if normalise(option.name).startswith(wanted)]
    if starts:
        return tuple(starts)

    return tuple(option for option in catalogue if wanted in normalise(option.name))
