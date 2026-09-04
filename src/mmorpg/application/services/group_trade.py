"""Что на самом деле делает команда в группе.

Хендлер разбирает сообщение и печатает фразу; всё, что между этими двумя делами,
происходит здесь (``Claude.md``, правило 5). Этот сервис знает хранилища и
правила домена и ничего не знает о Telegram: он принимает идентификаторы
аккаунтов и время, а возвращает исход, который слой представления превращает в
слова.

Рядом живут два разных вида действий:

- **передача** случается сразу: получателю она ничего не стоит, и просить у него
  подтверждения подарка значило бы добавить лишний шаг (``Narrative.md``,
  раздел 9);
- **предложение** чего-то стоит обеим сторонам, поэтому оно объявляется, получает
  номер и пять минут ждёт ответа того, кому предложили.

Предложение держит сторону автора в эскроу. Объявленная продажа вынимает вещь из
сумки продавца, объявленная покупка - золото из кошелька покупателя. И то и
другое возвращается в ту минуту, когда предложение отклонили или оно вышло по
сроку. Тот, кому предложили, не ставит ничего, пока не ответил, потому что его
пока ни о чём не просили.

Из этого следуют три вещи, и в них весь смысл Roadmap 2.3:

- предложение не может сорваться из-за того, что *автор* тем временем истратил
  свою сторону: тратить ему уже нечего;
- эскроу переживает перезапуск, потому что живёт в журнале сделок в PostgreSQL,
  а не в кэше, истекающем самом по себе;
- каждая закрытая сделка платит пошлину, и это единственное место, где золото
  уходит из игры (``domain/rules/economy.trade_tax``).

А вот то, *чем* отвечает вторая сторона, обязано быть у неё в ту самую минуту,
когда она отвечает, и проверяется на шаг раньше. Поэтому каждый кошелёк в этом
модуле двигается через ``spend_gold``/``grant_gold`` - одним условным шагом в
базе - и никогда записью персонажа, прочитанного несколько ``await`` назад.
Проверить кошелёк и записать кошелёк - два разных мгновения, и игрок, который
дерётся в личной переписке, пока закрывается его предложение в группе, живёт
как раз между ними.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg import economy_log
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.trade import (
    Offer,
    OfferKind,
    Party,
    TradeRecord,
    TradeStatus,
)
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    PrivacyRepository,
    TradeRepository,
)
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules.economy import payout, refund, trade_tax
from mmorpg.domain.rules.group_commands import GroupCommand, GroupIntent
from mmorpg.domain.rules.group_offers import (
    OFFER_KIND_FOR_INTENT,
    ItemOption,
    Refusal,
    answerable_by,
    check_contact,
    check_gift,
    check_gold_gift,
    check_profile,
    check_proposal,
    check_settlement,
    match_items,
    stakes_gold,
    stakes_item,
    sweep_before,
)
from mmorpg.domain.rules.stats import DerivedStats, derived_stats


async def return_stake(
    offer: Offer, *, characters: CharacterRepository, inventory: InventoryRepository
) -> None:
    """Вернуть автору то, что забрало у него объявление предложения.

    Отдельная функция, а не метод, потому что ставку возвращают в двух очень разных
    случаях: групповым сервисом на ходу игры и :func:`release_expired_offers` на её
    старте. Обоим двигать одни и те же две вещи.
    """
    if stakes_item(offer):
        await inventory.add(offer.author.character_id, offer.item_id, offer.quantity)
        return
    await characters.grant_gold(offer.author.character_id, offer.price)


async def release_expired_offers(
    *,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    now: int,
) -> int:
    """Откатить все предложения, вышедшие по сроку без ответа. Возвращает, сколько.

    Уборка перед каждой командой в группе (``GroupTrade._sweep``) - обычный способ,
    которым это происходит, и его хватает, пока кто-то говорит. Самого по себе его
    мало: предложение, объявленное в группе, которая потом затихла, - или в группе,
    откуда бота убрали, - держало бы вещь автора, пока эта группа не заговорит
    снова, а она может и не заговорить. Поэтому игра убирает ещё и один раз на
    старте, а уборка в группе больше не смотрит на одну область.

    Это не таймер (``Claude.md``, правило 3): ничего не откладывается и ничто не
    просыпается. Это случается один раз там, где игра и так останавливается, чтобы
    себя собрать.
    """
    stale = await trades.expire(before=sweep_before(now))
    for record in stale:
        await return_stake(record.offer, characters=characters, inventory=inventory)
    return len(stale)


@dataclass(frozen=True, slots=True)
class Rollback:
    """Чем кончился откат сделки. Числами, а фразу составит экран."""

    #: Сделка, какой она была до отката. ``None`` — откатывать было нечего.
    record: TradeRecord | None = None
    #: Вернулась ли вещь тому, кто её отдал.
    item_returned: bool = False
    #: Сколько золота вернулось плательщику.
    gold_returned: int = 0
    #: Сколько золота вернуть не удалось: у получателя его уже нет.
    gold_missing: int = 0

    @property
    def done(self) -> bool:
        return self.record is not None

    @property
    def whole(self) -> bool:
        """Вернулось ли всё, что двигала сделка."""
        return self.done and self.item_returned and not self.gold_missing


async def roll_back(
    trade_id: int,
    *,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
) -> Rollback:
    """Отменить расчёт, который уже прошёл. Работа смотрителя (``docs/keeper.md``).

    Строку журнала переводит в «откачено» само хранилище и только один раз
    (``TradeRepository.revert``), поэтому два смотрителя, нажавшие вместе,
    двигают вещи один раз.

    Вещь и золото возвращаются порознь, и это решение, а не недосмотр. Откат
    нужен там, где сделку признали обманом, а обманувший к этому времени успел и
    вещь надеть, и золото потратить. Требовать, чтобы вернулось всё или ничего,
    значило бы, что чаще всего не возвращается ничего. Поэтому возвращается то,
    что есть, а чего нет — названо числом, и остаток смотритель выдаёт руками.

    Плательщику приходит ровно то, что получил продавец: пошлина ушла из игры в
    момент расчёта, и вернуть её означало бы её напечатать
    (``domain/rules/economy.refund``).
    """
    record = await trades.revert(trade_id)
    if record is None:
        return Rollback()

    offer = record.offer
    # Вещь у того, кто за неё платил, — в обе стороны: и в продаже, и в скупке.
    returned = await inventory.remove(offer.payer.character_id, offer.item_id, offer.quantity)
    if returned:
        await inventory.add(offer.giver.character_id, offer.item_id, offer.quantity)

    owed = refund(offer.price, record.tax)
    paid_back = bool(owed) and await characters.spend_gold(offer.giver.character_id, owed)
    if paid_back:
        await characters.grant_gold(offer.payer.character_id, owed)
        economy_log.record(
            economy_log.TRADE_ROLLBACK,
            -owed,
            character_id=offer.giver.character_id,
            detail=f"trade {trade_id}",
        )
        economy_log.record(
            economy_log.TRADE_ROLLBACK,
            owed,
            character_id=offer.payer.character_id,
            detail=f"trade {trade_id}",
        )
    return Rollback(
        record=record,
        item_returned=returned,
        gold_returned=owed if paid_back else 0,
        gold_missing=0 if paid_back else owed,
    )


class GroupResult(StrEnum):
    """Что произошло, в тех же словах, какими это опишет сообщение в группе."""

    PROFILE = "profile"
    PROFILE_CLOSED = "profile_closed"
    PROFILE_OPENED = "profile_opened"
    BLOCK_ADDED = "block_added"
    BLOCK_REMOVED = "block_removed"
    GOLD_GIVEN = "gold_given"
    ITEM_GIVEN = "item_given"
    OFFER_MADE = "offer_made"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    PARTY_INVITED = "party_invited"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class GroupOutcome:
    """Итог одной команды в группе, готовый к тому, чтобы его высказали."""

    result: GroupResult
    refusal: Refusal | None = None
    offer: Offer | None = None
    character: Character | None = None
    stats: DerivedStats | None = None
    item_name: str = ""
    quantity: int = 1
    gold: int = 0
    # Пошлина с этой сделки: берётся при закрытии, называется при объявлении.
    tax: int = 0
    author_name: str = ""
    target_name: str = ""
    # Кандидаты, стоящие за AMBIGUOUS_ITEM, чтобы ответ мог их перечислить.
    options: tuple[str, ...] = ()
    # Отказ, у которого нет своего названия: правила отряда отказывают целой
    # фразой, и придумывать ей ещё и имя незачем (``domain/rules/party.py``).
    reason: str = ""
    # Кого позвали в отряд: телеграм-номер, чтобы зов дошёл и в личные сообщения.
    invited_user_id: int = 0


def _refused(
    refusal: Refusal,
    *,
    offer: Offer | None = None,
    item_name: str = "",
    quantity: int = 1,
    options: tuple[str, ...] = (),
    author_name: str = "",
    target_name: str = "",
) -> GroupOutcome:
    return GroupOutcome(
        result=GroupResult.REFUSED,
        refusal=refusal,
        offer=offer,
        item_name=item_name,
        quantity=quantity,
        options=options,
        author_name=author_name,
        target_name=target_name,
    )


@dataclass(frozen=True, slots=True)
class GroupTrade:
    """Работа группы поверх хранилищ. Один образец на приложение."""

    content: GameContent
    characters: CharacterRepository
    inventory: InventoryRepository
    trades: TradeRepository
    privacy: PrivacyRepository
    # Отряд лежит не в базе, а в хранилище со сроком, поэтому он приходит
    # отдельно (``application/services/party.py``).
    parties: PartyStore | None = None
    # Предложения нумеруются внутри группы, поэтому две группы никогда не дерутся за
    # «принять 7».
    scope: str = "group"

    # --- точки входа --------------------------------------------------

    async def run(
        self,
        command: GroupCommand,
        *,
        author_id: int,
        target_id: int | None,
        now: int,
    ) -> GroupOutcome:
        """Выполнить одну разобранную команду. ``target_id`` - тот, кому автор ответил."""
        await self._sweep(now)

        author_character = await self.characters.get_active(author_id)
        if author_character is None:
            return _refused(Refusal.NO_CHARACTER)
        author = _party(author_id, author_character)

        if command.intent in (GroupIntent.ACCEPT, GroupIntent.DECLINE):
            return await self._answer(
                command.amount,
                accept=command.intent is GroupIntent.ACCEPT,
                answering=author,
                now=now,
            )
        if command.intent in (GroupIntent.HIDE_PROFILE, GroupIntent.SHOW_PROFILE):
            return await self._set_visible(
                author, visible=command.intent is GroupIntent.SHOW_PROFILE
            )

        if target_id is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target_character = await self.characters.get_active(target_id)
        if target_character is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target = _party(target_id, target_character)

        # Провести черту можно всегда и в любую сторону: чёрный список, который
        # заморозила бы чужая блокировка, был бы ловушкой, а не списком.
        if command.intent in (GroupIntent.BLOCK, GroupIntent.UNBLOCK):
            return await self._list_entry(
                author, target, adding=command.intent is GroupIntent.BLOCK, now=now
            )

        wall = check_contact(
            blocked_by_target=await self.privacy.blocks(target.user_id, author.user_id),
            blocks_target=await self.privacy.blocks(author.user_id, target.user_id),
        )
        if wall is not None:
            return _refused(wall, author_name=author.name, target_name=target.name)

        match command.intent:
            case GroupIntent.PROFILE:
                # Собственную карточку игрок видит всегда, закрыта она или нет.
                if author.user_id != target.user_id:
                    hidden = check_profile(
                        visible=await self.privacy.profile_visible(target.user_id)
                    )
                    if hidden is not None:
                        return _refused(hidden, target_name=target.name)
                return GroupOutcome(
                    result=GroupResult.PROFILE,
                    character=target_character,
                    stats=derived_stats(self.content, target_character),
                    target_name=target.name,
                )
            case GroupIntent.PARTY_INVITE:
                return await self._invite(author, author_character, target, target_character)
            case GroupIntent.GIVE_GOLD:
                return await self._give_gold(command, author, target)
            case GroupIntent.GIVE_ITEM:
                return await self._give_item(command, author, target)
            # Сюда больше не доходит ничего: ответы, приватность и чёрный список
            # разобраны выше, а других намерений разборщик не порождает.
            case _:
                return await self._propose(command, author, target, now=now)

    # --- отряд ----------------------------------------------------------

    async def _invite(
        self,
        author: Party,
        inviter: Character,
        target: Party,
        invitee: Character,
    ) -> GroupOutcome:
        """Позвать в отряд ответом на сообщение. Согласие даёт позванный сам.

        Отряд к этому времени уже заведён: звать умеет только тот, у кого он
        есть (``domain/rules/party.invite_refusal``). Правило одно на все три
        дороги - имя, кнопку на узле и этот ответ в группе.
        """
        if self.parties is None:  # pragma: no cover - отряд есть везде, где есть игра
            return GroupOutcome(result=GroupResult.REFUSED, reason="Отряды сейчас недоступны.")
        mine = await self.parties.of(author.character_id)
        theirs = await self.parties.of(target.character_id)
        refused = party_rules.invite_refusal(
            inviter_level=inviter.level,
            invitee_name=target.name,
            invitee_level=invitee.level,
            party=mine,
            invitee_in_party=theirs is not None,
        )
        if refused or mine is None:
            return GroupOutcome(
                result=GroupResult.REFUSED,
                reason=refused,
                author_name=author.name,
                target_name=target.name,
            )
        await self.parties.call(leader_id=mine.leader_id, invitee_id=target.character_id)
        return GroupOutcome(
            result=GroupResult.PARTY_INVITED,
            author_name=author.name,
            target_name=target.name,
            invited_user_id=target.user_id,
        )

    # --- приватность --------------------------------------------------

    async def _set_visible(self, author: Party, *, visible: bool) -> GroupOutcome:
        """Открыть или закрыть карточку. Сказать это дважды - не ошибка."""
        await self.privacy.set_profile_visible(author.user_id, visible)
        return GroupOutcome(
            result=GroupResult.PROFILE_OPENED if visible else GroupResult.PROFILE_CLOSED,
            author_name=author.name,
        )

    async def _list_entry(
        self, author: Party, target: Party, *, adding: bool, now: int
    ) -> GroupOutcome:
        """Занести кого-то в чёрный список или убрать оттуда."""
        if author.user_id == target.user_id:
            return _refused(Refusal.SELF, author_name=author.name, target_name=target.name)
        if adding:
            await self.privacy.block(author.user_id, target.user_id, at=now)
        else:
            await self.privacy.unblock(author.user_id, target.user_id)
        # Ответ называет состояние, а не изменение, поэтому повторная команда читается
        # так же, как первая, - именно это и нужно игроку, потерявшему ответ.
        return GroupOutcome(
            result=GroupResult.BLOCK_ADDED if adding else GroupResult.BLOCK_REMOVED,
            author_name=author.name,
            target_name=target.name,
        )

    # --- передачи -----------------------------------------------------

    async def _give_gold(self, command: GroupCommand, author: Party, target: Party) -> GroupOutcome:
        giver = await self._character(author)
        refusal = check_gold_gift(
            author=author, target=target, purse=giver.gold, amount=command.amount
        )
        if refusal is not None:
            return _refused(refusal, author_name=author.name, target_name=target.name)

        # Обе стороны двигаются приращениями, а не записью персонажа целиком: кошелёк,
        # прочитанный мгновением раньше, уже устарел, если его владелец играет.
        if not await self.characters.spend_gold(author.character_id, command.amount):
            return _refused(
                Refusal.AUTHOR_LACKS_GOLD, author_name=author.name, target_name=target.name
            )
        await self.characters.grant_gold(target.character_id, command.amount)
        return GroupOutcome(
            result=GroupResult.GOLD_GIVEN,
            gold=command.amount,
            author_name=author.name,
            target_name=target.name,
        )

    async def _give_item(self, command: GroupCommand, author: Party, target: Party) -> GroupOutcome:
        found = await self._resolve(command.item_query, author)
        if isinstance(found, GroupOutcome):
            return replace(found, author_name=author.name, target_name=target.name)

        held = await self.inventory.count(author.character_id, found.item_id)
        refusal = check_gift(author=author, target=target, holds=held, quantity=command.amount)
        if refusal is not None:
            return _refused(
                refusal,
                item_name=found.name,
                quantity=command.amount,
                author_name=author.name,
                target_name=target.name,
            )

        await self._move_item(found.item_id, command.amount, author, target)
        return GroupOutcome(
            result=GroupResult.ITEM_GIVEN,
            item_name=found.name,
            quantity=command.amount,
            author_name=author.name,
            target_name=target.name,
        )

    # --- offers -------------------------------------------------------

    async def _propose(
        self, command: GroupCommand, author: Party, target: Party, *, now: int
    ) -> GroupOutcome:
        kind = OFFER_KIND_FOR_INTENT[command.intent]
        owner = author if kind is OfferKind.SELL else target

        found = await self._resolve(command.item_query, owner)
        if isinstance(found, GroupOutcome):
            return replace(found, author_name=author.name, target_name=target.name)

        purse = await self._character(author)
        refusal = check_proposal(
            kind=kind,
            author=author,
            target=target,
            giver_holds=await self.inventory.count(owner.character_id, found.item_id),
            quantity=1,
            price=command.amount,
            author_gold=purse.gold,
        )
        if refusal is not None:
            return _refused(
                refusal,
                item_name=found.name,
                author_name=author.name,
                target_name=target.name,
            )

        record = await self.trades.open(
            Offer(
                # Номер, который будут набирать игроки, выдаёт хранилище; до тех пор у
                # этого предложения номера нет.
                number=0,
                kind=kind,
                author=author,
                target=target,
                item_id=found.item_id,
                item_name=found.name,
                price=command.amount,
                created_at=now,
            ),
            scope=self.scope,
        )
        if record is None:
            return _refused(
                Refusal.TOO_MANY_OFFERS, author_name=author.name, target_name=target.name
            )

        # Строка появляется раньше, чем двинется ставка, поэтому двинувшаяся ставка
        # всегда где-то записана. Если двинуть её нельзя, строка тут же закрывается
        # снова.
        if not await self._take_stake(record.offer):
            await self.trades.close(
                record.number, scope=self.scope, status=TradeStatus.DECLINED, settled_at=now
            )
            lacking = (
                Refusal.AUTHOR_LACKS_ITEM
                if stakes_item(record.offer)
                else Refusal.AUTHOR_LACKS_GOLD
            )
            return _refused(
                lacking, item_name=found.name, author_name=author.name, target_name=target.name
            )

        return GroupOutcome(
            result=GroupResult.OFFER_MADE,
            offer=record.offer,
            tax=trade_tax(record.offer.price),
        )

    async def _answer(
        self, number: int, *, accept: bool, answering: Party, now: int
    ) -> GroupOutcome:
        record = await self.trades.pending(number, scope=self.scope)
        if record is None:
            return _refused(Refusal.UNKNOWN_OFFER)
        offer = record.offer

        # Уйти от предложения вправе любая сторона; согласиться - только тот, кому
        # предложили.
        if not accept and answering.user_id in (offer.target.user_id, offer.author.user_id):
            closed = await self._close(number, TradeStatus.DECLINED, now=now)
            if closed is None:
                return _refused(Refusal.UNKNOWN_OFFER)
            return GroupOutcome(result=GroupResult.OFFER_DECLINED, offer=offer)
        if not answerable_by(offer, answering.user_id):
            return _refused(Refusal.NOT_YOURS, offer=offer)

        # Блокировка, проведённая, пока предложение стояло, его отменяет: ставка уходит
        # обратно автору, и ни одна сторона не остаётся ждать дела, от которого
        # отказалась.
        wall = check_contact(
            blocked_by_target=await self.privacy.blocks(offer.author.user_id, answering.user_id),
            blocks_target=await self.privacy.blocks(answering.user_id, offer.author.user_id),
        )
        if wall is not None:
            await self._close(number, TradeStatus.DECLINED, now=now)
            return _refused(wall, offer=offer)

        target = await self._character(offer.target)
        refusal = check_settlement(
            offer,
            target_holds=await self.inventory.count(offer.target.character_id, offer.item_id),
            target_gold=target.gold,
            now=now,
        )
        if refusal is not None:
            status = TradeStatus.EXPIRED if refusal is Refusal.EXPIRED else TradeStatus.DECLINED
            await self._close(number, status, now=now)
            return _refused(refusal, offer=offer)

        return await self._settle(record, now=now)

    async def _settle(self, record: TradeRecord, *, now: int) -> GroupOutcome:
        """Двинуть всё, один раз. Кому это позволено, решает строка сделки."""
        offer = record.offer
        tax = trade_tax(offer.price)

        # Покупка сначала забирает вещь у второй стороны, потому что только её изъятие
        # неделимо: если вещи не стало ровно в это мгновение, ещё ничего не случилось, и
        # предложение просто отказывает.
        took_item = stakes_gold(offer) and await self.inventory.remove(
            offer.target.character_id, offer.item_id, offer.quantity
        )
        if stakes_gold(offer) and not took_item:
            await self._close(offer.number, TradeStatus.DECLINED, now=now)
            return _refused(Refusal.TARGET_LACKS_ITEM, offer=offer)

        # Сторона того, кому предложили, забирается до закрытия строки и забирается
        # неделимо: покупатель, отвечающий на продажу, платит из кошелька, который может
        # тратить в это самое мгновение, а ``check_settlement`` его только прочитал.
        # Покупатель, который сам *предложил* покупку, заплатил при объявлении, и его
        # золото уже в эскроу.
        paid = stakes_item(offer) and await self.characters.spend_gold(
            offer.payer.character_id, offer.price
        )
        if stakes_item(offer) and not paid:
            await self._close(offer.number, TradeStatus.DECLINED, now=now)
            if took_item:  # pragma: no cover - продажа никогда не забирает вещь первой
                await self.inventory.add(offer.target.character_id, offer.item_id, offer.quantity)
            return _refused(Refusal.TARGET_LACKS_GOLD, offer=offer)

        closed = await self._close(offer.number, TradeStatus.ACCEPTED, now=now, tax=tax)
        if closed is None:
            # Кто-то ответил раньше. Отменить всё, что уже сделано.
            if took_item:
                await self.inventory.add(offer.target.character_id, offer.item_id, offer.quantity)
            if paid:
                await self.characters.grant_gold(offer.payer.character_id, offer.price)
            return _refused(Refusal.UNKNOWN_OFFER)

        # Вещь уходит тому, кто за неё заплатил, как бы предложение ни было сказано: из
        # эскроу при продаже и прямо от второй стороны при покупке.
        await self.inventory.add(offer.payer.character_id, offer.item_id, offer.quantity)

        # Продавцу платят из эскроу или из кошелька покупателя; так или иначе пошлина
        # просто не зачисляется никому. Записываются обе половины, потому что «пять
        # процентов - верное ли число» - вопрос о дне настоящей торговли, а не об
        # арифметике (``mmorpg.economy_log``).
        await self.characters.grant_gold(offer.giver.character_id, payout(offer.price))
        economy_log.record(
            economy_log.TRADE_PRICE,
            offer.price,
            character_id=offer.giver.character_id,
            detail=f"payer {offer.payer.character_id}",
        )
        economy_log.record(economy_log.TRADE_DUTY, -tax, character_id=offer.giver.character_id)
        return GroupOutcome(result=GroupResult.OFFER_ACCEPTED, offer=offer, tax=tax)

    # --- escrow -------------------------------------------------------

    async def _take_stake(self, offer: Offer) -> bool:
        """Придержать сторону автора у объявленного предложения. False, если её нет."""
        if stakes_item(offer):
            return await self.inventory.remove(
                offer.author.character_id, offer.item_id, offer.quantity
            )
        return await self.characters.spend_gold(offer.author.character_id, offer.price)

    async def _return_stake(self, offer: Offer) -> None:
        """Вернуть автору то, что забрало у него объявление предложения."""
        await return_stake(offer, characters=self.characters, inventory=self.inventory)

    async def _close(
        self, number: int, status: TradeStatus, *, now: int, tax: int = 0
    ) -> TradeRecord | None:
        """Закрыть стоящую сделку и, если она не состоялась, вернуть ставку.

        Ставку возвращает только тот, кто действительно закрыл строку, поэтому два
        ответа, пришедшие вместе, не вернут одну вещь дважды.
        """
        closed = await self.trades.close(
            number, scope=self.scope, status=status, settled_at=now, tax=tax
        )
        if closed is not None and status is not TradeStatus.ACCEPTED:
            await self._return_stake(closed.offer)
        return closed

    async def _sweep(self, now: int) -> None:
        """Вернуть ставки всех предложений, на которые никто не ответил вовремя, где бы они ни были.

        Выполняется перед каждой командой, а не по таймеру: предложения делают только в
        группе, поэтому просроченное обнаруживается в следующий раз, когда кто-то
        заговорит, а затихшей группе фоновая работа не нужна вовсе.

        Нарочно не ограничено ``self.scope``. Кто заговорил следующим, тот и убрал за
        всех, поэтому одна оживлённая группа освобождает ставки, оставшиеся в тихой; а
        то, что иначе держала бы область, в которой больше не заговорят, подберёт
        :func:`release_expired_offers` при следующем запуске игры.

        Отсрочка — это то, что делает «просрочено» настоящим ответом вместо «не
        найдено», см. ``SWEEP_GRACE_SECONDS``.
        """
        for record in await self.trades.expire(before=sweep_before(now)):
            await self._return_stake(record.offer)

    # --- общие шаги ---------------------------------------------------

    async def _resolve(self, query: str, owner: Party) -> ItemOption | GroupOutcome:
        """Превратить набранное игроком в одну вещь из сумки владельца."""
        entries = await self.inventory.list_items(owner.character_id)
        # Вещь, которой в содержимом больше нет, пропускается, а не роняет команду:
        # сумка переживает выпуски, а сохранённому состоянию не верят
        # (``Claude.md``, правило 8).
        catalogue = [
            ItemOption(item_id=entry.item_id, name=self.content.item(entry.item_id).name)
            for entry in entries
            if self.content.has_item(entry.item_id)
        ]
        found = match_items(query, catalogue)
        if not found:
            return _refused(Refusal.UNKNOWN_ITEM, item_name=query)
        if len(found) > 1:
            return _refused(
                Refusal.AMBIGUOUS_ITEM,
                item_name=query,
                options=tuple(option.name for option in found),
            )
        return found[0]

    async def _move_item(self, item_id: str, quantity: int, giver: Party, taker: Party) -> None:
        """Сначала забрать, потом отдать: несостоявшееся изъятие не должно выдумывать вещь."""
        if await self.inventory.remove(giver.character_id, item_id, quantity):
            await self.inventory.add(taker.character_id, item_id, quantity)

    async def _character(self, party: Party) -> Character:
        character = await self.characters.get(party.character_id)
        if character is None:  # pragma: no cover - отряд собран из персонажа
            msg = f"character {party.character_id} disappeared mid-trade"
            raise LookupError(msg)
        return character


def _party(user_id: int, character: Character) -> Party:
    return Party(user_id=user_id, character_id=character.id, name=character.name)
