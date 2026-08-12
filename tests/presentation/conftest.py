"""Screen fixtures.

``all_screens`` builds every screen the game can show, so the accessibility tests
can inspect all of them at once. Every new screen must be added here - that is
deliberate: a screen nobody listed is a screen nobody checked.
"""

from __future__ import annotations

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities import (
    Character,
    GameContent,
    GeneratedLocation,
    SkillLoadout,
    StatBlock,
)
from mmorpg.domain.entities.combat import CombatState
from mmorpg.domain.entities.content import Item
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.procgen import generate_location
from mmorpg.domain.rules.combat import start_combat
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.handlers import creation as handlers_creation
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import creation, play, shop
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    ListFilters,
    PageState,
    paginated_screen,
)
from tests.conftest import CONTENT_ROOT


@pytest.fixture(scope="session")
def content() -> GameContent:
    return load_content(CONTENT_ROOT)


@pytest.fixture(scope="session")
def hero() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


@pytest.fixture(scope="session")
def sample_location() -> GeneratedLocation:
    return generate_location(
        world_seed="vellar-test",
        city_id="farhold",
        slot=1,
        cycle=100,
        name="Тихие Луга",
        biome="луга",
        level_min=1,
        level_max=4,
    )


@pytest.fixture(scope="session")
def fighter(content: GameContent) -> Character:
    return Character(
        id=2,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", None, None, None, None),
            passives=("warrior_toughness", None, None),
            racial="race_human_second_wind",
        ),
    )


@pytest.fixture(scope="session")
def sample_fight(content: GameContent, fighter: Character) -> CombatState:
    enemy = Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=4,
        max_health=120,
        damage=9,
        armor=3,
        initiative=9.0,
        is_elite=False,
        loot=("wolf_pelt",),
        gold=14,
    )
    return start_combat(content, fighter, (enemy,))


@pytest.fixture(scope="session")
def sample_stock(content: GameContent) -> tuple[Item, ...]:
    return roll_assortment(
        content, world_seed="vellar-test", city_id="farhold", cycle=100, character_level=8
    )


@pytest.fixture
def complete_draft() -> CharacterDraft:
    return CharacterDraft(
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        trait_ids=("berserker", "duelist"),
        allocated=StatBlock(STR=3, END=2),
    )


@pytest.fixture
def all_screens(
    content: GameContent,
    hero: Character,
    complete_draft: CharacterDraft,
    sample_location: GeneratedLocation,
    fighter: Character,
    sample_fight: CombatState,
    sample_stock: tuple[Item, ...],
) -> list[Screen]:
    """Every screen in the game, rendered with sample data.

    New screens must be added here: a screen nobody listed is a screen nobody
    checked against the accessibility rules.
    """
    empty = CharacterDraft()
    screens: list[Screen] = [
        creation.name_screen(empty),
        creation.name_screen(complete_draft, notice="Имя Аргус уже занято."),
        creation.race_screen(content, empty, PageState()),
        creation.race_screen(content, complete_draft, PageState(page=2)),
        creation.race_details_screen(content, "dwarf"),
        creation.class_screen(content, complete_draft),
        creation.class_details_screen(content, "warrior"),
        creation.traits_screen(content, complete_draft, PageState()),
        creation.traits_screen(content, empty, PageState(page=3)),
        creation.points_screen(content, complete_draft),
        creation.confirm_screen(content, complete_draft),
        handlers_creation.welcome_screen(),
        handlers_creation.created_screen("Аргус", "Дальний Оплот"),
        play.main_menu_screen(content, hero, derived_stats(content, hero)),
        play.world_screen(content, hero, PageState()),
        play.city_screen(content, content.city("farhold"), hero),
        play.location_list_screen(content, content.city("farhold"), hero, PageState()),
        play.location_screen(sample_location, sample_location.entrance, cleared=0),
        play.location_screen(
            sample_location, sample_location.exit_node, cleared=0b101, notice="Узел пройден."
        ),
        play.character_screen(content, hero, derived_stats(content, hero)),
        play.stub_screen("Таверна"),
        combat_screens.combat_screen(content, fighter, sample_fight),
        combat_screens.bag_screen(content, (("small_healing_potion", "Малое зелье лечения", 3),)),
        combat_screens.bag_screen(content, ()),
        combat_screens.victory_screen(sample_fight),
        combat_screens.defeat_screen(),
        combat_screens.escaped_screen(fled=True),
        shop.inventory_screen(
            content, (shop.OwnedItem("small_healing_potion", 3),), PageState(), gold=120
        ),
        shop.inventory_screen(content, (), PageState(), gold=0),
        shop.shop_screen(
            content,
            sample_stock,
            {item.id: buy_price(content, item) for item in sample_stock},
            PageState(),
            gold=250,
            city_name="Дальний Оплот",
        ),
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[
                ListEntry(key=item.id, text=item.name, detail=item.text[:40])
                for item in content.items[:20]
            ],
            state=PageState(page=1, filters=ListFilters(category="Оружие")),
        ),
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[],
            state=PageState(),
        ),
    ]
    return screens
