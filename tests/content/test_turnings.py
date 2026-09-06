"""Ступени нового имени в содержимом (ADR 0070).

Файл `content/turnings.toml` держит три ступени, на которых уровень кончается и
начинается заново. Проверяется то, что загрузчик обязан ловить на старте: ступень,
которая ничего не даёт, разорванный порядок номеров и список открываемого,
разошедшийся с тем, что просят сами подклассы.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content.loader import ContentError, load_content
from tests.conftest import CONTENT_ROOT


def test_the_book_holds_three_steps(content: GameContent) -> None:
    """Три имени: на семьдесят пятом, на сотом и на сто пятидесятом."""
    assert len(content.rebirths) == 3
    assert [one.rank for one in content.rebirths] == [1, 2, 3]
    assert [one.level for one in content.rebirths] == [75, 100, 150]


def test_every_step_says_what_it_gives(content: GameContent) -> None:
    """Уход стирает полторы сотни уровней: молча этого делать нельзя."""
    for step in content.rebirths:
        assert step.name, step.id
        assert step.text, step.id
        assert step.stat_bonus > 0, step.id
        assert step.stat_points > 0, step.id


def test_each_step_is_worth_more_than_the_one_before(content: GameContent) -> None:
    for earlier, later in itertools.pairwise(content.rebirths):
        assert later.level > earlier.level
        assert later.stat_bonus > earlier.stat_bonus
        assert later.legacy_slots >= earlier.legacy_slots


def test_there_is_a_title_for_every_step(content: GameContent) -> None:
    """Титул — то немногое, что игрок носит от прошлой жизни."""
    assert len(content.rebirth_titles) >= len(content.rebirths)
    assert all(content.rebirth_titles)


def test_the_unlock_list_is_checked_against_the_subclasses(content: GameContent) -> None:
    """Два места говорят об одном и не расходятся.

    Список повторяет ``gate.remorts`` нарочно — чтобы игрок прочитал цену вместе с
    покупкой, — и ровно поэтому сверяется загрузчиком.
    """
    for step in content.rebirths:
        promised = {one.id for one in content.subclasses if one.gate.remorts == step.rank}
        assert set(step.unlocks) == promised, step.id
        for subclass_id in step.unlocks:
            assert content.has_subclass(subclass_id)


def test_a_step_that_gives_nothing_is_refused(tmp_path: Path) -> None:
    """Ступень без прибавки — это кнопка, стирающая дорогу даром."""
    broken = _copied(tmp_path)
    text = (broken / "turnings.toml").read_text(encoding="utf-8")
    (broken / "turnings.toml").write_text(
        text.replace("stat_bonus = 20", "stat_bonus = 0", 1), encoding="utf-8"
    )
    with pytest.raises(ContentError) as raised:
        load_content(broken)
    assert any("gives nothing" in problem for problem in raised.value.problems)


def test_a_lying_unlock_list_is_refused(tmp_path: Path) -> None:
    """Список, обещающий ступень, которая просит другого числа уходов, не проходит."""
    broken = _copied(tmp_path)
    text = (broken / "turnings.toml").read_text(encoding="utf-8")
    (broken / "turnings.toml").write_text(
        text.replace('    "warrior_ironsworn",\n', "", 1), encoding="utf-8"
    )
    with pytest.raises(ContentError) as raised:
        load_content(broken)
    assert any("forgets to name" in problem for problem in raised.value.problems)


def _copied(tmp_path: Path) -> Path:
    """Копия каталога содержимого, которую можно портить."""
    target = tmp_path / "content"
    shutil.copytree(CONTENT_ROOT, target)
    return target
