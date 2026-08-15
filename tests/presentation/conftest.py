"""Screen fixtures.

``all_screens`` builds every screen the game can show, so the accessibility tests
can inspect all of them at once. Every new screen must be added here - that is
deliberate: a screen nobody listed is a screen nobody checked.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities import (
    Character,
    GameContent,
    GeneratedLocation,
    QuestLog,
    SkillLoadout,
    StatBlock,
)
from mmorpg.domain.entities.combat import ActionTag, CombatState, Trace
from mmorpg.domain.entities.content import Item, SkillKind
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank
from mmorpg.domain.procgen import generate_location
from mmorpg.domain.rules.combat import start_combat
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.handlers import creation as handlers_creation
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import crafts as craft_screens
from mmorpg.presentation.telegram.screens import creation, play, shop
from mmorpg.presentation.telegram.screens import keeper as keeper_screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
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
        generation=100,
        name="Луга у Заставы",
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
        gold=400,
        health=90,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", None, None, None, None),
            passives=("warrior_toughness", None, None),
            racial="race_human_second_wind",
            ranks=MappingProxyType({"warrior_cleave": 3, "warrior_taunt": 1}),
        ),
        quests=QuestLog(taken=MappingProxyType({"farhold_tallies": 2})),
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
        loot=("wolf_pelt",),
        gold=14,
    )
    return start_combat(content, fighter, (enemy,))


@pytest.fixture(scope="session")
def crowded_fight(content: GameContent, fighter: Character) -> CombatState:
    """The longest the combat screen can get: three enemies, each announcing, and
    a trace with something to say about it."""
    pack = tuple(
        Enemy(
            archetype_id="grey_wolf",
            name=name,
            kind=EnemyKind.BEAST,
            level=4,
            max_health=120,
            damage=9,
            armor=3,
            initiative=9.0 + index,
            loot=("wolf_pelt",),
            gold=14,
        )
        for index, name in enumerate(("Серый волк", "Волчица", "Вожак стаи"))
    )
    state = start_combat(content, fighter, pack)
    return replace(state, turn=7, trace=Trace((ActionTag.GUARD, ActionTag.PRESS)))


@pytest.fixture(scope="session")
def boss_fight(content: GameContent, fighter: Character) -> CombatState:
    """A tier that announces itself: the enemy line has to say how long this will
    take before the player commits a turn to it."""
    boss = Enemy(
        archetype_id="grey_wolf",
        name="Владыка серого волка",
        kind=EnemyKind.BEAST,
        level=12,
        max_health=900,
        damage=22,
        armor=18,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=140,
        rank=EnemyRank.BOSS,
    )
    return start_combat(content, fighter, (boss,))


@pytest.fixture(scope="session")
def craftsman(fighter: Character) -> Character:
    """Somebody who has already put a watch or two into two crafts."""
    return replace(
        fighter,
        crafts=CraftLog(
            MappingProxyType(
                {
                    "mining": CraftProgress(experience=260, gathered_at=1_700_000_000),
                    "smithing": CraftProgress(experience=140),
                }
            )
        ),
    )


@pytest.fixture(scope="session")
def keeper(fighter: Character) -> Character:
    """Somebody whose Telegram id is in ADMIN_IDS: one extra row, nothing else."""
    return replace(fighter, is_admin=True)


@pytest.fixture(scope="session")
def sample_stock(content: GameContent) -> tuple[Item, ...]:
    return roll_assortment(
        content, world_seed="vellar-test", city_id="farhold", rotation=100, character_level=8
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
    crowded_fight: CombatState,
    boss_fight: CombatState,
    sample_stock: tuple[Item, ...],
    craftsman: Character,
    keeper: Character,
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
        handlers_creation.created_screen("Аргус", "Дубно"),
        play.main_menu_screen(content, hero, derived_stats(content, hero)),
        play.main_menu_screen(content, keeper, derived_stats(content, keeper)),
        keeper_screens.keeper_screen(content, keeper, derived_stats(content, keeper)),
        keeper_screens.keeper_screen(
            content, keeper, derived_stats(content, keeper), notice="Выдано 1000 золота."
        ),
        play.world_screen(content, hero, PageState()),
        play.world_screen(content, keeper, PageState()),
        play.city_screen(content, content.city("farhold"), hero),
        play.location_list_screen(content, content.city("farhold"), hero, PageState()),
        play.location_screen(sample_location, sample_location.entrance, cleared=0),
        play.location_screen(
            sample_location, sample_location.exit_node, cleared=0b101, notice="Узел пройден."
        ),
        play.character_screen(content, hero, derived_stats(content, hero)),
        play.character_screen(content, fighter, derived_stats(content, fighter)),
        play.character_screen(
            content,
            replace(fighter, unspent_stat_points=3, unspent_skill_points=1),
            derived_stats(content, fighter),
        ),
        play.stats_screen(content, hero, derived_stats(content, hero)),
        play.stats_screen(
            content,
            replace(fighter, unspent_stat_points=4),
            derived_stats(content, fighter),
        ),
        tutorial_screens.tutorial_screen(hero),
        tutorial_screens.tutorial_screen(replace(hero, tutorial=0b000111)),
        tutorial_screens.tutorial_screen(replace(hero, tutorial=0b111111)),
        play.stub_screen("Арена"),
        skill_screens.skills_screen(content, fighter, PageState()),
        skill_screens.skills_screen(content, hero, PageState(page=2)),
        skill_screens.slots_screen(content, fighter),
        skill_screens.pick_screen(content, fighter, SkillKind.ACTIVE, 2, PageState()),
        skill_screens.pick_screen(content, hero, SkillKind.PASSIVE, 0, PageState()),
        skill_screens.edge_screen(content, fighter, content.skill("warrior_cleave")),
        craft_screens.crafts_screen(content, hero),
        craft_screens.crafts_screen(content, craftsman),
        craft_screens.craft_screen(
            content, craftsman, content.craft("mining"), {}, now=1_700_000_000, cooldown=900
        ),
        craft_screens.craft_screen(
            content,
            hero,
            content.craft("mining"),
            {},
            now=1_700_000_000,
            cooldown=900,
            notice="Собрано: Железный лом.",
        ),
        craft_screens.craft_screen(
            content,
            craftsman,
            content.craft("smithing"),
            {"iron_scrap": 4, "mountain_ore": 3},
            now=1_700_000_000,
            cooldown=900,
        ),
        craft_screens.craft_screen(
            content, hero, content.craft("alchemy"), {}, now=1_700_000_000, cooldown=900
        ),
        quest_screens.journal_screen(content, fighter),
        quest_screens.journal_screen(content, hero),
        quest_screens.board_screen(content, hero, PageState()),
        quest_screens.offer_screen(content, content.quest("farhold_tallies")),
        city_screens.tavern_screen(content, fighter, content.city("farhold")),
        city_screens.mentor_screen(content, fighter, content.city("farhold"), PageState()),
        city_screens.bank_screen(content, fighter, content.city("farhold")),
        city_screens.dungeon_screen(
            content, fighter, content.city("farhold"), level=12, depth=1, total=3
        ),
        combat_screens.combat_screen(content, fighter, sample_fight),
        combat_screens.combat_screen(content, fighter, crowded_fight),
        combat_screens.combat_screen(content, fighter, boss_fight),
        combat_screens.bag_screen(content, (("small_healing_potion", "Малое зелье лечения", 3),)),
        combat_screens.bag_screen(content, ()),
        combat_screens.victory_screen(sample_fight),
        combat_screens.victory_screen(
            sample_fight,
            extra=("Уровень 11. Очков характеристик: 3, очков умений: 1.",),
            rows=((labels.DUNGEON_DEEPER, labels.DUNGEON_LEAVE),),
        ),
        combat_screens.defeat_screen(42),
        combat_screens.escaped_screen(fled=True),
        shop.inventory_screen(
            content,
            (shop.OwnedItem("small_healing_potion", 3), shop.OwnedItem("rusty_sword", 1)),
            PageState(),
            gold=120,
        ),
        shop.inventory_screen(content, (), PageState(), gold=0),
        shop.sell_screen(
            content,
            (shop.OwnedItem("wolf_pelt", 4),),
            {"wolf_pelt": 3},
            PageState(),
            gold=10,
            city_name="Дубно",
        ),
        shop.shop_screen(
            content,
            sample_stock,
            {item.id: buy_price(content, item) for item in sample_stock},
            PageState(),
            gold=250,
            city_name="Дубно",
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
