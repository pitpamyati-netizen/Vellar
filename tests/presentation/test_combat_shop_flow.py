"""Combat screen, shop economy and inventory rendering."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, CombatOutcome
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules.economy import buy_price, roll_assortment, sell_price
from mmorpg.presentation.telegram.flows import combat as flow
from mmorpg.presentation.telegram.screens import shop as shop_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import PageState

WORLD_SEED = "vellar-test"
FIGHT_SEED = b"fight-seed-00001"


def make_enemy(name: str = "Серый волк", health: int = 400) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=4,
        max_health=health,
        damage=9,
        armor=3,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=14,
    )


@pytest.fixture
def fighter(content: GameContent) -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        gold=500,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", "warrior_shield_bash", None, None, None),
            passives=("warrior_toughness", None, None),
            racial="race_human_second_wind",
        ),
    )


@pytest.fixture
def session(content: GameContent, fighter: Character) -> flow.CombatSession:
    return flow.begin(content, fighter, (make_enemy(),), seed=FIGHT_SEED, node=3)


# --- the combat screen ------------------------------------------------


def test_screen_leads_with_the_situation(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    text = flow.render(content, fighter, session).text()
    lines = text.split("\n")
    assert lines[0].startswith("Бой. Ход 1.")
    assert "Серый волк: здоровье" in lines[1]
    assert "Вы: здоровье" in lines[2]
    assert lines[-1] == "Ваш ход."
    assert "[" not in text, "no pseudo-graphics"


def test_panel_has_exactly_six_numbered_slots_plus_racial(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    screen = flow.render(content, fighter, session)
    texts = [row[0].text for row in screen.rows]
    assert texts[0] == "Атака — натиск"
    assert [text.split(".")[0] for text in texts[1:7]] == ["1", "2", "3", "4", "5", "6"]
    assert "расовое" in texts[7]
    assert screen.rows[-1][0].text == "Сумка"


def test_empty_slots_are_rendered_in_place(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    """Positions must not shift, so empty slots keep their buttons (rule 7)."""
    screen = flow.render(content, fighter, session)
    texts = [row[0].text for row in screen.rows]
    assert texts[4] == "4. Пустой слот"
    assert texts[6] == "6. Пустой слот"


def test_cooldown_is_written_into_the_button(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    ready = flow.render(content, fighter, session).rows[3][0].text
    # Ready, the button states the price of using it.
    assert "откат 3 хода" in ready
    assert ready.endswith("готово")

    used, _ = flow.advance(content, fighter, session, ready)
    third = flow.render(content, fighter, used).rows[3][0].text
    # Spent, it states what is left of it - a different question.
    assert third.startswith("3. Удар щитом — точность,")
    assert third.endswith("ещё 3 хода")


def test_every_button_says_what_it_does(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    """A panel of six buttons that all read "готово" tells the player nothing.

    Each filled slot has to name a consequence - a number of damage, of healing,
    of a shield, or the rule it applies - before it is pressed. That is the whole
    difference between a skill and a lottery ticket for a player who cannot see
    a tooltip.
    """
    screen = flow.render(content, fighter, session)
    cleave, taunt = screen.rows[1][0].text, screen.rows[2][0].text
    assert "урон " in cleave, cleave
    assert "стоит 10" in cleave, cleave
    assert "урон минус 20 процентов" in taunt, taunt
    assert "на 2 хода" in taunt, taunt


def test_a_skill_states_a_number_that_grows_with_the_character(
    content: GameContent, fighter: Character
) -> None:
    """The number on the button is the character's, not the skill's."""

    def damage_on_the_button(level: int) -> int:
        hero = replace(fighter, level=level)
        session = flow.begin(content, hero, (make_enemy(),), seed=FIGHT_SEED, node=1)
        label = flow.render(content, hero, session).rows[1][0].text
        return int(label.split("урон ")[1].split(",")[0])

    assert damage_on_the_button(300) > damage_on_the_button(10) > damage_on_the_button(1)


def test_panel_size_never_changes_with_level(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    veteran = replace(fighter, level=300)
    high = flow.begin(content, veteran, (make_enemy(),), seed=FIGHT_SEED, node=1)
    assert len(flow.render(content, veteran, high).rows) == len(
        flow.render(content, fighter, session).rows
    )


# --- actions ----------------------------------------------------------


def test_attack_resolves_a_turn(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    after, notice = flow.advance(content, fighter, session, "Атака")
    assert notice == ""
    assert after.state.turn == 2


def test_typed_commands_work_in_combat(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    assert flow.action_for(content, fighter, session, "/бой атака") == flow.CombatAction(
        kind=ActionKind.ATTACK
    )
    skill = flow.action_for(content, fighter, session, "/умение 2")
    assert skill is not None
    assert (skill.kind, skill.slot) == (ActionKind.SKILL, 1)
    assert flow.action_for(content, fighter, session, "/бой бежать") == flow.CombatAction(
        kind=ActionKind.FLEE
    )


def test_racial_slot_is_addressable_separately(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    action = flow.action_for(content, fighter, session, "/раса")
    assert action is not None
    assert action.kind is ActionKind.RACIAL


def test_pressing_an_empty_slot_answers(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    after, _ = flow.advance(content, fighter, session, "5. Пустой слот")
    assert any(event.kind.value == "empty_slot" for event in after.state.events)


def test_unknown_input_is_refused_without_burning_a_turn(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    after, notice = flow.advance(content, fighter, session, "Купить зелье")
    assert after.state.turn == session.state.turn
    assert notice


# --- the tag rules, spoken -------------------------------------------


def test_the_enemy_announces_its_intent_and_the_way_in(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    """Rule 1.1.1 and 1.1.3: the choice is made against something, in words."""
    line = flow.render(content, fighter, session).text().split("\n")[1]
    assert "Намерение:" in line
    assert "брешь — " in line
    assert any(tag in line for tag in ("натиск", "оборона", "точность"))


def test_the_trace_is_a_spoken_line(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    """Rule 1.1.4: no bar, no icon - the state of the exchange is a sentence."""
    opening = flow.render(content, fighter, session).text()
    assert "След пуст." in opening

    pressed, _ = flow.advance(content, fighter, session, "Атака — натиск")
    once = flow.render(content, fighter, pressed).text()
    assert "След: натиск." in once
    assert "повтор даст разгон" in once

    twice, _ = flow.advance(content, fighter, pressed, "Атака — натиск")
    assert (
        "След: натиск, 2 следа подряд, разгон 25 процентов."
        in flow.render(content, fighter, twice).text()
    )


def test_every_action_button_names_its_tag(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    texts = [row[0].text for row in flow.render(content, fighter, session).rows]
    assert texts[0] == "Атака — натиск"
    assert "натиск" in texts[1], "Рассечение is a plain blow"
    assert "оборона" in texts[2], "Провокация pulls the blow onto you"
    assert "оборона" in texts[7], "the racial slot names its tag too"


def test_the_plain_attack_word_still_works(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    """The label grew a tag; a player who typed the old word is not locked out."""
    assert flow.action_for(content, fighter, session, "Атака") == flow.CombatAction(
        kind=ActionKind.ATTACK
    )
    assert flow.action_for(content, fighter, session, "Атака — натиск") == flow.CombatAction(
        kind=ActionKind.ATTACK
    )


def test_victory_screen_reports_the_rewards(content: GameContent, fighter: Character) -> None:
    weak = flow.begin(content, fighter, (make_enemy(health=1),), seed=FIGHT_SEED, node=2)
    state = weak
    for turn in range(10):
        state = replace(state, seed=turn.to_bytes(16, "big"))
        state, _ = flow.advance(content, fighter, state, "Атака")
        if state.state.is_over:
            break
    assert state.state.outcome is CombatOutcome.VICTORY
    text = flow.render(content, fighter, state).text()
    assert text.startswith("Победа.")
    assert "Опыт:" in text


# --- serialisation ----------------------------------------------------


def test_a_fight_survives_a_round_trip_through_redis(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    fought, _ = flow.advance(content, fighter, session, "Атака")
    restored = flow.deserialise(flow.serialise(fought))
    assert restored.state.turn == fought.state.turn
    assert restored.state.trace == fought.state.trace, "the trace outlives a reconnect"
    assert restored.state.player.health == fought.state.player.health
    assert restored.state.enemies[0].health == fought.state.enemies[0].health
    assert restored.seed == fought.seed
    assert restored.node == fought.node


def test_effects_and_cooldowns_survive_the_round_trip(
    content: GameContent, fighter: Character, session: flow.CombatSession
) -> None:
    taunted, _ = flow.advance(content, fighter, session, "2. Провокация — готово, 15 ресурса")
    restored = flow.deserialise(flow.serialise(taunted))
    assert len(restored.state.enemies[0].effects) == len(taunted.state.enemies[0].effects)
    assert dict(restored.state.player.cooldowns) == dict(taunted.state.player.cooldowns)


# --- shop economy -----------------------------------------------------


def test_assortment_is_deterministic_per_rotation(content: GameContent) -> None:
    first = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=10, character_level=5
    )
    second = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=10, character_level=5
    )
    assert first == second


def test_assortment_rotates_with_the_shelf(content: GameContent) -> None:
    first = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=10, character_level=5
    )
    later = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=11, character_level=5
    )
    assert first != later


def test_assortment_differs_between_cities(content: GameContent) -> None:
    farhold = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=10, character_level=5
    )
    harbor = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="dusk_harbor", rotation=10, character_level=5
    )
    assert farhold != harbor


def test_assortment_matches_the_player_level(content: GameContent) -> None:
    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=3, character_level=20
    )
    assert stock
    assert all(14 <= item.level <= 24 for item in stock)


def test_reputation_widens_the_shelf(content: GameContent) -> None:
    plain = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=3, character_level=10
    )
    trusted = roll_assortment(
        content,
        world_seed=WORLD_SEED,
        city_id="farhold",
        rotation=3,
        character_level=10,
        reputation=400,
    )
    assert len(trusted) >= len(plain)


def test_charisma_and_traits_lower_the_price(content: GameContent) -> None:
    item = content.item("leather_armor")
    plain = buy_price(content, item)
    charming = buy_price(content, item, charisma=20)
    haggler = buy_price(content, item, modifiers={"shop_price_percent": -12.0})
    assert charming < plain
    assert haggler < plain


def test_selling_pays_less_than_buying(content: GameContent) -> None:
    item = content.item("leather_armor")
    assert sell_price(content, item) < buy_price(content, item)


def test_rarity_raises_the_price(content: GameContent) -> None:
    common = buy_price(content, content.item("rusty_sword"))
    epic = buy_price(content, content.item("stormcaller_rod"))
    assert epic > common


# --- shop and inventory screens ---------------------------------------


def test_shop_screen_states_price_and_affordability(content: GameContent) -> None:
    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=3, character_level=5
    )
    prices = {item.id: buy_price(content, item) for item in stock}
    screen = shop_screens.shop_screen(
        content, stock, prices, PageState(), gold=50, city_name="Дубно"
    )
    assert screen.id is ScreenId.SHOP
    text = screen.text()
    assert "Лавка города Дубно" in text
    assert "золота" in text
    assert "хватает золота" in text or "не хватает золота" in text


def test_shop_buttons_map_back_to_items(content: GameContent) -> None:
    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", rotation=3, character_level=5
    )
    prices = {item.id: buy_price(content, item) for item in stock}
    first = stock[0]
    pressed = shop_screens.buy_label(first, prices[first.id]).text
    assert shop_screens.item_from_button(content, pressed, stock) == first


def test_inventory_screen_counts_stacks(content: GameContent) -> None:
    owned = [
        shop_screens.OwnedItem("small_healing_potion", 3),
        shop_screens.OwnedItem("wolf_pelt", 12),
    ]
    screen = shop_screens.inventory_screen(content, owned, PageState(), gold=120)
    text = screen.text()
    assert "Малое зелье лечения, штук 3" in text
    assert "120 золотых" in text


def test_inventory_filters_narrow_the_list(content: GameContent) -> None:
    from mmorpg.presentation.telegram.screens.paginated import ListFilters

    owned = [
        shop_screens.OwnedItem("small_healing_potion", 3),
        shop_screens.OwnedItem("wolf_pelt", 12),
    ]
    filtered = shop_screens.inventory_screen(
        content,
        owned,
        PageState(filters=ListFilters(query="зелье")),
        gold=0,
    )
    assert "Малое зелье лечения" in filtered.text()
    assert "Звериная шкура" not in filtered.text()


def test_empty_inventory_says_so(content: GameContent) -> None:
    screen = shop_screens.inventory_screen(content, [], PageState(), gold=0)
    assert "В инвентаре пусто." in screen.text()
