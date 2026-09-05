"""Экраны самой игры: меню, мир, город, локации.

Каждый начинается с того, где игрок, потом идут подробности, потом - что можно
сделать, и в таком порядке, потому что игрок слышит сообщение сверху вниз и
должен иметь возможность перестать слушать, как только узнал достаточно
(правило доступности 4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent, Location
from mmorpg.domain.entities.location import (
    Enemy,
    GeneratedLocation,
    LocationNode,
    NodeKind,
    Presence,
    Roamer,
)
from mmorpg.domain.rules import economy as economy_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.domain.rules.combat import blow_range
from mmorpg.domain.rules.mood import LocationMood
from mmorpg.domain.rules.nodes import Standing
from mmorpg.domain.rules.progression import LevelUp, experience_into_level
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import (
    amount,
    gold,
    head,
    number,
    percent,
    plural,
)
from mmorpg.presentation.telegram.screens.items import SLOT_NAMES
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

# Действия, предлагаемые в узле, по виду узла. Слова говорят, что произойдёт, поэтому
# ничто не зависит ни от значка, ни от цвета.
NODE_ACTIONS: dict[NodeKind, str] = {
    NodeKind.ENTRANCE: "Осмотреть вход",
    NodeKind.BATTLE: "Вступить в бой",
    NodeKind.ELITE_BATTLE: "Вызвать эпического противника",
    NodeKind.BOSS_BATTLE: "Вызвать хозяина логова",
    # «Сырьё», а не «ресурсы»: узел для сбора отдаёт только материалы
    # (``rules/adventure.GATHER_SOURCES``), и ремесло зовёт их тем же словом.
    NodeKind.GATHER: "Собрать сырьё",
    NodeKind.EVENT: "Разобраться с событием",
    NodeKind.CACHE: "Обыскать тайник",
    NodeKind.SHRINE: "Передохнуть у святилища",
    # Отличается от кнопки «Покинуть локацию», которая стоит всегда и ниже: две
    # кнопки одного экрана не вправе делить надпись - маршрутизация идёт по тексту.
    NodeKind.EXIT: "Выйти через узел",
}

# Одна строка на узел, и она же читается в списке соседних узлов - поэтому
# короткая.
NODE_DESCRIPTIONS: dict[NodeKind, str] = {
    NodeKind.ENTRANCE: "Отсюда вы вошли",
    NodeKind.BATTLE: "здесь ждёт противник",
    NodeKind.ELITE_BATTLE: "здесь ждёт эпический противник, бой будет долгим",
    NodeKind.BOSS_BATTLE: "здесь ждёт хозяин логова, бой будет самым долгим",
    NodeKind.GATHER: "здесь берут сырьё",
    NodeKind.EVENT: "здесь что-то стряслось до вас",
    NodeKind.CACHE: "здесь что-то припрятано",
    NodeKind.SHRINE: "здесь переводят дух",
    NodeKind.EXIT: "отсюда можно уйти",
}

# Что считает узел, сказанное словами самой вещи: «Противников: 2 из 3» читается как
# место, в котором что-то есть, а «осталось 2» — как остаток.
#: Каким словом экран называет то, что в узле стоит (ADR 0063). У боя это те,
#: кто ждёт, у жилы - то, что в ней лежит.
WATCH_WORDS: dict[NodeKind, str] = {
    NodeKind.BATTLE: "Здесь ждут",
    NodeKind.ELITE_BATTLE: "Здесь ждёт",
    NodeKind.BOSS_BATTLE: "Здесь ждёт",
    NodeKind.GATHER: "Здесь берут",
}

NODE_COUNT_WORDS: dict[NodeKind, str] = {
    NodeKind.BATTLE: "Противников",
    NodeKind.ELITE_BATTLE: "Эпических противников",
    NodeKind.BOSS_BATTLE: "Хозяев логова",
    NodeKind.GATHER: "Осталось собрать",
    NodeKind.EVENT: "Событий",
    NodeKind.CACHE: "Тайников",
    NodeKind.SHRINE: "Святилищ",
}

#: Узел, о котором экрану ничего не сказали: держит ноль и ничего не обещает.
EMPTY_NODE = Standing(index=0, size=0, left=0, taken=0, wave=0, refill_in=0)

LEAVE_LOCATION = label("Покинуть локацию")


def unequip_label(slot_name: str) -> Label:
    return label(f"Снять: {slot_name}")


def node_button(node: LocationNode) -> Label:
    """Кнопки узлов несут свой номер, поэтому две «Стычки» остаются различимыми."""
    return label(f"Узел {node.index}: {node.name}")


def standing_in(content: GameContent, character: Character) -> City:
    """Город, в котором стоит персонаж, или первый на дороге.

    Смотритель может убрать город правкой, пока в нём кто-то стоит
    (``docs/keeper.md``). Главное меню обязано открыться и тогда (``Claude.md``,
    правило 8).
    """
    if content.has_city(character.city_id):
        return content.city(character.city_id)
    return content.cities[0]


def main_menu_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    city = standing_in(content, character)
    earned, needed = experience_into_level(character.experience)
    progress = (
        f"Опыт: {amount(earned, needed, with_percent=False)} до следующего уровня."
        if needed
        else "Достигнут максимальный уровень."
    )
    lines = [
        *head(f"Главное меню. Вы в городе {city.name}.", notice),
        f"{character.name}, уровень {character.level}, "
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {amount(character.health_or(stats.max_health), stats.max_health)}.",
        f"{stats.resource_name}: {amount(stats.max_resource, stats.max_resource)}.",
        progress,
        f"Золото: {gold(character.gold)}.",
    ]
    if character.unspent_stat_points or character.unspent_skill_points:
        lines.append(
            f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
            f"очков умений {character.unspent_skill_points}."
        )
    rows = [
        (labels.WORLD,),
        (labels.CHARACTER, labels.INVENTORY),
        (labels.SKILLS, labels.QUESTS),
        (labels.CRAFTS, labels.PARTY),
        (labels.GUILD,),
        (labels.SETTINGS,),
    ]
    # Вступление предлагают, пока ему есть что сказать, а потом оно уходит навсегда:
    # вечная кнопка «обучение» отвечает на вопрос, которого больше никто не задаёт.
    from mmorpg.domain.rules import tutorial as tutorial_rules

    if not tutorial_rules.finished(character):
        next_task = tutorial_rules.next_task(character)
        if next_task is not None:
            from mmorpg.presentation.telegram.screens.tutorial import CARDS

            lines.append(f"Обучение: {CARDS[next_task].title.lower()}.")
        rows.append((labels.TUTORIAL,))
    # Служебная дверь, и только для того, кто держит игру: обычный игрок этого ряда не
    # слышит вовсе.
    if character.is_admin:
        rows.append((labels.KEEPER,))
    # Служебного ряда здесь нет, и это единственный экран, где его нет: «Назад» из
    # главного меню вело бы в главное меню (``Claude.md``, правило 9). Обе команды -
    # /назад и /меню - работают.
    return Screen(id=ScreenId.MAIN_MENU, lines=tuple(lines), rows=tuple(rows), service_row=False)


def world_screen(
    content: GameContent, character: Character, state: PageState, notice: str = ""
) -> Screen:
    # Смотритель ходит по всей дороге даром: запрет по уровню и плата за дорогу -
    # правила для игроков.
    here = standing_in(content, character)
    open_cities = (
        content.cities if character.is_admin else content.cities_available_at(character.level)
    )

    def leg(city: City) -> str:
        if city.id == here.id:
            return "вы здесь"
        if character.is_admin:
            return "дорога даром"
        fare = economy_rules.travel_price(character.level, abs(city.order - here.order))
        return f"дорога {gold(fare)}"

    entries = [
        ListEntry(
            key=city.id,
            text=city.name,
            detail=f"уровни с {city.level_min} по {city.level_max}, {leg(city)}",
        )
        for city in open_cities
    ]
    # Города стоят вдоль одной дороги в одном порядке, и каждый открывается на своём
    # уровне. Названный следующий превращает «закрыт: 12» в то, к чему можно стремиться.
    ahead = [city for city in content.cities if city.unlock_level > character.level]
    next_city = min(ahead, key=lambda city: city.unlock_level) if ahead else None
    lead = [
        notice or f"Большая дорога. Вы в городе {here.name}. У вас {gold(character.gold)}.",
        "Города стоят вдоль неё по порядку: чем дальше, тем выше уровни и дороже дорога.",
        "Дорога до другого города стоит золота и проходится сразу.",
    ]
    if next_city is not None:
        lead.append(
            f"Следующий город {next_city.name} откроется на уровне {next_city.unlock_level}. "
            f"Ваш уровень: {character.level}. Закрыто городов: {len(ahead)}."
        )
    else:
        lead.append("Дорога открыта до конца: закрытых городов нет.")
    return paginated_screen(
        screen_id=ScreenId.WORLD,
        title="Мир",
        entries=entries,
        state=state,
        lead_lines=tuple(lead),
        empty_text="Пока не открыт ни один город.",
        show_filters=False,
    )


# Какая кнопка открывает какую объявленную службу. Город, который её не объявил, кнопки
# не показывает - и говорит об этом, если кнопка пришла со старой клавиатуры (правило
# доступности 12).
CITY_SERVICES: tuple[tuple[str, Label], ...] = (
    ("locations", labels.LOCATIONS),
    ("shop", labels.SHOP),
    ("dungeons", labels.DUNGEONS),
    ("arena", labels.ARENA),
    ("chamber", labels.CHAMBER),
    ("house", labels.HOUSE),
    ("tavern", labels.TAVERN),
    ("summary", labels.SUMMARY),
    ("mentor", labels.MENTOR),
    ("bank", labels.BANK),
    ("forge", labels.FORGE),
)


def city_screen(content: GameContent, city: City, character: Character, notice: str = "") -> Screen:
    offered = [item for service, item in CITY_SERVICES if service in city.services]
    # Жители - не услуга города, а люди в нём: кнопка есть там, где кто-то стоит,
    # и нет там, где никого нет. Объявлять их в world.toml незачем - они приходят
    # правкой смотрителя (``docs/keeper.md``).
    if content.npcs_in(city.id):
        offered.append(labels.NPCS)
    rows: list[tuple[Label, ...]] = [
        tuple(offered[index : index + 2]) for index in range(0, len(offered), 2)
    ]
    # Отсюда уходит большая дорога: своим рядом, потому что это выход из города, а не
    # его служба (ADR 0051).
    rows.append((labels.ROAD,))
    return Screen(
        id=ScreenId.CITY,
        lines=(
            *head(f"Город {city.name}.", notice),
            city.description,
            f"Уровни города: с {city.level_min} по {city.level_max}. "
            f"Ваш уровень: {character.level}.",
            "Доступно: " + ", ".join(item.text.lower() for item in offered) + ".",
            "«Дорога» уводит в другой город.",
        ),
        rows=tuple(rows),
    )


def location_list_screen(
    content: GameContent,
    city: City,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    entries = [
        ListEntry(
            key=str(location.slot),
            text=f"{location.slot}. {location.name}",
            detail=_location_fit(location, character.level),
        )
        for location in city.locations
    ]
    return paginated_screen(
        screen_id=ScreenId.LOCATION_LIST,
        title=f"Локации города {city.name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Ваш уровень: {character.level}.",
            "В локации ходят по узлам: бой платит опытом, золотом и добычей, "
            "тайники и заросли — находками. Раны остаются с вами до лекаря.",
            "Локации перекрываются по уровням: всегда есть куда идти и куда вернуться.",
        ),
        show_filters=False,
    )


def _location_fit(location: Location, level: int) -> str:
    """Стоит ли это место дороги прямо сейчас, одной фразой."""
    band = f"уровни с {location.level_min} по {location.level_max}"
    if location.pvp:
        band = f"{band}, вольная: здесь нападают друг на друга"
    if level < location.level_min:
        return f"{band}, вам сюда рано"
    if level > location.level_max:
        return f"{band}, платит мало на вашем уровне"
    return f"{band}, по вашему уровню"


def risk_note(node: LocationNode, character_level: int) -> str:
    """Чем этот бой обернётся - словами, без номера уровня.

    Номер называет сама стая, когда стаи перечислены поимённо (ADR 0065):
    повторять его строкой ниже значит читать одно и то же дважды.
    """
    if not node.kind.is_combat:
        return ""
    gap = node.level - character_level
    if gap <= -3:
        risk = "противник слабее вас, бой короткий"
    elif gap <= 1:
        risk = "противник вам вровень"
    elif gap <= 4:
        risk = "противник сильнее вас, бой опасный"
    else:
        risk = "противник намного сильнее вас, сюда рано"
    tier = {
        NodeKind.ELITE_BATTLE: " Эпический противник держится вдвое дольше обычного.",
        NodeKind.BOSS_BATTLE: " Хозяин логова держится вчетверо дольше обычного.",
    }.get(node.kind, "")
    return f"{risk}.{tier}"


def risk_line(node: LocationNode, character_level: int) -> str:
    """То же самое, но с обоими уровнями: так его слышат в соседних узлах.

    Один номер уровня не говорит игроку ничего: важна разница, её экран и
    называет.
    """
    note = risk_note(node, character_level)
    return f"Уровень узла {node.level}, ваш {character_level}: {note}" if note else ""


def attack_label(name: str) -> Label:
    return label(f"Напасть: {name}")


#: Слово, которым зовут в чужой бой (ADR 0065). Первое и неизменное: по нему
#: ветка узнаёт нажатие, а игрок - кнопку, которую нажимал вчера.
JOIN_FIGHT = "Вмешаться"


@dataclass(frozen=True, slots=True)
class NodeFoe:
    """Одна стая, стоящая в узле, - так её слышит игрок (ADR 0065).

    ``place`` - её место в волне узла, и оно не меняется от того, что соседнюю
    убили: третья стая остаётся третьей. ``fighter`` - имя того, чей бой её уже
    держит; пусто - стая свободна.
    """

    place: int
    line: str
    level: int
    fighter: str = ""

    @property
    def busy(self) -> bool:
        return bool(self.fighter)


def foe_number(foe: NodeFoe, single: bool) -> str:
    """Номер стаи в узле или пустая строка, если стая в нём одна."""
    return "" if single else f" {foe.place + 1}"


def foe_label(kind: NodeKind, foe: NodeFoe, single: bool = False) -> Label:
    """Кнопка боя с одной названной стаей: слово действия, номер, кто это."""
    return label(f"{NODE_ACTIONS[kind]}{foe_number(foe, single)}: {foe.line}")


def join_label(foe: NodeFoe, single: bool = False) -> Label:
    """Кнопка «вмешаться» - в тот бой, что уже идёт за эту стаю (ADR 0065)."""
    return label(f"{JOIN_FIGHT}{foe_number(foe, single)}: {foe.line}")


def foe_line(foe: NodeFoe, single: bool = False) -> str:
    """Строка стаи в узле: кто, какого уровня и свободна ли она."""
    number = "" if single else f"{foe.place + 1}. "
    if foe.busy:
        return f"{number}{foe.line}, уровень {foe.level}: сражается {foe.fighter}."
    return f"{number}{foe.line}, уровень {foe.level}."


def invite_label(name: str) -> Label:
    """Позвать соседа по узлу в отряд.

    Стоит рядом с «Напасть» нарочно: на одном и том же узле с одним и тем же
    человеком можно сделать ровно две вещи, и обе видны сразу
    (``domain/rules/party.py``).
    """
    return label(f"Позвать в отряд: {name}", "🤝")


def pack_line(pack: Sequence[Enemy]) -> str:
    """Стая словами: кто в ней и сколько (ADR 0063).

    Имена складываются, а не перечисляются подряд: «Серый волк, 3 штуки» - это
    одна строка, а три «Серых волка» подряд - три одинаковые строки, которые
    диктор читает трижды.
    """
    counted: dict[str, int] = {}
    for enemy in pack:
        counted[enemy.name] = counted.get(enemy.name, 0) + 1
    return ", ".join(
        name if count == 1 else f"{name}, {count} {plural(count, 'штука', 'штуки', 'штук')}"
        for name, count in counted.items()
    )


def node_action(kind: NodeKind, watch: str = "") -> Label:
    """Кнопка действия узла, назвавшая то, к чему она ведёт.

    Слово действия стоит первым и не меняется: по нему ветка узнаёт нажатие, и
    по нему же игрок узнаёт кнопку, которую нажимал вчера (ADR 0063).
    """
    action = NODE_ACTIONS[kind]
    return label(f"{action}: {watch}" if watch else action)


def node_left_line(standing: Standing, kind: NodeKind) -> str:
    """Сколько тут ещё есть — словами, без псевдографики.

    Узел больше не «пройден кем-то»: он держит волну, из неё берут по одному, и
    пустой узел через несколько минут снова полон (``domain/rules/nodes.py``).
    """
    if kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        return ""
    if standing.empty:
        minutes = max(1, (standing.refill_in + 59) // 60)
        return (
            f"Здесь пусто. Новое появится примерно через {minutes} "
            f"{plural(minutes, 'минуту', 'минуты', 'минут')}."
        )
    word = NODE_COUNT_WORDS[kind]
    return f"{word}: {standing.left} из {standing.size}."


#: Как подземелье описывается на экране, смотря на кого оно рассчитано.
ROAMER_AUDIENCE: dict[bool, str] = {False: "рассчитано на одного", True: "рассчитано на отряд"}

#: Одна строка о том, как звучит округа (``domain/rules/mood.py``, ADR 0055).
#: «Встревожена» здесь не названа: блуждающий ход и без того описан отдельными
#: строками, а вторая о том же — лишняя.
MOOD_LINE: dict[LocationMood, str] = {
    LocationMood.UNTOUCHED: "Округа тихая: давно её как следует не тревожили.",
    LocationMood.WORKED: "По округе ходят: часть узлов уже подчищена.",
    LocationMood.DEPLETED: "Округу выработали, и она переложилась: тропы и места легли заново.",
}


def location_screen(
    location: GeneratedLocation,
    node: LocationNode,
    *,
    standing: Mapping[int, Standing],
    character_level: int = 1,
    others: Sequence[Presence] = (),
    pvp: bool = False,
    roamer: Roamer | None = None,
    mood: LocationMood = LocationMood.UNTOUCHED,
    tool_note: str = "",
    watch: Mapping[int, str] = MappingProxyType({}),
    foes: Sequence[NodeFoe] = (),
    notice: str = "",
) -> Screen:
    """Узел локации: что здесь, кто здесь и куда отсюда.

    ``tool_note`` - одна строка об инструменте, и только у жилы: чем её берут и
    надолго ли этого хватит (``screens/crafts.tool_line``, ADR 0056). Услышать
    отказ нужно до нажатия, а не после.

    ``watch`` - что стоит в узле, названное словами: стая по именам, жила по
    сырью (``flows/play.node_watch``, ADR 0063). Экран не гадает и не хранит:
    и то и другое - чистые функции от сида, и названы они теми же числами, с
    какими их соберёт бой.

    ``foes`` - стаи этого узла поимённо, каждая со своим местом в волне и своей
    кнопкой (ADR 0065). Стая, за которую уже дерутся, названа тем, чей это бой, и
    зовёт не напасть, а вмешаться.
    """
    neighbours = tuple(location.node(index) for index in node.links)
    # Подземелье этого узла, если оно тут и свободно, - только тогда рисуется кнопка.
    roamer_here = roamer if (roamer is not None and roamer.node == node.index) else None
    roamer_open = roamer_here if (roamer_here is not None and not roamer_here.taken) else None

    def left_at(index: int) -> Standing:
        """Что стоит в узле. Про узел, о котором не сказали, экран не падает."""
        return standing.get(index, EMPTY_NODE)

    here = left_at(node.index)
    # Двери не считают никогда: в них ничего не лежит, а счёт заставил бы локацию
    # выглядеть наполовину пустой в ту минуту, как в неё вошли.
    worth_doing = tuple(
        item for item in location.nodes if item.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT}
    )
    busy = sum(1 for item in worth_doing if not left_at(item.index).empty)
    boss = next((item for item in location.nodes if item.kind is NodeKind.BOSS_BATTLE), None)

    lines = list(head(f"Локация {location.name}, узел {node.index}: {node.name}.", notice))
    if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        lines.append(f"{NODE_DESCRIPTIONS[node.kind].capitalize()}.")
        lines.append("Это дверь, а не дело: здесь ничего не найти.")
    elif here.empty:
        # "Здесь есть что собрать" сразу под "здесь пусто" - это две строки,
        # которые спорят друг с другом. Пустой узел говорит только одно.
        lines.append(node_left_line(here, node.kind))
    else:
        lines.append(f"{NODE_DESCRIPTIONS[node.kind].capitalize()}.")
        alone = here.size <= 1
        if foes:
            # Стаи в узле стоят порознь, и каждая называет себя сама: одна строка
            # на стаю, номер держит её место в волне (ADR 0065).
            lines.append(f"{WATCH_WORDS.get(node.kind, 'Здесь')}:")
            lines.extend(foe_line(foe, alone) for foe in foes)
        elif seen := watch.get(node.index, ""):
            lines.append(f"{WATCH_WORDS.get(node.kind, 'Здесь')}: {seen}.")
        risk = (
            f"Ваш уровень {character_level}: {risk_note(node, character_level)}"
            if foes
            else risk_line(node, character_level)
        )
        if risk_note(node, character_level):
            lines.append(risk)
        lines.append(node_left_line(here, node.kind))
        if node.kind is NodeKind.GATHER and tool_note:
            lines.append(tool_note)

    if roamer_open is not None:
        lines.append(
            f"Прямо здесь в земле открылось блуждающее подземелье, "
            f"{ROAMER_AUDIENCE[roamer_open.group]}: тёмный ход вниз, комнаты с "
            "развилками, назад пути нет, в глубине логово."
        )
    elif roamer_here is not None:
        lines.append(
            "Прямо здесь блуждающее подземелье, но в него уже спустились. Дождитесь, пока выйдут."
        )

    mood_line = MOOD_LINE.get(mood)
    if mood_line:
        lines.append(mood_line)

    lines.append(f"Узлов, где ещё что-то есть: {busy} из {len(worth_doing)}.")
    if busy == len(worth_doing):
        # Говорится один раз, в начале вылазки: что это за место и как оно устроено.
        lines.append(
            "Узлы связаны тропами: чем дальше от входа, тем выше уровень и тем "
            "больше платят. Обходить можно что угодно, идти до конца не обязательно."
        )
        lines.append(
            "Кто и что в узлах — заводится заново через несколько минут после "
            "того, как узел вычистили. А когда округу как следует выработают, она "
            "заселяется заново и по-другому: другие тропы, места встают иначе."
        )
    if boss is not None and not left_at(boss.index).empty:
        lines.append(
            f"Хозяин логова стоит в узле {boss.index}, уровень {boss.level}, в "
            "самой глубине. Мимо его логова путь к выходу есть: драться с хозяином "
            "необязательно."
        )
    if roamer is not None and roamer_here is None:
        occupied = " Сейчас туда уже спустились." if roamer.taken else ""
        lines.append(
            f"В сводке этого не было: у узла {roamer.node} открылось блуждающее "
            f"подземелье, уровень {roamer.level}, "
            f"{ROAMER_AUDIENCE[roamer.group]}.{occupied}"
        )

    lines.append("Отсюда ведут тропы:")
    for neighbour in neighbours:
        # Двери не пустеют: у них и не было ничего, и говорить о них "сейчас
        # пусто" значит обещать, что там когда-то что-то будет.
        door = neighbour.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}
        mark = "" if door or not left_at(neighbour.index).empty else ", сейчас пусто"
        # Что там стоит, видно отсюда: соседний узел в одном переходе, и идти в
        # него вслепую значит выбирать наугад (ADR 0063).
        seen = watch.get(neighbour.index, "")
        tail = f" — {seen}" if seen and not mark else ""
        lines.append(
            f"Узел {neighbour.index}: {neighbour.name}, "
            f"{NODE_DESCRIPTIONS[neighbour.kind]}{mark}{tail}."
        )

    # Кто здесь ещё, идёт после выхода и перед соседями: это новость, а не перемещение,
    # и на вольной локации это как раз та новость, которая важна.
    if others:
        lines.append("Здесь же:")
        lines.extend(f"{person.name}, уровень {person.level}." for person in others)
    elif pvp:
        lines.append("Здесь вольная земля: на этом узле могут напасть. Сейчас никого нет.")

    if foes:
        # У каждой стаи своя кнопка: занятая зовёт вмешаться в тот бой, что за
        # неё уже идёт, свободная - начать свой (ADR 0065).
        alone = here.size <= 1
        rows: list[tuple[Label, ...]] = [
            (join_label(foe, alone),) if foe.busy else (foe_label(node.kind, foe, alone),)
            for foe in foes
        ]
    else:
        action = node_action(node.kind, "" if here.empty else watch.get(node.index, ""))
        rows = [(action,)]
    if roamer_open is not None:
        rows.insert(0, (labels.ENTER_ROAMER,))
    for person in others:
        # Позвать можно везде, напасть - только на вольной земле.
        row = (
            (attack_label(person.name), invite_label(person.name))
            if pvp
            else (invite_label(person.name),)
        )
        rows.append(row)
    rows.extend((node_button(neighbour),) for neighbour in neighbours)
    rows.append((LEAVE_LOCATION,))

    return Screen(
        id=ScreenId.LOCATION, lines=tuple(line for line in lines if line), rows=tuple(rows)
    )


def _wear_note(content: GameContent, character: Character, item_id: str) -> str:
    """Прочность надетой вещи, если о ней есть что сказать (ADR 0057).

    Целая вещь молчит: строка «прочность 40 из 40» на каждом слоте - это шесть
    строк ни о чём. Сточенная говорит числом, сломанная - словом, потому что
    сломанная не даёт ничего.
    """
    item = repair_rules.worn_of(content, character, item_id)
    if item is None:
        return ""
    if repair_rules.is_broken(character, item):
        return ", сломана и не даёт ничего"
    left = repair_rules.left(character, item)
    limit = repair_rules.limit(item)
    if left >= limit:
        return ""
    return f", прочность {amount(left, limit)}"


def character_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        *head(f"Персонаж {character.name}, уровень {character.level}.", notice),
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {character.health_or(stats.max_health)} из {stats.max_health}. "
        f"{stats.resource_name}: {stats.max_resource}.",
        f"Удар: {damage_line(content, character)}.",
        *derived_lines(stats),
    ]
    lines.extend(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items())
    if character.trait_ids:
        names = ", ".join(content.trait(trait_id).name for trait_id in character.trait_ids)
        lines.append(f"Особенности: {names}.")

    worn: list[tuple[Label, ...]] = []
    for slot, slot_name in SLOT_NAMES.items():
        item_id = character.equipment.item_in(slot)
        if item_id is None or not content.has_item(item_id):
            continue
        note = _wear_note(content, character, item_id)
        lines.append(f"{slot_name}: {content.item(item_id).name}{note}.")
        worn.append((unequip_label(slot_name),))
    if not worn:
        lines.append("Ничего не надето. Снаряжение надевается из инвентаря.")

    if character.unspent_stat_points or character.unspent_skill_points:
        lines.append(
            f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
            f"очков умений {character.unspent_skill_points}."
        )
    if character.unspent_stat_points:
        lines.append("Очки характеристик вкладываются в разделе «Характеристики».")
    return Screen(
        id=ScreenId.CHARACTER,
        lines=tuple(lines),
        rows=((labels.STATS, labels.SKILLS), *worn),
    )


def level_up_report(
    content: GameContent, character: Character, stats: DerivedStats, level_up: LevelUp
) -> str:
    """Всё, что принёс новый уровень, - отдельным сообщением.

    Единственное место, где одно действие отвечает двумя сообщениями
    (``docs/accessibility.md``, правило 3): строкой между добычей и здоровьем
    уровень тонул. Клавиатура при этом та же, что и на экране боя, так что игрок
    никуда не уходит.
    """
    previous = level_up.previous_level
    new = level_up.new_level
    lines = [
        f"Новый уровень: {new}."
        if level_up.levels_gained == 1
        else f"Взято уровней: {level_up.levels_gained}. Теперь {new}.",
        f"Очков характеристик: плюс {level_up.stat_points}, "
        f"нераспределено {character.unspent_stat_points}.",
        f"Очков умений: плюс {level_up.skill_points}, "
        f"нераспределено {character.unspent_skill_points}.",
        f"Здоровье: {stats.max_health}. {stats.resource_name}: {stats.max_resource}.",
    ]

    opened = [
        skill.name
        for skill in content.skills_of(f"class:{character.class_id}")
        if previous < skill.level <= new
    ]
    if opened:
        lines.append(f"Открылись умения: {', '.join(opened)}.")

    cities = [city.name for city in content.cities if previous < city.unlock_level <= new]
    if cities:
        lines.append(f"Открылся город: {', '.join(cities)}.")

    lines.append("Очки вкладываются в разделах «Характеристики» и «Умения».")
    return "\n".join(lines)


def derived_lines(stats: DerivedStats) -> tuple[str, ...]:
    """Всё, что движок считает сам, - целиком и одними и теми же словами.

    Число, которое движок считает, а экран молчит, ничем не лучше числа, которое
    экран обещает, а движок не считает (``Claude.md``, правило 7).
    """
    lines = [
        f"Броня: {stats.armor}. Точность: {number(stats.accuracy)}. "
        f"Уклонение: {percent(stats.dodge)}.",
        f"Шанс крита: {percent(stats.crit_chance)}. "
        f"Сила крита: {percent(stats.crit_damage)} от урона. "
        f"Инициатива: {number(stats.initiative)}.",
        f"{stats.resource_name} за ход: {number(stats.resource_regen)}.",
    ]
    if stats.health_regen_percent:
        lines.append(f"Здоровья за ход в бою: {percent(stats.health_regen_percent)} от максимума.")
    return tuple(lines)


def damage_line(content: GameContent, character: Character) -> str:
    """Что герой бьёт этим оружием — границами, и чем именно он бьёт.

    Границы, а не среднее: урон бросается по костям, и одно число обещало бы
    точность, которой нет. Голые руки называются голыми руками.
    """
    low, high = blow_range(content, character)
    weapon = gear.weapon_of(content, character)
    held = weapon.name.lower() if weapon is not None else "голыми руками"
    return f"от {low} до {high}, {held}"


def spend_label(stat_name: str) -> Label:
    return label(f"Вложить: {stat_name}")


def stat_effect_lines(content: GameContent, character: Character) -> tuple[str, ...]:
    """Что даёт одно очко в каждой характеристике, в числах этого персонажа.

    Каждое число читается из постоянных правил, а не выписано руками, поэтому
    правка баланса не может оставить объяснение лгать. Чего не стоит за
    постоянной, того не пишут вовсе (``Claude.md``, правило 7).

    Две строки несут весь ответ на «куда вкладывать»: от чего этот класс бьёт и
    чем наполняется его запас. Обе читаются с класса, поэтому воин и маг получают
    разные фразы.
    """
    from mmorpg.domain.entities.stats import StatCode
    from mmorpg.domain.rules import economy
    from mmorpg.domain.rules import stats as stat_rules
    from mmorpg.presentation.telegram.screens.creation import STAT_GENITIVE, STAT_NAMES

    klass = content.character_class(character.class_id)
    key_codes = tuple(klass.key_stats)
    key_names = ", ".join(STAT_NAMES[StatCode(code)].lower() for code in key_codes)
    blow = StatCode(key_codes[0]) if key_codes else None
    pool = StatCode(klass.resource.stat)

    effects: dict[StatCode, str] = {
        StatCode.STR: "Сила: тяжесть удара в ближнем бою.",
        StatCode.AGI: (
            f"Ловкость: за очко плюс {number(stat_rules.ACCURACY_PER_AGILITY)} к точности, "
            f"{percent(stat_rules.DODGE_PER_AGILITY)} уклонения и "
            f"{number(stat_rules.INITIATIVE_PER_AGILITY)} инициативы — это ещё и очередь удара."
        ),
        StatCode.END: (
            f"Выносливость: за очко плюс {number(stat_rules.ARMOR_PER_ENDURANCE)} брони и "
            f"{number(klass.health.per_endurance)} здоровья."
        ),
        StatCode.INT: "Интеллект: сила чар.",
        StatCode.WIS: (
            f"Мудрость: за очко плюс {number(stat_rules.RESOURCE_REGEN_PER_WISDOM)} ресурса в ход."
        ),
        StatCode.CHA: (
            f"Харизма: за очко в лавке уступают "
            f"{percent(economy.CHARISMA_DISCOUNT_PER_POINT)}, "
            f"и так до {percent(economy.MAX_CHARISMA_DISCOUNT)}."
        ),
        StatCode.LCK: (
            f"Удача: за очко плюс {number(stat_rules.CRIT_CHANCE_PER_LUCK)} процента к шансу "
            f"крита и столько же к его силе; выше {percent(stat_rules.MAX_CRIT_CHANCE)} "
            "шанс не поднимется."
        ),
    }
    lead = [klass.power] if klass.power else []
    if blow is not None:
        lead.append(
            f"Ваш удар растёт от {STAT_GENITIVE[blow]}, "
            f"а {klass.resource.name.lower()} — от {STAT_GENITIVE[pool]}."
            if pool is not blow
            else f"От {STAT_GENITIVE[blow]} у вас и удар, и {klass.resource.name.lower()}."
        )
    if key_names:
        lead.append(f"Ключевые: {key_names}.")
    return (*lead, *(effects[code] for code in STAT_NAMES))


def stats_screen(
    content: GameContent,
    character: Character,
    stats: DerivedStats,
    notice: str = "",
    *,
    verbose: bool = True,
) -> Screen:
    """Каждая основная характеристика: сколько она сейчас, что даёт и куда пойдёт очко.

    ``verbose`` - переключатель «Подробные описания» с экрана настроек: он решает,
    идут ли семь объяснений вместе с семью числами.
    """
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        *head(
            f"Характеристики. Свободных очков: {character.unspent_stat_points}.",
            notice,
        ),
        "Вложенное очко назад не берут.",
        # Числа целиком - на карточке персонажа: здесь их место заняли бы
        # объяснения, ради которых этот экран и открывают.
        f"Здоровье {stats.max_health}, броня {stats.armor}, "
        f"точность {number(stats.accuracy)}, уклонение {percent(stats.dodge)}.",
        *(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items()),
        *(
            stat_effect_lines(content, character)
            if verbose
            else (
                content.character_class(character.class_id).power,
                "Подробные описания выключены в настройках: что даёт очко, там же и включается.",
            )
        ),
    ]
    rows: list[tuple[Label, ...]] = []
    if character.unspent_stat_points:
        stat_names = list(STAT_NAMES.values())
        rows = [
            tuple(spend_label(name) for name in stat_names[index : index + 2])
            for index in range(0, len(stat_names), 2)
        ]
    else:
        lines.append("Свободных очков нет: их даёт новый уровень.")
    return Screen(id=ScreenId.STATS, lines=tuple(lines), rows=tuple(rows))
