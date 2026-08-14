"""Gear, the skup, the mentor and the descent, at the level of the pure flow.

The flow decides and hands the handler a :class:`PendingWrite`; nothing here
touches a repository. What is checked is that every button leads somewhere, that
a refusal explains itself, and that nothing is ever lost on the way - an item
taken off goes back into the bag, an item bought is paid for exactly once.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.content import SkillKind
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.economy import mentor_price, sell_price
from mmorpg.presentation.telegram.flows.play import Goods, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.shop import OwnedItem

WORLD_SEED = "vellar-test"
CYCLE = 100


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=12,
        gold=900,
        unspent_skill_points=3,
        loadout=SkillLoadout(
            actives=("warrior_cleave", None, None, None, None, None),
            racial="race_human_second_wind",
            ranks={"warrior_cleave": 2},
        ),
    )


def step(
    content: GameContent,
    hero: Character,
    state: PlayState,
    *messages: str,
    goods: Goods | None = None,
) -> PlayState:
    current = state
    for message in messages:
        current = advance(
            content,
            hero,
            current,
            message,
            cycle=CYCLE,
            world_seed=WORLD_SEED,
            goods=goods,
        )
    return current


@pytest.fixture
def in_city(content: GameContent, hero: Character) -> PlayState:
    return step(content, hero, begin(hero), "Мир", "Дальний Оплот")


# --- gear -------------------------------------------------------------


def test_putting_on_a_thing_takes_it_out_of_the_bag(content: GameContent, hero: Character) -> None:
    goods = Goods(gold=hero.gold, owned=(OwnedItem("chain_shirt", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    worn = step(content, hero, inventory, "Кольчужная рубаха, штук 1 — надеть", goods=goods)

    assert worn.pending.character is not None
    assert worn.pending.character.equipment.item_in("body") == "chain_shirt"
    assert worn.pending.items == (("chain_shirt", -1),)
    assert "надет" in worn.notice


def test_what_it_replaces_goes_back_into_the_bag(content: GameContent, hero: Character) -> None:
    dressed = replace(hero, equipment=hero.equipment.equip("body", "leather_armor"))
    goods = Goods(gold=dressed.gold, owned=(OwnedItem("chain_shirt", 1),))
    inventory = step(content, dressed, begin(dressed), "Инвентарь", goods=goods)
    worn = step(content, dressed, inventory, "Кольчужная рубаха, штук 1 — надеть", goods=goods)

    assert worn.pending.items == (("chain_shirt", -1), ("leather_armor", 1))


def test_taking_a_thing_off_from_the_character_screen(
    content: GameContent, hero: Character
) -> None:
    dressed = replace(hero, equipment=hero.equipment.equip("weapon", "rusty_sword"))
    sheet = step(content, dressed, begin(dressed), "Персонаж")
    assert "Ржавый меч" in render(content, dressed, sheet, world_seed=WORLD_SEED).text()

    stripped = step(content, dressed, sheet, play_screens.unequip_label("Оружие").text)
    assert stripped.pending.character is not None
    assert stripped.pending.character.equipment.item_in("weapon") is None
    assert stripped.pending.items == (("rusty_sword", 1),)


def test_a_level_point_is_spent_from_the_character_screen(
    content: GameContent, hero: Character
) -> None:
    """A point with nowhere to go would be a level that changed nothing."""
    fresh = replace(hero, unspent_stat_points=2)
    sheet = step(content, fresh, begin(fresh), "Персонаж")
    assert "Вложить: Сила" in [
        item.text
        for row in render(content, fresh, sheet, world_seed=WORLD_SEED).rows
        for item in row
    ]

    stronger = step(content, fresh, sheet, "Вложить: Сила")
    assert stronger.pending.character is not None
    assert stronger.pending.character.allocated.STR == fresh.allocated.STR + 1
    assert stronger.pending.character.unspent_stat_points == 1

    spent = replace(fresh, unspent_stat_points=0)
    refused = step(content, spent, sheet, "Вложить: Сила")
    assert refused.pending.empty


def test_a_potion_is_drunk_from_the_bag_and_only_when_it_helps(
    content: GameContent, hero: Character
) -> None:
    hurt = replace(hero, health=5)
    goods = Goods(gold=hurt.gold, owned=(OwnedItem("small_healing_potion", 2),))
    inventory = step(content, hurt, begin(hurt), "Инвентарь", goods=goods)
    drunk = step(
        content, hurt, inventory, "Малое зелье лечения, штук 2 — использовать", goods=goods
    )
    assert drunk.pending.items == (("small_healing_potion", -1),)
    assert "восстановлено" in drunk.notice

    whole = step(
        content, hero, inventory, "Малое зелье лечения, штук 2 — использовать", goods=goods
    )
    assert whole.pending.empty
    assert "ничего не даст" in whole.notice


def test_selling_pays_and_takes_exactly_one(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    goods = Goods(gold=hero.gold, owned=(OwnedItem("wolf_pelt", 4),))
    skup = step(content, hero, in_city, "Лавка", "Продать вещи", goods=goods)
    assert skup.screen is ScreenId.SELL

    price = sell_price(content, content.item("wolf_pelt"))
    sold = step(content, hero, skup, f"Волчья шкура — {price} золота, продать", goods=goods)
    assert sold.pending.items == (("wolf_pelt", -1),)
    assert sold.pending.character is not None
    assert sold.pending.character.gold == hero.gold + price


# --- skills -----------------------------------------------------------


def test_learning_walks_from_the_menu_to_a_slot(content: GameContent, hero: Character) -> None:
    skills = step(content, hero, begin(hero), "Умения")
    assert skills.screen is ScreenId.SKILLS

    fresh = next(
        skill
        for skill in skill_rules.teachable(content, hero)
        if skill.is_active and not skill_rules.is_known(hero, skill.code)
    )
    learned = step(content, hero, skills, skill_screens.skill_entry_text(content, hero, fresh))
    assert learned.pending.character is not None
    assert skill_rules.is_known(learned.pending.character, fresh.code)
    assert "Положите его в слот" in learned.notice


def test_a_point_you_do_not_have_is_refused_in_words(content: GameContent, hero: Character) -> None:
    broke = replace(hero, unspent_skill_points=0)
    skills = step(content, broke, begin(broke), "Умения")
    fresh = next(
        skill
        for skill in skill_rules.teachable(content, broke)
        if not skill_rules.is_known(broke, skill.code)
    )
    refused = step(content, broke, skills, skill_screens.skill_entry_text(content, broke, fresh))
    assert refused.pending.empty
    assert "Очков умений нет" in refused.notice


def test_the_third_rank_asks_for_an_edge_before_anything_else(
    content: GameContent, hero: Character
) -> None:
    skill = content.skill("warrior_cleave")
    ready = replace(
        hero, loadout=replace(hero.loadout, ranks={skill.code: content.rules.edge_rank})
    )
    skills = step(content, ready, begin(ready), "Умения")
    asked = step(content, ready, skills, skill_screens.skill_entry_text(content, ready, skill))
    assert asked.screen is ScreenId.SKILL_EDGE

    chosen = step(content, ready, asked, skill_screens.edge_label(skill.edges[0].name).text)
    assert chosen.pending.character is not None
    assert chosen.pending.character.loadout.edge_of(skill.code) == skill.edges[0].code
    assert chosen.screen is ScreenId.SKILLS


def test_a_slot_is_filled_and_emptied_from_the_panel(content: GameContent, hero: Character) -> None:
    known = replace(
        hero,
        loadout=replace(hero.loadout, ranks={"warrior_cleave": 2, "warrior_taunt": 1}),
    )
    slots = step(content, known, begin(known), "Умения", "Слоты умений")
    assert slots.screen is ScreenId.SKILL_SLOTS

    picking = step(
        content,
        known,
        slots,
        skill_screens.slot_label(content, known, SkillKind.ACTIVE, 1).text,
    )
    assert picking.screen is ScreenId.SKILL_PICK

    filled = step(content, known, picking, "Провокация — ранг 1")
    assert filled.pending.character is not None
    assert filled.pending.character.loadout.actives[1] == "warrior_taunt"

    emptied = step(content, filled.pending.character, picking, "Освободить слот")
    assert emptied.pending.character is not None
    assert emptied.pending.character.loadout.actives[1] is None


# --- the city ---------------------------------------------------------


def test_the_mentor_takes_gold_and_hands_the_points_back(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    student = replace(hero, loadout=replace(hero.loadout, ranks={"warrior_cleave": 2}), gold=900)
    mentor = step(content, student, in_city, "Наставник")
    assert mentor.screen is ScreenId.MENTOR

    forgotten = step(content, student, mentor, city_screens.forget_label("Рассечение", 2).text)
    assert forgotten.pending.character is not None
    assert forgotten.pending.character.unspent_skill_points == student.unspent_skill_points + 2
    assert forgotten.pending.character.gold == student.gold - mentor_price(student.level)


def test_the_mentor_refuses_a_customer_who_cannot_pay(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    broke = replace(hero, gold=0, loadout=replace(hero.loadout, ranks={"warrior_cleave": 2}))
    mentor = step(content, broke, in_city, "Наставник")
    refused = step(content, broke, mentor, city_screens.forget_label("Рассечение", 2).text)
    assert refused.pending.empty
    assert "Наставник берёт" in refused.notice


def test_the_strongbox_moves_gold_both_ways(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    bank = step(content, hero, in_city, "Банк")
    stored = step(content, hero, bank, "Положить 250")
    assert stored.pending.character is not None
    assert stored.pending.character.bank_gold == 250
    assert stored.pending.character.gold == hero.gold - 250

    taken = step(content, stored.pending.character, bank, "Забрать 250")
    assert taken.pending.character is not None
    assert taken.pending.character.bank_gold == 0
    assert taken.pending.character.gold == hero.gold


def test_the_strongbox_refuses_what_is_not_there(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    bank = step(content, hero, in_city, "Банк")
    refused = step(content, hero, bank, "Забрать 1000")
    assert refused.pending.empty
    assert "В ячейке только" in refused.notice


def test_the_descent_asks_for_a_fight(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    dungeon = step(content, hero, in_city, "Данжи")
    assert dungeon.screen is ScreenId.DUNGEON
    assert "Три схватки подряд" in render(content, hero, dungeon, world_seed=WORLD_SEED).text()

    down = step(content, hero, dungeon, "Спуститься")
    assert down.fight == "dungeon"
    assert down.descent.depth == 1
    assert down.descent.city_id == "farhold"


def test_the_journal_lists_what_is_taken(content: GameContent, hero: Character) -> None:
    from mmorpg.domain.rules import quests as quest_rules

    took = quest_rules.take(content, hero, content.quest("farhold_tallies"))
    journal = step(content, took, begin(took), "Подряды")
    assert journal.screen is ScreenId.QUESTS
    text = render(content, took, journal, world_seed=WORLD_SEED).text()
    assert "Столбы на Тракте: 0 из 3" in text


def test_walking_away_from_a_contract_keeps_it_on_the_board(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    quest = content.quest("farhold_tallies")
    board = step(content, hero, in_city, "Таверна", "Доска подрядов")
    offer = step(
        content, hero, board, f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}"
    )
    assert offer.screen is ScreenId.QUEST_OFFER

    asked = step(content, hero, offer, "Спросить, кто платит")
    assert "Платит" in asked.notice
    assert asked.screen is ScreenId.QUEST_OFFER

    left = step(content, hero, offer, "Уйти")
    assert left.screen is ScreenId.QUEST_BOARD
    assert left.pending.empty
    assert quest_still_offered(content, hero)


def quest_still_offered(content: GameContent, hero: Character) -> bool:
    from mmorpg.domain.rules import quests as quest_rules

    return any(quest.id == "farhold_tallies" for quest in quest_rules.available(content, hero))
