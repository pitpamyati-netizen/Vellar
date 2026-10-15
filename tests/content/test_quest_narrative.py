"""Условия на доске объясняют цену, вид работы и опасность выбранного места."""

from __future__ import annotations

import re

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.quest import ObjectiveKind
from mmorpg.domain.procgen.enemies import candidates


def test_city_contracts_state_their_actual_price_and_target_count(content: GameContent) -> None:
    for quest in content.quests:
        if quest.is_trial:
            continue
        numbers = {int(number) for number in re.findall(r"\b\d+\b", quest.terms)}
        assert quest.reward_gold in numbers, f"{quest.id}: объявленная плата устарела"
        # Полный спуск считается один раз; его реплика называет все встречи.
        if quest.objective is not ObjectiveKind.DELVE:
            assert quest.target_count in numbers, f"{quest.id}: неясно, сколько сделать"


def test_contracts_warn_before_sending_someone_to_contested_land(content: GameContent) -> None:
    for quest in content.quests:
        if quest.is_trial or not quest.location_slot:
            continue
        location = content.city(quest.city_id).location(quest.location_slot)
        if location.pvp:
            assert "могут нападать другие путники" in quest.terms, quest.id


def test_craft_contracts_explain_that_finished_goods_stay_with_the_maker(
    content: GameContent,
) -> None:
    for quest in content.quests:
        if not quest.is_trial and quest.objective is ObjectiveKind.CRAFT:
            assert "останутся у вас" in quest.terms, quest.id


def test_every_named_combat_target_can_appear_in_its_quest_location(content: GameContent) -> None:
    """Существования породы в каталоге мало: она должна встречаться по месту работы."""
    for quest in content.quests:
        if quest.is_trial or not quest.location_slot:
            continue
        if quest.objective not in {ObjectiveKind.KILL, ObjectiveKind.ELITE}:
            continue
        location = content.city(quest.city_id).location(quest.location_slot)
        pool = candidates(content.enemy_archetypes, location.biome, dungeon=False)
        assert any(
            not quest.target_kind or quest.target_kind in {archetype.id, archetype.kind.value}
            for archetype in pool
        ), f"{quest.id}: требуемые противники не появляются в этой местности"
