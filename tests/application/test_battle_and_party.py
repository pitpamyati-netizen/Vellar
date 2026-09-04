"""Бой, который лежит один на всех, и отряд, который в него идёт.

Проверяется то, чего не видно из домена: что бой переживает дорогу через общее
хранилище, что занятость снимается вместе с ним и что отряд собирается только
по согласию того, кого позвали (ADR 0021).
"""

from __future__ import annotations

import pytest

from mmorpg.application.services import battle as battle_service
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Verdict
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules.combat import act
from mmorpg.domain.rules.party import Party
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import InMemoryPartyRepository

SEED = b"battle-store-0001"


def a_hero(name: str, character_id: int) -> Character:
    return Character(
        id=character_id,
        user_id=500 + character_id,
        name=name,
        race_id="human",
        class_id="warrior",
        level=12,
        gold=200,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", None, None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


@pytest.fixture
def cache() -> InMemoryStateCache:
    return InMemoryStateCache()


def a_battle(content: GameContent, *, live_defender: bool = True) -> battle_service.BattleSession:
    session, _ = battle_service.begin(
        content,
        battle_id="duel-1",
        attackers=[(a_hero("Аргус", 1), True)],
        defenders=[(a_hero("Мирна", 2), live_defender)],
        seed=SEED,
        kind=battle_service.BattleKind.DUEL,
        owner=1,
    )
    return session


def test_opening_effects_land_on_the_right_side(content: GameContent) -> None:
    """Условия захода в данж ложатся на бойцов ещё до первого хода (ADR 0036)."""
    from mmorpg.domain.entities.effects import ActiveEffect
    from mmorpg.domain.entities.location import Enemy, EnemyKind

    hazard = ActiveEffect(
        id="dungeon:gloom",
        name="Промозглая тьма",
        modifiers={"initiative_percent": -15.0},
        turns_left=1,
        beneficial=False,
        permanent=True,
    )
    enemy = Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=12,
        max_health=80,
        damage=10,
        armor=5,
        initiative=9.0,
        loot=(),
        gold=5,
    )
    session, _ = battle_service.begin(
        content,
        battle_id="dungeon-1",
        attackers=[(a_hero("Аргус", 1), True)],
        enemies=[enemy],
        seed=SEED,
        kind=battle_service.BattleKind.DESCENT,
        owner=1,
        depth=1,
        opening_effects={0: [hazard]},
    )
    hero_side = [one for one in session.state.combatants if one.is_hero]
    foe_side = [one for one in session.state.combatants if not one.is_hero]
    assert hero_side and all("dungeon:gloom" in one.effects for one in hero_side)
    assert foe_side and all("dungeon:gloom" not in one.effects for one in foe_side)


def test_a_stalking_affix_enters_the_battle_out_of_sight(content: GameContent) -> None:
    """«Неуловимый» заходит в бой незаметным (ADR 0043)."""
    from mmorpg.domain.entities.location import Enemy, EnemyKind
    from mmorpg.domain.entities.statuses import StatusKind

    enemy = Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=12,
        max_health=800,
        damage=4,
        armor=5,
        initiative=0.5,
        loot=(),
        gold=5,
        affixes=("stalking",),
    )
    session, _ = battle_service.begin(
        content,
        battle_id="stalk-1",
        attackers=[(a_hero("Аргус", 1), True)],
        enemies=[enemy],
        seed=SEED,
        kind=battle_service.BattleKind.DESCENT,
        owner=1,
        depth=1,
    )
    foe = next(one for one in session.state.combatants if not one.is_hero)
    assert foe.effects.has(StatusKind.UNSEEN)
    assert session.state.visible_foes_of(1) == ()


# --- запись боя --------------------------------------------------------


async def test_a_battle_survives_the_road_through_the_store(
    content: GameContent, cache: InMemoryStateCache
) -> None:
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    restored = await store.load(session.id)
    assert restored is not None
    assert restored.state.combatants == session.state.combatants
    assert restored.state.order == session.state.order
    assert restored.kind is battle_service.BattleKind.DUEL
    assert restored.owner == 1


async def test_both_sides_are_marked_busy(content: GameContent, cache: InMemoryStateCache) -> None:
    """В два боя сразу не зовут: занятость лежит на каждом участнике."""
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    assert await store.busy(1) == session.id
    assert await store.busy(2) == session.id
    assert await store.busy(3) is None


async def test_the_keeper_frees_a_stuck_battle_lock(
    content: GameContent, cache: InMemoryStateCache
) -> None:
    store = battle_service.BattleStore(cache)
    await store.save(a_battle(content))

    assert await store.free(1) is True  # замок был
    assert await store.busy(1) is None
    assert await store.free(1) is False  # и его больше нет


async def test_a_finished_battle_frees_everybody_but_stays_readable(
    content: GameContent, cache: InMemoryStateCache
) -> None:
    """Экран итога - настоящий экран, и читается он по той же записи."""
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    roster = battle_service.roster_for(session, {1: a_hero("Аргус", 1), 2: a_hero("Мирна", 2)})
    current = session.state.active
    assert current is not None
    finished = act(content, roster, session.state, BattleAction(kind=ActionKind.YIELD), SEED)
    assert finished.is_over

    settled = battle_service.settled(session)
    await store.release(settled)

    assert await store.busy(1) is None, "занятость снимается вместе с боем"
    kept = await store.load(session.id)
    assert kept is not None and kept.settled, "сама запись ещё на месте"

    await store.forget(settled)
    assert await store.load(session.id) is None


async def test_a_battle_that_expired_frees_the_character(cache: InMemoryStateCache) -> None:
    """Пропавший бой освобождает: персонажу нельзя остаться занятым навсегда."""
    store = battle_service.BattleStore(cache)
    await cache.set(store.key_for_character(7), "gone-battle", 60)
    assert await store.busy(7) is None


async def test_a_battle_the_code_can_no_longer_read_is_no_battle(
    cache: InMemoryStateCache,
) -> None:
    """Запись боя, которая не разбирается, читается как «боя нет».

    Кэш переживает выпуск: бой, отложенный прежним кодом, может не сойтись с
    нынешним разбором. Падение здесь заперло бы игрока в бою, который не открыть
    и не бросить, - вызывающие умеют отвечать на ``None``, а на исключение нет
    (``Claude.md``, правило 8).
    """
    store = battle_service.BattleStore(cache)
    await cache.set(store.key_of("stale"), '{"id": "stale"}', 60)
    await cache.set(store.key_for_character(9), "stale", 60)

    assert await store.load("stale") is None
    assert await store.busy(9) is None


async def test_a_battle_written_as_nonsense_is_no_battle(cache: InMemoryStateCache) -> None:
    """И то, что вовсе не разбирается как запись, - тоже «боя нет»."""
    store = battle_service.BattleStore(cache)
    await cache.set(store.key_of("junk"), "не json вовсе", 60)
    assert await store.load("junk") is None


def test_roster_maps_fighters_to_the_characters_behind_them(content: GameContent) -> None:
    session = a_battle(content)
    roster = battle_service.roster_for(session, {1: a_hero("Аргус", 1), 2: a_hero("Мирна", 2)})
    assert sorted(roster) == [1, 2]
    assert roster[1].name == "Аргус"


def test_the_engine_side_gets_no_telegram_id(content: GameContent) -> None:
    """Слепку писать некому: он ходит сам (``BattleSession.live_participants``)."""
    session = a_battle(content, live_defender=False)
    assert [one.character_id for one in session.live_participants()] == [1]
    assert len(session.participants()) == 2


def test_the_verdict_is_read_per_participant(content: GameContent) -> None:
    session = a_battle(content)
    for one in session.participants():
        assert session.state.verdict_for(one.id) is Verdict.ONGOING


# --- отряд -------------------------------------------------------------


async def test_a_party_is_created_before_anyone_is_called(cache: InMemoryStateCache) -> None:
    """Отряд из одного - это отряд: он заведён нарочно, и звать в него можно."""
    parties = PartyStore(InMemoryPartyRepository(), cache)
    party = await parties.create(1)
    assert party is not None and party.members == (1,) and party.alone
    assert await parties.of(1) == party
    assert await parties.create(1) is None, "второго отряда у одного человека не бывает"


async def test_nobody_is_called_into_a_party_that_was_never_created(
    cache: InMemoryStateCache,
) -> None:
    """Зов без отряда ни к чему не ведёт: звать умеет тот, у кого отряд есть."""
    parties = PartyStore(InMemoryPartyRepository(), cache)
    await parties.call(leader_id=1, invitee_id=2)
    assert await parties.accept(2) is None
    assert await parties.of(2) is None


async def test_disbanding_lets_everyone_go(cache: InMemoryStateCache) -> None:
    parties = PartyStore(InMemoryPartyRepository(), cache)
    await parties.save(Party(leader_id=1, members=(1, 2, 3)))
    party = await parties.of(1)
    assert party is not None

    await parties.disband(party)
    assert await parties.of(1) is None
    assert await parties.of(3) is None


async def test_a_party_is_joined_when_the_call_is_accepted(cache: InMemoryStateCache) -> None:
    parties = PartyStore(InMemoryPartyRepository(), cache)
    await parties.create(1)
    await parties.call(leader_id=1, invitee_id=2)
    assert await parties.called_by(2) == 1

    party = await parties.accept(2)
    assert party is not None
    assert party.members == (1, 2)
    assert await parties.of(1) == party
    assert await parties.of(2) == party
    assert await parties.called_by(2) == 0, "зов израсходован"


async def test_nobody_joins_a_party_they_were_not_called_to(cache: InMemoryStateCache) -> None:
    parties = PartyStore(InMemoryPartyRepository(), cache)
    assert await parties.accept(5) is None
    assert await parties.of(5) is None


async def test_declining_forgets_the_call(cache: InMemoryStateCache) -> None:
    parties = PartyStore(InMemoryPartyRepository(), cache)
    await parties.create(1)
    await parties.call(leader_id=1, invitee_id=2)
    await parties.forget_call(2)
    assert await parties.accept(2) is None


async def test_leaving_shrinks_the_party_and_the_leader_ends_it(
    cache: InMemoryStateCache,
) -> None:
    parties = PartyStore(InMemoryPartyRepository(), cache)
    await parties.save(Party(leader_id=1, members=(1, 2, 3)))

    left = await parties.leave(3)
    assert left is not None and left.members == (1, 2)

    alone = await parties.leave(2)
    assert alone is not None and alone.alone, "оставшийся один отряд не распускается сам"

    assert await parties.leave(1) is None, "ушёл собравший - отряда больше нет"
    assert await parties.of(1) is None


async def test_the_party_roster_lives_in_the_repository_not_the_cache(
    cache: InMemoryStateCache,
) -> None:
    """Состав отряда лежит в базе и сроком не ограничен: он переживает вылазку (ADR 0029).

    Зов - другое дело, он в кэше со сроком; поэтому истечение кэша забирает
    приглашение и не трогает состав.
    """
    roster = InMemoryPartyRepository()
    parties = PartyStore(roster, cache)
    await parties.save(Party(leader_id=1, members=(1, 2)))
    await parties.call(leader_id=1, invitee_id=3)

    read = await parties.of(2)
    assert read is not None
    assert (read.leader_id, read.members) == (1, (1, 2))

    # Кэш выметен целиком - как после разрыва Redis или суток простоя.
    await cache.delete("party-call:3")
    assert await parties.called_by(3) == 0
    still = await parties.of(2)
    assert still is not None and still.members == (1, 2), "состав кэшем не держится"


def test_the_party_has_a_ceiling_and_a_level_window() -> None:
    full = Party(leader_id=1, members=tuple(range(1, party_rules.MAX_MEMBERS + 1)))
    assert full.full
    assert "Создайте его" in party_rules.invite_refusal(
        inviter_level=10,
        invitee_name="Мирна",
        invitee_level=10,
        party=None,
        invitee_in_party=False,
    ), "звать некуда, пока отряда нет"
    assert "не помещается" in party_rules.invite_refusal(
        inviter_level=10,
        invitee_name="Мирна",
        invitee_level=10,
        party=full,
        invitee_in_party=False,
    )
    mine = Party(leader_id=1)
    assert "Разница уровней" in party_rules.invite_refusal(
        inviter_level=10,
        invitee_name="Мирна",
        invitee_level=10 + party_rules.LEVEL_WINDOW + 1,
        party=mine,
        invitee_in_party=False,
    )
    assert (
        party_rules.invite_refusal(
            inviter_level=10,
            invitee_name="Мирна",
            invitee_level=12,
            party=mine,
            invitee_in_party=False,
        )
        == ""
    )


def test_the_loot_goes_round_the_party() -> None:
    """Добыча раздаётся по кругу, а не оседает у собравшего отряд."""
    import random

    shares = party_rules.distribute(("a", "b", "c", "d"), (1, 2), random.Random(7))
    assert sorted(len(items) for items in shares.values()) == [2, 2]
    assert sorted(item for items in shares.values() for item in items) == ["a", "b", "c", "d"]
