"""Добровольный новый старт без удаления личности и истории операций."""

import json
from dataclasses import asdict, replace

from pydantic import TypeAdapter

from mmorpg import economy_log
from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.application.operations import MissingResourceError, atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.market import CODEC as MARKET
from mmorpg.application.services.market import KEY as MARKET_KEY
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.character import Character, Equipment
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.long_goal import GoalState
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    StateCache,
    TradeRepository,
)
from mmorpg.domain.rules import tutorial

GOALS = TypeAdapter(GoalState)


class CharacterReset:
    def __init__(
        self,
        content: GameContent,
        characters: CharacterRepository,
        inventory: InventoryRepository,
        cache: StateCache,
        trades: TradeRepository,
        parties: PartyStore,
        guilds: GuildStore,
    ) -> None:
        self.content = content
        self.characters = characters
        self.inventory = inventory
        self._cache = cache
        self.trades = trades
        self.parties = parties
        self.guilds = guilds

    async def refusal(self, character_id: int) -> str:
        if await BattleStore(self._cache).busy(character_id):
            return "Сначала завершите текущий бой или сдавайтесь: /сдаться."
        if await self.parties.of(character_id):
            return "Сначала выйдите из отряда: /отряд уйти."
        if await self.guilds.of(character_id):
            return (
                "Сначала выйдите из гильдии: /гильдия выйти. Главе нужно передать "
                "руководство или распустить гильдию через её меню."
            )
        if await self.trades.has_pending(character_id):
            return "Сначала закройте предложения покупки и продажи в игровых группах."
        raw = await self._cache.get(MARKET_KEY)
        if raw:
            market = MARKET.validate_json(raw)
            if any(
                lot.status in {"open", "reserved"} and character_id in {lot.seller_id, lot.buyer_id}
                for lot in market.lots
            ) or any(
                order.status == "open" and order.buyer_id == character_id for order in market.orders
            ):
                return "Сначала завершите или отмените лоты, резервы и заказы: /рынок."
        return ""

    async def confirm(
        self,
        user_id: int,
        expected_revision: int,
        draft: CharacterDraft,
        *,
        operation_id: str | None = None,
    ) -> tuple[Character | None, str]:
        return await self._confirm(
            user_id,
            expected_revision,
            draft,
            command=json.dumps([user_id, expected_revision, asdict(draft)], default=str),
            operation_id=operation_id,
        )

    @atomic_action
    async def _confirm(
        self,
        user_id: int,
        expected_revision: int,
        draft: CharacterDraft,
        *,
        command: str,
        operation_id: str | None = None,
    ) -> tuple[Character | None, str]:
        actor = await self.characters.get_active(user_id)
        if actor is None:
            return None, "Персонаж не найден. Начните с /start."
        if actor.revision != expected_revision:
            return None, "Персонаж изменился. Прочитайте условия заново: /сброс."
        if refused := await self.refusal(actor.id):
            return None, refused
        if (
            draft.name != actor.name
            or not any(one.id == draft.race_id for one in self.content.races)
            or not draft.is_complete(self.content, check_name=False)
            or not any(one.id == draft.class_id for one in self.content.classes)
            or len(set(draft.trait_ids)) != len(draft.trait_ids)
            or not all(any(one.id == key for one in self.content.traits) for key in draft.trait_ids)
            or any(value < 0 for _, value in draft.allocated)
        ):
            return None, "Выберите расу, класс, начальные черты и характеристики заново: /сброс."

        fresh = replace(
            draft.to_character(self.content, actor.user_id),
            id=actor.id,
            revision=actor.revision,
            is_admin=actor.is_admin,
            tutorial=(1 << len(tutorial.ORDER)) - 1,
        )
        for item in await self.inventory.list_items(actor.id):
            if not await self.inventory.remove(actor.id, item.item_id, item.quantity):
                raise MissingResourceError("reset inventory changed")
        # Стартовые вещи проходят обычную выдачу физических экземпляров.
        equipment = {}
        for slot, item_id in fresh.equipment.items.items():
            await self.inventory.add(actor.id, item_id)
            items = await self.inventory.list_items(actor.id)
            ref = next(one.item_id for one in items if one.quantity > 0)
            if not await self.inventory.remove(actor.id, ref):
                raise MissingResourceError("reset starter item disappeared")
            equipment[slot] = ref
        saved = await self.characters.save(replace(fresh, equipment=Equipment(equipment)))
        key = f"long-goal:hero:{actor.id}"
        raw = await self._cache.get(key)
        previous = GOALS.validate_json(raw) if raw else GoalState()
        # Разовые права остаются использованными; личная работа начинается с нуля.
        await self._cache.set(
            key, GOALS.dump_json(GoalState(claimed=previous.claimed)).decode(), 10**12
        )
        await self._cache.delete(f"long-goal:offer:{actor.id}")
        await self.parties.invitations.finish(actor.id, "declined")
        await self.guilds.invitations.finish(actor.id, "declined")
        economy_log.record(
            "character_reset",
            -(actor.gold + actor.bank_gold + actor.arena_credit),
            character_id=actor.id,
            detail="voluntary level-one restart",
        )
        return saved, "Персонаж сброшен до первого уровня. Обучение повторно не включается."
