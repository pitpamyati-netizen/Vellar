"""Правки смотрителя: что ложится на мир, что не ложится, и почему.

Здесь проверяется единственное свойство, ради которого правки вообще устроены
поверх, а не вместо: файлы в ``content/`` остаются нетронутыми, поэтому любую
правку можно снять и получить ровно тот мир, что был до неё.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import ObjectiveKind, QuestLog
from mmorpg.domain.rules import modifiers as modifier_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.overlay import FieldKind

DOVEN = OverlayRecord(
    kind=OverlayKind.NPC,
    entity_id="keeper_npc_1",
    fields={
        "name": "Довен",
        "city": "farhold",
        "role": "писарь заставы",
        "text": "Водит пальцем по строкам.",
    },
)

TALLY = OverlayRecord(
    kind=OverlayKind.QUEST,
    entity_id="keeper_quest_1",
    fields={
        "name": "Столбы у брода",
        "city": "farhold",
        "npc": "keeper_npc_1",
        "terms": "Обойдите три места и скажите, что там.",
        "objective": "search",
        "target_count": "3",
        "level": "1",
        "reward_gold": "40",
    },
)


def apply(content: GameContent, *records: OverlayRecord) -> GameContent:
    return overlay_rules.apply(content, records)


def refused_for(content: GameContent, record: OverlayRecord, because: str) -> bool:
    """Отказано **именно по этой причине**, и ни по какой другой.

    Просто «отказов не пусто» ловит не тот отказ: у недописанной записи их
    несколько сразу, и проверка прошла бы, даже если бы искомую убрали из кода.
    """
    return [line for line in overlay_rules.problems(content, record) if because in line] != []


def accepted(content: GameContent, record: OverlayRecord) -> bool:
    """Обратная сторона: без изъяна запись обязана проходить целиком."""
    return overlay_rules.problems(content, record) == ()


# --- люди в городах ----------------------------------------------------


def test_a_keeper_puts_a_person_into_a_city(content: GameContent) -> None:
    """Жителей в content нет вовсе: они целиком приходят правкой."""
    assert content.npcs == ()

    edited = apply(content, DOVEN)

    assert edited.has_npc("keeper_npc_1")
    assert [npc.name for npc in edited.npcs_in("farhold")] == ["Довен"]
    assert edited.npc("keeper_npc_1").title == "Довен, писарь заставы"


def test_taking_the_edit_back_gives_back_the_very_same_world(content: GameContent) -> None:
    """Не «жителей снова нет», а мир, неотличимый от прочитанного из файлов."""
    edited = apply(content, DOVEN)
    assert edited.npcs

    back = apply(content)

    assert back.npcs == ()
    assert [city.name for city in back.cities] == [city.name for city in content.cities]
    assert [quest.id for quest in back.quests] == [quest.id for quest in content.quests]
    assert [enemy.id for enemy in back.enemy_archetypes] == [
        enemy.id for enemy in content.enemy_archetypes
    ]


# --- задания -----------------------------------------------------------


def test_a_contract_can_be_handed_to_a_person_added_by_the_same_panel(
    content: GameContent,
) -> None:
    """Правка может ссылаться на правку: сначала встают люди, потом их работа."""
    edited = apply(content, DOVEN, TALLY)

    quest = edited.quest("keeper_quest_1")
    assert quest.giver_id == "keeper_npc_1"
    # Имя нанимателя берётся у жителя, а не хранится вторым экземпляром.
    assert quest.giver == "Довен, писарь заставы"
    assert edited.quests_of("keeper_npc_1") == (quest,)


def test_a_new_contract_reaches_the_board_like_any_other(content: GameContent) -> None:
    edited = apply(content, DOVEN, TALLY)
    player = Character(id=1, user_id=1, name="Аргус", race_id="human", class_id="warrior")

    offered = quest_rules.available(edited, player)

    assert "keeper_quest_1" in {quest.id for quest in offered}


def test_a_contract_without_anybody_to_pay_does_not_appear(content: GameContent) -> None:
    orphan = replace(
        TALLY, fields={key: value for key, value in TALLY.fields.items() if key != "npc"}
    )

    assert refused_for(content, orphan, "Некому платить")
    assert not apply(content, orphan).has_quest("keeper_quest_1")
    # Впишите имя нанимателя рукой — и та же запись проходит.
    assert accepted(content, orphan.with_field("giver", "Мерла с Отмелей"))


def test_a_contract_that_counts_to_nothing_is_refused(content: GameContent) -> None:
    """Житель уже стоит в мире, поэтому отказ может быть только один — про счёт."""
    world = apply(content, DOVEN)

    assert accepted(world, TALLY)
    assert refused_for(world, TALLY.with_field("target_count", "0"), "меньше одного")


def test_a_contract_cannot_follow_itself(content: GameContent) -> None:
    world = apply(content, DOVEN)

    assert refused_for(world, TALLY.with_field("follows", TALLY.entity_id), "после себя")


# --- локации -----------------------------------------------------------


NEW_PLACE = OverlayRecord(
    kind=OverlayKind.LOCATION,
    entity_id="keeper_location_1",
    fields={
        "name": "Брод у Ольхи",
        "city": "farhold",
        "slot": "6",
        "biome": "лес",
        "level_min": "2",
        "level_max": "6",
        "pvp": "нет",
    },
)


def test_a_location_is_added_to_a_city(content: GameContent) -> None:
    edited = apply(content, NEW_PLACE)
    city = edited.city("farhold")

    assert len(city.locations) == len(content.city("farhold").locations) + 1
    assert city.location(6).name == "Брод у Ольхи"


def test_an_existing_location_is_edited_in_place(content: GameContent) -> None:
    renamed = OverlayRecord(
        kind=OverlayKind.LOCATION,
        entity_id="quiet_meadows",
        fields={**overlay_rules.snapshot(content, OverlayKind.LOCATION, "quiet_meadows")},
    ).with_field("name", "Луга у Брода")

    city = apply(content, renamed).city("farhold")

    assert city.location(1).name == "Луга у Брода"
    assert len(city.locations) == len(content.city("farhold").locations)


def test_a_place_cannot_take_a_slot_that_is_already_taken(content: GameContent) -> None:
    assert accepted(content, NEW_PLACE)

    clashing = NEW_PLACE.with_field("slot", "1")

    assert refused_for(content, clashing, "занято")
    assert refused_for(content, clashing, content.city("farhold").location(1).name)


def test_an_inverted_level_band_is_refused(content: GameContent) -> None:
    assert accepted(content, NEW_PLACE)

    assert refused_for(content, NEW_PLACE.with_field("level_min", "40"), "больше уровня")
    assert refused_for(content, NEW_PLACE.with_field("slot", "99"), "Место в списке")


def test_a_location_is_removed_from_the_list(content: GameContent) -> None:
    gone = OverlayRecord(kind=OverlayKind.LOCATION, entity_id="dusk_grove", removed=True)

    city = apply(content, gone).city("farhold")

    assert "dusk_grove" not in {location.id for location in city.locations}


def test_the_last_location_of_a_city_is_never_removed(content: GameContent) -> None:
    """Город без локаций — город, в который незачем идти."""
    stripped = tuple(
        OverlayRecord(kind=OverlayKind.LOCATION, entity_id=location.id, removed=True)
        for location in content.city("farhold").locations
    )

    left = overlay_rules.apply(content, stripped).city("farhold")

    assert len(left.locations) == 1


# --- противники --------------------------------------------------------


WOLF = OverlayRecord(
    kind=OverlayKind.ENEMY,
    entity_id="keeper_enemy_1",
    fields={
        "name": "Болотная пиявка",
        "kind": "aberration",
        "biomes": "болото",
        "health": "0,8",
        "damage": "1,4",
        "armor": "0,5",
        "initiative": "1",
    },
)


def test_a_new_enemy_can_be_settled_into_a_biome(content: GameContent) -> None:
    edited = apply(content, WOLF)
    added = next(enemy for enemy in edited.enemy_archetypes if enemy.id == "keeper_enemy_1")

    assert added.kind is EnemyKind.ABERRATION
    assert added.fits("болото")
    # Запятая — то, что набирают на телефоне, и она должна значить дробь.
    assert added.damage == pytest.approx(1.4)


def test_an_enemy_needs_somewhere_to_live(content: GameContent) -> None:
    assert accepted(content, WOLF)

    assert refused_for(content, WOLF.with_field("biomes", ""), "негде водиться")


def test_the_dungeon_flag_survives_an_enemy_overlay_round_trip(content: GameContent) -> None:
    """Правка не должна молча вытащить подземную породу на дорогу (ADR 0042)."""
    snap = overlay_rules.snapshot(content, OverlayKind.ENEMY, "rockjaw")
    assert snap["dungeon"] == "да"
    record = OverlayRecord(kind=OverlayKind.ENEMY, entity_id="rockjaw", fields=snap)
    edited = apply(content, record)
    kept = next(one for one in edited.enemy_archetypes if one.id == "rockjaw")
    assert kept.dungeon is True


def test_an_unknown_biome_is_refused(content: GameContent) -> None:
    """Известная половина не спасает: перечисление годится целиком или никак."""
    assert refused_for(content, WOLF.with_field("biomes", "луга, изнанка"), "изнанка")
    assert accepted(content, WOLF.with_field("biomes", "луга, болото"))


def test_an_enemy_can_be_taken_out_of_the_game(content: GameContent) -> None:
    gone = OverlayRecord(kind=OverlayKind.ENEMY, entity_id="grey_wolf", removed=True)

    edited = apply(content, gone)

    assert "grey_wolf" not in {enemy.id for enemy in edited.enemy_archetypes}


def test_emptying_a_biome_is_reported_rather_than_forbidden(content: GameContent) -> None:
    """Смотритель может обезлюдить местность — но должен об этом услышать."""
    emptied = tuple(
        OverlayRecord(kind=OverlayKind.ENEMY, entity_id=enemy.id, removed=True)
        for enemy in content.enemy_archetypes
    )

    assert overlay_rules.orphaned_biomes(content) == ()
    assert overlay_rules.orphaned_biomes(overlay_rules.apply(content, emptied))


# --- города ------------------------------------------------------------


def test_a_city_is_renamed_without_touching_its_levels(content: GameContent) -> None:
    renamed = OverlayRecord(
        kind=OverlayKind.CITY,
        entity_id="farhold",
        fields={"name": "Порубежье", "description": "Застава, за которой тропа."},
    )

    city = apply(content, renamed).city("farhold")

    assert city.name == "Порубежье"
    assert (city.level_min, city.level_max) == (
        content.city("farhold").level_min,
        content.city("farhold").level_max,
    )


# --- карточка ----------------------------------------------------------


def test_an_untouched_entity_reads_its_own_values(content: GameContent) -> None:
    card = overlay_rules.effective(content, (), OverlayKind.QUEST, "farhold_tallies")

    assert card.value("name") == content.quest("farhold_tallies").name
    assert card.value("objective") == ObjectiveKind.SEARCH.value


def test_a_removed_entity_keeps_its_fields_so_it_can_come_back(content: GameContent) -> None:
    gone = OverlayRecord(
        kind=OverlayKind.ENEMY,
        entity_id="grey_wolf",
        fields=overlay_rules.snapshot(content, OverlayKind.ENEMY, "grey_wolf"),
        removed=True,
    )
    edited = apply(content, gone)

    card = overlay_rules.effective(edited, (gone,), OverlayKind.ENEMY, "grey_wolf")

    assert card.value("name") == "Серый волк"
    assert card.removed is True


def test_a_field_is_shown_by_word_not_by_key(content: GameContent) -> None:
    spec = overlay_rules.spec_of(OverlayKind.NPC, "city")
    assert spec is not None

    assert overlay_rules.shown(content, spec, DOVEN) == "Дубно"


def test_an_empty_field_says_so_out_loud(content: GameContent) -> None:
    spec = overlay_rules.spec_of(OverlayKind.NPC, "role")
    assert spec is not None
    silent = OverlayRecord(kind=OverlayKind.NPC, entity_id="keeper_npc_2")

    assert overlay_rules.shown(content, spec, silent) == "не заполнено"


def test_a_flag_reads_as_a_word(content: GameContent) -> None:
    spec = overlay_rules.spec_of(OverlayKind.LOCATION, "pvp")
    assert spec is not None

    assert overlay_rules.shown(content, spec, NEW_PLACE) == "нет"
    assert overlay_rules.shown(content, spec, NEW_PLACE.with_field("pvp", "да")) == "да"


def test_the_target_list_follows_what_the_contract_counts(content: GameContent) -> None:
    spec = overlay_rules.spec_of(OverlayKind.QUEST, "target_kind")
    assert spec is not None

    hunting = overlay_rules.options(content, spec, TALLY.with_field("objective", "kill"))
    searching = overlay_rules.options(content, spec, TALLY)

    assert "beast" in hunting
    assert "cache" in searching
    assert "beast" not in searching


def test_a_new_key_never_collides_with_content(content: GameContent) -> None:
    first = overlay_rules.next_id(OverlayKind.NPC, ())
    second = overlay_rules.next_id(OverlayKind.NPC, (DOVEN,))

    assert first == "keeper_npc_1"
    assert second != first
    assert not content.has_npc(second)


def test_every_field_of_every_kind_can_be_shown(content: GameContent) -> None:
    """Экран карточки строится по описанию, поэтому описание должно быть полным."""
    for kind, specs in overlay_rules.FIELDS.items():
        entity_id = (
            overlay_rules.listing(content, kind)[0][0] if kind is not OverlayKind.NPC else ""
        )
        card = overlay_rules.effective(content, (), kind, entity_id)
        for spec in specs:
            assert overlay_rules.shown(content, spec, card)
            if spec.kind in {FieldKind.CHOICE, FieldKind.LIST}:
                overlay_rules.options(content, spec, card)


def test_a_value_longer_than_a_screen_is_refused(content: GameContent) -> None:
    assert accepted(content, DOVEN.with_field("text", "а" * overlay_rules.MAX_TEXT))

    wordy = DOVEN.with_field("text", "а" * (overlay_rules.MAX_TEXT + 1))

    assert refused_for(content, wordy, f"длиннее {overlay_rules.MAX_TEXT} знаков")


def test_a_name_is_held_to_the_length_of_a_button(content: GameContent) -> None:
    """Имя окажется на кнопке, а кнопка — одна строка."""
    long_name = DOVEN.with_field("name", "и" * (overlay_rules.NAME_LIMIT + 1))

    assert refused_for(content, long_name, f"длиннее {overlay_rules.NAME_LIMIT} знаков")
    assert accepted(content, DOVEN.with_field("name", "и" * overlay_rules.NAME_LIMIT))


def test_a_refusal_names_the_bad_value_without_reciting_it(content: GameContent) -> None:
    wrong = TALLY.with_field("objective", "ж" * overlay_rules.MAX_TEXT)

    why = overlay_rules.problems(apply(content, DOVEN), wrong)

    assert why
    assert all(len(line) < 120 for line in why), why


def test_a_number_field_refuses_words(content: GameContent) -> None:
    world = apply(content, DOVEN)

    assert refused_for(world, TALLY.with_field("target_count", "три"), "нужно целое число")
    assert refused_for(world, WOLF.with_field("health", "много"), "нужна доля")
    assert accepted(world, WOLF.with_field("health", "1,5"))


def test_a_half_written_record_changes_nothing(content: GameContent) -> None:
    """Правка работает целиком или не работает вовсе."""
    half = OverlayRecord(kind=OverlayKind.NPC, entity_id="keeper_npc_9", fields={"city": "farhold"})

    assert refused_for(content, half, "Не заполнено: имя")
    assert apply(content, half).npcs == ()
    # И соседнюю правку она за собой не утягивает.
    assert overlay_rules.apply(content, (half, DOVEN)).npcs_in("farhold")[0].name == "Довен"


# --- разбор набранного -------------------------------------------------


def test_a_field_that_is_not_a_number_falls_back_instead_of_raising() -> None:
    """Значения приходят строками из базы и переживают смену кода.

    Разбор поэтому не имеет права падать: он отвечает запасным значением, а
    ругается на это отдельно — :func:`problems`, словами и в лицо смотрителю.
    """
    broken = OverlayRecord(
        kind=OverlayKind.QUEST,
        entity_id="keeper_quest_2",
        fields={"target_count": "три", "reward_gold": "", "level": "-4"},
    )

    assert broken.number("target_count", 1) == 1
    assert broken.number("reward_gold") == 0
    assert broken.number("level") == -4
    assert broken.number("нет такого поля", 7) == 7


def test_a_share_is_read_with_a_comma_or_a_dot() -> None:
    """Запятую набирают на телефоне, точку — на клавиатуре. Обе значат дробь."""
    record = OverlayRecord(
        kind=OverlayKind.ENEMY,
        entity_id="keeper_enemy_2",
        fields={"health": "1,25", "damage": "0.5", "armor": "много"},
    )

    assert record.rate("health") == pytest.approx(1.25)
    assert record.rate("damage") == pytest.approx(0.5)
    assert record.rate("armor") == pytest.approx(1.0)


def test_a_flag_and_a_list_read_what_a_person_would_type() -> None:
    record = OverlayRecord(
        kind=OverlayKind.LOCATION,
        entity_id="keeper_location_2",
        fields={"pvp": " Да ", "biomes": "луга,  лес , ", "loot": ""},
    )

    assert record.flag("pvp") is True
    assert record.flag("нет такого поля") is False
    assert record.listed("biomes") == ("луга", "лес")
    assert record.listed("loot") == ()


def test_a_keeper_key_is_told_apart_from_a_content_key() -> None:
    assert DOVEN.is_keepers is True
    assert OverlayRecord(kind=OverlayKind.ENEMY, entity_id="grey_wolf").is_keepers is False


def test_a_field_can_be_cleared_without_touching_its_neighbours() -> None:
    stripped = DOVEN.without_field("role")

    assert stripped.value("role") == ""
    assert stripped.value("name") == "Довен"
    # Исходная запись не тронута: записи неизменяемы.
    assert DOVEN.value("role") == "писарь заставы"


# --- задание называет, кого именно и куда идти ---------------------------


def _hunt(**extra: str) -> OverlayRecord:
    """Задание на охоту: то, что смотритель заводит чаще всего."""
    fields = {
        "name": "Охота на кабанов",
        "city": "farhold",
        "npc": "keeper_npc_1",
        "terms": "Убей пятерых кабанов в лугах у заставы.",
        "objective": "kill",
        "target_count": "5",
        "reward_gold": "30",
    }
    return OverlayRecord(kind=OverlayKind.QUEST, entity_id="keeper_quest_2", fields=fields | extra)


def test_a_contract_can_name_one_opponent_and_not_only_a_breed(content: GameContent) -> None:
    spec = overlay_rules.spec_of(OverlayKind.QUEST, "target_kind")
    assert spec is not None

    offered = overlay_rules.options(content, spec, _hunt())

    # Породы остаются первыми: «любое зверьё» - по-прежнему законное условие.
    assert offered[: len(EnemyKind)] == tuple(kind.value for kind in EnemyKind)
    assert "wild_boar" in offered
    assert overlay_rules.option_name(content, spec, "wild_boar") == "Кабан"


def _slain(content: GameContent, archetype_id: str) -> Enemy:
    """Побеждённый противник этой породы - ровно то, что кладут в счёт задания."""
    archetype = next(one for one in content.enemy_archetypes if one.id == archetype_id)
    return Enemy(
        archetype_id=archetype.id,
        name=archetype.name,
        kind=archetype.kind,
        level=3,
        max_health=30,
        damage=5,
        armor=1,
        initiative=1.0,
        loot=(),
        gold=3,
    )


def test_a_named_opponent_is_counted_and_its_neighbours_are_not(content: GameContent) -> None:
    world = apply(content, DOVEN, _hunt(target_kind="wild_boar"))
    hunter = replace(
        Character(id=1, user_id=1, name="Ловчий", race_id="human", class_id="ranger", level=3),
        quests=QuestLog().take("keeper_quest_2"),
    )

    log, moved = quest_rules.record_kills(
        world, hunter, (_slain(world, "wild_boar"), _slain(world, "grey_wolf"))
    )

    # Волк - тоже зверьё, но заказывали кабана.
    assert log.progress("keeper_quest_2") == 1
    assert [step.quest.id for step in moved] == ["keeper_quest_2"]


def test_a_contract_made_by_the_panel_can_hunt_an_opponent_it_also_made(
    content: GameContent,
) -> None:
    """Две правки подряд: сначала противник, потом задание на него.

    Раньше задание проверялось против мира без свежего противника и отклонялось
    целиком - то есть противника завести было можно, а заказать его нельзя.
    """
    beast = OverlayRecord(
        kind=OverlayKind.ENEMY,
        entity_id="keeper_enemy_1",
        fields={
            "name": "Секач",
            "kind": "beast",
            "biomes": "луга",
            "health": "1,2",
            "damage": "1",
            "armor": "1",
            "initiative": "1",
        },
    )
    world = apply(content, beast, DOVEN, _hunt(target_kind="keeper_enemy_1"))

    assert world.quest("keeper_quest_2").target_kind == "keeper_enemy_1"


def test_a_contract_says_which_place_it_sends_you_to(content: GameContent) -> None:
    world = apply(content, DOVEN, _hunt(location_slot="1"))

    assert world.quest("keeper_quest_2").location_slot == 1


def test_a_place_the_city_does_not_have_is_refused_in_words(content: GameContent) -> None:
    assert refused_for(content, _hunt(location_slot="9"), "нет места 9")


def test_the_name_of_the_employer_is_asked_only_when_nobody_gives_it(
    content: GameContent,
) -> None:
    """Житель выбран - значит, имя нанимателя уже названо, и второй раз не спрашивается."""
    del content
    keys = [spec.key for spec in overlay_rules.fields_for(_hunt())]
    assert "giver" not in keys

    alone = [spec.key for spec in overlay_rules.fields_for(_hunt(npc=""))]
    assert "giver" in alone


# --- черты, ремёсла и рецепты -----------------------------------------


def _trait_edit(content: GameContent, trait_id: str, **fields: str) -> OverlayRecord:
    """Правка существующей черты: снимок плюс перечисленные поля."""
    return OverlayRecord(
        kind=OverlayKind.TRAIT,
        entity_id=trait_id,
        fields={**overlay_rules.snapshot(content, OverlayKind.TRAIT, trait_id), **fields},
    )


def test_a_keeper_edits_a_trait_bonus_and_the_engine_counts_it(content: GameContent) -> None:
    edited = apply(content, _trait_edit(content, "berserker", modifiers="damage_percent=25"))

    bundle = modifier_rules.trait_modifiers(edited, ["berserker"])
    assert bundle["damage_percent"] == pytest.approx(25.0)


def test_an_unknown_modifier_key_on_a_trait_is_refused(content: GameContent) -> None:
    wrong = _trait_edit(content, "berserker", modifiers="damage_percent=10, moxie=5")

    assert refused_for(content, wrong, "неизвестно")
    # Ключ из EFFECTIVE_KEYS проходит, придуманный — нет.
    assert accepted(content, _trait_edit(content, "berserker", modifiers="crit_chance_percent=5"))


def test_a_keeper_invents_a_whole_trait(content: GameContent) -> None:
    fresh = OverlayRecord(
        kind=OverlayKind.TRAIT,
        entity_id="keeper_trait_1",
        fields={
            "name": "Клеймо заставы",
            "category": "combat",
            "text": "Рука помнит стену.",
            "modifiers": "accuracy_percent=4, dodge_percent=-2",
        },
    )
    world = apply(content, fresh)

    assert world.has_trait("keeper_trait_1")
    assert "keeper_trait_1" in {trait.id for trait in world.traits_in_category("combat")}
    assert modifier_rules.trait_modifiers(world, ["keeper_trait_1"])["accuracy_percent"] == 4


def test_renaming_a_craft_keeps_its_gathering_yields(content: GameContent) -> None:
    assert content.craft("mining").yields

    snap = overlay_rules.snapshot(content, OverlayKind.CRAFT, "mining")
    renamed = OverlayRecord(
        kind=OverlayKind.CRAFT, entity_id="mining", fields={**snap, "name": "Рудокопство"}
    )
    world = apply(content, renamed)

    assert world.craft("mining").name == "Рудокопство"
    assert world.craft("mining").yields == content.craft("mining").yields


def _recipe(**fields: str) -> OverlayRecord:
    base = {
        "craft": "smithing",
        "rank": "2",
        "inputs": "iron_scrap=3",
        "output": "whetstone",
        "output_count": "1",
        "experience": "15",
    }
    return OverlayRecord(kind=OverlayKind.RECIPE, entity_id="keeper_recipe_1", fields=base | fields)


def test_a_keeper_adds_a_recipe(content: GameContent) -> None:
    world = apply(content, _recipe())

    added = next(r for r in world.recipes_of("smithing") if r.id == "keeper_recipe_1")
    assert added.output_id == "whetstone"
    assert [(part.item_id, part.count) for part in added.inputs] == [("iron_scrap", 3)]


def test_a_recipe_with_an_unknown_ingredient_is_refused(content: GameContent) -> None:
    assert refused_for(content, _recipe(inputs="moondust=1"), "неизвестно")
    assert "keeper_recipe_1" not in {
        r.id for r in apply(content, _recipe(inputs="moondust=1")).recipes
    }


def test_a_recipe_cannot_hang_on_a_gathering_craft(content: GameContent) -> None:
    assert refused_for(content, _recipe(craft="mining"), "сбор")


def test_a_recipe_rank_stays_inside_the_ladder(content: GameContent) -> None:
    assert refused_for(content, _recipe(rank="9"), "С какого ранга")
    assert accepted(content, _recipe(rank=str(content.craft_rules.max_rank)))


def test_a_recipe_names_its_other_bad_values(content: GameContent) -> None:
    assert refused_for(content, _recipe(output="лунная пыль"), "такой вещи нет")
    assert refused_for(content, _recipe(inputs="iron_scrap=0"), "меньше одного")
    assert refused_for(content, _recipe(craft="кузня"), "такого нет")


def test_a_craft_refuses_a_made_up_kind_or_stat(content: GameContent) -> None:
    good = OverlayRecord(
        kind=OverlayKind.CRAFT,
        entity_id="keeper_craft_1",
        fields={"name": "Дублёж", "kind": "making", "stat": "STR", "description": ""},
    )
    assert accepted(content, good)
    assert refused_for(content, good.with_field("kind", "колдовство"), "сбор» или «работа")
    assert refused_for(content, good.with_field("stat", "МОЩЬ"), "характеристику из списка")


def test_a_trait_refuses_a_category_that_is_not_there(content: GameContent) -> None:
    assert refused_for(content, _trait_edit(content, "berserker", category="небыль"), "такого нет")


def test_a_pairs_field_is_shown_key_by_value(content: GameContent) -> None:
    spec = next(s for s in overlay_rules.FIELDS[OverlayKind.TRAIT] if s.key == "modifiers")
    record = _trait_edit(content, "berserker", modifiers="crit_chance_percent=5")

    assert overlay_rules.shown(content, spec, record) == "crit_chance_percent 5"
    empty = OverlayRecord(kind=OverlayKind.TRAIT, entity_id="keeper_trait_9")
    assert overlay_rules.shown(content, spec, empty) == "не заполнено"


def test_any_edit_keeps_turnings_and_deep_dungeon_gear(content: GameContent) -> None:
    """Пересборка мира с правкой не роняет то, что в неё не передавали явно."""
    world = apply(content, DOVEN)

    assert world.turnings == content.turnings
    assert world.open_turning_id == content.open_turning_id
    assert world.gear_archetypes == content.gear_archetypes
    assert world.gear_tiers == content.gear_tiers


# --- опорные числа ----------------------------------------------------


def _meta(content: GameContent, **fields: str) -> OverlayRecord:
    snap = overlay_rules.snapshot(content, OverlayKind.META, overlay_rules.META_ID)
    return OverlayRecord(
        kind=OverlayKind.META, entity_id=overlay_rules.META_ID, fields=snap | fields
    )


def test_meta_has_one_entity_that_cannot_be_created(content: GameContent) -> None:
    listed = overlay_rules.listing(content, OverlayKind.META)

    assert len(listed) == 1
    assert listed[0][0] == overlay_rules.META_ID
    assert OverlayKind.META not in overlay_rules.CREATABLE


def test_a_keeper_tunes_stat_points_per_level(content: GameContent) -> None:
    assert content.rules.stat_points_per_level != 7

    world = apply(content, _meta(content, stat_points_per_level="7"))

    assert world.rules.stat_points_per_level == 7
    # Всё, что не трогали, осталось как в файлах.
    assert world.rules.rank_costs == content.rules.rank_costs
    assert world.rules.base_stat_value == content.rules.base_stat_value


def test_a_keeper_rewrites_the_rank_ladder(content: GameContent) -> None:
    world = apply(content, _meta(content, rank_costs="1, 1, 2, 2, 3"))

    assert world.rules.rank_costs == (1, 1, 2, 2, 3)
    assert world.rules.rank_cost(3) == 2


def test_dropping_the_meta_edit_restores_the_files(content: GameContent) -> None:
    edited = _meta(content, stat_points_per_level="9")
    assert apply(content, edited).rules.stat_points_per_level == 9
    assert apply(content).rules.stat_points_per_level == content.rules.stat_points_per_level


def test_meta_refuses_values_that_break_the_rules(content: GameContent) -> None:
    assert refused_for(content, _meta(content, stat_points_per_level="-1"), "меньше нуля")
    assert refused_for(content, _meta(content, base_stat_value="500"), "не тюнинг")
    assert refused_for(content, _meta(content, rank_costs="1, 2"), "по одному на ранг")
    assert refused_for(content, _meta(content, rank_costs="1, -2, 2, 3, 4"), "отрицательных")
    assert refused_for(content, _meta(content, branch_gates="4, 2, 1"), "всегда 0")
    assert refused_for(content, _meta(content, branch_gates="0, 8, 4"), "по возрастанию")
    assert refused_for(content, _meta(content, rank_costs="раз, два"), "целые числа через запятую")


def test_a_broken_meta_edit_does_not_touch_the_world(content: GameContent) -> None:
    world = apply(content, _meta(content, stat_points_per_level="-1"))

    assert world.rules.stat_points_per_level == content.rules.stat_points_per_level


def test_meta_cannot_be_removed_from_the_game(content: GameContent) -> None:
    gone = OverlayRecord(kind=OverlayKind.META, entity_id=overlay_rules.META_ID, removed=True)

    assert refused_for(content, gone, "правят, а не убирают")


# --- выгрузка правки в content/ (Roadmap: навигация и экспорт) --------


def _toml_entry(fragment: str, section: str) -> dict:
    """Разобрать фрагмент как TOML и достать первую таблицу секции ``[[section]]``."""
    import tomllib

    body = "\n".join(line for line in fragment.splitlines() if not line.lstrip().startswith("#"))
    parsed = tomllib.loads(body)
    top = section.split(".")[0]
    inner: object = parsed[top]
    for part in section.split(".")[1:]:
        inner = inner[part]  # type: ignore[index]
    return inner[0]  # type: ignore[index]


def test_a_quest_edit_exports_as_a_toml_fragment(content: GameContent) -> None:
    edited = overlay_rules.effective(
        apply(content, DOVEN), (DOVEN, TALLY), OverlayKind.QUEST, TALLY.entity_id
    )

    fragment = overlay_rules.to_toml(apply(content, DOVEN), edited)

    assert "content/quests.toml" in fragment
    quest = _toml_entry(fragment, "quest")
    assert quest["city"] == "farhold"
    assert quest["objective"] == "search"
    assert quest["target_count"] == 3
    assert quest["giver"].startswith("Довен")  # имя (с занятием) берётся у жителя, не id


def test_an_enemy_edit_exports_with_its_biomes_and_rates(content: GameContent) -> None:
    wolf = overlay_rules.effective(content, (), OverlayKind.ENEMY, "grey_wolf")

    enemy = _toml_entry(overlay_rules.to_toml(content, wolf), "enemy")

    assert enemy["kind"] == "beast"
    assert "луга" in enemy["biomes"]
    assert isinstance(enemy["health"], float)


def test_a_trait_edit_exports_its_modifiers_as_an_inline_table(content: GameContent) -> None:
    berserker = overlay_rules.effective(content, (), OverlayKind.TRAIT, "berserker")

    trait = _toml_entry(overlay_rules.to_toml(content, berserker), "trait")

    assert trait["modifiers"]["damage_percent"] == 10


def test_a_resident_edit_has_no_file_home(content: GameContent) -> None:
    resident = overlay_rules.effective(
        apply(content, DOVEN), (DOVEN,), OverlayKind.NPC, DOVEN.entity_id
    )

    assert "в content/ не хранятся" in overlay_rules.to_toml(apply(content, DOVEN), resident)
    assert OverlayKind.NPC not in overlay_rules.EXPORTABLE


# --- голосования Палаты и находки сбора: вложенные списки (ADR 0046) --


def _turning(**fields: str) -> OverlayRecord:
    base = {
        "name": "Мосты",
        "question": "Чинить ли мосты на перевале?",
        "options": "yes | Чинить | Дороже, но целее\nno | Не чинить | Дешевле, объезд длиннее",
    }
    return OverlayRecord(
        kind=OverlayKind.TURNING, entity_id="keeper_turning_1", fields=base | fields
    )


def test_a_keeper_adds_a_turning_with_its_options(content: GameContent) -> None:
    world = apply(content, _turning())

    added = next((t for t in world.turnings if t.id == "keeper_turning_1"), None)
    assert added is not None
    assert [one.id for one in added.options] == ["yes", "no"]
    assert added.options[0].name == "Чинить"


def test_a_turning_with_one_answer_is_refused(content: GameContent) -> None:
    assert refused_for(content, _turning(options="only | Единственный"), "меньше двух")


def test_the_open_flag_makes_the_turning_the_one_being_asked(content: GameContent) -> None:
    world = apply(content, _turning(open="да"))

    assert world.open_turning_id == "keeper_turning_1"


def test_a_turning_edit_exports_with_nested_option_tables(content: GameContent) -> None:
    edited = overlay_rules.effective(
        content, (_turning(),), OverlayKind.TURNING, "keeper_turning_1"
    )

    fragment = overlay_rules.to_toml(content, edited)

    assert "[[turning]]" in fragment and "[[turning.options]]" in fragment
    parsed = _toml_entry(fragment, "turning")
    assert parsed["question"].startswith("Чинить")
    assert [one["id"] for one in parsed["options"]] == ["yes", "no"]


def _mining_with_yields(content: GameContent, yields: str) -> OverlayRecord:
    """Как это делает панель: открыть карточку (свести с файлом), поправить поле."""
    card = overlay_rules.effective(content, (), OverlayKind.CRAFT, "mining")
    return card.with_field("yields", yields)


def test_craft_yields_are_edited_from_the_same_card(content: GameContent) -> None:
    edit = _mining_with_yields(content, "iron_scrap | 1 | луга\nbog_iron | 4")

    world = apply(content, edit)

    mining = world.craft("mining")
    assert {one.item_id for one in mining.yields} == {"iron_scrap", "bog_iron"}
    scrap = next(one for one in mining.yields if one.item_id == "iron_scrap")
    assert scrap.level == 1 and scrap.biomes == ("луга",)


def test_craft_yields_reject_an_unknown_item(content: GameContent) -> None:
    assert refused_for(content, _mining_with_yields(content, "нет_такой | 1"), "нет")
