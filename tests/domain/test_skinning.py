"""Свежевание туш: шкуру снимают там, где кончился бой (ADR 0062).

До сих пор шкуры лежали только в узле-жиле, и «свежевание» было ремеслом,
которое к самому свежеванию отношения не имело. Теперь оно случается там, где ему
и место: над тем, что осталось от зверя, - и только у того, у кого в слоте нож.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import BattleState, Combatant, CombatantKind
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules import tools as tool_rules

KNIFE = "skinning_knife@1#common"
PICK = "pick@1#common"


@pytest.fixture
def hunter(content: GameContent) -> Character:
    bare = Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        level=20,
        crafts=CraftLog(MappingProxyType({"skinning": CraftProgress(experience=60)})),
    )
    return replace(bare, equipment=bare.equipment.equip(tool_rules.TOOL_SLOT, KNIFE))


def carcass(kind: EnemyKind, level: int = 10, name: str = "Серый волк") -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=kind,
        level=level,
        max_health=30,
        damage=4,
        armor=1,
        initiative=8.0,
        loot=(),
        gold=5,
    )


def battle(content: GameContent, hero: Character, *fallen: Enemy) -> BattleState:
    """Бой, который уже выигран: герой стоит, всё прочее лежит."""
    side = Combatant(
        id=1,
        side=0,
        kind=CombatantKind.HERO,
        character_id=hero.id,
        name=hero.name,
        level=hero.level,
        max_health=100,
        health=60,
        initiative=10.0,
    )
    dead = tuple(
        Combatant(
            id=index,
            side=1,
            kind=CombatantKind.MONSTER,
            name=enemy.name,
            level=enemy.level,
            max_health=enemy.max_health,
            health=0,
            initiative=enemy.initiative,
            enemy=enemy,
        )
        for index, enemy in enumerate(fallen, start=2)
    )
    return BattleState(
        combatants=(side, *dead),
        order=tuple(one.id for one in (side, *dead)),
        experience=10,
        gold=5,
    )


def test_a_knife_takes_a_hide_off_a_beast(content: GameContent, hunter: Character) -> None:
    won = adventure.resolve_victory(
        content, hunter, battle(content, hunter, carcass(EnemyKind.BEAST)), 1
    )
    assert won.skinned_id
    assert content.item(won.skinned_id).source == "шкуры"
    assert won.skinned_count >= 1
    assert won.skinned_work > 0
    assert won.character.crafts.progress("skinning").experience > 60


def test_a_humanoid_is_not_skinned(content: GameContent, hunter: Character) -> None:
    """«Негуманоидных»: с человека шкуру не снимают, и это не ремесло."""
    won = adventure.resolve_victory(
        content, hunter, battle(content, hunter, carcass(EnemyKind.HUMANOID)), 1
    )
    assert not won.skinned_id
    assert won.character.crafts.progress("skinning").experience == 60


def test_without_a_knife_there_is_no_hide(content: GameContent, hunter: Character) -> None:
    """Тот же уговор, что и у жилы: без своего инструмента не выйдет ничего (ADR 0056)."""
    barehanded = replace(hunter, equipment=hunter.equipment.unequip(tool_rules.TOOL_SLOT))
    won = adventure.resolve_victory(
        content, barehanded, battle(content, barehanded, carcass(EnemyKind.BEAST)), 1
    )
    assert not won.skinned_id

    wrong = replace(hunter, equipment=hunter.equipment.equip(tool_rules.TOOL_SLOT, PICK))
    picked = adventure.resolve_victory(
        content, wrong, battle(content, wrong, carcass(EnemyKind.BEAST)), 1
    )
    assert not picked.skinned_id


def test_the_grade_of_the_hide_follows_the_beast(content: GameContent, hunter: Character) -> None:
    """С матёрого зверя снимают не то же, что с придорожного."""
    early = adventure.resolve_victory(
        content, hunter, battle(content, hunter, carcass(EnemyKind.BEAST, level=2)), 1
    )
    late = adventure.resolve_victory(
        content, hunter, battle(content, hunter, carcass(EnemyKind.BEAST, level=140)), 1
    )
    assert early.skinned_id and late.skinned_id
    assert content.item(late.skinned_id).level > content.item(early.skinned_id).level


def test_the_knife_wears_once_a_fight_however_many_carcasses(
    content: GameContent, hunter: Character
) -> None:
    """Цена берётся за работу, а не за каждую тушу."""
    pack = (carcass(EnemyKind.BEAST), carcass(EnemyKind.ABERRATION, name="Ползун"))
    won = adventure.resolve_victory(content, hunter, battle(content, hunter, *pack), 1)
    assert won.character.wear.spent(KNIFE) == 1
    assert won.skinned_count >= 1
