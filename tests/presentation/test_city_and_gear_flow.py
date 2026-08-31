"""Снаряжение, скупка, наставник и спуск на уровне чистой ветки.

Ветка решает и передаёт хендлеру :class:`PendingWrite`; здесь не трогают ни
одного хранилища. Проверяется, что каждая кнопка куда-то ведёт, что отказ
объясняет себя и что по дороге ничего не теряется: снятая вещь возвращается в
сумку, а купленная оплачивается ровно один раз.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.combat import blow_range
from mmorpg.domain.rules.economy import mentor_price, sell_price
from mmorpg.domain.rules.stats import DerivedStats, derived_stats
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    Goods,
    PlayState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.city import mentor_screen
from mmorpg.presentation.telegram.screens.format import number
from mmorpg.presentation.telegram.screens.paginated import PageState
from mmorpg.presentation.telegram.screens.shop import OwnedItem

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)


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
            actives=("warrior_rassechenie", None, None, None, None, None),
            racial="race_human_second_wind",
            ranks={"warrior_rassechenie": 2},
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
            clock=CLOCK,
            world_seed=WORLD_SEED,
            goods=goods,
        )
    return current


@pytest.fixture
def in_city(content: GameContent, hero: Character) -> PlayState:
    return step(content, hero, begin(hero), "Мир")


# --- gear -------------------------------------------------------------


def test_a_thing_in_the_bag_opens_its_card_first(content: GameContent, hero: Character) -> None:
    """Нажатие на вещь больше не действует ею: сперва карточка, потом кнопка."""
    dressed = replace(hero, equipment=hero.equipment.equip("body", "light_body@6#common"))
    goods = Goods(gold=dressed.gold, owned=(OwnedItem("medium_body@6#uncommon", 1),))
    inventory = step(content, dressed, begin(dressed), "Инвентарь", goods=goods)
    card = step(
        content, dressed, inventory, "Простая кольчуга доброй ковки, штук 1 — надеть", goods=goods
    )

    assert card.screen is ScreenId.ITEM
    assert card.pending.empty
    text = render(content, dressed, card, world_seed=WORLD_SEED, goods=goods).text()
    assert "Простая кольчуга доброй ковки" in text
    # И вот ради чего экран заведён: сравнение с тем, что уже надето.
    assert "Сейчас надето: Простая куртка" in text


def test_putting_on_a_thing_takes_it_out_of_the_bag(content: GameContent, hero: Character) -> None:
    goods = Goods(gold=hero.gold, owned=(OwnedItem("medium_body@6#uncommon", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    worn = step(
        content,
        hero,
        inventory,
        "Простая кольчуга доброй ковки, штук 1 — надеть",
        "Надеть",
        goods=goods,
    )

    assert worn.pending.character is not None
    assert worn.pending.character.equipment.item_in("body") == "medium_body@6#uncommon"
    assert worn.pending.items == (("medium_body@6#uncommon", -1),)
    assert "надет" in worn.notice


def test_what_it_replaces_goes_back_into_the_bag(content: GameContent, hero: Character) -> None:
    dressed = replace(hero, equipment=hero.equipment.equip("body", "light_body@6#common"))
    goods = Goods(gold=dressed.gold, owned=(OwnedItem("medium_body@6#uncommon", 1),))
    inventory = step(content, dressed, begin(dressed), "Инвентарь", goods=goods)
    worn = step(
        content,
        dressed,
        inventory,
        "Простая кольчуга доброй ковки, штук 1 — надеть",
        "Надеть",
        goods=goods,
    )

    assert worn.pending.items == (("medium_body@6#uncommon", -1), ("light_body@6#common", 1))


def test_a_card_names_the_kind_and_the_armour_it_holds(
    content: GameContent, hero: Character
) -> None:
    """Броня называется числом: процент от выносливости и был тем, что ничего не менял."""
    goods = Goods(gold=hero.gold, owned=(OwnedItem("medium_body@6#uncommon", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    card = step(
        content, hero, inventory, "Простая кольчуга доброй ковки, штук 1 — надеть", goods=goods
    )
    text = render(content, hero, card, world_seed=WORLD_SEED, goods=goods).text()

    assert "Род доспеха: средний доспех" in text
    assert "Броня: " in text


def test_a_thing_your_class_never_wears_warns_but_never_refuses(
    content: GameContent, hero: Character
) -> None:
    """Чужое не запрещено, оно дорого: цена сказана до нажатия, кнопка на месте."""
    rogue = replace(hero, class_id="rogue", loadout=SkillLoadout())
    goods = Goods(gold=rogue.gold, owned=(OwnedItem("medium_body@6#uncommon", 1),))
    inventory = step(content, rogue, begin(rogue), "Инвентарь", goods=goods)
    card = step(
        content, rogue, inventory, "Простая кольчуга доброй ковки, штук 1 — надеть", goods=goods
    )
    screen = render(content, rogue, card, world_seed=WORLD_SEED, goods=goods)

    assert "разбойник в таком не обучен" in screen.text()
    assert any("Надеть" in button.text for row in screen.rows for button in row)


def test_wearing_a_thing_your_class_never_wears_costs_accuracy_and_speed(
    content: GameContent, hero: Character
) -> None:
    """Штраф — не надпись: он виден на экране характеристик тем же числом."""
    rogue = replace(hero, class_id="rogue", loadout=SkillLoadout())
    bare = derived_stats(content, rogue)
    dressed = derived_stats(
        content, replace(rogue, equipment=rogue.equipment.equip("body", "medium_body@6#uncommon"))
    )

    assert dressed.accuracy < bare.accuracy
    assert dressed.initiative < bare.initiative


def test_a_weapon_card_states_the_blow_as_a_range(content: GameContent, hero: Character) -> None:
    """«от 6 до 114» — это то, что случится; одно число обещало бы точность."""
    goods = Goods(gold=hero.gold, owned=(OwnedItem("sword@26#rare", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    card = step(content, hero, inventory, "Добрый меч редкой работы, штук 1 — надеть", goods=goods)
    text = render(content, hero, card, world_seed=WORLD_SEED, goods=goods).text()

    sword = content.item("sword@26#rare")
    assert sword.damage is not None
    assert f"Урон: {sword.damage.spoken()}." in text
    assert "Род оружия: меч." in text
    # Редкая вещь даёт две характеристики, и обе названы числом.
    assert "Прибавка от редкости:" in text


def test_a_common_thing_gives_no_stats_and_says_nothing_about_them(
    content: GameContent, hero: Character
) -> None:
    goods = Goods(gold=hero.gold, owned=(OwnedItem("sword@26#common", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    card = step(content, hero, inventory, "Добрый меч, штук 1 — надеть", goods=goods)
    text = render(content, hero, card, world_seed=WORLD_SEED, goods=goods).text()

    assert "Прибавка от редкости" not in text


def test_a_relic_says_that_it_grows_with_you(content: GameContent, hero: Character) -> None:
    goods = Goods(gold=hero.gold, owned=(OwnedItem("charm@14#relic", 1),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    card = step(
        content, hero, inventory, "Крепкий оберег давних времён, штук 1 — надеть", goods=goods
    )
    text = render(content, hero, card, world_seed=WORLD_SEED, goods=goods).text()

    assert "растут вместе с вашим уровнем" in text


def test_the_character_sheet_names_the_blow_and_what_makes_it(
    content: GameContent, hero: Character
) -> None:
    armed = replace(hero, equipment=hero.equipment.equip("weapon", "sword@14#common"))
    low, high = blow_range(content, armed)
    text = play_screens.character_screen(content, armed, derived_stats(content, armed)).text()

    assert f"Удар: от {low} до {high}, крепкий меч." in text
    bare = play_screens.character_screen(content, hero, derived_stats(content, hero)).text()
    assert "голыми руками" in bare


def test_the_character_sheet_names_every_number_the_engine_counts(
    content: GameContent, hero: Character
) -> None:
    """Число, которое движок считает, а экран молчит, - половина обещания.

    Карточка называла шесть значений из девяти: сила крита, восстановление
    ресурса и лечение по ходам считались в каждом бою и не были сказаны нигде.
    Проверяется весь ``DerivedStats``, чтобы новое производное значение нельзя
    было добавить молча.
    """
    regenerating = replace(hero, trait_ids=("steady_breath",))
    stats = derived_stats(content, regenerating)
    text = play_screens.character_screen(content, regenerating, stats).text()

    spoken = {
        "max_health": str(stats.max_health),
        "max_resource": str(stats.max_resource),
        "resource_name": stats.resource_name,
        "armor": str(stats.armor),
        "accuracy": number(stats.accuracy),
        "dodge": number(stats.dodge),
        "crit_chance": number(stats.crit_chance),
        "crit_damage": number(stats.crit_damage),
        "initiative": number(stats.initiative),
        "resource_regen": number(stats.resource_regen),
        "health_regen_percent": number(stats.health_regen_percent),
    }
    assert stats.health_regen_percent, "«Ровное дыхание» и есть лечение по ходам"
    for name, value in spoken.items():
        assert value in text, f"карточка молчит о {name}"

    # Новое производное значение не добавится молча: список полей и список
    # сказанного обязаны совпадать.
    assert {field.name for field in fields(DerivedStats)} == set(spoken)


def test_raw_stock_says_what_it_is_instead_of_doing_nothing(
    content: GameContent, hero: Character
) -> None:
    """Звериная шкура раньше в ответ на нажатие только читала своё описание."""
    goods = Goods(gold=hero.gold, owned=(OwnedItem("wolf_pelt", 3),))
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    card = step(content, hero, inventory, "Звериная шкура, штук 3 — сырьё", goods=goods)

    assert card.screen is ScreenId.ITEM
    text = render(content, hero, card, world_seed=WORLD_SEED, goods=goods).text()
    assert "В сумке: 3." in text
    assert "Скупщик даст" in text
    assert "Сырьё: идёт в дело" in text


def test_taking_a_thing_off_from_the_character_screen(
    content: GameContent, hero: Character
) -> None:
    dressed = replace(hero, equipment=hero.equipment.equip("weapon", "sword@1#common"))
    sheet = step(content, dressed, begin(dressed), "Персонаж")
    assert "Ветхий меч" in render(content, dressed, sheet, world_seed=WORLD_SEED).text()

    stripped = step(content, dressed, sheet, play_screens.unequip_label("Оружие").text)
    assert stripped.pending.character is not None
    assert stripped.pending.character.equipment.item_in("weapon") is None
    assert stripped.pending.items == (("sword@1#common", 1),)


def test_a_level_point_is_spent_from_the_stats_screen(
    content: GameContent, hero: Character
) -> None:
    """Очко, которое некуда деть, было бы уровнем, ничего не изменившим.

    Тратят его на «Характеристиках», рядом со строкой, которая говорит, что это очко
    на самом деле покупает, — а экран персонажа только показывает туда дорогу.
    """
    fresh = replace(hero, unspent_stat_points=2)
    sheet = step(content, fresh, begin(fresh), "Персонаж")
    assert "Характеристики" in [
        item.text
        for row in render(content, fresh, sheet, world_seed=WORLD_SEED).rows
        for item in row
    ]

    sheet = step(content, fresh, sheet, "Характеристики")
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
        content,
        hurt,
        inventory,
        "Малое зелье лечения, штук 2 — использовать",
        "Использовать",
        goods=goods,
    )
    assert drunk.pending.items == (("small_healing_potion", -1),)
    assert "восстановлено" in drunk.notice

    whole = step(
        content,
        hero,
        inventory,
        "Малое зелье лечения, штук 2 — использовать",
        "Использовать",
        goods=goods,
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
    sold = step(content, hero, skup, f"Звериная шкура — {price} золота, продать", goods=goods)
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
    # Отказ называет цену и остаток, а не отсылает к общему правилу: цена ранга
    # теперь у каждого своя (ADR 0024).
    assert "а есть 0" in refused.notice
    assert "Очки дают за уровень" in refused.notice


def test_the_third_rank_asks_for_an_edge_before_anything_else(
    content: GameContent, hero: Character
) -> None:
    skill = content.skill("warrior_rassechenie")
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


def test_a_skill_waiting_for_its_edge_says_so_on_its_own_button(
    content: GameContent, hero: Character
) -> None:
    """Кнопка обязана обещать то, что нажатие сделает.

    На ранге грани нажатие уходит на выбор грани, а не на ранг. Пока кнопка
    говорила «следующий за одно очко», игрок нажимал, выбирал грань, возвращался и
    видел тот же ранг и ту же надпись - то есть заевший экран.
    """
    skill = content.skill("warrior_rassechenie")
    ready = replace(
        hero, loadout=replace(hero.loadout, ranks={skill.code: content.rules.edge_rank})
    )

    assert "сначала выберите грань" in skill_screens.skill_entry_text(content, ready, skill)

    chosen = replace(ready, loadout=replace(ready.loadout, edges={skill.code: skill.edges[0].code}))
    assert "следующий за" in skill_screens.skill_entry_text(content, chosen, skill)


def test_choosing_an_edge_says_the_rank_grows_again(content: GameContent, hero: Character) -> None:
    skill = content.skill("warrior_rassechenie")
    ready = replace(
        hero, loadout=replace(hero.loadout, ranks={skill.code: content.rules.edge_rank})
    )
    skills = step(content, ready, begin(ready), "Умения")
    asked = step(content, ready, skills, skill_screens.skill_entry_text(content, ready, skill))

    chosen = step(content, ready, asked, skill_screens.edge_label(skill.edges[0].name).text)

    assert "Ранг снова растёт" in chosen.notice


def test_a_slot_is_filled_and_emptied_from_the_panel(content: GameContent, hero: Character) -> None:
    known = replace(
        hero,
        loadout=replace(hero.loadout, ranks={"warrior_rassechenie": 2, "warrior_provokatsiya": 1}),
    )
    slots = step(content, known, begin(known), "Умения", "Слоты умений")
    assert slots.screen is ScreenId.SKILL_SLOTS

    picking = step(
        content,
        known,
        slots,
        skill_screens.slot_label(content, known, 1).text,
    )
    assert picking.screen is ScreenId.SKILL_PICK

    filled = step(content, known, picking, "Провокация — ранг 1")
    assert filled.pending.character is not None
    assert filled.pending.character.loadout.actives[1] == "warrior_provokatsiya"

    emptied = step(content, filled.pending.character, picking, "Освободить слот")
    assert emptied.pending.character is not None
    assert emptied.pending.character.loadout.actives[1] is None


# --- город ------------------------------------------------------------


def test_the_mentor_takes_gold_and_hands_the_points_back(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    student = replace(
        hero, loadout=replace(hero.loadout, ranks={"warrior_rassechenie": 2}), gold=900
    )
    mentor = step(content, student, in_city, "Наставник")
    assert mentor.screen is ScreenId.MENTOR

    spent = skill_rules.spent_on(content, student, "warrior_rassechenie")
    forgotten = step(content, student, mentor, city_screens.forget_label("Рассечение", 2).text)
    assert forgotten.pending.character is not None
    assert forgotten.pending.character.unspent_skill_points == student.unspent_skill_points + spent
    assert forgotten.pending.character.gold == student.gold - mentor_price(student.level)


def test_the_mentor_does_not_sell_what_he_cannot_take(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """Расовое умение в разбор не идёт: за него не платили очком.

    Оно стояло в списке наравне с классовыми, наставник брал деньги, объявлял
    «забыто» - и умение оставалось на месте: расовый слот заводит ранг заново.
    Кнопка, которая берёт плату и ничего не делает, - это баг.
    """
    racial = content.race(hero.race_id).active_code
    bearer = replace(
        hero,
        gold=900,
        loadout=replace(hero.loadout, racial=racial, ranks={"warrior_rassechenie": 2}),
    )
    mentor = step(content, bearer, in_city, "Наставник")
    listed = mentor_screen(content, bearer, content.city(bearer.city_id), PageState()).text()
    assert content.skill(racial).name not in listed

    # И даже нажатая со старой клавиатуры, эта строка ничего не спишет.
    pressed = step(
        content,
        bearer,
        mentor,
        city_screens.forget_label(content.skill(racial).name, 1).text,
    )
    assert pressed.pending.empty
    assert pressed.notice


def test_the_mentor_refuses_a_customer_who_cannot_pay(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    broke = replace(hero, gold=0, loadout=replace(hero.loadout, ranks={"warrior_rassechenie": 2}))
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


def test_the_descent_lists_dungeons_then_asks_for_difficulty(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    dungeon = step(content, hero, in_city, "Подземелья")
    assert dungeon.screen is ScreenId.DUNGEON
    listed = render(content, hero, dungeon, world_seed=WORLD_SEED).text()
    assert "Барсучьи ходы" in listed and "Первая штольня" in listed

    picked = step(content, hero, dungeon, "Первая штольня")
    assert picked.screen is ScreenId.DUNGEON_PICK
    assert picked.dungeon_pick == "farhold_first_adit"
    assert "около 3 схваток" in render(content, hero, picked, world_seed=WORLD_SEED).text()

    down = step(content, hero, picked, "Разведка")
    assert down.fight == "dungeon"
    assert down.descent.layer == 0
    assert down.descent.difficulty == "recon"
    assert down.descent.dungeon_id == "farhold_first_adit"
    assert down.descent.city_id == "farhold"
    assert down.descent.level == 12


def test_a_dungeon_is_a_place_and_not_a_mirror(content: GameContent) -> None:
    """У подземелья свой фиксированный уровень, не растущий с игроком (ADR 0041)."""
    city = content.city("farhold")
    levels = sorted(one.level for one in city.regular_dungeons)
    assert levels[0] <= city.level_min + 8
    assert city.deep_dungeon.level == city.level_max
    assert all(city.level_min <= one.level <= city.level_max for one in city.dungeons)


def test_a_dungeon_you_outgrew_says_so_before_you_walk_in(
    content: GameContent, hero: Character
) -> None:
    grown = replace(hero, level=150)
    picked = step(content, grown, begin(grown), "Мир", "Подземелья", "Барсучьи ходы")
    text = render(content, grown, picked, world_seed=WORLD_SEED).text()
    assert "переросли этот спуск" in text


def test_a_service_the_city_does_not_offer_answers_instead_of_a_stub(
    content: GameContent, hero: Character, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Экрана-заглушки больше нет: нажатая со старой клавиатуры служба, которой у
    города нет, получает объяснение и оставляет игрока в городе (правило 12).
    """
    from mmorpg.presentation.telegram.flows import play as play_flow
    from mmorpg.presentation.telegram.routing import Command, Intent

    bare = replace(content.city("farhold"), services=("shop", "locations"))
    monkeypatch.setattr(play_flow, "known_city", lambda *a, **k: bare)

    state = replace(begin(hero), screen=ScreenId.CITY, city_id="farhold")
    answered = play_flow._handle_city(
        content, hero, state, Command(intent=Intent.SELECT, argument="Арена")
    )

    assert answered.screen is ScreenId.CITY
    assert "нет такой службы" in answered.notice


def test_a_city_has_a_deep_dungeon_open_only_to_the_grown(
    content: GameContent, hero: Character
) -> None:
    """Глубокое подземелье идёт по верху полосы города и открыто дошедшему до
    последней локации (ADR 0041, ADR 0019).
    """
    city = content.city("farhold")
    deep = city.deep_dungeon
    assert deep.level == city.level_max

    # Третий уровень до последней локации Дубно (22) не дорос: глубокий ход
    # назван строкой, но кнопки не получает.
    early = step(content, hero, begin(hero), "Мир", "Подземелья")
    early_text = render(content, hero, early, world_seed=WORLD_SEED).text()
    assert deep.name in early_text and "закрыт до уровня 22" in early_text
    assert step(content, hero, early, deep.name).screen is ScreenId.DUNGEON

    # Двадцать второй - дорос: кнопка есть, и она уводит в глубокий спуск.
    ready = replace(hero, level=22)
    screen = step(content, ready, begin(ready), "Мир", "Подземелья")
    picked = step(content, ready, screen, deep.name)
    assert picked.screen is ScreenId.DUNGEON_PICK
    assert deep.flavour in render(content, ready, picked, world_seed=WORLD_SEED).text()
    down = step(content, ready, picked, "Тёмный ход")
    assert down.fight == "dungeon"
    assert down.descent.dungeon_id == deep.id
    assert down.descent.difficulty == "delve"
    assert down.descent.level == city.level_max


def test_the_journal_lists_what_is_taken(content: GameContent, hero: Character) -> None:
    from mmorpg.domain.rules import quests as quest_rules

    took = quest_rules.take(content, hero, content.quest("farhold_tallies"))
    journal = step(content, took, begin(took), "Задания")
    assert journal.screen is ScreenId.QUESTS
    text = render(content, took, journal, world_seed=WORLD_SEED).text()
    assert "Столбы на Тракте: 0 из 3" in text


def test_walking_away_from_a_contract_keeps_it_on_the_board(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    quest = content.quest("farhold_tallies")
    board = step(content, hero, in_city, "Таверна", "Доска заданий")
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


def test_a_contract_says_what_to_do_and_where_to_go(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """Первое задание читалось как «разобраться с местами без боя» и больше ничего."""
    quest = content.quest("farhold_tallies")
    board = step(content, hero, in_city, "Таверна", "Доска заданий")
    offer = step(
        content, hero, board, f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}"
    )
    text = render(content, hero, offer, world_seed=WORLD_SEED).text()

    assert "Что делать:" in text
    assert "Нужно 3 раза" in text
    assert "Луга у Заставы" in text, "задание обязано назвать место, куда идти"
    assert "нажмите его действие" in text, "и то, что там нажимать"


def test_a_contract_for_made_goods_opens_instead_of_crashing(
    content: GameContent, hero: Character
) -> None:
    """Разговор о задании на изготовление падал: строки про «изготовить» не было."""
    quest = content.quest("farhold_whetstones")
    screen = quest_screens.offer_screen(content, quest, hero)
    text = screen.text()
    assert "изготовить своими руками" in text
    assert "Точильный камень" in text
    assert "Ремёсла" in text


def test_a_taken_contract_stays_on_the_board_with_its_count(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """Игрок соглашался на задание и не находил его там, где брал."""
    quest = content.quest("farhold_tallies")
    board = step(content, hero, in_city, "Таверна", "Доска заданий")
    offer = step(
        content, hero, board, f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}"
    )
    took = step(content, hero, offer, "Согласиться")
    assert took.pending.character is not None
    holder = took.pending.character
    assert holder.quests.is_taken(quest.id)

    again = step(content, holder, begin(holder), "Мир", "Таверна", "Доска заданий")
    text = render(content, holder, again, world_seed=WORLD_SEED).text()
    assert "Столбы на Тракте — взято, 0 из 3" in text
    assert "Взято отсюда: 1" in text


def test_a_taken_contract_can_be_given_back(content: GameContent, hero: Character) -> None:
    from mmorpg.domain.rules import quests as quest_rules

    quest = content.quest("farhold_tallies")
    holder = quest_rules.take(content, hero, quest)
    board = step(content, holder, begin(holder), "Мир", "Таверна", "Доска заданий")
    offer = step(content, holder, board, f"{quest.name} — взято, 0 из 3, в работе")
    assert offer.screen is ScreenId.QUEST_OFFER
    text = render(content, holder, offer, world_seed=WORLD_SEED).text()
    assert "Задание уже взято: 0 из 3" in text

    given = step(content, holder, offer, "Отказаться от задания")
    assert given.pending.character is not None
    assert not given.pending.character.quests.is_taken(quest.id)
    assert "возвращено" in given.notice


def quest_still_offered(content: GameContent, hero: Character) -> bool:
    from mmorpg.domain.rules import quests as quest_rules

    return any(quest.id == "farhold_tallies" for quest in quest_rules.available(content, hero))


# --- найти одну вещь в полной сумке -----------------------------------


def test_search_narrows_the_bag(content: GameContent, hero: Character) -> None:
    goods = Goods(
        gold=hero.gold,
        owned=(
            OwnedItem("wolf_pelt", 4),
            OwnedItem("small_healing_potion", 2),
            OwnedItem("medium_body@6#uncommon", 1),
        ),
    )
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    asked = step(content, hero, inventory, "Поиск", goods=goods)
    assert asked.searching
    assert "Наберите" in asked.notice

    found = step(content, hero, asked, "шкура", goods=goods)
    assert found.searching is False
    text = render(content, hero, found, world_seed=WORLD_SEED, goods=goods).text()
    assert "Найдено 1" in text
    assert "Звериная шкура" in text


def test_sections_cut_the_bag_and_reset_puts_it_back(content: GameContent, hero: Character) -> None:
    goods = Goods(
        gold=hero.gold,
        owned=(OwnedItem("wolf_pelt", 4), OwnedItem("medium_body@6#uncommon", 1)),
    )
    inventory = step(content, hero, begin(hero), "Инвентарь", goods=goods)
    sections = step(content, hero, inventory, "Фильтры", goods=goods)
    assert sections.screen is ScreenId.LIST_FILTERS

    raw = step(content, hero, sections, "Сырьё", goods=goods)
    assert raw.screen is ScreenId.INVENTORY
    text = render(content, hero, raw, world_seed=WORLD_SEED, goods=goods).text()
    assert "Найдено 1" in text
    assert "Звериная шкура" in text

    cleared = step(content, hero, raw, "Сбросить фильтры", goods=goods)
    assert cleared.list_page.filters.active is False
