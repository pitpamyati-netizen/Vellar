"""Всё, что слышит игрок, названо на языке Vellar.

Чёрный список из ``Narrative.md``, раздел 2, живёт здесь: одно слово из него
превращает имя в тот дежурный фэнтезийный набор, против которого написан мир.
Расы и классы - названное в том же разделе исключение: их читают вслух на экране
создания, и узнавать их надо на слух, поэтому они несут привычные слова. Чего им
нельзя никогда - это сползти в ремесло: делать вещи за плату - это
``content/crafts.toml`` и больше ничто (Roadmap 1.7).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from mmorpg.domain.entities import GameContent
from tests.content.conftest import FORBIDDEN_WORDS

# Класс - это как приключенец дерётся, ремесло - что он делает за плату. Название
# ремесла на любой из осей создания значит, что эти двое снова перепутаны.
CRAFT_WORDS = (
    "кузнец",
    "кожевник",
    "травник",
    "рудокоп",
    "плотник",
    "алхимик",
    "ремесленник",
)


def spoken(content: GameContent) -> Iterator[tuple[str, str]]:
    """Каждая видимая игроку строка каталога содержимого, вместе с её источником."""
    for race in content.races:
        yield race.id, f"{race.name} {race.description}"
        yield race.id, f"{race.passive.name} {race.passive.text}"
    for klass in content.classes:
        yield (
            klass.id,
            f"{klass.name} {klass.role} {klass.description} {klass.power} {klass.resource.name}",
        )
    for skill in content.skills:
        yield skill.code, f"{skill.name} {skill.text}"
    for trait in content.traits:
        yield trait.id, f"{trait.name} {trait.text} {' '.join(trait.tags)}"
    for item in content.items:
        yield item.id, item.name
    for rarity in content.rarities:
        yield rarity.id, rarity.name
    for archetype in content.enemy_archetypes:
        yield archetype.id, archetype.name
    for title in content.elite_titles:
        yield "elite_title", title
    for affix in content.affixes:
        yield affix.id, affix.adjective
    for city in content.cities:
        yield city.id, f"{city.name} {city.description}"
        for location in city.locations:
            yield location.id, location.name
        for dungeon in city.dungeons:
            yield dungeon.id, f"{dungeon.name} {dungeon.flavour}"
    yield from content.trait_categories.items()


def test_no_content_speaks_the_forbidden_vocabulary(content: GameContent) -> None:
    for source, text in spoken(content):
        lowered = text.casefold()
        for word in FORBIDDEN_WORDS:
            assert word not in lowered, f"{source} says {word!r}"


def test_neither_axis_is_named_after_a_craft(content: GameContent) -> None:
    """Раса говорит, кто ты, класс - как ты дерёшься, и ни то ни другое не работа."""
    named = [(race.id, race.name) for race in content.races]
    named += [(klass.id, klass.name) for klass in content.classes]
    for source, name in named:
        lowered = name.casefold()
        for craft in CRAFT_WORDS:
            assert craft not in lowered, f"{source} is named after a craft: {name!r}"


def test_races_and_classes_are_distinct_by_ear(content: GameContent) -> None:
    """И то и другое выбирают из списка кнопок, а кнопка ведёт по своему тексту."""
    race_names = [race.name for race in content.races]
    class_names = [klass.name for klass in content.classes]
    assert len(set(race_names)) == len(race_names)
    assert len(set(class_names)) == len(class_names)
    assert not set(race_names) & set(class_names)


# Только кириллические слова: ни апострофов, ни латиницы, ни выдуманного написания,
# которое экранному диктору пришлось бы читать по буквам.
PLAIN_NAME = re.compile(r"^[А-ЯЁ][а-яё]+(?: [а-яё]+)?$")


def test_a_race_is_named_plainly(content: GameContent) -> None:
    for race in content.races:
        assert PLAIN_NAME.fullmatch(race.name), f"{race.id}: {race.name!r} is not plain Russian"
        assert len(race.name) <= 20, race.id
        # Что раса делает с человеком - это строка под именем, и её читают целиком на
        # экране подробностей.
        assert race.description.endswith("."), race.id
        assert len(race.description) <= 120, race.id


def test_a_class_is_one_word(content: GameContent) -> None:
    """Кнопка класса несёт роль после имени, поэтому имя остаётся коротким."""
    for klass in content.classes:
        assert PLAIN_NAME.fullmatch(klass.name), klass.id
        assert " " not in klass.name, f"{klass.id}: {klass.name!r} is more than one word"


def test_every_race_keeps_its_frozen_id(content: GameContent) -> None:
    """Переименование - это правка текста: персонажи в базе показывают на эти идентификаторы."""
    assert {race.id for race in content.races} == {
        "human",
        "high_elf",
        "wood_elf",
        "dark_elf",
        "half_elf",
        "dwarf",
        "gnome",
        "halfling",
        "orc",
        "half_orc",
        "goblin",
        "troll",
        "dragonborn",
        "tiefling",
        "aasimar",
        "lizardfolk",
    }
    assert {klass.id for klass in content.classes} == {
        "warrior",
        "barbarian",
        "paladin",
        "ranger",
        "rogue",
        "mage",
        "cleric",
        "druid",
    }
