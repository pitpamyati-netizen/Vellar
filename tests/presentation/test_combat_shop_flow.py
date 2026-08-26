"""Боевой экран, экономика лавки и отрисовка сумки."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.application.services import battle as battle_service
from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Verdict
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
            actives=(
                # Простой удар, провокация и удар щитом: натиск, оборона, точность.
                "warrior_sekushchiy_roscherk",
                "warrior_provokatsiya",
                "warrior_udar_shchitom",
                None,
                None,
                None,
            ),
            ranks={"warrior_stoykost": 1},
            racial="race_human_second_wind",
        ),
    )


#: Номер героя в бою: сборщик всегда кладёт нападающих первыми.
HERO = 1


def open_fight(
    content: GameContent,
    character: Character,
    enemy: Enemy | None = None,
    *,
    node: int = 3,
) -> battle_service.BattleSession:
    session, _ = battle_service.begin(
        content,
        battle_id="test-battle",
        attackers=[(character, True)],
        enemies=(enemy or make_enemy(),),
        seed=FIGHT_SEED,
        node=node,
    )
    return session


@pytest.fixture
def session(content: GameContent, fighter: Character) -> battle_service.BattleSession:
    return open_fight(content, fighter)


# --- боевой экран -----------------------------------------------------


def test_screen_leads_with_the_situation(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    text = flow.render(content, fighter, session, HERO).text()
    lines = text.split("\n")
    assert lines[0].startswith("Бой. Круг 1.")
    assert lines[1] == "Против вас:"
    assert "Серый волк: здоровье" in lines[2]
    assert "Вы: здоровье" in lines[3]
    assert lines[-1] == "Ваш ход."
    assert "[" not in text, "no pseudo-graphics"


def test_the_panel_holds_the_filled_slots_by_their_own_numbers(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Умение помнит свой номер, а пустое место кнопки не получает."""
    screen = flow.render(content, fighter, session, HERO)
    texts = [row[0].text for row in screen.rows]
    assert texts[0] == "Атака — натиск"
    # У бойца заняты три слота из шести, и это ровно три кнопки, с 1 по 3.
    assert [text.split(".")[0] for text in texts[1:4]] == ["1", "2", "3"]
    assert "расовое" in texts[4]
    assert screen.rows[-1][0].text == "Сумка"


def test_an_empty_slot_gets_no_button(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Кнопка, отвечающая «слот пуст», - это баг (``Claude.md``, правило 9).

    Нажатие на неё стоило игроку целого хода: он не делал ничего, а враги
    отвечали.
    """
    screen = flow.render(content, fighter, session, HERO)
    assert not any("Пустой слот" in row[0].text for row in screen.rows)


def test_a_slot_keeps_its_number_when_an_earlier_one_is_empty(
    content: GameContent, fighter: Character
) -> None:
    """Номер закреплён за умением: третье остаётся третьим и без первого."""
    gapped = replace(
        fighter,
        loadout=replace(
            fighter.loadout,
            actives=(None, None, "warrior_udar_shchitom", None, None, None),
        ),
    )
    session = open_fight(content, gapped, node=1)
    texts = [row[0].text for row in flow.render(content, gapped, session, HERO).rows]
    assert texts[1].startswith("3. Удар щитом")


def test_cooldown_is_written_into_the_button(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    ready = flow.render(content, fighter, session, HERO).rows[3][0].text
    # Готовое умение называет на кнопке цену применения.
    assert "откат 3 хода" in ready
    assert ready.endswith("готово")

    used, _ = flow.advance(content, {HERO: fighter}, session, HERO, ready)
    third = flow.render(content, fighter, used, HERO).rows[3][0].text
    # Потраченное называет, сколько от него осталось, - а это другой вопрос.
    assert third.startswith("3. Удар щитом — оборона,")
    assert third.endswith("ещё 3 хода")


def test_every_button_says_what_it_does(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Панель из шести кнопок, на каждой из которых написано «готово», не говорит игроку ничего.

    Каждый заполненный слот обязан назвать последствие — число урона, лечения, щита
    или правило, которое он применяет, — до того, как его нажали. В этом вся разница
    между умением и лотерейным билетом для того, кто не может увидеть подсказку.
    """
    screen = flow.render(content, fighter, session, HERO)
    cleave, taunt = screen.rows[1][0].text, screen.rows[2][0].text
    assert "урон " in cleave, cleave
    assert "стоит 8" in cleave, cleave
    assert "урон минус 18 процентов" in taunt, taunt
    assert "на 2 хода" in taunt, taunt


def test_a_skill_states_a_number_that_grows_with_the_character(
    content: GameContent, fighter: Character
) -> None:
    """Число на кнопке принадлежит персонажу, а не умению."""

    def damage_on_the_button(level: int) -> int:
        hero = replace(fighter, level=level)
        session = open_fight(content, hero, node=1)
        label = flow.render(content, hero, session, HERO).rows[1][0].text
        # Урон на кнопке — границы, а не одно число: сравнивается верхняя.
        return int(label.split("до ")[1].split(",")[0])

    assert damage_on_the_button(300) > damage_on_the_button(10) > damage_on_the_button(1)


def test_panel_size_follows_the_loadout_not_the_level(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    veteran = replace(fighter, level=300)
    high = open_fight(content, veteran, node=1)
    assert len(flow.render(content, veteran, high, HERO).rows) == len(
        flow.render(content, fighter, session, HERO).rows
    )


# --- действия ---------------------------------------------------------


def test_attack_resolves_a_turn(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    after, notice = flow.advance(content, {HERO: fighter}, session, HERO, "Атака")
    assert notice == ""
    assert after.state.round == 2


def test_typed_commands_work_in_combat(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    assert flow.action_for(content, fighter, session, HERO, "/бой атака") == BattleAction(
        kind=ActionKind.ATTACK
    )
    skill = flow.action_for(content, fighter, session, HERO, "/умение 2")
    assert skill is not None
    assert (skill.kind, skill.slot) == (ActionKind.SKILL, 1)
    assert flow.action_for(content, fighter, session, HERO, "/бой бежать") == BattleAction(
        kind=ActionKind.FLEE
    )


def test_racial_slot_is_addressable_separately(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    action = flow.action_for(content, fighter, session, HERO, "/раса")
    assert action is not None
    assert action.kind is ActionKind.RACIAL


def test_an_empty_slot_answers_without_burning_a_turn(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Слот пуст - значит хода не было: счётчик стоит, враги не отвечают.

    Кнопки такой в панели больше нет, но набранная команда до слота дотянется,
    и старая клавиатура тоже.
    """
    after, _ = flow.advance(content, {HERO: fighter}, session, HERO, "/умение 5")
    assert any(event.kind.value == "empty_slot" for event in after.state.events)
    assert after.state.round == session.state.round
    before, now = session.state.by_id(HERO), after.state.by_id(HERO)
    assert before is not None and now is not None
    assert now.health == before.health


def test_a_skill_on_cooldown_costs_no_turn_either(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Тот же отказ, та же цена: никакой."""
    ready = flow.render(content, fighter, session, HERO).rows[3][0].text
    used, _ = flow.advance(content, {HERO: fighter}, session, HERO, ready)
    again, _ = flow.advance(content, {HERO: fighter}, used, HERO, "/умение 3")
    assert any(event.kind.value == "on_cooldown" for event in again.state.events)
    assert again.state.round == used.state.round


def test_unknown_input_is_refused_without_burning_a_turn(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    after, notice = flow.advance(content, {HERO: fighter}, session, HERO, "Купить зелье")
    assert after.state.round == session.state.round
    assert notice


# --- правила тегов, сказанные вслух ----------------------------------


def test_the_enemy_announces_its_intent_and_the_way_in(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Правила 1.1.1 и 1.1.3: выбор делается против чего-то, и это сказано словами."""
    line = flow.render(content, fighter, session, HERO).text().split("\n")[2]
    assert "Намерение:" in line
    assert "брешь — " in line
    assert any(tag in line for tag in ("натиск", "оборона", "точность"))


def test_the_trace_is_a_spoken_line(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """Правило 1.1.4: ни полосы, ни значка - состояние размена это фраза."""
    opening = flow.render(content, fighter, session, HERO).text()
    assert "След пуст." in opening

    pressed, _ = flow.advance(content, {HERO: fighter}, session, HERO, "Атака — натиск")
    once = flow.render(content, fighter, pressed, HERO).text()
    assert "След: натиск." in once
    assert "повтор даст разгон" in once

    twice, _ = flow.advance(content, {HERO: fighter}, pressed, HERO, "Атака — натиск")
    assert (
        "След: натиск, 2 следа подряд, разгон 25 процентов."
        in flow.render(content, fighter, twice, HERO).text()
    )


def test_every_action_button_names_its_tag(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    texts = [row[0].text for row in flow.render(content, fighter, session, HERO).rows]
    assert texts[0] == "Атака — натиск"
    assert "натиск" in texts[1], "Секущий росчерк is a plain blow"
    assert "оборона" in texts[2], "Провокация pulls the blow onto you"
    assert "оборона" in texts[4], "the racial slot names its tag too"


def test_the_plain_attack_word_still_works(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    """У надписи вырос тег; игрок, набравший прежнее слово, не оказывается заперт."""
    assert flow.action_for(content, fighter, session, HERO, "Атака") == BattleAction(
        kind=ActionKind.ATTACK
    )
    assert flow.action_for(content, fighter, session, HERO, "Атака — натиск") == BattleAction(
        kind=ActionKind.ATTACK
    )


def test_victory_screen_reports_the_rewards(content: GameContent, fighter: Character) -> None:
    weak = open_fight(content, fighter, make_enemy(health=1), node=2)
    state = weak
    for turn in range(10):
        state = replace(state, seed=turn.to_bytes(16, "big"))
        state, _ = flow.advance(content, {HERO: fighter}, state, HERO, "Атака")
        if state.state.is_over:
            break
    assert state.state.verdict_for(HERO) is Verdict.VICTORY
    text = flow.render(content, fighter, state, HERO).text()
    assert text.startswith("Победа.")
    assert "Опыт:" in text


# --- перевод в строку и обратно ---------------------------------------


def test_a_fight_survives_a_round_trip_through_redis(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    fought, _ = flow.advance(content, {HERO: fighter}, session, HERO, "Атака")
    restored = battle_service.deserialise(battle_service.serialise(fought))
    assert restored.state.round == fought.state.round
    assert restored.state.by_id(HERO) == fought.state.by_id(HERO), "след переживает разрыв связи"
    assert restored.state.order == fought.state.order
    assert restored.seed == fought.seed
    assert restored.node == fought.node


def test_effects_and_cooldowns_survive_the_round_trip(
    content: GameContent, fighter: Character, session: battle_service.BattleSession
) -> None:
    pressed = flow.render(content, fighter, session, HERO).rows[2][0].text
    taunted, _ = flow.advance(content, {HERO: fighter}, session, HERO, pressed)
    restored = battle_service.deserialise(battle_service.serialise(taunted))
    before, after = taunted.state.by_id(2), restored.state.by_id(2)
    assert before is not None and after is not None
    assert len(after.effects) == len(before.effects)
    hero_before, hero_after = taunted.state.by_id(HERO), restored.state.by_id(HERO)
    assert hero_before is not None and hero_after is not None
    assert dict(hero_after.cooldowns) == dict(hero_before.cooldowns)


# --- экономика лавки --------------------------------------------------


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
    item = content.item("light_body@6#common")
    plain = buy_price(content, item)
    charming = buy_price(content, item, charisma=20)
    haggler = buy_price(content, item, modifiers={"shop_price_percent": -12.0})
    assert charming < plain
    assert haggler < plain


def test_selling_pays_less_than_buying(content: GameContent) -> None:
    item = content.item("light_body@6#common")
    assert sell_price(content, item) < buy_price(content, item)


def test_rarity_raises_the_price(content: GameContent) -> None:
    common = buy_price(content, content.item("sword@1#common"))
    epic = buy_price(content, content.item("wand@26#legendary"))
    assert epic > common


# --- экраны лавки и сумки ---------------------------------------------


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
