"""Управа: новое имя и наследие — нажатиями, как их нажимает игрок (ADR 0070)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.rules import milestones as milestone_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)


@pytest.fixture
def elder(content: GameContent) -> Character:
    """Семьдесят пятый уровень с собранной силой: первое имя ему уже дадут."""
    first = content.rebirth_at(1)
    assert first is not None
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=first.level,
        gold=400,
        unspent_stat_points=2,
        allocated=StatBlock(STR=280),
    )


def step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, hero, current, message, clock=CLOCK, world_seed=WORLD_SEED)
    return current


@pytest.fixture
def in_chamber(content: GameContent, elder: Character) -> PlayState:
    return step(content, elder, begin(elder), "Мир", "Управа")


def test_the_chamber_stands_in_every_city(content: GameContent) -> None:
    for city in content.cities:
        assert "chamber" in city.services, city.id


def test_a_new_name_resets_the_allocation_and_keeps_the_haul(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    assert in_chamber.screen is ScreenId.CHAMBER

    confirm = step(content, elder, in_chamber, "Просить новое имя")
    assert confirm.screen is ScreenId.CHAMBER_REMORT

    done = step(content, elder, confirm, "Подтвердить")
    assert done.screen is ScreenId.CHAMBER
    stored = done.pending.character
    assert stored is not None
    first = content.rebirth_at(1)
    assert first is not None
    assert stored.level == 1
    assert stored.gold == 400
    assert stored.remorts == 1
    # Розданное вернулось нерозданным — вот что делает уход уходом.
    assert stored.allocated == StatBlock()
    assert stored.unspent_stat_points == content.rules.free_points_at_creation + first.stat_points
    assert content.rebirth_titles[0] in done.notice


def test_the_chamber_names_the_price_before_the_button(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    """Всё, что случится по нажатию, названо до него."""
    shown = render(content, elder, in_chamber, world_seed=WORLD_SEED)
    first = content.rebirth_at(1)
    assert first is not None
    assert first.name in shown.text()
    assert "Ступень" in shown.text() or "ступени специализации" in shown.text()

    warned = render(
        content,
        elder,
        step(content, elder, in_chamber, "Просить новое имя"),
        world_seed=WORLD_SEED,
    )
    body = warned.text()
    assert "вернутся нерозданными" in body
    assert "навсегда" in body


def test_nobody_short_of_the_threshold_is_let_in(content: GameContent, elder: Character) -> None:
    young = replace(elder, level=40)
    in_chamber = step(content, young, begin(young), "Мир", "Управа")
    shown = render(content, young, in_chamber, world_seed=WORLD_SEED)
    first = content.rebirth_at(1)
    assert first is not None
    assert f"с {first.level} уровня" in shown.text()
    assert all("просить новое имя" not in item.text.lower() for row in shown.rows for item in row)

    # Кнопки нового имени на экране нет вовсе, а нажатая мимо неё уводит не дальше
    # самой управы (доступность, правило 12).
    refused = step(content, young, in_chamber, "Просить новое имя")
    assert refused.screen is ScreenId.CHAMBER


def test_a_milestone_is_named_and_then_carried_through_the_reset(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    """Наследие — ответ ровно на ту потерю, которую наносит уход."""
    taken = milestone_rules.reached(content, elder)
    assert taken, "герою нужна хотя бы одна веха, чтобы было что уносить"
    kept = taken[0]

    on_legacy = step(content, elder, in_chamber, "Наследие")
    assert on_legacy.screen is ScreenId.LEGACY
    listed = render(content, elder, on_legacy, world_seed=WORLD_SEED)
    assert content.trait(kept.trait_id).name in listed.text()

    named = step(content, elder, on_legacy, f"Унести: {content.trait(kept.trait_id).name}")
    carrier = named.pending.character
    assert carrier is not None
    assert carrier.legacy_ids == (kept.trait_id,)

    confirm = step(content, carrier, begin(carrier), "Мир", "Управа", "Просить новое имя")
    done = step(content, carrier, confirm, "Подтвердить")
    stored = done.pending.character
    assert stored is not None
    assert stored.legacy_ids == (kept.trait_id,)
    # Порогом она больше не держится, а наследием — держится.
    assert kept.trait_id not in milestone_rules.trait_ids(content, stored)


def test_a_named_milestone_can_be_taken_back(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    taken = milestone_rules.reached(content, elder)
    kept = taken[0]
    carrier = replace(elder, legacy_ids=(kept.trait_id,))

    on_legacy = step(content, carrier, begin(carrier), "Мир", "Управа", "Наследие")
    shown = render(content, carrier, on_legacy, world_seed=WORLD_SEED)
    assert "Занято мест: 1 из 1." in shown.text()

    freed = step(content, carrier, on_legacy, f"Оставить: {content.trait(kept.trait_id).name}")
    stored = freed.pending.character
    assert stored is not None
    assert stored.legacy_ids == ()


def test_the_last_step_closes_the_book(content: GameContent, elder: Character) -> None:
    """Пройдя все ступени, игрок слышит об этом, а не видит молчащую кнопку."""
    done = replace(elder, level=150, remorts=len(content.rebirths))
    in_chamber = step(content, done, begin(done), "Мир", "Управа")
    shown = render(content, done, in_chamber, world_seed=WORLD_SEED)
    assert "Ступеней больше нет" in shown.text()
    assert not shown.rows or all(
        "просить новое имя" not in item.text.lower() for row in shown.rows for item in row
    )
