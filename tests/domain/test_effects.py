"""Стопка эффектов: ключ - идентификатор, и эффект обновляется, а не складывается."""

from __future__ import annotations

from mmorpg.domain.entities import ActiveEffect, EffectStack


def buff(effect_id: str = "buff", turns: int = 3, **modifiers: float) -> ActiveEffect:
    return ActiveEffect(
        id=effect_id,
        name=effect_id,
        modifiers=modifiers or {"damage_percent": 10.0},
        turns_left=turns,
    )


def debuff(effect_id: str = "curse", turns: int = 3) -> ActiveEffect:
    return ActiveEffect(
        id=effect_id,
        name=effect_id,
        modifiers={"damage_taken_percent": 20.0},
        turns_left=turns,
        beneficial=False,
    )


def test_empty_stack() -> None:
    stack = EffectStack()
    assert len(stack) == 0
    assert stack.modifiers() == {}


def test_apply_adds_an_effect() -> None:
    stack = EffectStack().apply(buff())
    assert len(stack) == 1
    assert "buff" in stack
    assert stack.modifiers() == {"damage_percent": 10.0}


def test_applying_twice_does_not_stack() -> None:
    stack = EffectStack().apply(buff()).apply(buff())
    assert len(stack) == 1
    assert stack.modifiers() == {"damage_percent": 10.0}


def test_applying_twice_keeps_the_longer_duration() -> None:
    stack = EffectStack().apply(buff(turns=5)).apply(buff(turns=2))
    assert next(iter(stack)).turns_left == 5


def test_different_effects_do_stack() -> None:
    stack = EffectStack().apply(buff("rally")).apply(buff("frenzy", damage_percent=25.0))
    assert len(stack) == 2
    assert stack.modifiers()["damage_percent"] == 35.0


def test_tick_expires_effects() -> None:
    stack = EffectStack().apply(buff(turns=1))
    assert len(stack.tick()) == 0


def test_tick_counts_down() -> None:
    stack = EffectStack().apply(buff(turns=3)).tick()
    assert next(iter(stack)).turns_left == 2


def test_cleanse_removes_penalties_only() -> None:
    stack = EffectStack().apply(buff("rally")).apply(debuff("curse"))
    cleansed = stack.cleanse(1)
    assert "curse" not in cleansed
    assert "rally" in cleansed


def test_cleanse_respects_the_count() -> None:
    stack = EffectStack().apply(debuff("a")).apply(debuff("b")).apply(debuff("c"))
    assert len(stack.cleanse(2).penalties()) == 1


def test_remove_by_id() -> None:
    stack = EffectStack().apply(buff("rally")).apply(buff("frenzy"))
    assert "rally" not in stack.remove("rally")


def test_stack_is_immutable() -> None:
    original = EffectStack().apply(buff("rally"))
    original.apply(buff("frenzy"))
    original.tick()
    original.remove("rally")
    assert len(original) == 1
    assert "rally" in original
