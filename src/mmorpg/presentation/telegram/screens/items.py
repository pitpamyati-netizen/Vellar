"""Карточка вещи: что она даёт, чем отличается от надетого и что с ней делать.

Раньше вещь была только строкой в списке. В лавке она называла цену и молчала о
том, что даст; в сумке нажатие сразу надевало её или выпивало, а материал в ответ
на нажатие просто читал своё описание — и выходило, что про волчью шкуру игра
ничего сказать не может.

Теперь нажатие на вещь открывает её карточку — один экран, на котором:

- что это, какого уровня и какой редкости;
- какого рода: меч это или кинжал, кожа это или латы, чем он бьёт и сколько на
  нём брони — числами, а не долями;
- **чего вещь будет стоить вашему классу**: чужое не запрещено, но за него платят
  точностью и инициативой, и услышать цену нужно до нажатия, а не после;
- что она меняет, каждая строка словами: «урон плюс 5 процентов»;
- **чем она отличается от того, что уже надето** в тот же слот, — вот ради
  этой строки экран и заведён;
- что с ней можно сделать здесь и сейчас: надеть, выпить, купить, продать.

Ничего из этого не зависит от цвета, значка или таблицы (правила доступности 5-7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.rules import equipment as gear
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import gold as gold_words
from mmorpg.presentation.telegram.screens.format import percent

#: Как называется каждый ключ модификатора по-русски. Ключи объявлены в
#: ``traits.toml [meta].modifier_keys``; вещь, умение и особенность говорят на
#: одном языке, поэтому и словарь один.
MODIFIER_NAMES: dict[str, str] = {
    "stat_STR": "сила",
    "stat_AGI": "ловкость",
    "stat_END": "выносливость",
    "stat_INT": "интеллект",
    "stat_WIS": "мудрость",
    "stat_CHA": "харизма",
    "stat_LCK": "удача",
    "damage_percent": "урон",
    "physical_damage_percent": "физический урон",
    "magic_damage_percent": "магический урон",
    "damage_taken_percent": "получаемый урон",
    "armor_percent": "броня",
    "health_percent": "здоровье",
    "accuracy_percent": "точность",
    "dodge_percent": "уклонение",
    "initiative_percent": "инициатива",
    "crit_chance_percent": "шанс критического удара",
    "crit_damage_percent": "критический урон",
    "resource_percent": "запас ресурса",
    "resource_regen_percent": "восстановление ресурса",
    "cost_reduction_percent": "цена умений",
    "cooldown_reduction_percent": "откаты умений",
    "regen_per_turn_percent": "восстановление за ход",
    "healing_done_percent": "ваше лечение",
    "healing_taken_percent": "лечение вас",
    "resist_fire_percent": "сопротивление огню",
    "resist_cold_percent": "сопротивление холоду",
    "resist_poison_percent": "сопротивление яду",
    "resist_magic_percent": "сопротивление магии",
    "resist_physical_percent": "сопротивление железу",
    "exp_percent": "опыт",
    "gold_percent": "золото с боёв",
    "shop_price_percent": "цены в лавке",
    "sell_price_percent": "цена скупки",
    "drop_rate_percent": "шанс добычи",
    "rarity_percent": "редкость добычи",
    "quest_reward_percent": "плата за задания",
    "event_reward_percent": "находки на событиях",
    "craft_quality_percent": "качество изделий",
    "gather_yield_percent": "сбор сырья",
    "salvage_yield_percent": "разбор на части",
    "reflect_percent": "отражённый урон",
    "dot_damage_percent": "урон ядов и горения",
    "ambush_chance_percent": "шанс попасть в засаду",
    "flee_chance_percent": "шанс сбежать",
    "trap_damage_percent": "урон ловушек",
    "first_turn_damage_percent": "урон в первый ход",
    "single_target_damage_percent": "урон по одной цели",
    "aoe_damage_percent": "урон по всем",
    "low_health_damage_percent": "урон на низком здоровье",
    "wounded_target_damage_percent": "урон по раненым",
    "elite_damage_percent": "урон по эпическим",
    "beast_damage_percent": "урон по зверям",
    "undead_damage_percent": "урон по мертвякам",
    "humanoid_damage_percent": "урон по людям",
    "reputation_percent": "доброе имя",
}

#: Ключи, которые называют число, а не проценты: прибавка к характеристике.
FLAT_PREFIX = "stat_"

#: Характеристики по кодам — так их называет вещь, когда прибавляет числом.
STAT_NAMES: dict[str, str] = {
    "STR": "сила",
    "AGI": "ловкость",
    "END": "выносливость",
    "INT": "интеллект",
    "WIS": "мудрость",
    "CHA": "харизма",
    "LCK": "удача",
}

# Слоты снаряжения в порядке, в котором их зачитывают. Идентификаторы - ключи
# содержимого, названия - то, что слышит игрок.
SLOT_NAMES: dict[str, str] = {
    "weapon": "Оружие",
    "head": "Голова",
    "body": "Тело",
    "hands": "Руки",
    "feet": "Ноги",
    "trinket": "Украшение",
}

EQUIP = label("Надеть", "🛡")
USE = label("Использовать", "🧪")
BUY = label("Купить", "🛒")
SELL_ONE = label("Продать одну", "💱")


def modifier_line(content: GameContent, key: str, value: float) -> str:
    """Одна строка «что даёт»: название, знак и величина — словами."""
    name = MODIFIER_NAMES.get(key, key)
    if key.startswith(FLAT_PREFIX):
        sign = "плюс" if value > 0 else "минус"
        return f"{name} {sign} {abs(int(value))}"
    sign = "плюс" if value > 0 else "минус"
    tail = "" if content.is_bonus(key, value) else ", это хуже"
    return f"{name} {sign} {percent(abs(value))}{tail}"


def effect_line(item: Item) -> str:
    """Что делает расходник. Пусто, если он ничего не делает."""
    if item.effect is None:
        return ""
    match item.effect.kind:
        case "heal_flat":
            return f"Восстанавливает {round(item.effect.power)} здоровья."
        case "heal_percent":
            return f"Восстанавливает {percent(item.effect.power)} здоровья."
        case _:
            turns = item.effect.turns
            span = f" на {turns} хода" if turns else ""
            return f"Даёт усиление{span}: {percent(item.effect.power)}."


def gives_lines(content: GameContent, item: Item) -> tuple[str, ...]:
    """Что вещь даёт — по строке на каждое изменение."""
    lines = [modifier_line(content, key, value) for key, value in item.modifiers.items() if value]
    if not lines:
        return ()
    return (f"Даёт: {'; '.join(lines)}.",)


def kind_lines(content: GameContent, character: Character, item: Item) -> tuple[str, ...]:
    """Какого рода эта вещь, что она бьёт и сколько на ней брони.

    Урон называется границами, а не средним: «от 2 до 124» — это то, что
    случится, а «63» — то, чего не случится никогда. Броня называется числом, а
    не процентом: процент от выносливости и был тем доспехом, который ничего не
    менял.
    """
    worn = gear.worn_item(content, item.id, character.level) or item
    lines: list[str] = []
    if worn.is_weapon and content.has_weapon_type(worn.weapon_type):
        weapon = content.weapon_type(worn.weapon_type)
        lines.append(f"Род оружия: {weapon.name.lower()}.")
        if worn.damage is not None:
            lines.append(f"Урон: {worn.damage.spoken()}.")
        lines.extend(_type_gives("Всякое такое оружие", content, weapon.modifiers))
    if worn.is_armor and content.has_armor_type(worn.armor_type):
        armor = content.armor_type(worn.armor_type)
        lines.append(f"Род доспеха: {armor.name.lower()}. Броня: {worn.armor}.")
        lines.extend(_type_gives("Всякий такой доспех", content, armor.modifiers))
    if worn.stat_bonuses:
        given = "; ".join(
            f"{STAT_NAMES.get(code, code)} плюс {value}"
            for code, value in sorted(worn.stat_bonuses.items())
        )
        lines.append(f"Прибавка от редкости: {given}.")
    if content.has_rarity(worn.rarity) and content.rarity(worn.rarity).scaling:
        lines.append("Реликтовая вещь: её числа растут вместе с вашим уровнем.")
    return tuple(lines)


def _type_gives(
    whose: str, content: GameContent, modifiers: Mapping[str, float]
) -> tuple[str, ...]:
    """Что даёт род сам по себе — одной строкой или ни одной."""
    changes = [modifier_line(content, key, value) for key, value in modifiers.items() if value]
    return (f"{whose} даёт: {'; '.join(changes)}.",) if changes else ()


def comparison_lines(content: GameContent, character: Character, item: Item) -> tuple[str, ...]:
    """Чем эта вещь отличается от надетой в тот же слот.

    Сравнение печатается само: игрок не должен держать в голове цифры снятой
    вещи, чтобы понять, стоит ли надевать новую. Разница считается по каждому
    ключу обеих вещей, а не только новой, — иначе потеря молча пропадёт.
    """
    if not item.is_equipment:
        return ()
    worn_id = character.equipment.item_in(item.slot)
    if worn_id is None or not content.has_item(worn_id):
        return ("В этом слоте сейчас пусто, сравнивать не с чем.",)
    if worn_id == item.id:
        return ("Эта вещь на вас и надета.",)

    worn = gear.worn_item(content, worn_id, character.level) or content.item(worn_id)
    item = gear.worn_item(content, item.id, character.level) or item
    keys = sorted(set(item.modifiers) | set(worn.modifiers))
    changes = [
        modifier_line(content, key, item.modifiers.get(key, 0.0) - worn.modifiers.get(key, 0.0))
        for key in keys
        if item.modifiers.get(key, 0.0) != worn.modifiers.get(key, 0.0)
    ]
    # Урон, броня и характеристики вещь даёт не прибавкой, а собой, поэтому в
    # разницу они попадают отдельными строками и числами.
    for code in sorted(set(item.stat_bonuses) | set(worn.stat_bonuses)):
        shift = item.stat_bonuses.get(code, 0) - worn.stat_bonuses.get(code, 0)
        if shift:
            sign = "плюс" if shift > 0 else "минус"
            changes.insert(0, f"{STAT_NAMES.get(code, code)} {sign} {abs(shift)}")
    armor_change = item.armor - worn.armor
    if armor_change:
        sign = "плюс" if armor_change > 0 else "минус"
        changes.insert(0, f"броня доспеха {sign} {abs(armor_change)}")
    if item.damage is not None or worn.damage is not None:
        was = worn.damage.spoken() if worn.damage else "ничего"
        now = item.damage.spoken() if item.damage else "ничего"
        if was != now:
            changes.insert(0, f"урон был {was}, станет {now}")
    lines = [f"Сейчас надето: {worn.name}, уровень {worn.level}."]
    if changes:
        lines.append(f"Если надеть эту, разница: {'; '.join(changes)}.")
    else:
        lines.append("Разницы в числах нет.")
    return tuple(lines)


def card_lines(
    content: GameContent,
    character: Character,
    item: Item,
    *,
    quantity: int = 0,
    price: int = 0,
    sale: int = 0,
    notice: str = "",
) -> tuple[str, ...]:
    """Общее тело карточки. Первая строка отвечает «что это»."""
    where = SLOT_NAMES.get(item.slot, "")
    head = f"{item.name}. {content.rarity(item.rarity).name}, уровень {item.level}."
    lines = [notice or head]
    if notice:
        lines.append(head)
    if item.is_equipment and where:
        lines.append(f"Слот: {where}.")
    # Предупреждение идёт сразу за слотом, а не в конце карточки: услышать, чего
    # эта вещь будет стоить, нужно прежде, чем дослушивать, что она даёт.
    if warning := gear.equip_warning(content, character, item):
        lines.append(warning)
    lines.extend(kind_lines(content, character, item))
    lines.extend(gives_lines(content, item))
    if effect := effect_line(item):
        lines.append(effect)
    lines.extend(comparison_lines(content, character, item))
    if quantity:
        lines.append(f"В сумке: {quantity}.")
    if price:
        lines.append(f"Цена в лавке: {gold_words(price)}.")
    if sale:
        lines.append(f"Скупщик даст: {gold_words(sale)}.")
    return tuple(line for line in lines if line)


def item_screen(
    content: GameContent,
    character: Character,
    item: Item,
    *,
    quantity: int,
    sale: int = 0,
    notice: str = "",
) -> Screen:
    """Карточка вещи из сумки: всё о ней и всё, что с ней можно сделать."""
    lines = list(card_lines(content, character, item, quantity=quantity, sale=sale, notice=notice))
    rows: list[tuple[Label, ...]] = []
    if item.is_equipment:
        # Кнопка есть всегда: чужую вещь надеть можно, и запрещать это игре не
        # положено. Что она будет стоить, сказано строкой выше — предупреждением,
        # а не отказом.
        rows.append((EQUIP,))
    elif item.kind.value == "consumable":
        rows.append((USE,))
    else:
        lines.append("Сырьё: идёт в дело у ремесленника и в скупку у лавочника.")
    return Screen(id=ScreenId.ITEM, lines=tuple(lines), rows=tuple(rows))


def shop_item_screen(
    content: GameContent,
    character: Character,
    item: Item,
    *,
    price: int,
    gold: int,
    notice: str = "",
) -> Screen:
    """Карточка товара на прилавке: то же самое плюс цена и кнопка «Купить»."""
    lines = list(card_lines(content, character, item, price=price, notice=notice))
    lines.append(
        f"У вас {gold_words(gold)}."
        if price <= gold
        else f"У вас {gold_words(gold)}, не хватает {price - gold}."
    )
    rows: Sequence[tuple[Label, ...]] = ((BUY,),) if price <= gold else ()
    return Screen(id=ScreenId.SHOP_ITEM, lines=tuple(lines), rows=tuple(rows))
