"""Что именно даёт надетое: кости оружия, броня доспеха и цена чужой вещи.

До этого модуля броня росла из одной выносливости, а доспех менял её на проценты:
`armor_percent = 8` на кожаном доспехе — это восемь процентов от полутора
десятков, то есть ничего. Игрок надевал латы и не замечал разницы, и был прав.

Здесь её заводят четырьмя правилами.

1. **У оружия есть кости.** Урон — не доля чего-то, а число в границах: «от 2 до
   124». Кости берутся у рода (``WeaponType.dice``) и растут гранями со ступенью
   вещи (``domain/procgen/items.py``). Голыми руками бьют тоже костями, только
   маленькими, — и это единственная разница между кулаком и мечом первой ступени.
2. **У доспеха есть своё число брони**, посчитанное из ступени вещи, её рода и
   того, сколько это место вообще прикрывает.
3. **Редкость даёт характеристики, и только она.** Числа лежат на самой вещи
   (``Item.stat_bonuses``), а не выводятся из процентов.
4. **Чужая вещь не запрещена — она дорога.** Класс носит своё (``classes.toml``),
   но надеть можно что угодно: за не своё оружие и не свой доспех платят
   точностью и инициативой. Запрет молчит, а штраф говорит, и говорит числом.

Умение — отдельный разговор: оно может просить род оружия (``skills.toml``), и без
него не срабатывает вовсе. Выстрел без лука — это не «хуже», это «нечем».

Модуль чистый: ни времени, ни случая, ни ввода-вывода.
"""

from __future__ import annotations

from collections.abc import Iterable

from mmorpg.domain.entities.character import Character, Equipment
from mmorpg.domain.entities.content import GameContent, Item, Skill
from mmorpg.domain.entities.damage import UNARMED, DamageType
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.procgen import items as gear_procgen

# Броня смягчается против уровня того, кого бьют. Обе величины растут с уровнем
# линейно, поэтому несмягчённая броня выиграла бы гонку: на трёхсотом уровне
# обычный удар доходил бы четвертью себя (ADR 0007).
ARMOR_SOFTENER_BASE = 55.0
ARMOR_SOFTENER_PER_LEVEL = 3.2

#: Чем бьют, когда в руках ничего. Кости растут с уровнем героя так же, как у
#: оружия со ступенью, — иначе на сотом уровне безоружный не бил бы вовсе. Но и
#: близко не оружие: на первом уровне это в среднем 3 против 7 у меча.
UNARMED_DICE = Dice(count=1, faces=3, bonus=1)

#: Кулак бьёт слабо, но ровнее всякого оружия: размаха у него меньше, чем у
#: самого ровного меча.
UNARMED_SPREAD = 1.15

WEAPON_SLOT = "weapon"

#: Чего стоит чужое оружие. Не запрет: игрок волен взять что угодно, но держит
#: он его хуже, и это видно в двух числах, которые он и так читает на экране
#: характеристик: точность и инициатива. Прытью инициативу здесь не называют —
#: Прыть это запас разбойника (``classes.toml``), и путать их нельзя.
FOREIGN_WEAPON_ACCURACY = -20.0
FOREIGN_WEAPON_INITIATIVE = -15.0
#: И чего стоит каждая часть чужого доспеха. Полный чужой доспех — это четыре
#: части, то есть вчетверо.
FOREIGN_ARMOR_ACCURACY = -5.0
FOREIGN_ARMOR_INITIATIVE = -8.0


def armor_softener(level: int) -> float:
    """Против чего работает броня на этом уровне."""
    return ARMOR_SOFTENER_BASE + ARMOR_SOFTENER_PER_LEVEL * level


def armor_factor(armor: float, level: int) -> float:
    """Доля удара, пережившая броню, от 0 до 1."""
    softener = armor_softener(level)
    return softener / (softener + max(0.0, armor))


def worn_item(content: GameContent, item_id: str, hero_level: int = 0) -> Item | None:
    """Вещь такой, какая она на этом герое, или ``None``, если её больше нет.

    Реликтовая вещь пересобирается по уровню героя: в этом вся её суть. Всё
    остальное отдаётся как есть.
    """
    if not content.has_item(item_id):
        return None
    return gear_procgen.worn(content, content.item(item_id), hero_level)


# --- доспех ----------------------------------------------------------


def armor_of(content: GameContent, item: Item, hero_level: int = 0) -> int:
    """Сколько брони держит одна надетая вещь."""
    return gear_procgen.worn(content, item, hero_level).armor


def worn_armor(content: GameContent, item_ids: Iterable[str], hero_level: int = 0) -> int:
    """Броня всего надетого разом. Вещь, которой больше нет, не даёт ничего."""
    total = 0
    for item_id in item_ids:
        item = worn_item(content, item_id, hero_level)
        if item is not None:
            total += item.armor
    return total


# --- оружие ----------------------------------------------------------


def weapon_of(content: GameContent, character: Character) -> Item | None:
    """Что у персонажа в руке, или ``None``, если ничего."""
    item_id = character.equipment.item_in(WEAPON_SLOT)
    if item_id is None:
        return None
    item = worn_item(content, item_id, character.level)
    return item if item is not None and item.is_weapon else None


def weapon_type_of(content: GameContent, character: Character) -> str:
    """Род оружия в руке. Пусто — рук хватает и без него."""
    weapon = weapon_of(content, character)
    return weapon.weapon_type if weapon is not None else ""


def weapon_damage_type(content: GameContent, character: Character) -> DamageType:
    """Каким родом урона бьёт этот герой без умения.

    Род урона живёт у рода оружия (``items.toml``, ``damage_type``): копьё колет,
    булава дробит, а голые руки дробят тоже - бьют они костяшками.
    """
    type_id = weapon_type_of(content, character)
    if type_id and content.has_weapon_type(type_id):
        return content.weapon_type(type_id).damage_type
    return UNARMED


def weapon_dice(content: GameContent, character: Character) -> Dice:
    """Чем этот герой бьёт: кости оружия, а без оружия — кости кулака."""
    weapon = weapon_of(content, character)
    if weapon is not None and weapon.damage is not None:
        return weapon.damage
    return UNARMED_DICE.scaled(
        1.0 + gear_procgen.FACES_PER_LEVEL * (character.level - 1), spread=UNARMED_SPREAD
    )


def type_modifiers(content: GameContent, item_ids: Iterable[str]) -> dict[str, float]:
    """Прибавки, которые надетое даёт не собой, а своим родом.

    Кинжал прибавляет инициативу, латы её отнимают — и это не свойство конкретного
    клинка, а свойство кинжалов вообще, поэтому написано оно один раз в
    ``items.toml [meta]``, а не в каждой из тысячи вещей.
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


# --- чужая вещь ------------------------------------------------------


def is_foreign(content: GameContent, character: Character, item: Item) -> bool:
    """Не своё ли это для его класса. Украшение — ничьё, оно подходит всем."""
    if not item.is_equipment:
        return False
    klass = content.character_class(character.class_id)
    if item.is_weapon:
        return not klass.can_wield(item.weapon_type)
    if item.is_armor:
        return not klass.can_wear(item.armor_type)
    return False


def proficiency_penalty(content: GameContent, character: Character) -> dict[str, float]:
    """Чего стоит всё чужое, что сейчас надето, — точностью и инициативой.

    Запрета нет: латы на маге застёгиваются. Просто он в них медленнее и чаще
    мажет, и обе цифры он видит на своём же экране характеристик.
    """
    accuracy = 0.0
    initiative = 0.0
    for item_id in character.equipment.item_ids():
        if not content.has_item(item_id):
            continue
        item = content.item(item_id)
        if not is_foreign(content, character, item):
            continue
        if item.is_weapon:
            accuracy += FOREIGN_WEAPON_ACCURACY
            initiative += FOREIGN_WEAPON_INITIATIVE
        else:
            accuracy += FOREIGN_ARMOR_ACCURACY
            initiative += FOREIGN_ARMOR_INITIATIVE
    if not accuracy and not initiative:
        return {}
    return {"accuracy_percent": accuracy, "initiative_percent": initiative}


def equip_warning(content: GameContent, character: Character, item: Item) -> str:
    """Чем обернётся эта вещь на этом классе. Пусто, если ничем.

    Предупреждение, а не отказ: игрок вправе надеть что угодно и вправе знать
    цену заранее — на карточке, а не потом на экране характеристик.
    """
    if not is_foreign(content, character, item):
        return ""
    klass = content.character_class(character.class_id)
    if item.is_weapon:
        kind = _weapon_name(content, item.weapon_type)
        return (
            f"{item.name} — это {kind}, а {klass.name.lower()} таким драться не учился: "
            f"с ним точность ниже на {abs(int(FOREIGN_WEAPON_ACCURACY))} процентов, "
            f"инициатива — на {abs(int(FOREIGN_WEAPON_INITIATIVE))}."
        )
    kind = _armor_name(content, item.armor_type)
    return (
        f"{item.name} — {kind}, а {klass.name.lower()} в таком не обучен: "
        f"в нём точность ниже на {abs(int(FOREIGN_ARMOR_ACCURACY))} процентов, "
        f"инициатива — на {abs(int(FOREIGN_ARMOR_INITIATIVE))}."
    )


def skill_refusal(content: GameContent, character: Character, skill: Skill) -> str:
    """Почему это умение сейчас не сработает — словами. Пусто, если сработает.

    Здесь отказ, а не штраф, и разница по смыслу: чужим мечом можно махать плохо,
    а выстрелить без лука нечем.
    """
    if not skill.weapon_types:
        return ""
    held = weapon_type_of(content, character)
    if held in skill.weapon_types:
        return ""
    wanted = ", ".join(_weapon_name(content, type_id) for type_id in skill.weapon_types)
    if not held:
        return f"{skill.name} просит другое оружие: {wanted}. В руках ничего."
    return f"{skill.name} просит другое оружие: {wanted}. В руках — {_weapon_name(content, held)}."


def fill_gear(
    content: GameContent, class_id: str, base: Equipment, slots: Iterable[str]
) -> Equipment:
    """Дозаполнить названные слоты комплектом класса первой ступени.

    Трогает только пустые слоты ``base``. Вещь — первой ступени, обычной редкости,
    то есть без единой прибавки: род оружия — первый из списка класса, род доспеха
    — самый крепкий, какой класс носит (``classes.toml``). Это начало дороги, а не
    подарок: тем же собирается стартовый набор при создании персонажа и
    completion-набор обучения (ADR 0038).
    """
    if not content.gear_tiers:
        return base
    klass = content.character_class(class_id)
    first = content.gear_tiers[0].level
    by_kind = {
        (archetype.slot, archetype.weapon_type or archetype.armor_type): archetype
        for archetype in content.gear_archetypes
    }
    weapon_kind = klass.weapon_types[0] if klass.weapon_types else ""
    armor_kind = klass.armor_types[-1] if klass.armor_types else ""

    equipment = base
    for slot in slots:
        if equipment.item_in(slot) is not None:
            continue
        kind = weapon_kind if slot == WEAPON_SLOT else armor_kind
        archetype = by_kind.get((slot, kind))
        if archetype is None:
            continue
        item_id = gear_procgen.gear_id(archetype.id, first, "common")
        if content.has_item(item_id):
            equipment = equipment.equip(slot, item_id)
    return equipment


def _weapon_name(content: GameContent, type_id: str) -> str:
    if not content.has_weapon_type(type_id):
        return type_id
    return content.weapon_type(type_id).name.lower()


def _armor_name(content: GameContent, type_id: str) -> str:
    if not content.has_armor_type(type_id):
        return type_id
    return content.armor_type(type_id).name.lower()
