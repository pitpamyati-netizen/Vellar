"""What the bot says in the group.

These are **not** :class:`Screen` objects, and that is deliberate. A screen is a
place the player stands in, with the service row ``Назад · Осмотреться · Главное
меню`` at the bottom; the group is a conversation between people, where the bot
answers a specific message and then gets out of the way. There is nothing to go
back to, so offering a "Назад" would be a lie.

Two constraints shape the wording:

- **no verbs that agree with gender.** A character may be anyone, and Russian
  past tense would force a guess, so every event is stated with nouns:
  "Передача золота: 100 золотых. Отправитель: Аргус."
- **no declension of names.** A player's name is theirs; bending it into the
  dative would mangle half of them. Names always appear after a role word.

The result reads as a short record, which is also the easiest thing to follow by
ear in a fast-moving group chat. The keyboard is only ever two buttons, and those
buttons are literally the typed commands they duplicate (``Narrative.md``,
section 9), so pressing one and typing it produce the same message.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.application.services.group_trade import GroupOutcome, GroupResult
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.trade import Offer, OfferKind
from mmorpg.domain.rules.group_offers import Refusal
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.screens.format import gold, items

ACCEPT_WORD = "Принять"
DECLINE_WORD = "Отказ"


@dataclass(frozen=True, slots=True)
class GroupReply:
    """One message the bot posts in the group.

    ``buttons`` is shown to the target alone (a ``selective`` keyboard), so a
    bystander never sees a button that would refuse them anyway.
    """

    text: str
    buttons: tuple[str, ...] = ()

    @property
    def awaits_answer(self) -> bool:
        return bool(self.buttons)


REFUSALS: dict[Refusal, str] = {
    Refusal.SELF: "Так нельзя: предложение самому себе.",
    Refusal.NO_CHARACTER: (
        "У вас пока нет персонажа. Напишите боту в личные сообщения и создайте его."
    ),
    Refusal.TARGET_HAS_NO_CHARACTER: "У игрока нет персонажа в Велларе.",
    Refusal.UNKNOWN_ITEM: "Такой вещи нет.",
    Refusal.AMBIGUOUS_ITEM: "Подходит несколько вещей, уточните название.",
    Refusal.AUTHOR_LACKS_ITEM: "У вас нет столько.",
    Refusal.TARGET_LACKS_ITEM: "У игрока нет такой вещи.",
    Refusal.AUTHOR_LACKS_GOLD: "У вас не хватает золота.",
    Refusal.TARGET_LACKS_GOLD: "У второй стороны не хватает золота.",
    Refusal.NOT_YOURS: "Предложение не вам: отвечает только тот, кому оно адресовано.",
    Refusal.UNKNOWN_OFFER: "Предложение с таким номером не найдено или уже закрыто.",
    Refusal.EXPIRED: "Предложение просрочено: прошло больше пяти минут.",
    Refusal.TOO_MANY_COMMANDS: "Слишком много команд подряд. Подождите немного.",
    Refusal.TOO_MANY_OFFERS: (
        "Сейчас в группе слишком много открытых предложений. Попробуйте позже."
    ),
    Refusal.PROFILE_HIDDEN: "Игрок закрыл свой профиль.",
    Refusal.BLOCKED_BY_TARGET: "Игрок не ведёт с вами дел.",
    Refusal.BLOCKED_TARGET: ("Игрок у вас в чёрном списке. Снять: ответьте ему словом «разблок»."),
}

# What publishing an offer takes from its author and holds until it is answered.
ESCROW_WORDS: dict[OfferKind, str] = {
    OfferKind.SELL: "Вещь отложена до ответа.",
    OfferKind.BUY: "Золото отложено до ответа.",
}

KIND_WORDS: dict[OfferKind, str] = {
    OfferKind.SELL: "продажа",
    OfferKind.BUY: "покупка",
}


def render(content: GameContent, outcome: GroupOutcome) -> GroupReply:
    """Turn a service outcome into the message the group will see."""
    match outcome.result:
        case GroupResult.PROFILE:
            if outcome.character is None or outcome.stats is None:  # pragma: no cover
                return GroupReply(text=REFUSALS[Refusal.TARGET_HAS_NO_CHARACTER])
            return GroupReply(text=profile_text(content, outcome.character, outcome.stats))
        case GroupResult.PROFILE_CLOSED:
            return GroupReply(
                text=_lines(
                    "Профиль закрыт: в группе его больше не показывают.",
                    "Открыть обратно: «открыть профиль».",
                )
            )
        case GroupResult.PROFILE_OPENED:
            return GroupReply(
                text=_lines(
                    "Профиль открыт: в группе его снова показывают.",
                    "Закрыть: «скрыть профиль».",
                )
            )
        case GroupResult.BLOCK_ADDED:
            return GroupReply(
                text=_lines(
                    f"Чёрный список: {outcome.target_name}.",
                    "Профиль, обмен и передача между вами закрыты.",
                    "Снять: ответьте ему словом «разблок».",
                )
            )
        case GroupResult.BLOCK_REMOVED:
            return GroupReply(
                text=_lines(
                    f"Из чёрного списка: {outcome.target_name}.",
                    "Обмен между вами снова открыт.",
                )
            )
        case GroupResult.GOLD_GIVEN:
            return GroupReply(
                text=_lines(
                    f"Передача золота: {gold(outcome.gold)}.",
                    _sides("Отправитель", outcome.author_name, "Получатель", outcome.target_name),
                )
            )
        case GroupResult.ITEM_GIVEN:
            return GroupReply(
                text=_lines(
                    f"Передача вещи: {_goods(outcome.item_name, outcome.quantity)}.",
                    _sides("Отправитель", outcome.author_name, "Получатель", outcome.target_name),
                )
            )
        case GroupResult.OFFER_MADE | GroupResult.OFFER_ACCEPTED | GroupResult.OFFER_DECLINED:
            if outcome.offer is None:  # pragma: no cover
                return GroupReply(text=REFUSALS[Refusal.UNKNOWN_OFFER])
            return _offer_result(outcome.result, outcome.offer, outcome.tax)
        case GroupResult.REFUSED:
            return GroupReply(text=refusal_text(outcome))


def _offer_result(result: GroupResult, offer: Offer, tax: int) -> GroupReply:
    if result is GroupResult.OFFER_MADE:
        return offer_reply(offer, tax)
    if result is GroupResult.OFFER_ACCEPTED:
        return GroupReply(text=accepted_text(offer, tax))
    # A declined offer gives the author back what the offer was holding.
    return GroupReply(text=f"Предложение {offer.number} отклонено. Отложенное вернулось владельцу.")


def profile_text(content: GameContent, character: Character, stats: DerivedStats) -> str:
    """The target's character, in one message and without their purse.

    Gold and inventory are not shown: what a player carries is their business,
    and a public profile that lists it turns the group into a target list.
    """
    lines = [
        f"{character.name}, уровень {character.level}, "
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {stats.max_health}. {stats.resource_name}: {stats.max_resource}.",
        f"Броня: {stats.armor}. Точность: {stats.accuracy}. Уклонение: {stats.dodge} процентов.",
    ]
    if character.trait_ids:
        names = ", ".join(content.trait(trait_id).name for trait_id in character.trait_ids)
        lines.append(f"Особенности: {names}.")
    return "\n".join(lines)


def offer_reply(offer: Offer, tax: int = 0) -> GroupReply:
    """A published offer, plus the two buttons only its target can see.

    The duty is quoted before anyone agrees to anything: a seller who reads
    "получит 95" and then finds 95 in their purse has been told the truth, and
    the same line explains where the missing five went.
    """
    seller, buyer = offer.giver.name, offer.payer.name
    return GroupReply(
        text=_lines(
            f"Предложение {offer.number}, {KIND_WORDS[offer.kind]}: "
            f"{offer.item_name} за {gold(offer.price)}.",
            _sides("Отдаёт вещь", seller, "Платит", buyer),
            _duty(offer.price, tax),
            f"Отвечает только {offer.target.name}: "
            f"«{ACCEPT_WORD} {offer.number}» или «{DECLINE_WORD} {offer.number}».",
            f"Предложение действует пять минут. {ESCROW_WORDS[offer.kind]}",
        ),
        buttons=answer_buttons(offer.number),
    )


def answer_buttons(number: int) -> tuple[str, ...]:
    """The button texts. Each one is exactly the command it stands for."""
    return (f"{ACCEPT_WORD} {number}", f"{DECLINE_WORD} {number}")


def accepted_text(offer: Offer, tax: int = 0) -> str:
    return _lines(
        f"Предложение {offer.number} принято: {offer.item_name} за {gold(offer.price)}.",
        _sides("Вещь получает", offer.payer.name, "Золото получает", offer.giver.name),
        _duty(offer.price, tax),
    )


def _duty(price: int, tax: int) -> str:
    """The Road Chamber takes its share of every deal (``Narrative.md``, section 1)."""
    if tax <= 0:
        return ""
    return f"Пошлина Палаты: {gold(tax)}. Продавцу на руки: {gold(price - tax)}."


def refusal_text(outcome: GroupOutcome) -> str:
    """A refusal always says what was refused, then why."""
    if outcome.refusal is None:  # pragma: no cover
        return REFUSALS[Refusal.UNKNOWN_OFFER]
    reason = REFUSALS[outcome.refusal]
    if outcome.refusal is Refusal.AMBIGUOUS_ITEM and outcome.options:
        return f"{reason} Подходят: {', '.join(outcome.options)}."
    if outcome.refusal in (Refusal.UNKNOWN_ITEM, Refusal.TARGET_LACKS_ITEM) and outcome.item_name:
        return f"{reason} Искали: {outcome.item_name}."
    return reason


def _goods(name: str, quantity: int) -> str:
    return name if quantity <= 1 else f"{name}, {items(quantity)}"


def _sides(first_role: str, first: str, second_role: str, second: str) -> str:
    return f"{first_role}: {first}. {second_role}: {second}."


def _lines(*lines: str) -> str:
    return "\n".join(line for line in lines if line)
