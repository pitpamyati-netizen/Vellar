"""Дерево специализации от развилки до взятой ветки (ADR 0074).

Сквозной проход: игрок открывает «Ступень», видит две ветки, берёт испытание,
сдаёт его шаги и только после этого становится тем, кем хотел, — а вместе с
веткой получает умение, которого до неё в списке изучаемых не было.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)

GLADIATOR = "warrior_gladiator"


@pytest.fixture
def grown() -> Character:
    """Воин, доросший до первой развилки и не сделавший ни шага по испытанию."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=40,
        gold=500,
    )


def _step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    for message in messages:
        state = advance(content, hero, state, message, clock=CLOCK, world_seed=WORLD_SEED)
    return state


def _open(content: GameContent, hero: Character) -> PlayState:
    return _step(content, hero, begin(hero), "Персонаж", "Характеристики", "Ступень")


def test_the_fork_is_reached_from_the_stats_screen(content: GameContent, grown: Character) -> None:
    state = _open(content, grown)
    assert state.screen is ScreenId.SUBCLASS

    text = render(content, grown, state, world_seed=WORLD_SEED, clock=CLOCK).text()
    assert "Гладиатор" in text
    assert "Рыцарь" in text
    assert "Испытание не пройдено — сдано 0 из 3" in text


def test_the_fork_offers_no_branch_before_the_trial(content: GameContent, grown: Character) -> None:
    """Кнопки «Стать» нет вовсе, пока испытание не пройдено: она бы молчала."""
    screen = render(content, grown, _open(content, grown), world_seed=WORLD_SEED, clock=CLOCK)
    labels = [one.text for row in screen.rows for one in row]
    assert not any(text.startswith("Стать:") for text in labels)
    assert any(text.startswith("Испытание: Гладиатор") for text in labels)


def test_the_trial_is_taken_and_handed_in_step_by_step(
    content: GameContent, grown: Character
) -> None:
    one = content.subclass(GLADIATOR)
    hero = grown
    state = _step(content, hero, _open(content, hero), "Испытание: Гладиатор")
    assert state.screen is ScreenId.SUBCLASS_TRIAL

    for number, quest_id in enumerate(one.trial_ids, start=1):
        state = _step(content, hero, state, "Взяться за испытание")
        taken = state.pending.character
        assert taken is not None and taken.quests.is_taken(quest_id), quest_id
        hero = taken

        # Счёт двигают бои и узлы, а не этот экран: досчитываем его напрямую.
        quest = content.quest(quest_id)
        hero = replace(hero, quests=hero.quests.advanced(quest_id, quest.target_count))
        state = _step(content, hero, state, "Сдать задание")
        handed = state.pending.character
        assert handed is not None and handed.quests.is_done(quest_id), quest_id
        hero = handed
        assert subclass_rules.trial_progress(content, hero, one) == (number, 3)

    assert subclass_rules.trial_done(content, hero, one)


def test_the_branch_is_taken_only_after_the_trial(content: GameContent, grown: Character) -> None:
    one = content.subclass(GLADIATOR)
    ready = replace(grown, quests=grown.quests)
    for quest_id in one.trial_ids:
        ready = replace(ready, quests=ready.quests.take(quest_id).complete(quest_id))

    state = _open(content, ready)
    screen = render(content, ready, state, world_seed=WORLD_SEED, clock=CLOCK)
    assert any("Стать: Гладиатор" in label.text for row in screen.rows for label in row)

    state = _step(content, ready, state, "Стать: Гладиатор")
    taken = state.pending.character
    assert taken is not None
    assert taken.subclass_ids == (GLADIATOR,)
    assert "Вы стали: Гладиатор" in state.notice
    assert "Вихрь арены" in state.notice


def test_the_branch_puts_its_skill_into_the_mentor_list(
    content: GameContent, grown: Character
) -> None:
    """Умение ветки приходит в список изучаемых и раньше него там не лежало."""
    before = {skill.code for skill in skill_rules.teachable(content, grown)}
    taken = replace(grown, subclass_ids=(GLADIATOR,))
    after = {skill.code for skill in skill_rules.teachable(content, taken)}

    code = content.subclass(GLADIATOR).skill_code
    assert code not in before
    assert code in after


def test_the_second_fork_only_shows_the_children_of_the_first(
    content: GameContent, grown: Character
) -> None:
    hero = replace(grown, level=80, subclass_ids=(GLADIATOR,))
    screen = render(content, hero, _open(content, hero), world_seed=WORLD_SEED, clock=CLOCK)
    text = screen.text()

    assert "Дуэлянт" in text
    assert "Полководец" in text
    assert "Страж" not in text, "ветка Рыцаря на этой дороге не растёт"


def test_a_walked_tree_says_so_and_offers_nothing(content: GameContent, grown: Character) -> None:
    hero = replace(
        grown,
        level=150,
        subclass_ids=(GLADIATOR, "warrior_duelist", "warrior_blademaster"),
    )
    screen = render(content, hero, _open(content, hero), world_seed=WORLD_SEED, clock=CLOCK)

    assert "Дерево пройдено до конца" in screen.text()
    assert "Мастер клинка" in screen.text()
    assert screen.rows == ()
