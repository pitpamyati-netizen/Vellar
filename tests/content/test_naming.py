"""Everything the player hears is named in the language of Vellar.

The black list from ``Narrative.md``, section 2, lives here: one word from it
turns a name into the generic fantasy set the world is written against. Races
and classes are the exception the section names - they are read out on the
creation screen and have to be recognised by ear, so they carry the familiar
words. What they must never do is drift into a craft: making things for pay is
``content/crafts.toml`` and nothing else (Roadmap 1.7).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from mmorpg.domain.entities import GameContent
from tests.content.conftest import FORBIDDEN_WORDS

# A class is how the adventurer fights; a craft is what they make for pay. A
# craft name on either creation axis means the two have been mixed up again.
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
    """Every player-visible string in the content directory, with its source."""
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
        for edge in skill.edges:
            yield edge.code, f"{edge.name} {edge.text}"
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


def test_neither_axis_is_named_after_a_craft(content: GameContent) -> None:
    """A race says what you are, a class says how you fight - neither is a job."""
    named = [(race.id, race.name) for race in content.races]
    named += [(klass.id, klass.name) for klass in content.classes]
    for source, name in named:
        lowered = name.casefold()
        for craft in CRAFT_WORDS:
            assert craft not in lowered, f"{source} is named after a craft: {name!r}"


def test_races_and_classes_are_distinct_by_ear(content: GameContent) -> None:
    """Both are picked from a list of buttons, and a button routes by its text."""
    race_names = [race.name for race in content.races]
    class_names = [klass.name for klass in content.classes]
    assert len(set(race_names)) == len(race_names)
    assert len(set(class_names)) == len(class_names)
    assert not set(race_names) & set(class_names)


# Cyrillic words and nothing else: no apostrophes, no Latin, no invented spelling
# a screen reader would have to spell out letter by letter.
PLAIN_NAME = re.compile(r"^[А-ЯЁ][а-яё]+(?: [а-яё]+)?$")


def test_a_race_is_named_plainly(content: GameContent) -> None:
    for race in content.races:
        assert PLAIN_NAME.fullmatch(race.name), f"{race.id}: {race.name!r} is not plain Russian"
        assert len(race.name) <= 20, race.id
        # What the race does to a person is the line under the name, and it is
        # read out in full on the details screen.
        assert race.description.endswith("."), race.id
        assert len(race.description) <= 120, race.id


def test_a_class_is_one_word(content: GameContent) -> None:
    """The class button carries the role after the name, so the name stays short."""
    for klass in content.classes:
        assert PLAIN_NAME.fullmatch(klass.name), klass.id
        assert " " not in klass.name, f"{klass.id}: {klass.name!r} is more than one word"


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
