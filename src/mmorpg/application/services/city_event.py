"""Вклад, имущество, награда и последствия входят в общую операцию M01–M03."""

from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg import economy_log
from mmorpg.application.operations import MissingResourceError, atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.city_event import CityEvent, CityEventState
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.long_goal import StoryState
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository, StateCache
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import city_event as rules
from mmorpg.domain.rules import crafts, quests

CODEC = TypeAdapter(CityEventState)


class CityEvents:
    def __init__(
        self,
        content: GameContent,
        cache: StateCache,
        characters: CharacterRepository,
        inventory: InventoryRepository,
    ) -> None:
        self.content = content
        self._cache = cache
        self.characters = characters
        self.inventory = inventory

    def event(self, city_id: str) -> CityEvent | None:
        return next((one for one in self.content.city_events if one.city_id == city_id), None)

    async def load(self, event: CityEvent) -> CityEventState:
        raw = await self._cache.get(f"city-event:{event.id}")
        return CODEC.validate_json(raw) if raw else CityEventState()

    async def _save(self, event: CityEvent, state: CityEventState) -> None:
        await self._cache.set(f"city-event:{event.id}", CODEC.dump_json(state).decode(), 10**12)

    @atomic_action
    async def record(
        self,
        character_id: int,
        city_id: str,
        slot: int,
        kind: str,
        receipt: str,
        *,
        expected_stage: int | None = None,
    ) -> str:
        event = self.event(city_id)
        actor = await self.characters.get(character_id)
        if (
            event is None
            or actor is None
            or actor.city_id != city_id
            or kind not in {"battle", "scout"}
        ):
            return ""
        state = await self.load(event)
        if expected_stage is not None and expected_stage != state.stage:
            return ""
        if state.stage >= len(event.stages) or slot != event.stages[state.stage].location_slot:
            return ""
        if rules.refusal(event, state, actor.id, kind, receipt):
            return ""
        updated, notice = rules.contribute(event, state, actor.id, kind, receipt)
        await self._save(event, updated)
        return notice

    @atomic_action
    async def craft(
        self, character_id: int, city_id: str, expected_stage: int, receipt: str
    ) -> str:
        event = self.event(city_id)
        actor = await self.characters.get(character_id)
        if event is None or actor is None or actor.city_id != city_id:
            return "Для ремесленной помощи приезжайте в город общего дела."
        if await BattleStore(self._cache).busy(actor.id):
            return "Сначала завершите текущий бой."
        state = await self.load(event)
        if expected_stage != state.stage:
            return "Общее дело изменилось. Проверьте новый этап и материалы перед помощью."
        if reason := rules.refusal(event, state, actor.id, "craft", receipt):
            return reason
        recipe = self.content.recipe(event.stages[state.stage].recipe_id)
        owned = {one.item_id: one.quantity for one in await self.inventory.list_items(actor.id)}
        worked, made = crafts.make(
            self.content,
            actor,
            recipe,
            owned,
            seed=derive(event.id, state.stage, actor.id, receipt),
        )
        if not made.ok:
            return made.refused + " Можно помочь боем или разведкой."
        for item_id, quantity in made.spent:
            if not await self.inventory.remove(actor.id, item_id, -quantity):
                raise MissingResourceError("city event material disappeared")
        log, steps = quests.record_craft(
            self.content, worked, made.item_id, made.count, city_id=city_id
        )
        await self.characters.save(replace(worked, quests=log))
        updated, notice = rules.contribute(event, state, actor.id, "craft", receipt)
        await self._save(event, updated)
        produced = self.content.item(made.item_id).name
        notice = (
            f"Изготовлено и передано городу: {produced}, {made.count}. "
            f"Работы записано: {made.experience}. {notice}"
        )
        for step in steps:
            notice += f" Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
        return notice

    @atomic_action
    async def claim(self, character_id: int, city_id: str) -> str:
        event = self.event(city_id)
        actor = await self.characters.get(character_id)
        if event is None or actor is None:
            return "Городское дело недоступно."
        state = await self.load(event)
        gold, stages = rules.reward_due(event, state, actor.id)
        if not stages:
            return "Новых наград нет. Награда доступна после завершения этапа, которому вы помогли."
        await self.characters.save(actor.with_gold(gold))
        await self._save(
            event,
            replace(state, claimed=(*state.claimed, *((index, actor.id) for index in stages))),
        )
        economy_log.record(economy_log.CITY_EVENT, gold, character_id=actor.id, detail=event.id)
        return f"Награда за завершённые этапы: {gold} золота. Повторная выдача исключена."

    @atomic_action
    async def supply(
        self, character_id: int, city_id: str, expected_stage: int, receipt: str
    ) -> str:
        event = self.event(city_id)
        actor = await self.characters.get(character_id)
        if event is None or actor is None or actor.city_id != city_id:
            return "Для снабжения приезжайте в город общего дела."
        if await BattleStore(self._cache).busy(actor.id):
            return "Сначала завершите текущий бой."
        state = await self.load(event)
        if expected_stage != state.stage:
            return "Общее дело изменилось. Проверьте новый этап перед передачей."
        if reason := rules.refusal(event, state, actor.id, "craft", receipt):
            return reason
        recipe = self.content.recipe(event.stages[state.stage].recipe_id)
        if not await self.inventory.remove(actor.id, recipe.output_id, recipe.output_count):
            return (
                "Для снабжения не хватает изделий. Их можно изготовить, "
                "купить на рынке или заказать мастеру."
            )
        updated, notice = rules.contribute(event, state, actor.id, "craft", receipt)
        await self._save(event, updated)
        return (
            f"Передано городу: {self.content.item(recipe.output_id).name}, "
            f"{recipe.output_count}. {notice}"
        )

    async def travel_discount(self, city_id: str) -> int:
        event = self.event(city_id)
        discount = rules.discount(event, await self.load(event)) if event else 0
        if city_id == self.content.long_goals.city_id:
            raw = await self._cache.get(f"long-goal:story:{city_id}")
            if raw:
                discount = max(
                    discount, TypeAdapter(StoryState).validate_json(raw).outcome.travel_discount
                )
        return discount

    async def summary(self) -> str:
        if not self.content.city_events:
            return ""
        event = self.content.city_events[0]
        state = await self.load(event)
        city = self.content.city(event.city_id)
        if state.stage < len(event.stages):
            stage = event.stages[state.stage]
            return (
                f"Общее дело, {city.name}: {stage.name}, "
                f"{rules.progress(state, state.stage)} из {stage.target}. Подробности: /событие."
            )
        return (
            f"Общее дело, {city.name}: переправа работает. "
            f"Проезд из города дешевле на {rules.discount(event, state)} процентов для всех. "
            "Итог: /событие."
        )
