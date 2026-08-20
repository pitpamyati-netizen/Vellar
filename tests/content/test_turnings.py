"""Голосования Палаты: каждое спрашивает и каждое даёт из чего выбрать."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content.loader import ContentError, load_content


def test_every_turning_asks_and_offers(content: GameContent) -> None:
    assert content.turnings, "без вопросов эндгейму не о чем считать"
    for turning in content.turnings:
        assert turning.question, turning.id
        assert len(turning.options) >= 2, turning.id
        assert all(option.name for option in turning.options), turning.id


def test_exactly_one_question_is_open(content: GameContent) -> None:
    """Игрок отвечает на один вопрос, а не на три сразу."""
    open_now = content.open_turning()
    assert open_now is not None
    assert open_now.id == content.open_turning_id


def test_an_open_question_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """Имя, за которым ничего нет, ловится на старте, а не у игрока на экране."""
    source = Path("content")
    for name in source.glob("*.toml"):
        (tmp_path / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    broken = (tmp_path / "turnings.toml").read_text(encoding="utf-8")
    (tmp_path / "turnings.toml").write_text(
        broken.replace('open = "toll"', 'open = "нет такого"'), encoding="utf-8"
    )

    with pytest.raises(ContentError) as failure:
        load_content(tmp_path)
    assert "turnings.toml" in str(failure.value)
