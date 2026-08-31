"""Сколько длится бой и чего стоит хорошее решение.

Это те числа, которые замысел обещает вслух: обычный бой — примерно три хода,
эпический вдвое дольше, босс вчетверо, а игрок, читающий объявленные намерения,
кончает раньше того, кто только жмёт «Атака». Здесь не проверяется ни одна
формула — проверяется то ощущение, в которое формулы складываются, а это
единственное, что игрок может почувствовать.

Бои разыгрываются нарочно простым «толковым игроком»: бери самый сильный
доступный удар, предпочитай тег, отвечающий объявленному намерению. Живой игрок
сыграет лучше; если уж этот не укладывается в срок, значит, баланс неверен.
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
    ActionTag,
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
    INTENT_ARMOR,
    _check_outcome,
    act,
    blow_of,
    hero_combatant,
    intent_of,
    monster_combatant,
    open_battle,
)
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for, tag_of_skill
from mmorpg.domain.rules.stats import derived_stats, stat_allowance

#: Бой, взятый по всей полосе уровней, а не только на тех уровнях, на которых случается
#: играть разработчику.
LEVELS = (1, 10, 40, 150, 300)
CLASSES = ("warrior", "rogue", "mage", "cleric")
TRIALS = 20

#: Что обещает замысел. Медиана - это договор; потолок лишь не даёт назвать откатом один
#: неудачный бросок, а пол не даёт случиться откату обратному - бою, который кончился
#: раньше, чем стал боем.
#:
#: Стало на ход больше: пока «лучшее оружие» бралось любой редкости, толковому
#: игроку доставался реликтовый клинок, растущий по уровню героя, - и он ел
#: обычный бой в три хода. С тех пор как редкость обычная (``armed``, ADR 0052),
#: разбойник к середине пути открывает бой уходом в тень и лишь потом бьёт из-за
#: спины: пять ходов - это его ступенчатый разгон на честном оружии, а не откат.
ORDINARY_TURNS = 5
ORDINARY_FLOOR = 2
#: Потолок стал выше на два хода вместе с костями: урон теперь бросается, и один
#: неудачный бой действительно бывает вдвое длиннее обычного. Медиана — вот
#: договор; потолок только отделяет невезение от поломки, и хвост у распределения
#: с костями честно длиннее, чем был у одного числа (ADR 0015).
ORDINARY_CEILING = 10
ELITE_FLOOR = 1.5
BOSS_FLOOR = 2.5

#: Обычный бой выигрывают, но он не бесплатен: как раз эта доля запаса здоровья и делает
#: проход по локации чередой решений, а не формальностью. Сложено по классам - воин в
#: латах тратит меньше мага, и в этом весь смысл лат.
ORDINARY_HEALTH_COST = 0.07
#: По скольким обычным боям подряд стоит мерить проход по локации: примерно столько и
#: стоит между входом и выходом.
RUN_LENGTH_FLOOR = 4
#: Сколько таких пробегов меряется и какая их доля должна дойти до конца.
#: Один пробег - это один сид, а один сид это не обещание: полгода он был
#: единственным, и стоило костям лечь иначе, как «мага держит четыре боя»
#: превращалось в «мага держал именно этот бой». Доля считается по всем классам
#: разом - ровно потому же, почему по ним разом считается цена боя: латы и
#: должны доходить чаще рясы, и вопрос только в том, доходит ли вылазка.
RUN_TRIALS = 16
RUN_SURVIVAL = 0.7
#: Сколько боёв из двадцати обычный герой обязан выиграть. Не все двадцать: с
#: тех пор как урон бросается костями (ADR 0015), а очередь решается инициативой
#: (ADR 0021), у боя есть хвост - двое быстрых противников на хрупком классе
#: складываются в проигрыш примерно раз в двадцать боёв. Обещание игры - «бой,
#: который мир кладёт перед тобой, выигрывается», а не «выигрывается, что бы ни
#: выпало»: второе значило бы, что бросок ничего не решает.
ORDINARY_WINS = 0.95
#: Насколько прочитанное намерение вправе ускорить бой. Оно обязано платить
#: (``test_reading_the_intent_shortens_the_fight``) и обязано остаться способом драться
#: хорошо, а не единственным существующим боем.
TEMPO_CEILING = 2.0
#: Насколько далеко могут стоять самый быстрый и самый медленный класс на боссе. Классу
#: позволен характер; превращать одного и того же босса в другую игру ему не позволено.
CLASS_SPREAD = 1.75


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
    announced = intent_of(state, enemies[0])
    unseen = hero(state).effects.has(StatusKind.UNSEEN)

    if action.kind is ActionKind.ATTACK:
        tag, worth, hits_enemy = ActionTag.PRESS, blow, True
    else:
        skill = content.skill(character.loadout.actives[action.slot])
        spec = spec_for(skill.effect)
        tag = tag_of_skill(skill)
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

    # Враг, объявивший напор, на замахе: удар по нему мимо брони, его ответ
    # вполсилы (брешь). Враг в заслоне - глухая оборона, бить его невыгодно.
    if hits_enemy and announced is ActionTag.PRESS:
        worth *= 2.4
    elif hits_enemy and announced is ActionTag.GUARD:
        worth *= 1.0 / INTENT_ARMOR[ActionTag.GUARD]
    if hero(state).trace.last is tag:
        worth *= 1.4  # разгон
    if hero(state).trace.breaks_with(tag) and announced is not ActionTag.PRESS:
        # Разнобой отнимает у врага ход, но обрывает брешь: тратить его на
        # открытого врага - потеря. Только против глухой обороны.
        worth *= 1.4
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


# --- обещание ---------------------------------------------------------


@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_is_about_three_turns(
    content: GameContent, class_id: str, level: int
) -> None:
    turns = sample(content, class_id, level, rank=EnemyRank.NORMAL)
    median = statistics.median(turns)
    assert ORDINARY_FLOOR <= median <= ORDINARY_TURNS, f"{class_id} at {level}: median {median}"
    assert max(turns) <= ORDINARY_CEILING, f"{class_id} at {level}: worst {max(turns)} turns"


@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_at_your_own_level_is_won(
    content: GameContent, class_id: str, level: int
) -> None:
    """Бой, который выдаёт мир, - не подбрасывание монеты. Проигрывают тем ступеням,
    которые сами объявляют себя долгими.
    """
    results = trials(content, class_id, level, rank=EnemyRank.NORMAL)
    won = sum(1 for result in results if result.outcome is Verdict.VICTORY)
    assert won >= ORDINARY_WINS * len(results), f"{class_id} at {level}: won {won}/{len(results)}"


def test_the_long_tiers_are_the_only_long_fights(content: GameContent) -> None:
    """Сложено по классам нарочно: ступени - это обещание об игре, а не о каком-то
    одном классе, а отношение по классам поверх медиан из двух и трёх ходов -
    арифметический шум.
    """

    def pooled(rank: EnemyRank) -> float:
        turns = [turn for class_id in CLASSES for turn in sample(content, class_id, 40, rank=rank)]
        return statistics.median(turns)

    ordinary = pooled(EnemyRank.NORMAL)
    elite = pooled(EnemyRank.ELITE)
    boss = pooled(EnemyRank.BOSS)
    assert elite >= ordinary * ELITE_FLOOR, f"epic {elite} against ordinary {ordinary}"
    assert boss >= ordinary * BOSS_FLOOR, f"boss {boss} against ordinary {ordinary}"


@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_is_won_but_not_for_free(content: GameContent, level: int) -> None:
    """Бой, который ничего не стоит, — это не бой, а кнопка.

    Обычные противники когда-то отнимали примерно двадцатую часть запаса здоровья, и
    локацию можно было пройти из конца в конец не задумываясь: раны, переживающие
    бой, зелья и постели были украшением. Сложено по классам, потому что латам
    *полагается* тратить меньше робы.
    """
    spent = [
        result.health_spent
        for class_id in CLASSES
        for result in trials(content, class_id, level, rank=EnemyRank.NORMAL)
    ]
    median = statistics.median(spent)
    assert median >= ORDINARY_HEALTH_COST, f"at {level}: an ordinary fight costs {median:.0%}"


def _walk(content: GameContent, class_id: str, offset: int) -> tuple[bool, float]:
    """Один проход по локации: бои подряд, ничего не выпито, постель не оплачена.

    Возвращает, пройдена ли вылазка до конца и какая доля запаса здоровья осталась к
    этой минуте.
    """
    character = build(content, class_id, 40)
    stats = derived_stats(content, character)
    for step in range(RUN_LENGTH_FLOOR):
        result = fight(
            content, character, rank=EnemyRank.NORMAL, trial=offset * RUN_LENGTH_FLOOR + step
        )
        if result.outcome is not Verdict.VICTORY:
            return False, 0.0
        character = character.with_health(result.health_left, stats.max_health)
    return True, character.health / stats.max_health


def test_wounds_add_up_over_a_run(content: GameContent) -> None:
    """Раны переносятся, поэтому по локации идут, поглядывая на полосу здоровья.

    Бой за боем, ничего не выпито и за постель не заплачено. Как далеко дойдёт
    персонаж - его дело: латы проходят локацию целиком, а роба останавливается на
    зелье посередине, - но вылазку проходят куда чаще, чем нет, и нетронутым из неё
    не выходит никто.
    """
    walked = [
        _walk(content, klass.id, offset)
        for klass in content.classes
        for offset in range(RUN_TRIALS)
    ]
    share = sum(1 for done, _ in walked if done) / len(walked)
    assert share >= RUN_SURVIVAL, f"only {share:.0%} of runs are walked to the end"

    left = [health for done, health in walked if done]
    # «Нетронутым не выходит никто» - про правило, а не про каждый сид: с тех пор
    # как напор объявленного противника выносит его броню и режет ответ вполсилы
    # (ADR 0050), один пробег из сотни у самого хрупкого-и-быстрого класса
    # случается идеальным. Договор держит медиана, а не единственный лучший бросок.
    assert statistics.median(left) < 0.9, f"{RUN_LENGTH_FLOOR} fights and barely a scratch"
    pristine = sum(health >= 1.0 for health in left)
    assert pristine <= 1, "чаще одного идеального пробега - это уже не рана"


def test_no_class_makes_a_boss_a_different_game(content: GameContent) -> None:
    """Классам позволено ощущаться по-разному, но не быть разной длины.

    Разбойник когда-то кончал босса вдвое быстрее, чем требовалось жрецу, и одно и то
    же содержимое было десятиходовым боем для одного игрока и пятиходовым для
    другого. Берётся с обоих концов полосы: разрыв был шире всего наверху, где крит
    упирается в потолок.
    """
    for level in (10, 40, 150, 300):
        medians = {
            class_id: statistics.median(sample(content, class_id, level, rank=EnemyRank.BOSS))
            for class_id in (klass.id for klass in content.classes)
        }
        fastest = min(medians.values())
        slowest = max(medians.values())
        assert slowest <= fastest * CLASS_SPREAD, f"at {level}: {medians}"


@pytest.mark.parametrize("class_id", CLASSES)
def test_reading_the_intent_shortens_the_fight(content: GameContent, class_id: str) -> None:
    """Весь смысл намерения, следа и бреши: выбирать хорошо обязано платить.

    Обычные бои, где выигрывают оба игрока, чтобы сравнивались ходы, а не выживание:
    игрок, погибший на четвёртом ходу, тоже «закончил» за четыре хода.

    Потолок — вторая половина обещания: темп это то, как бой ведут хорошо, а не
    единственный существующий бой. Если брешь когда-нибудь станет стоить больше всего
    остального вместе взятого, двигать надо пороги в ``domain/rules/combat.py``, а не
    саму механику.
    """
    clever = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL))
    plain = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL, clever=False))
    assert clever < plain, f"{class_id}: {clever} turns played well vs {plain} turns of attacking"
    assert clever * TEMPO_CEILING >= plain, (
        f"{class_id}: tempo alone is worth {plain / clever:.1f} fights"
    )


@pytest.mark.parametrize("level", (1, 40, 300))
def test_a_skill_always_beats_a_plain_attack(content: GameContent, level: int) -> None:
    """Тот откат, с которого всё началось: сила умения была в содержимом плоским
    числом, пока обычная атака росла с уровнем, — и к тридцатому уровню всякое
    умение в игре было хуже нажатия «Атака».
    """
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
