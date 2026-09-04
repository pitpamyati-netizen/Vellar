"""Что вылазка в локацию делает с персонажем.

Как идёт бой, решает боевой движок; этот модуль решает, чего бой *стоил*: опыт,
золото, добыча, счётчики заданий и раны, вынесенные обратно. То же и с тихими
узлами: тайник платит, святилище латает, и то и другое бросается из сида узла, а
не хранится.

Всё здесь чистое. Ничто не пишет: каждая функция возвращает нового персонажа и
разложенное по полям описание того, что изменилось, а сохраняет вызывающий.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import BattleState
from mmorpg.domain.entities.content import GameContent, Item, ItemKind
from mmorpg.domain.entities.location import LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import rng
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.domain.rules import economy
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.domain.rules import tools as tool_rules
from mmorpg.domain.rules.equipment import fill_gear
from mmorpg.domain.rules.progression import (
    LevelUp,
    earned,
    experience_reward,
    grant_experience,
)
from mmorpg.domain.rules.quests import QuestStep
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tutorial import (
    COMPLETION_REWARD,
    GEAR_SLOTS,
    STEP_REWARD,
    TutorialTask,
    finished,
)

# Поражение дорого, но никогда не разорительно: десятая часть того, что при тебе, и
# просыпаешься в городе с четвертью здоровья. Престол не отнимает - он просто и не
# помогает.
DEFEAT_GOLD_PERCENT = 10
DEFEAT_HEALTH_PERCENT = 25

# Тихий узел платит долю от того, что платит бой того же уровня: поиск - безопасный
# способ потратить стражу, а значит, и более медленный.
CACHE_GOLD_BASE = 6.0
CACHE_GOLD_PER_LEVEL = 3.2
EVENT_GOLD_PER_LEVEL = 1.6
SEARCH_EXPERIENCE_BASE = 5
SEARCH_EXPERIENCE_PER_LEVEL = 3
SHRINE_HEAL_PERCENT = 35
CACHE_ITEM_CHANCE = 45.0

#: Прибавка к тому, что приносит тихий узел: тайник, находка, развилка. Ключ
#: обещали «Рассказчик», «Разведчик» и «Звезда странника», и до сих пор его не
#: читал никто (``Roadmap.md``, ADR 0018). Святилище он не трогает: оно лечит, а
#: не платит, и «награда за событие» лечением не бывает.
EVENT_REWARD_KEY = "event_reward_percent"

#: Что лежит в узле для сбора, по его имени - и каким инструментом это берут
#: (ADR 0056). Из зарослей не выкапывают руду, а с
#: останков не срезают травы: узел, который отдаёт железный лом вместо трав,
#: читается как ошибка - ровно так же, как волчья шкура с кабана.
#: Имена приходят из постоянного имени узла (``procgen/location._CATEGORY_NAMES``,
#: категория «находка»); узел, которого тут нет, отдаёт любое сырьё по уровню.
#: Имя у узла постоянно, а вот вид - сбор он или тайник - меняется с поколением
#: округи, поэтому здесь только те имена, что честны при любом из видов.
GATHER_SOURCES: dict[str, str] = {
    "Заросли": "травы",
    "Останки": "шкуры",
}


@dataclass(frozen=True, slots=True)
class Aftermath:
    """Итог одного боя, уже оплаченного."""

    character: Character
    experience: int = 0
    gold: int = 0
    loot: tuple[str, ...] = ()
    level_up: LevelUp | None = None
    quest_steps: tuple[QuestStep, ...] = ()
    gold_lost: int = 0
    #: Что сточилось до конца этим боем - именами вещей. Сломанное остаётся
    #: надетым, но не даёт ничего до кузницы (ADR 0057).
    broken: tuple[str, ...] = ()

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Что отдал узел, в котором не было боя."""

    character: Character
    kind: NodeKind
    gold: int = 0
    experience: int = 0
    item_id: str = ""
    #: Сколько взято. Тайник отдаёт одну находку, жила - столько, сколько
    #: вынесет ремесло того, кто её отработал (``crafts.gather_amount``).
    count: int = 0
    healed: int = 0
    level_up: LevelUp | None = None
    quest_steps: tuple[QuestStep, ...] = ()
    #: В каком ремесле записана эта работа и сколько её записано. Пусто у всего,
    #: кроме сбора.
    craft_id: str = ""
    craft_experience: int = 0
    #: Инструмент: сточился ли он этой работой и сколько сборов в нём осталось.
    tool_broken: bool = False
    tool_left: int = 0

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


def carry_wounds(
    content: GameContent, character: Character, state: BattleState, combatant_id: int
) -> Character:
    """Записать на персонажа то здоровье, с которым он вышел из боя.

    Ниже единицы оно не опускается. Павший в отряде, который бой всё-таки
    выиграл, встаёт на ноги: персонаж с нулём здоровья не может ни драться, ни
    дойти до лекаря, и это была бы не цена поражения, а тупик.
    """
    stats = derived_stats(content, character)
    one = state.by_id(combatant_id)
    health = one.health if one is not None else character.health_or(stats.max_health)
    return character.with_health(max(1, health), stats.max_health)


def resolve_victory(
    content: GameContent,
    character: Character,
    state: BattleState,
    combatant_id: int,
    *,
    experience: int | None = None,
    gold: int | None = None,
    loot: tuple[str, ...] | None = None,
) -> Aftermath:
    """Заплатить за выигранный бой: опыт, золото, добыча и счёт по заданиям.

    Доли приходят снаружи: сколько именно досталось этому герою, решает отряд, а
    не бой (``domain/rules/party.split``). Не переданы - значит герой дрался
    один и забирает всё.
    """
    fallen = tuple(one.enemy for one in state.combatants if one.enemy is not None and not one.alive)
    log, steps = quest_rules.record_kills(content, character, fallen)

    share_gold = state.gold if gold is None else gold
    share_experience = state.experience if experience is None else experience
    share_loot = state.loot if loot is None else loot

    paid = replace(
        carry_wounds(content, character, state, combatant_id).with_gold(share_gold), quests=log
    )
    grown, level_up = grant_experience(content, paid, share_experience)
    # Бой точит надетое, и выигранный тоже: доспех держит удары независимо от
    # того, кто в итоге упал (``domain/rules/repair.py``, ADR 0057).
    worn, broke = repair_rules.wear(content, grown, repair_rules.WEAR_PER_FIGHT)
    return Aftermath(
        character=worn,
        experience=earned(content, paid, share_experience),
        gold=share_gold,
        loot=share_loot,
        level_up=level_up,
        quest_steps=steps,
        broken=tuple(item.name for item in broke),
    )


def resolve_defeat(content: GameContent, character: Character) -> Aftermath:
    """Поражение стоит золота и стражи обратной дороги, но никогда - уровня."""
    stats = derived_stats(content, character)
    lost = character.gold * DEFEAT_GOLD_PERCENT // 100
    revived = character.with_gold(-lost).with_health(
        max(1, stats.max_health * DEFEAT_HEALTH_PERCENT // 100), stats.max_health
    )
    # Поражение точит надетое втрое: с поля боя выносят не только раны.
    worn, broke = repair_rules.wear(content, revived, repair_rules.WEAR_ON_DEFEAT)
    return Aftermath(character=worn, gold_lost=lost, broken=tuple(item.name for item in broke))


def resolve_search(
    content: GameContent,
    character: Character,
    node: LocationNode,
    seed: bytes,
    *,
    tool: Item | None = None,
    biomes: frozenset[str] = frozenset(),
) -> SearchResult:
    """Отработать узел, в котором нет боя. Определяется сидом.

    ``tool`` - инструмент в слоте, ``biomes`` - земля вокруг этого места. Оба
    нужны одному узлу - жиле: без инструмента она не отдаёт ничего, а что
    именно в ней лежит, решает земля (``crafts.yields_here``, ADR 0056).
    """
    source = rng(seed)
    stats = derived_stats(content, character)
    log, steps = quest_rules.record_search(content, character, node.kind)
    working = replace(character, quests=log)

    gold = 0
    item_id = ""
    count = 0
    healed = 0
    craft_id = ""
    craft_experience = 0
    tool_broken = False
    tool_left = 0
    experience = SEARCH_EXPERIENCE_BASE + SEARCH_EXPERIENCE_PER_LEVEL * node.level

    match node.kind:
        case NodeKind.CACHE:
            gold = max(1, round(CACHE_GOLD_BASE + CACHE_GOLD_PER_LEVEL * node.level))
            if source.uniform(0, 100) < CACHE_ITEM_CHANCE:
                item_id = _pick_item(content, source, node.level)
                count = 1 if item_id else 0
        case NodeKind.GATHER:
            gathered = _gather(content, working, node, source, tool=tool, biomes=biomes)
            working = gathered.character
            item_id = gathered.item_id
            count = gathered.count
            craft_id = gathered.craft_id
            craft_experience = gathered.experience
            tool_broken = gathered.tool_broken
            tool_left = gathered.tool_left
        case NodeKind.SHRINE:
            current = working.health_or(stats.max_health)
            restored = round(stats.max_health * SHRINE_HEAL_PERCENT / 100)
            healed = min(restored, stats.max_health - current)
            working = working.with_health(current + healed, stats.max_health)
        case _:
            gold = max(1, round(EVENT_GOLD_PER_LEVEL * node.level))

    share = max(0.0, mods.percent(mods.collect_modifiers(content, character), EVENT_REWARD_KEY))
    if node.kind is not NodeKind.SHRINE:
        gold = round(gold * share)
        experience = max(1, round(experience * share))
    paid = working.with_gold(gold)
    grown, level_up = grant_experience(content, paid, experience)
    return SearchResult(
        character=grown,
        kind=node.kind,
        gold=gold,
        experience=earned(content, paid, experience),
        item_id=item_id,
        count=count,
        healed=healed,
        level_up=level_up,
        quest_steps=steps,
        craft_id=craft_id,
        craft_experience=craft_experience,
        tool_broken=tool_broken,
        tool_left=tool_left,
    )


@dataclass(frozen=True, slots=True)
class Gathered:
    """Что вышло из одной отработанной жилы, вместе с ценой для инструмента."""

    character: Character
    item_id: str = ""
    count: int = 0
    craft_id: str = ""
    experience: int = 0
    tool_broken: bool = False
    tool_left: int = 0


def _gather(
    content: GameContent,
    character: Character,
    node: LocationNode,
    source: random.Random,
    *,
    tool: Item | None,
    biomes: frozenset[str],
) -> Gathered:
    """Взять сырьё из жилы. Без инструмента - не взять ничего (ADR 0056).

    Что лежит в жиле, решают трое: сама жила, если она называет своё сырьё
    (``GATHER_SOURCES``), земля вокруг (``crafts.yields_here``) и инструмент в
    руках - им берут не всё. Сколько вышло, решает ранг ремесла, а стоит это
    одной единицы прочности - всегда, какой бы богатой ни была жила.
    """
    if tool is None:
        return Gathered(character=character)

    wanted = GATHER_SOURCES.get(node.name, "")
    sources = (wanted,) if wanted else tool_rules.sources_of(content, tool)
    here = craft_rules.yields_here(content, level=character.level, biomes=biomes, sources=sources)
    if not here:
        # Земля, в которой этому ремеслу нечего взять, не остаётся немой: жила
        # отдаёт то, что вообще бывает такого рода, - иначе игрок стачивал бы
        # инструмент о пустое место, ничего об этом не зная.
        here = craft_rules.yields_here(content, level=character.level, sources=sources)
    item_id = source.choice(here) if here else ""
    if not item_id:
        return Gathered(character=character)

    craft_id = tool_rules.craft_of(content, tool) or craft_rules.craft_of_source(content, item_id)
    experience = content.craft_rules.gather_experience if craft_id else 0
    worked = character
    if craft_id:
        worked = character.with_crafts(character.crafts.with_experience(craft_id, experience))
    worked, broken = tool_rules.wear(content, worked, tool)
    return Gathered(
        character=worked,
        item_id=item_id,
        count=craft_rules.gather_amount(content, worked, craft_id) if craft_id else 1,
        craft_id=craft_id,
        experience=experience,
        tool_broken=broken,
        tool_left=0 if broken else tool_rules.left(content, worked, tool),
    )


# --- дно спуска -------------------------------------------------------  Спуск когда-то
# был тремя боями, которые просто шли подряд: эпический противник на последнем этаже был
# единственным, что отличало его от трёх боёв в локации, а экран обещал награду «внизу и
# целиком», которой в коде не платило ничто. Это и есть та награда.  Она стоит примерно
# ещё одного эпического боя — достаточно, чтобы спускаться раненым было настоящим
# решением, а уйти на втором этаже чего-то стоило.
DESCENT_GOLD_BASE = 25.0
DESCENT_GOLD_PER_LEVEL = 9.0
#: Дно платит опытом как ещё один противник уровня самого спуска.
DESCENT_EXPERIENCE_BASE = 30


@dataclass(frozen=True, slots=True)
class DescentPrize:
    """Что отдало дно спуска."""

    character: Character
    gold: int = 0
    experience: int = 0
    item_id: str = ""
    level_up: LevelUp | None = None

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


def descent_gold(level: int) -> int:
    """Сколько платит дно, до всяких бросков."""
    return max(1, round(DESCENT_GOLD_BASE + DESCENT_GOLD_PER_LEVEL * max(1, level)))


def descent_prize(
    content: GameContent,
    character: Character,
    *,
    level: int,
    seed: bytes,
    bounty: float = 1.0,
) -> DescentPrize:
    """Заплатить за заход, пройденный до логова. Определяется сидом.

    Находка никогда не бывает сырьём: дно ямы в земле - это место, где лежит вещь, а
    не место, где растут травы, и спуск, заплативший волчьими шкурами, читался бы
    как ошибка.

    ``bounty`` - множитель сложности данжа: на гиблом спуске дно платит вдвое
    (``domain/rules/dungeon.py``, ADR 0036).
    """
    source = rng(seed)
    gold = max(1, round(descent_gold(level) * bounty))
    item_id = _pick_item(content, source, level)
    experience = max(
        1,
        round(
            (
                DESCENT_EXPERIENCE_BASE
                + experience_reward(enemy_level=max(1, level), character_level=character.level)
            )
            * bounty
        ),
    )
    paid = character.with_gold(gold)
    grown, level_up = grant_experience(content, paid, experience)
    return DescentPrize(
        character=grown,
        gold=gold,
        experience=earned(content, paid, experience),
        item_id=item_id,
        level_up=level_up,
    )


@dataclass(frozen=True, slots=True)
class TutorialPayout:
    """Что начислила награда за шаг обучения (ADR 0038).

    ``character`` - уже с опытом, золотом и дозаполненным снаряжением. ``items``
    кладёт в сумку хендлер: у домена нет доступа к хранилищу. ``lines`` -
    фактические строки для игрока, как ``payout.extra`` в бою.
    """

    character: Character
    experience: int = 0
    gold: int = 0
    items: tuple[tuple[str, int], ...] = ()
    level_up: LevelUp | None = None
    lines: tuple[str, ...] = ()


def apply_tutorial_rewards(
    content: GameContent, character: Character, newly_done: frozenset[TutorialTask]
) -> TutorialPayout:
    """Начислить награду за только что закрытые шаги обучения.

    ``character`` приходит уже с проставленными битами
    (``tutorial.complete``); ``newly_done`` - что именно закрылось этим
    действием. Completion-набор идёт сверху, когда закрыт последний шаг, и ровно
    один раз: ``finished`` становится истиной только на шестом шаге.
    """
    if not newly_done:
        return TutorialPayout(character=character)

    completing = finished(character)
    steps = len(newly_done)
    gold = STEP_REWARD.gold * steps + (COMPLETION_REWARD.gold if completing else 0)
    experience = STEP_REWARD.experience * steps + (
        COMPLETION_REWARD.experience if completing else 0
    )
    items = COMPLETION_REWARD.items if completing else ()

    updated = character.with_gold(gold)
    if completing and COMPLETION_REWARD.fill_gear:
        updated = replace(
            updated, equipment=fill_gear(content, updated.class_id, updated.equipment, GEAR_SLOTS)
        )
    grown, level_up = grant_experience(content, updated, experience)

    lines = [f"Награда за обучение: {earned(content, character, experience)} опыта, {gold} золота."]
    if completing:
        pieces = sum(
            1
            for slot in GEAR_SLOTS
            if grown.equipment.item_in(slot) is not None
            and character.equipment.item_in(slot) is None
        )
        extra = []
        if pieces:
            extra.append(f"снаряжение в {pieces} слотов")
        for item_id, count in items:
            if content.has_item(item_id):
                extra.append(f"{content.item(item_id).name} ({count})")
        gift = "; ".join(extra) if extra else "опыт и золото"
        lines.append(f"Обучение пройдено целиком. Сверху: {gift}.")

    return TutorialPayout(
        character=grown,
        experience=earned(content, character, experience),
        gold=gold,
        items=items,
        level_up=level_up if level_up.levels_gained else None,
        lines=tuple(lines),
    )


@dataclass(frozen=True, slots=True)
class RestResult:
    """Ночь в городе: что она вылечила и чего стоила."""

    character: Character
    healed: int
    cost: int
    refused: str = ""


def rest(content: GameContent, character: Character, *, paid: bool) -> RestResult:
    """Отоспаться. Оплаченная постель лечит всё, солома - часть.

    Бесплатная постель существует затем, чтобы разорившийся персонаж никогда не
    застревал: без неё игрок, потерявший последнюю монету на одной единице
    здоровья, не имел бы ни одного хода.
    """
    stats = derived_stats(content, character)
    current = character.health_or(stats.max_health)
    if current >= stats.max_health:
        return RestResult(character=character, healed=0, cost=0, refused="whole")

    if not paid:
        restored = min(
            stats.max_health - current,
            max(1, round(stats.max_health * economy.STRAW_HEAL_PERCENT / 100)),
        )
        healed = character.with_health(current + restored, stats.max_health)
        return RestResult(character=healed, healed=restored, cost=0)

    cost = economy.inn_price(character.level)
    if character.gold < cost:
        return RestResult(character=character, healed=0, cost=cost, refused="poor")
    rested = character.with_gold(-cost).with_health(stats.max_health, stats.max_health)
    return RestResult(character=rested, healed=stats.max_health - current, cost=cost)


def use_consumable(
    content: GameContent, character: Character, item_id: str
) -> tuple[Character, int]:
    """Выпить зелье вне боя. Возвращает персонажа и то, что зелье вылечило.

    Здесь работает только лечение: усиление урона, когда бить некого, было бы
    способом случайно выбросить зелье.
    """
    item = content.item(item_id)
    if item.effect is None:
        return character, 0
    stats = derived_stats(content, character)
    current = character.health_or(stats.max_health)
    match item.effect.kind:
        case "heal_flat":
            restored = round(item.effect.power)
        case "heal_percent":
            restored = round(stats.max_health * item.effect.power / 100.0)
        case _:
            return character, 0
    restored = min(restored, stats.max_health - current)
    if restored <= 0:
        return character, 0
    return character.with_health(current + restored, stats.max_health), restored


def _pick_item(content: GameContent, source: random.Random, level: int) -> str:
    """Находка под уровень. Пусто, когда в содержимом нет ничего настолько простого.

    Сырьё сюда не попадает: его больше не находят - его собирают, и только
    инструментом (``_gather``, ADR 0056). Тайник платит тем, что можно надеть,
    выпить или продать.
    """
    pool = [
        item
        for item in content.items
        if item.kind is not ItemKind.MATERIAL and item.level <= max(1, level)
    ]
    if not pool:
        return ""
    return source.choice(pool).id
