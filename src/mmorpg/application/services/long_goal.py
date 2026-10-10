"""Общие проекты и история используют существующую операцию сохранения."""

from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg import economy_log
from mmorpg.application.operations import MissingResourceError, atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.guild import GuildStore
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import Recipe
from mmorpg.domain.entities.long_goal import GoalState, ProjectState, StoryOutcome, StoryState
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository, StateCache
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import crafts, quests
from mmorpg.domain.rules import long_goal as rules

PROJECT = TypeAdapter(ProjectState)
GOAL = TypeAdapter(GoalState)
STORY = TypeAdapter(StoryState)
OUTCOME = TypeAdapter(StoryOutcome)
TTL = 10**12


class LongGoals:
    def __init__(
        self,
        content: GameContent,
        cache: StateCache,
        characters: CharacterRepository,
        inventory: InventoryRepository,
        guilds: GuildStore,
    ) -> None:
        self.content = content
        self._cache = cache
        self.characters = characters
        self.inventory = inventory
        self.guilds = guilds

    async def project(self, guild_id: int) -> ProjectState | None:
        raw = await self._cache.get(f"long-goal:project:{guild_id}")
        return PROJECT.validate_json(raw) if raw else None

    @atomic_action
    async def start_project(self, character_id: int) -> str:
        guild = await self.guilds.of(character_id)
        if guild is None:
            return "Для общего проекта вступите в гильдию: /гильдия."
        if guild.founder_id != character_id:
            return "Проект начинает основатель. Каждый участник затем может помогать."
        if await self.project(guild.id):
            return "Этот проект уже начат или завершён. Вклад и итог доступны: /проект."
        state = ProjectState(self.content.long_goals)
        await self._cache.set(
            f"long-goal:project:{guild.id}", PROJECT.dump_json(state).decode(), TTL
        )
        return (
            "Путевой двор начат. Победы, полные спуски и новый вклад в казну "
            "идут в общий проект. Срока сдачи нет."
        )

    async def goals(self, character_id: int) -> GoalState:
        raw = await self._cache.get(f"long-goal:hero:{character_id}")
        return GOAL.validate_json(raw) if raw else GoalState()

    async def _save(self, character_id: int, state: GoalState) -> None:
        await self._cache.set(f"long-goal:hero:{character_id}", GOAL.dump_json(state).decode(), TTL)

    async def story(self) -> StoryState | None:
        raw = await self._cache.get(f"long-goal:story:{self.content.long_goals.city_id}")
        return STORY.validate_json(raw) if raw else None

    @atomic_action
    async def choose(self, character_id: int, choice: str, *, confirm: bool = False) -> str:
        actor = await self.characters.get(character_id)
        config = self.content.long_goals
        outcome = next((one for one in config.outcomes if one.id == choice), None)
        if actor is None or outcome is None:
            return "Выберите один из двух путей на экране /историямира."
        if actor.city_id != config.city_id or not all(
            actor.quests.is_done(key) for key in config.story_quests
        ):
            return (
                "Сначала завершите три прежних задания смотрителя маяка и вернитесь "
                "в Колву. Журнал: кнопка «Задания» в главном меню."
            )
        if await BattleStore(self._cache).busy(actor.id):
            return "Сначала завершите текущий бой."
        if previous := await self.story():
            return f"Решение уже принято: {previous.outcome.name}. Его видят все приезжие."
        key = f"long-goal:offer:{actor.id}"
        if not confirm:
            await self._cache.set(key, OUTCOME.dump_json(outcome).decode(), TTL)
            return (
                f"Ваш выбор: {outcome.name}. {outcome.text} Выезд из города дешевле на "
                f"{outcome.travel_discount} процентов для всех. Первый "
                "подтверждённый выбор станет общим и постоянным. "
                "Подтвердите кнопкой или вернитесь назад."
            )
        raw = await self._cache.get(key)
        if not raw or OUTCOME.validate_json(raw) != outcome:
            return "Сначала прочитайте последствие и выберите путь: /историямира."
        await self._cache.set(
            f"long-goal:story:{config.city_id}",
            STORY.dump_json(StoryState(outcome, actor.id)).decode(),
            TTL,
        )
        await self._cache.delete(key)
        return (
            f"Дом Курганов принял ваше решение. {outcome.text} Скидка выезда: "
            f"{outcome.travel_discount} процентов для всех."
        )

    async def offer(self, character_id: int) -> str:
        raw = await self._cache.get(f"long-goal:offer:{character_id}")
        return OUTCOME.validate_json(raw).id if raw else ""

    @atomic_action
    async def study(self, character_id: int) -> str:
        actor = await self.characters.get(character_id)
        config = self.content.long_goals
        story = await self.story()
        if actor is None or actor.city_id != config.city_id or story is None:
            return (
                "После общего решения приезжайте в Колву: там можно прочитать записи смотрителей."
            )
        if await BattleStore(self._cache).busy(actor.id):
            return "Сначала завершите текущий бой."
        state = await self.goals(actor.id)
        await self._save(actor.id, replace(state, studied=True))
        return (
            story.outcome.text
            + " В хронику внесена запись о решении дома. Старые задания и их награды сохранены."
        )

    def goal_recipes(self) -> tuple[Recipe, ...]:
        # Одна начальная работа последнего ранга на каждое производящее ремесло.
        selected: dict[str, Recipe] = {}
        for recipe in self.content.recipes:
            if recipe.rank == self.content.long_goals.craft_rank:
                selected.setdefault(recipe.craft_id, recipe)
        return tuple(selected.values())

    @atomic_action
    async def craft(self, character_id: int, number: int, receipt: str) -> str:
        actor = await self.characters.get(character_id)
        config = self.content.long_goals
        recipes = self.goal_recipes()
        if actor is None or actor.level < config.level:
            return (
                "Ремесленная цель открывается на уровне 150. До неё можно развивать "
                "ремесло в таверне."
            )
        if not 1 <= number <= len(recipes):
            return "Выберите номер работы на экране /цели."
        if await BattleStore(self._cache).busy(actor.id):
            return "Сначала завершите текущий бой."
        state = await self.goals(actor.id)
        if state.crafted >= config.craft_target or receipt in state.receipts:
            return "Работа уже зачтена. Итог и награда: /цели."
        recipe = recipes[number - 1]
        owned = {one.item_id: one.quantity for one in await self.inventory.list_items(actor.id)}
        worked, made = crafts.make(
            self.content, actor, recipe, owned, seed=derive("long-goal", actor.id, receipt)
        )
        if not made.ok:
            return (
                made.refused
                + " Материалы можно добыть или купить на рынке; ранг растёт обычной работой."
            )
        for item_id, delta in made.spent:
            if not await self.inventory.remove(actor.id, item_id, -delta):
                raise MissingResourceError("goal materials disappeared")
        await self.inventory.add(actor.id, made.item_id, made.count)
        log, _ = quests.record_craft(
            self.content, worked, made.item_id, made.count, city_id=actor.city_id
        )
        await self.characters.save(replace(worked, quests=log))
        await self._save(
            actor.id,
            replace(
                state,
                crafted=min(config.craft_target, state.crafted + 1),
                receipts=(*state.receipts, receipt),
            ),
        )
        return (
            f"Изготовлено: {self.content.item(made.item_id).name}, {made.count}. "
            f"Изделие осталось в сумке. Работ для цели: "
            f"{min(config.craft_target, state.crafted + 1)} из "
            f"{config.craft_target}."
        )

    @atomic_action
    async def record_descent(
        self, character_ids: tuple[int, ...], dungeon_id: str, receipt: str
    ) -> None:
        config = self.content.long_goals
        if dungeon_id != config.dungeon_id:
            return
        actors = [
            actor for key in character_ids if (actor := await self.characters.get(key)) is not None
        ]
        if len({actor.user_id for actor in actors}) < 2:
            return
        for actor in actors:
            if actor.level < config.level:
                continue
            state = await self.goals(actor.id)
            if receipt not in state.receipts:
                await self._save(
                    actor.id, replace(state, trial=True, receipts=(*state.receipts, receipt))
                )

    @atomic_action
    async def claim(self, character_id: int, track: str) -> str:
        actor = await self.characters.get(character_id)
        config = self.content.long_goals
        if actor is None or actor.level < config.level or track not in rules.TRACKS:
            return "Три долгие цели и награды доступны на уровне 150: /цели."
        state = await self.goals(actor.id)
        if track in state.claimed:
            return "Награда за это направление уже получена. Прогресс сохраняется."
        if not rules.ready(
            config, state, track, all(actor.quests.is_done(key) for key in config.story_quests)
        ):
            return "Цель ещё не завершена. Условия и следующий шаг: /цели."
        await self.characters.save(actor.with_gold(config.reward_gold))
        await self._save(actor.id, replace(state, claimed=(*state.claimed, track)))
        economy_log.record(
            economy_log.QUEST,
            config.reward_gold,
            character_id=actor.id,
            detail=f"long-goal:{track}",
        )
        return (
            f"Цель завершена. Получено {config.reward_gold} золота. Эта награда выдаётся один раз."
        )
