"""Что именно даёт надетое: род оружия, род доспеха и броня, которая считается.

До этого модуля броня росла из одной выносливости, а доспех менял её на
проценты: `armor_percent = 8` на кожаном доспехе — это восемь процентов от
полутора десятков, то есть ничего. Игрок надевал латы и не замечал разницы, и был
прав — разницы не было.

Здесь её заводят тремя правилами.

1. **У доспеха есть своё число брони.** Оно считается не из выносливости, а из
   уровня самой вещи, её рода (``ArmorType.armor``) и того, сколько это место
   вообще прикрывает (``EquipSlot.armor_share``). Абсолютных чисел в содержимом
   при этом не появляется (ADR 0007): вещь объявляет доли, а кривую держит тот же
   смягчитель, против которого броня и работает, — поэтому доспех своего уровня
   стоит одинаково и на первом уровне, и на трёхсотом.
2. **У оружия есть род**, и род решает две вещи: насколько тяжёл удар
   (``WeaponType.damage``) и что оружие даёт само по себе (``modifiers``).
   Единица — это голые руки. Оружие бывает лучше их и не бывает хуже: кинжал,
   который слабее кулака, — не выбор, а ошибка.
3. **Род — это допуск.** Класс носит не всё (``classes.toml``), и умение работает
   не с любым оружием (``skills.toml``): выстрел просит лук, удар в спину —
   кинжал. Отказ здесь всегда со словами, потому что игрок слышит только их.

Модуль чистый: ни времени, ни случая, ни ввода-вывода.
"""

from __future__ import annotations

from collections.abc import Iterable

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item, Skill

# Броня смягчается против уровня того, кого бьют. Обе величины растут с уровнем
# линейно, поэтому несмягчённая броня выиграла бы гонку: на трёхсотом уровне
# обычный удар доходил бы четвертью себя (ADR 0007).
ARMOR_SOFTENER_BASE = 55.0
ARMOR_SOFTENER_PER_LEVEL = 3.2

#: Сколько брони даёт нагрудник своего уровня — долей от смягчителя этого же
#: уровня. Полный тяжёлый доспех выходит примерно вполовину смягчителя, то есть
#: съедает около трети удара сверх того, что держит выносливость.
EQUIPMENT_ARMOR_SHARE = 0.10

#: Удар голыми руками. Отсчёт ведётся от него, и ниже него оружия нет.
UNARMED_DAMAGE = 1.0

WEAPON_SLOT = "weapon"


def armor_softener(level: int) -> float:
    """Против чего работает броня на этом уровне."""
    return ARMOR_SOFTENER_BASE + ARMOR_SOFTENER_PER_LEVEL * level


def armor_factor(armor: float, level: int) -> float:
    """The share of a blow that survives armour, between 0 and 1."""
    softener = armor_softener(level)
    return softener / (softener + max(0.0, armor))


# --- доспех ----------------------------------------------------------


def armor_of(content: GameContent, item: Item) -> int:
    """Сколько брони держит одна надетая вещь.

    Ноль у всего, что бронёй не является: у оружия, у украшения, у расходника — и
    у вещи, чей род доспеха содержимое больше не знает.
    """
    if not item.is_armor or not content.has_armor_type(item.armor_type):
        return 0
    share = content.slot(item.slot).armor_share if content.has_slot(item.slot) else 0.0
    if share <= 0:
        return 0
    kind = content.armor_type(item.armor_type)
    return round(armor_softener(item.level) * EQUIPMENT_ARMOR_SHARE * kind.armor * share)


def worn_armor(content: GameContent, item_ids: Iterable[str]) -> int:
    """Броня всего надетого разом. Вещь, которой больше нет, не даёт ничего."""
    return sum(
        armor_of(content, content.item(item_id))
        for item_id in item_ids
        if content.has_item(item_id)
    )


# --- оружие ----------------------------------------------------------


def weapon_of(content: GameContent, character: Character) -> Item | None:
    """Что у персонажа в руке, или ``None``, если ничего."""
    item_id = character.equipment.item_in(WEAPON_SLOT)
    if item_id is None or not content.has_item(item_id):
        return None
    item = content.item(item_id)
    return item if item.is_weapon else None


def weapon_type_of(content: GameContent, character: Character) -> str:
    """Род оружия в руке. Пусто — рук хватает и без него."""
    weapon = weapon_of(content, character)
    return weapon.weapon_type if weapon is not None else ""


def blow_factor(content: GameContent, character: Character) -> float:
    """Во сколько раз тяжелее обычного стандартный удар этим оружием."""
    type_id = weapon_type_of(content, character)
    if not type_id or not content.has_weapon_type(type_id):
        return UNARMED_DAMAGE
    return content.weapon_type(type_id).damage


def type_modifiers(content: GameContent, item_ids: Iterable[str]) -> dict[str, float]:
    """Прибавки, которые надетое даёт не собой, а своим родом.

    Кинжал прибавляет прыть, латы её отнимают — и это не свойство конкретного
    клинка, а свойство кинжалов вообще, поэтому написано оно один раз в
    ``items.toml [meta]``, а не в каждой из сотни вещей.
    """
    total: dict[str, float] = {}
    for item_id in item_ids:
        if not content.has_item(item_id):
            continue
        item = content.item(item_id)
        bundle: dict[str, float] = {}
        if item.is_weapon and content.has_weapon_type(item.weapon_type):
            bundle = dict(content.weapon_type(item.weapon_type).modifiers)
        elif item.is_armor and content.has_armor_type(item.armor_type):
            bundle = dict(content.armor_type(item.armor_type).modifiers)
        for key, value in bundle.items():
            total[key] = total.get(key, 0.0) + value
    return total


# --- допуски ---------------------------------------------------------


def equip_refusal(content: GameContent, character: Character, item: Item) -> str:
    """Почему эту вещь надеть нельзя — словами. Пусто, если можно.

    Класс, у которого списки пусты, носит всё: содержимое переживает код, и
    класс, заведённый до этих списков, не должен оказаться голым.
    """
    if not item.is_equipment:
        return ""
    klass = content.character_class(character.class_id)
    if item.is_weapon and not klass.can_wield(item.weapon_type):
        kind = _weapon_name(content, item.weapon_type)
        return f"{item.name} — это {kind}, а {klass.name.lower()} таким не дерётся."
    if item.is_armor and not klass.can_wear(item.armor_type):
        kind = _armor_name(content, item.armor_type)
        return f"{item.name} — {kind}, а {klass.name.lower()} такого не носит."
    return ""


def skill_refusal(content: GameContent, character: Character, skill: Skill) -> str:
    """Почему это умение сейчас не сработает — словами. Пусто, если сработает."""
    if not skill.weapon_types:
        return ""
    held = weapon_type_of(content, character)
    if held in skill.weapon_types:
        return ""
    wanted = ", ".join(_weapon_name(content, type_id) for type_id in skill.weapon_types)
    if not held:
        return f"{skill.name} просит другое оружие: {wanted}. В руках ничего."
    return f"{skill.name} просит другое оружие: {wanted}. В руках — {_weapon_name(content, held)}."


def _weapon_name(content: GameContent, type_id: str) -> str:
    if not content.has_weapon_type(type_id):
        return type_id
    return content.weapon_type(type_id).name.lower()


def _armor_name(content: GameContent, type_id: str) -> str:
    if not content.has_armor_type(type_id):
        return type_id
    return content.armor_type(type_id).name.lower()
