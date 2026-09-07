"""Выучка от третьего ранга до взятого выбора (ADR 0079, 0083).

Сквозной проход: игрок поднимает умение до третьего ранга, игра тут же
спрашивает, чему оно научилось, он выбирает - и с этого хода умение делает то,
что выбрано. Пока выбор не сделан, очко в это умение больше не тратится: ранг,
купленный и не потраченный на выбор, лежал бы без дела.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import skill_mastery as mastery_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)

STRIKE = "warrior_sekushchiy_roscherk"


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=40,
        gold=500,
        unspent_skill_points=5,
    )


def step(content: GameContent, character: Character, state: PlayState, *messages: str) -> PlayState:
    for message in messages:
        state = advance(content, character, state, message, clock=CLOCK, world_seed=WORLD_SEED)
    return state


def ranked(character: Character, rank: int) -> Character:
    return replace(character, loadout=character.loadout.with_rank(STRIKE, rank))


def test_the_third_rank_asks_at_once(content: GameContent, hero: Character) -> None:
    """Третий ранг спрашивает сразу: отложенный выбор читается как молчание."""
    ready = ranked(hero, 2)
    skill = content.skill(STRIKE)
    skills = step(content, ready, begin(ready), "Умения")
    raised = step(content, ready, skills, skill_screens.skill_entry_text(content, ready, skill))
    assert raised.screen is ScreenId.SKILL_MASTERY
    assert raised.pending.character is not None
    assert raised.pending.character.loadout.rank_of(STRIKE) == 3
    assert "Выберите, чему умение научилось" in raised.notice


def test_the_choice_is_named_before_it_is_made(content: GameContent, hero: Character) -> None:
    """Каждая выучка названа тем, что она делает, - до нажатия."""
    ready = ranked(hero, 3)
    state = replace(begin(ready), screen=ScreenId.SKILL_MASTERY, mastery_code=STRIKE)
    text = render(content, ready, state, world_seed=WORLD_SEED, clock=CLOCK).text()
    assert "Выучка" in text
    for one in skill_rules.mastery_choices(content, ready, content.skill(STRIKE)):
        assert one.name in text
        assert mastery_rules.words(one)[:20] in text


def test_the_chosen_mastery_sticks(content: GameContent, hero: Character) -> None:
    """Выбранное ложится на умение и возвращает игрока к списку умений."""
    ready = ranked(hero, 3)
    skill = content.skill(STRIKE)
    first = skill_rules.mastery_choices(content, ready, skill)[0]
    state = replace(begin(ready), screen=ScreenId.SKILL_MASTERY, mastery_code=STRIKE)
    taught = step(content, ready, state, first.name)
    assert taught.pending.character is not None
    assert skill_rules.masteries_of(taught.pending.character, skill) == (first.code,)
    assert taught.screen is ScreenId.SKILLS
    assert first.name in taught.notice


def test_a_waiting_skill_leads_to_the_choice_instead_of_a_rank(
    content: GameContent, hero: Character
) -> None:
    """Пока выучка не выбрана, нажатие ведёт к выбору, а не тратит очко."""
    ready = ranked(hero, 3)
    skill = content.skill(STRIKE)
    skills = step(content, ready, begin(ready), "Умения")
    said = skill_screens.skill_entry_text(content, ready, skill)
    assert "ждёт выучки" in said

    pressed = step(content, ready, skills, said)
    assert pressed.screen is ScreenId.SKILL_MASTERY
    assert pressed.pending.empty


def test_a_taken_mastery_is_said_on_the_list(content: GameContent, hero: Character) -> None:
    """Чему умение научено, читается в списке умений, а не только в бою."""
    skill = content.skill(STRIKE)
    first = skill.masteries[0]
    ready = replace(
        ranked(hero, 3), loadout=ranked(hero, 3).loadout.with_mastery(STRIKE, first.code)
    )
    said = skill_screens.skill_detail(content, skill, ready)
    assert first.name in said


def test_the_combo_is_named_on_the_list(content: GameContent, hero: Character) -> None:
    """По чему умение бьёт сильнее, слышно на экране умений (``rules/combo``)."""
    answering = next(one for one in content.skills if one.answers is not None)
    said = skill_screens.skill_detail(content, answering, hero)
    assert "Связка" in said
