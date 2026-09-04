"""Каждая разновидность должна быть названа словами — все до одной.

Экран переводит машинные разновидности в русские слова таблицей. Таблица,
в которой не хватает одной строки, — это не «нет текста», а KeyError посреди
разговора: задание на изготовление роняло экран нанимателя именно так, и заметили
это игроки, а не тесты.
"""

from __future__ import annotations

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.location import EnemyRank, NodeKind
from mmorpg.domain.entities.quest import ObjectiveKind
from mmorpg.domain.rules import combat as combat_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens


def test_every_objective_has_words_for_it() -> None:
    for kind in ObjectiveKind:
        assert kind in quest_screens.OBJECTIVES, kind
        assert kind in quest_screens.HOW, kind


def test_every_node_kind_has_words_for_it() -> None:
    for kind in NodeKind:
        assert kind in play_screens.NODE_ACTIONS, kind
        assert kind in play_screens.NODE_DESCRIPTIONS, kind
        assert kind in node_rules.WAVE_SIZE, kind
    # Двери ничего не держат; всё остальное держит хотя бы одну единицу.
    for kind in NodeKind:
        low, high = node_rules.WAVE_SIZE[kind]
        doors = kind in {NodeKind.ENTRANCE, NodeKind.EXIT}
        assert (low == 0) is doors, kind
        assert low <= high, kind
        if not doors:
            assert kind in play_screens.NODE_COUNT_WORDS, kind


def test_every_tag_and_rank_has_words_for_it() -> None:
    for tag in ActionTag:
        assert tag in combat_screens.TAG_NAMES, tag
    for rank in EnemyRank:
        assert rank in combat_screens.RANK_NAMES, rank


def test_every_tag_has_numbers_for_it() -> None:
    """У стойки должны быть и слова, и числа: движок берёт их без ``get``.

    ``INTENT_ARMOR[intent]`` и ``INTENT_DAMAGE[intent]`` - прямое обращение
    посреди боя, и строка, которой в таблице не хватает, была бы не «стойка без
    эффекта», а KeyError на чужом ходу.
    """
    for tag in ActionTag:
        assert tag in combat_rules.INTENT_ARMOR, tag
        assert tag in combat_rules.INTENT_DAMAGE, tag


def test_every_introduction_task_has_a_card() -> None:
    for task in TutorialTask:
        assert task in tutorial_screens.CARDS, task
