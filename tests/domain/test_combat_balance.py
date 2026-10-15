"""Проверка боёв законных полных и развивающихся сборок всех восьми классов.

Победы, незавершённые бои и неудачные начальные значения показываются явно.
Старые помощники первых рангов сохранены для сравнений и построения основы
персонажа; их исторические длины боя и потери HP не обещаются игроку.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import replace
from typing import NamedTuple

import pytest

from mmorpg.domain.entities import (
    Character,
    CharacterClass,
    Equipment,
    GameContent,
    Item,
    SkillLoadout,
)
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleState,
    Combatant,
    Verdict,
)
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.procgen.enemies import generate_enemy, generate_group
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules.combat import (
    _check_outcome,
    act,
    blow_of,
    hero_combatant,
    monster_combatant,
    open_battle,
)
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for
from mmorpg.domain.rules.stats import stat_allowance

# Старые помощники используют эти контрольные уровни. Действующая матрица
# законных сборок ниже отдельно задаёт восемь классов и шесть ступеней.
LEVELS = (1, 10, 40, 90, 150)
CLASSES = ("warrior", "rogue", "mage", "cleric")
TRIALS = 20


def armed(
    content: GameContent,
    klass: CharacterClass,
    level: int,
    actives: Sequence[str | None] = (),
) -> Equipment:
    """Лучшее оружие своего класса, какое этот персонаж мог бы держать к этой минуте.

    Оружие здесь не украшение: весь урон в игре растёт из костей оружия, а
    половина умений разбойника и следопыта без своего оружия просто не сработает.
    Голыми руками мерить бой значило бы мерить не ту игру, в которую играют.

    Доспех берётся тем же способом. Раньше его снимали намеренно — чтобы «чего
    стоит бой» мерилось по голому здоровью, — но с тех пор броня стала числом, и
    голый герой это уже не «худший случай», а другая игра: латы срезают треть
    удара, и меряя без них, мы меряли бы того, кого в игре нет.
    """
    # Игрок носит своё: чужое не запрещено, но стоит точности и инициативы, и брать
    # его нарочно незачем. Редкость - обычная, ровно как у доспеха ниже: клинок
    # именной славы это редкая находка с логова, а не то, с чем ходят по локации.
    # Прежде редкость не отбиралась, и «лучшее оружие» плыло от того, какое
    # свойство хэш навесил на легендарку, - а с ним и длина боя (ADR 0052).
    wieldable = [
        item
        for item in content.items
        if item.is_weapon
        and klass.can_wield(item.weapon_type)
        and item.level <= level
        and item.rarity == "common"
    ]
    if not wieldable:
        return Equipment()
    # Игрок берёт оружие под свою панель и под свой удар, а не самое дорогое:
    # клинок, которым работают три умения, стоит больше молота, которым не
    # работает ни одно, а кадило с прибавкой к лечению не бьёт вовсе.
    wanted = [
        skill.weapon_types
        for code in actives
        if code is not None
        for skill in (content.skill(code),)
        if skill.weapon_types
    ]

    def worth(item: Item) -> tuple[int, float, int]:
        average = item.damage.average if item.damage is not None else 0.0
        return sum(item.weapon_type in types for types in wanted), average, item.level

    equipment = Equipment().equip("weapon", max(wieldable, key=worth).id)
    for slot in ("head", "body", "hands", "feet"):
        fitting = [
            item
            for item in content.items
            if item.slot == slot
            and item.is_armor
            and klass.can_wear(item.armor_type)
            and item.level <= level
            and item.rarity == "common"
        ]
        if fitting:
            equipment = equipment.equip(slot, max(fitting, key=lambda item: item.armor).id)
    return equipment


def build(content: GameContent, class_id: str, level: int) -> Character:
    """Персонаж, собранный так, как собрал бы игрок: очки в ключевые характеристики,
    самые свежие умения в панели, урон первым, и оружие в руке.
    """
    klass = content.character_class(class_id)
    keys = list(klass.key_stats) or [StatCode.STR]
    allocated: dict[str, int] = {}
    for index in range(stat_allowance(content, level)):
        stat = keys[index % len(keys)]
        allocated[stat.value] = allocated.get(stat.value, 0) + 1

    unlocked = sorted(
        (
            skill
            for skill in content.skills
            if skill.owner == f"class:{class_id}" and skill.is_active and skill.level <= level
        ),
        key=lambda skill: (spec_for(skill.effect).category is EffectCategory.DAMAGE, skill.level),
    )
    actives = [skill.code for skill in unlocked[-6:]]
    # Толковый игрок держит в панели способ откачаться, если класс его вообще
    # умеет: без этого «клеврик» с одними карами гибнет там, где живой лечится.
    heal = next(
        (
            s.code
            for s in reversed(unlocked)
            if spec_for(s.effect).category is EffectCategory.HEAL and s.code not in actives
        ),
        None,
    )
    if heal is not None and len(actives) == 6:
        actives[0] = heal
    elif heal is not None:
        actives.append(heal)
    actives += [None] * (6 - len(actives))

    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        allocated=StatBlock.from_mapping(allocated),
        equipment=armed(content, klass, level, actives),
        loadout=SkillLoadout(actives=tuple(actives), racial=content.race("human").active_code),
    )


def open_fight(
    content: GameContent, character: Character, enemies: tuple[object, ...]
) -> BattleState:
    """Один герой против стаи: номер героя 1, противники со второго."""
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(one, combatant_id=index + 2, side=1)  # type: ignore[arg-type]
            for index, one in enumerate(enemies)
        ),
    ]
    return open_battle(content, {1: character}, fighters, b"balance-seed")


def hero(state: BattleState) -> Combatant:
    one = state.by_id(1)
    assert one is not None
    return one


def _options(content: GameContent, character: Character, state: BattleState) -> list[BattleAction]:
    actions = [BattleAction(kind=ActionKind.ATTACK)]
    for slot, code in enumerate(character.loadout.actives):
        if code is None:
            continue
        skill = content.skill(code)
        # Умение, для которого в руках не то оружие или нет незаметности, игрок
        # видит отказом прямо на кнопке и не нажимает: считать его доступным
        # значило бы мерить игрока, который каждый ход жмёт наугад.
        if gear.skill_refusal(content, character, skill):
            continue
        if skill.requires_stealth and not hero(state).effects.has(StatusKind.UNSEEN):
            continue
        if hero(state).cooldown_of(code) == 0 and skill.cost <= hero(state).resource:
            actions.append(BattleAction(kind=ActionKind.SKILL, slot=slot))
    return actions


def _value(
    content: GameContent, character: Character, state: BattleState, action: BattleAction
) -> float:
    """Примерно чего стоит это действие на этом ходу - в уроне и в темпе."""
    enemies = state.foes_of(1)
    if not enemies:
        return 0.0
    blow = blow_of(content, character, hero(state).effects)
    unseen = hero(state).effects.has(StatusKind.UNSEEN)

    if action.kind is ActionKind.ATTACK:
        worth, hits_enemy = blow, True
    else:
        skill = content.skill(character.loadout.actives[action.slot])
        spec = spec_for(skill.effect)
        hits_enemy = spec.category is EffectCategory.DAMAGE
        if hits_enemy:
            worth = blow * skill.power_at_rank(1) / 100.0 * spec.hits * spec.damage_scale
            if spec.aoe:
                worth *= len(enemies)
        elif skill.effect == "buff_vanish" and not unseen:
            # Уход в незаметность стоит того удара, который он открывает: если в
            # панели есть удар из тени, а героя пока видно - закрыться и ударить
            # со спины (ADR 0050).
            hammers = [
                content.skill(code)
                for code in character.loadout.actives
                if code is not None and content.skill(code).requires_stealth
            ]
            best = max((h.power_at_rank(1) * spec_for(h.effect).hits for h in hammers), default=0.0)
            # Удар со спины бьёт одного: против стаи заход в тень окупается хуже.
            worth = blow * best / 100.0 * 0.8 / len(enemies)
        elif spec.category in {EffectCategory.HEAL, EffectCategory.BARRIER, EffectCategory.CLEANSE}:
            # Лечение и щиты стоят того, что берегут: ничего на полном здоровье и
            # много, когда полоса просела - иначе толковый игрок не доживёт до
            # того, чтобы его расчёт окупился. Порог низкий: подлечиться на
            # трети - дешевле, чем откачиваться с грани.
            missing = 1.0 - hero(state).health / hero(state).max_health
            worth = blow * 14.0 * missing * missing
        else:
            # Усиления и помехи толковый игрок бросает редко: ход, потраченный не
            # на удар, окупается только сильной прибавкой, а её этот грубый счёт
            # не видит.
            worth = blow * 0.25

    return worth


class FightResult(NamedTuple):
    """Один разыгранный бой, в трёх числах, о которых и говорят обещания."""

    turns: int
    outcome: Verdict
    health_left: int
    health_start: int

    @property
    def health_spent(self) -> float:
        """Доля запаса, которой стоил этот бой, от 0 до 1."""
        return (self.health_start - max(0, self.health_left)) / self.health_start


def fight(
    content: GameContent,
    character: Character,
    *,
    rank: EnemyRank,
    trial: int,
    clever: bool = True,
) -> FightResult:
    """Прогнать один бой целиком и доложить, чего он стоил персонажу."""
    seed = derive(b"balance", character.class_id, character.level, trial, rank.value)
    enemies = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="*",
        level=character.level,
        rank=rank,
        elite_titles=content.elite_titles,
    )
    state = open_fight(content, character, enemies)
    started_with = hero(state).health
    turn = 0
    while not state.is_over and turn < 60:
        turn += 1
        action = (
            max(
                _options(content, character, state),
                key=lambda a: _value(content, character, state, a),
            )
            if clever
            else BattleAction(kind=ActionKind.ATTACK)
        )
        state = act(content, {1: character}, state, action, derive(seed, "turn", turn))
    return FightResult(turn, state.verdict_for(1), hero(state).health, started_with)


def trials(
    content: GameContent, class_id: str, level: int, *, rank: EnemyRank, clever: bool = True
) -> list[FightResult]:
    character = build(content, class_id, level)
    return [
        fight(content, character, rank=rank, trial=trial, clever=clever) for trial in range(TRIALS)
    ]


def sample(
    content: GameContent, class_id: str, level: int, *, rank: EnemyRank, clever: bool = True
) -> list[int]:
    return [result.turns for result in trials(content, class_id, level, rank=rank, clever=clever)]


# --- действующая проверка законных сборок -----------------------------

# Старые build/fight выше оставлены как контрольная модель первых рангов.
# Она не обещает игроку определённое число ходов и не измеряет полный билд.
PREPARED_CLASSES = ("warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid")
PREPARED_LEVELS = (1, 10, 30, 60, 100, 150)


def _real_enemies(content: GameContent, class_id: str, level: int, rank: EnemyRank, trial: int):
    from scripts.character_balance_report import SEED

    seed = derive(SEED, class_id, level, rank.value, trial)
    enemies = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="*",
        level=level,
        rank=rank,
        elite_titles=content.elite_titles,
    )
    return seed, enemies


@pytest.fixture(scope="module")
def prepared_ordinary(content: GameContent):
    from scripts.character_balance_report import developed, simulate

    class Samples(dict):
        def __missing__(self, key):
            class_id, level, partial = key
            character = developed(content, class_id, level, partial=partial)
            measured = []
            for trial in range(TRIALS):
                seed, enemies = _real_enemies(content, class_id, level, EnemyRank.NORMAL, trial)
                measured.append(simulate(content, {character.id: character}, enemies, seed))
            self[key] = measured
            return measured

    return Samples()


@pytest.mark.parametrize("class_id", PREPARED_CLASSES)
@pytest.mark.parametrize("level", PREPARED_LEVELS)
@pytest.mark.parametrize("partial", [False, True], ids=["prepared-common", "half-points-common"])
def test_ordinary_encounters_are_passable_for_all_eight_classes(
    prepared_ordinary, class_id: str, level: int, partial: bool
) -> None:
    """Порог выбран до измерений: первый уровень 95%, полная сборка 90%, неполная 80%."""
    results = prepared_ordinary[class_id, level, partial]
    required = 0.95 if level == 1 else 0.8 if partial else 0.9
    won = sum(bool(one["won"]) for one in results)
    failed = [index for index, one in enumerate(results) if not one["won"]]
    assert won >= required * len(results), (
        f"{class_id}, level={level}, partial={partial}: {won}/{len(results)} wins; "
        f"failed seeds={failed}, required={required:.0%}"
    )
    assert not any(one["unfinished"] for one in results), (
        f"{class_id}, level={level}, partial={partial}: battle exceeded 80 player commands"
    )


@pytest.mark.parametrize("level", PREPARED_LEVELS)
def test_ordinary_encounters_have_measured_pressure_and_real_resource_spend(
    prepared_ordinary, level: int
) -> None:
    """Барьер и восстановление могут вернуть полное HP; проверяем действительный урон и расход."""
    results = [
        one for class_id in PREPARED_CLASSES for one in prepared_ordinary[class_id, level, False]
    ]
    assert sum(one["received_damage"] for one in results) > 0
    assert sum(one["used_resource"] for one in results) > 0
    assert all(0 <= one["minimum_health_ratio"] <= 1 for one in results)


@pytest.mark.parametrize("level", (1, 30, 100, 150))
def test_stronger_ranks_have_more_health_armor_and_total_threat(
    content: GameContent, level: int
) -> None:
    """Сравнивается одна порода: сильная ступень требует больше урона и выдерживает больше атак."""
    enemies = [
        generate_enemy(
            derive(b"rank-comparison", level),
            archetypes=(content.enemy_archetypes[0],),
            biome=content.enemy_archetypes[0].biomes[0],
            level=level,
            rank=rank,
            elite_titles=content.elite_titles,
        )
        for rank in (EnemyRank.NORMAL, EnemyRank.ELITE, EnemyRank.BOSS)
    ]
    assert enemies[0].max_health < enemies[1].max_health < enemies[2].max_health
    assert enemies[0].armor <= enemies[1].armor <= enemies[2].armor
    threat = [one.max_health * one.damage for one in enemies]
    assert threat[0] < threat[1] < threat[2]
    assert enemies[0].gold < enemies[1].gold < enemies[2].gold


@pytest.mark.parametrize(
    "class_ids", [("warrior", "mage", "cleric"), ("barbarian", "rogue", "druid")]
)
@pytest.mark.parametrize("level", PREPARED_LEVELS)
def test_three_prepared_heroes_can_defeat_a_boss(
    content: GameContent, class_ids: tuple[str, ...], level: int
) -> None:
    """Групповой ориентир: не менее 19 побед из 20; одиночный босс не обещан всем классам."""
    from scripts.character_balance_report import SEED, developed, simulate

    roster = {
        index: replace(developed(content, class_id, level), id=index, user_id=index)
        for index, class_id in enumerate(class_ids, 1)
    }
    results = []
    for trial in range(TRIALS):
        seed = derive(SEED, "party", level, trial)
        enemies = generate_group(
            seed,
            archetypes=content.enemy_archetypes,
            biome="*",
            level=level,
            rank=EnemyRank.BOSS,
            elite_titles=content.elite_titles,
        )
        results.append(simulate(content, roster, enemies, seed, limit=240))
    won = sum(bool(one["won"]) for one in results)
    failed = [index for index, one in enumerate(results) if not one["won"]]
    assert won >= 0.95 * len(results), (
        f"{class_ids}, level={level}: {won}/{len(results)} wins; failed seeds={failed}"
    )
    assert not any(one["unfinished"] for one in results)


@pytest.mark.parametrize("class_id", PREPARED_CLASSES)
def test_using_learned_skills_improves_a_real_build(content: GameContent, class_id: str) -> None:
    """Один законный герой и одни противники: учитываются победы, затем скорость общих побед."""
    from unittest.mock import patch

    from scripts.character_balance_report import developed, simulate

    character = developed(content, class_id, 60)
    clever, plain = [], []
    for trial in range(TRIALS):
        seed, enemies = _real_enemies(content, class_id, 60, EnemyRank.NORMAL, trial)
        clever.append(simulate(content, {character.id: character}, enemies, seed))
        with patch(
            "scripts.character_balance_report.action_for",
            return_value=BattleAction(kind=ActionKind.ATTACK),
        ):
            plain.append(simulate(content, {character.id: character}, enemies, seed))
    wins = sum(bool(one["won"]) for one in clever)
    plain_wins = sum(bool(one["won"]) for one in plain)
    assert wins >= plain_wins, f"{class_id}: panel wins={wins}, attacking wins={plain_wins}"
    paired = [
        (left, right)
        for left, right in zip(clever, plain, strict=True)
        if left["won"] and right["won"]
    ]
    assert wins > plain_wins or (
        paired
        and statistics.median(left["actions"] for left, _ in paired)
        < statistics.median(right["actions"] for _, right in paired)
    ), f"{class_id}: panel gives neither more wins nor faster paired victories"


@pytest.mark.parametrize("level", (1, 40, 150))
def test_a_damage_skill_keeps_a_useful_base_power(content: GameContent, level: int) -> None:
    """Рост обычной атаки не превращает навык в слабый плоский удар."""
    character = build(content, "warrior", level)
    blow = blow_of(content, character)
    for code in character.loadout.equipped_actives():
        skill = content.skill(code)
        if spec_for(skill.effect).category is not EffectCategory.DAMAGE:
            continue
        power = blow * skill.power_at_rank(1) / 100.0
        assert power > blow, f"{skill.name} at level {level} is weaker than an attack"


# --- стая платит как один бой ----------------------------------------


def test_a_pack_pays_like_one_fight_because_it_is_one_fight(content: GameContent) -> None:
    """Троих делили по здоровью и урону, а платили за троих.

    Стая делит бюджет одного боя (``procgen/enemies.group_scale``): втроём
    противники слабее каждый и вместе тянут полтора боя по времени. Золото и
    опыт при этом множились на число тел — и грести стаи было самым выгодным,
    что есть в игре (``Roadmap.md``, аномалия роста уровней).
    """
    character = build(content, "warrior", 20)
    seed = derive(b"pack", "pay")

    def paid(members: int) -> tuple[int, int]:
        pack = tuple(
            generate_enemy(
                derive(seed, "member", index),
                archetypes=content.enemy_archetypes,
                biome="*",
                level=character.level,
                members=members,
            )
            for index in range(members)
        )
        state = open_fight(content, character, pack)
        for one in state.combatants:
            if not one.is_hero:
                state = state.replace_combatant(replace(one, health=0))
        done = _check_outcome(content, {1: character}, state)
        return done.experience, done.gold

    alone_xp, alone_gold = paid(1)
    pack_xp, pack_gold = paid(3)

    assert pack_xp < 3 * alone_xp
    assert pack_gold < 3 * alone_gold
    assert pack_xp > alone_xp
    assert pack_gold > alone_gold
