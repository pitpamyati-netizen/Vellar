"""Everything the player hears is named in the language of Vellar.

Two rules from ``Narrative.md``, section 2, live here. The black list is the
cheap half: one word from it turns a name into the generic fantasy set the world
is written against. The peoples-and-crafts rule is the expensive half - a people
carries a name of its own, a craft is named after the work - and it is guarded by
the species names that must never come back (Roadmap 1.4).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from mmorpg.domain.entities import GameContent
from tests.content.conftest import FORBIDDEN_WORDS

# Species and archetypes borrowed from the shelf. A race here would mean the
# rename was undone; a class here would mean the craft became a class again.
OFF_WORLD_ROSTER = (
    "эльф",
    "дварф",
    "гном",
    "полурослик",
    "хоббит",
    "орк",
    "гоблин",
    "тролль",
    "ящеролюд",
    "тифлинг",
    "аасимар",
    "драконорождённый",
    "варвар",
    "паладин",
    "друид",
    "жрец",
    "маг",
)


def spoken(content: GameContent) -> Iterator[tuple[str, str]]:
    """Every player-visible string in the content directory, with its source."""
    for race in content.races:
        yield race.id, f"{race.name} {race.description}"
        yield race.id, f"{race.passive.name} {race.passive.text}"
    for klass in content.classes:
        yield klass.id, f"{klass.name} {klass.role} {klass.description} {klass.resource.name}"
    for skill in content.skills:
        yield skill.code, f"{skill.name} {skill.text}"
        for edge in skill.edges:
            yield edge.code, f"{edge.name} {edge.text}"
    for trait in content.traits:
        yield trait.id, f"{trait.name} {trait.text} {' '.join(trait.tags)}"
    for item in content.items:
        yield item.id, f"{item.name} {item.text}"
    for rarity in content.rarities:
        yield rarity.id, rarity.name
    for archetype in content.enemy_archetypes:
        yield archetype.id, archetype.name
    for title in content.elite_titles:
        yield "elite_title", title
    for city in content.cities:
        yield city.id, f"{city.name} {city.description}"
        for location in city.locations:
            yield location.id, location.name
    yield from content.trait_categories.items()


def test_no_content_speaks_the_forbidden_vocabulary(content: GameContent) -> None:
    for source, text in spoken(content):
        lowered = text.casefold()
        for word in FORBIDDEN_WORDS:
            assert word not in lowered, f"{source} says {word!r}"


def test_peoples_and_crafts_are_vellars_own(content: GameContent) -> None:
    """No race or class is named after something off the fantasy shelf."""
    named = [(race.id, race.name) for race in content.races]
    named += [(klass.id, klass.name) for klass in content.classes]
    for source, name in named:
        lowered = name.casefold()
        for species in OFF_WORLD_ROSTER:
            assert species not in lowered, f"{source} is still called {name!r}"


def test_peoples_and_crafts_are_distinct_by_ear(content: GameContent) -> None:
    """Both are picked from a list of buttons, and a button routes by its text."""
    race_names = [race.name for race in content.races]
    class_names = [klass.name for klass in content.classes]
    assert len(set(race_names)) == len(race_names)
    assert len(set(class_names)) == len(class_names)
    assert not set(race_names) & set(class_names)


# One Cyrillic word, nothing else: no apostrophes, no Latin, no invented spelling
# a screen reader would have to spell out letter by letter.
PLAIN_NAME = re.compile(r"^[А-ЯЁ][а-яё]+$")


def test_a_people_carries_a_name_and_the_description_says_where_from(
    content: GameContent,
) -> None:
    for race in content.races:
        assert PLAIN_NAME.fullmatch(race.name), f"{race.id}: {race.name!r} is not one plain word"
        assert len(race.name) <= 12, race.id
        # The name is the people's own; where they live belongs in the line below it.
        assert race.description.endswith("."), race.id
        assert len(race.description) <= 120, race.id
        assert race.name not in race.description, race.id


def test_every_race_keeps_its_frozen_id(content: GameContent) -> None:
    """Renaming is a text change: characters in the database point at these ids."""
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
