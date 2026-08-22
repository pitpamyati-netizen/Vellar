"""Бой сторонами: один против стаи, отряд против стаи, отряд против отряда.

Один движок на все случаи - это и есть то, ради чего он переписан (ADR 0021).
Здесь проверяется не формула, а состав: что стороны считаются как стороны, что
очередь общая, что живой игрок ждёт нажатия, а за породу ходит движок, и что
уход одной стороны кончает бой.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ATTACKERS,
    DEFENDERS,
    ActionKind,
    BattleAction,
    BattleOutcome,
    BattleState,
    EventKind,
    Verdict,
)
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle

SEED = b"sides-seed-00001"


def make_enemy(name: str = "Волк", health: int = 400, damage: int = 10) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=10,
        max_health=health,
        damage=damage,
        armor=2,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=12,
    )


def a_hero(name: str, character_id: int, *, level: int = 10) -> Character:
    return Character(
        id=character_id,
        user_id=1000 + character_id,
        name=name,
        race_id="human",
        class_id="warrior",
        level=level,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", "warrior_provokatsiya", None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


def build(
    content: GameContent,
    attackers: list[tuple[Character, bool]],
    defenders: list[tuple[Character, bool]] | None = None,
    enemies: tuple[Enemy, ...] = (),
) -> tuple[BattleState, dict[int, Character]]:
    """Собрать бой любого состава: номера идут подряд, нападающие первыми."""
    roster: dict[int, Character] = {}
    fighters = []
    next_id = 1
    for character, live in attackers:
        fighters.append(
            hero_combatant(content, character, combatant_id=next_id, side=ATTACKERS, live=live)
        )
        roster[next_id] = character
        next_id += 1
    for character, live in defenders or []:
        fighters.append(
            hero_combatant(content, character, combatant_id=next_id, side=DEFENDERS, live=live)
        )
        roster[next_id] = character
        next_id += 1
    for enemy in enemies:
        fighters.append(monster_combatant(enemy, combatant_id=next_id, side=DEFENDERS))
        next_id += 1
    return open_battle(content, roster, fighters, SEED), roster


def strike(
    content: GameContent,
    roster: dict[int, Character],
    state: BattleState,
    seed: bytes = SEED,
) -> BattleState:
    return act(content, roster, state, BattleAction(kind=ActionKind.ATTACK), seed)


# --- отряд против стаи -------------------------------------------------


def test_a_party_fights_the_pack_in_one_queue(content: GameContent) -> None:
    """Двое против троих - это один бой с общей очередью, а не два боя рядом."""
    first, second = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, _ = build(
        content,
        [(first, True), (second, True)],
        enemies=(make_enemy(), make_enemy(name="Волчица"), make_enemy(name="Вожак")),
    )
    assert len(state.living(ATTACKERS)) == 2
    assert len(state.living(DEFENDERS)) == 3
    assert len(state.order) == 5, "в очереди стоят все живые"
    assert state.awaiting is not None, "бой ждёт нажатия живого игрока"


def test_the_queue_comes_back_to_the_second_player(content: GameContent) -> None:
    """После хода первого бой ждёт второго, а не крутит за него движок."""
    first, second = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, roster = build(
        content, [(first, True), (second, True)], enemies=(make_enemy(health=9_000),)
    )
    started = state.awaiting
    assert started is not None
    after = strike(content, roster, state)
    waiting = after.awaiting
    assert waiting is not None
    assert waiting.id != started.id, "ход перешёл к другому живому бойцу"


def test_a_fallen_ally_does_not_end_the_fight(content: GameContent) -> None:
    """Пока на стороне кто-то стоит, бой идёт: сторона проигрывает целиком."""
    first, second = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, roster = build(content, [(first, True), (second, True)], enemies=(make_enemy(),))
    fallen = state.by_id(2)
    assert fallen is not None
    state = state.replace_combatant(replace(fallen, health=0))
    after = strike(content, roster, state)
    assert not after.is_over or after.verdict_for(1) is Verdict.VICTORY


def test_the_pack_pays_the_side_that_beat_it(content: GameContent) -> None:
    """Плата считается на сторону; делит её отряд (``rules/party.split``)."""
    first, second = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, roster = build(content, [(first, True), (second, True)], enemies=(make_enemy(health=1),))
    for turn in range(10):
        state = strike(content, roster, state, turn.to_bytes(16, "big"))
        if state.is_over:
            break
    assert state.verdict_for(1) is Verdict.VICTORY
    assert state.verdict_for(2) is Verdict.VICTORY
    assert state.experience > 0

    shares = party_rules.split(state.experience, 2)
    assert sum(shares) == state.experience, "делёж не теряет ни единицы"


# --- отряд против отряда -----------------------------------------------


def test_players_fight_players_in_the_same_engine(content: GameContent) -> None:
    """Игроки против игроков - тот же бой, только напротив живые."""
    left = [(a_hero("Аргус", 1), True), (a_hero("Мирна", 2), True)]
    right = [(a_hero("Корин", 3), True), (a_hero("Тьен", 4), True)]
    state, _ = build(content, left, right)

    assert all(one.is_hero for one in state.combatants)
    assert len(state.living(ATTACKERS)) == 2
    assert len(state.living(DEFENDERS)) == 2
    current = state.active
    assert current is not None and current.live, "ходит живой, и бой его ждёт"


def test_only_the_one_whose_turn_it_is_moves(content: GameContent) -> None:
    """Ход чужой очереди движок не крутит: он останавливается и ждёт."""
    left = [(a_hero("Аргус", 1), True)]
    right = [(a_hero("Корин", 3), True)]
    state, roster = build(content, left, right)
    first = state.active
    assert first is not None

    after = strike(content, roster, state)
    second = after.active
    assert second is not None
    assert second.id != first.id, "очередь перешла ко второму игроку"
    assert after.awaiting is not None, "и бой ждёт уже его"


def test_a_snapshot_side_is_played_by_the_engine(content: GameContent) -> None:
    """Слепок арены не ждёт нажатия: за него ходит движок, и ходит сразу."""
    left = [(a_hero("Аргус", 1), True)]
    right = [(a_hero("Корин", 3), False)]
    state, roster = build(content, left, right)
    after = strike(content, roster, state)
    assert after.awaiting is None or after.awaiting.id == 1
    assert any(event.actor_id == 2 for event in after.events) or after.is_over


def test_yielding_hands_the_field_to_the_other_side(content: GameContent) -> None:
    """Сдача - единственная дверь из поединка, который бросили (ADR 0021)."""
    left = [(a_hero("Аргус", 1), True)]
    right = [(a_hero("Корин", 3), True)]
    state, roster = build(content, left, right)
    current = state.active
    assert current is not None

    after = act(content, roster, state, BattleAction(kind=ActionKind.YIELD), SEED)
    assert after.outcome is BattleOutcome.FLED
    assert after.verdict_for(current.id) is Verdict.FLED
    other = next(one for one in after.combatants if one.id != current.id)
    assert after.verdict_for(other.id) is Verdict.VICTORY
    assert any(event.kind is EventKind.YIELDED for event in after.events)


def test_a_duel_pays_no_experience(content: GameContent) -> None:
    """За поединок платят кошельки, а не тела: опыта и золота в бою нет."""
    left = [(a_hero("Аргус", 1), True)]
    right = [(a_hero("Корин", 3, level=10), True)]
    state, roster = build(content, left, right)
    for turn in range(80):
        current = state.active
        if current is None:
            break
        state = strike(content, roster, state, turn.to_bytes(16, "big"))
        if state.is_over:
            break
    assert state.is_over
    assert state.experience == 0
    assert state.gold == 0
    assert state.loot == ()


def test_every_hero_keeps_their_own_trace(content: GameContent) -> None:
    """След - у бойца, а не у боя: в поединке двоих их два, и они разные."""
    left = [(a_hero("Аргус", 1), True)]
    right = [(a_hero("Корин", 3), True)]
    state, roster = build(content, left, right)
    after = strike(content, roster, state)
    first, second = after.by_id(1), after.by_id(2)
    assert first is not None and second is not None
    assert first.trace != second.trace, "сходил один - след появился у него"
