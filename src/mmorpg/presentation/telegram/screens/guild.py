"""Гильдия: экраны, на которых её заводят, ведут, растят, хранят и воюют.

Гильдия - объединение надолго, и она растёт от того, что делают её люди
(``domain/rules/guild.py``, ADR 0030, 0076). Экран отвечает на «в гильдии ли я»,
«какое у меня звание», «на какой она ступени» и «что в казне», а кнопок на нём
ровно столько, сколько даёт звание смотрящего: новик кладёт в казну, но не
берёт; звания раздаёт основатель.

Сверх этого - три экрана из ADR 0077: **хранилище** (общая сумка со своим
пределом выемки), **подряд** (три дела на переворот, за которые платят гильдии)
и **война** (счёт поединков с враждебной гильдией). Все они рисуются по тому же
правилу: экран ничего не читает, всё приносит ``handlers/play._guild_view``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from mmorpg.domain.entities.content import GameContent, GuildTier
from mmorpg.domain.rules.guild import (
    FOUND_COST,
    FOUND_LEVEL,
    INVITE_RANK,
    KICK_RANK,
    GuildRank,
    Standing,
)
from mmorpg.domain.rules.guild_contract import Contract
from mmorpg.domain.rules.guild_war import WAR_ROTATIONS
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold, head, plural
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
    paging_row,
    total_pages,
)
from mmorpg.presentation.telegram.screens.shop import OwnedItem

#: Суммы, которыми двигают казну - те же, что у банка.
VAULT_STEPS: tuple[int, ...] = (50, 250, 1000)


@dataclass(frozen=True, slots=True)
class GuildView:
    """Что игра знает о гильдии на момент отрисовки. Экран ничего не читает."""

    name: str = ""
    my_rank: GuildRank | None = None
    #: (имя, звание, вклад) в порядке: основатель, старейшины, ниже - по вкладу.
    members: tuple[tuple[str, GuildRank, int], ...] = ()
    vault_gold: int = 0
    my_gold: int = 0
    #: Имя гильдии, которая зовёт этого игрока. Пусто - никто не зовёт.
    caller: str = ""
    #: Ступень гильдии и её деяния (ADR 0076).
    place: Standing = field(default_factory=Standing)
    #: Вся лестница ступеней - для экрана возвышения.
    tiers: tuple[GuildTier, ...] = ()
    #: Сколько смотрящий уже вынес из казны за этот переворот и сколько ему
    #: положено. ``None`` в пределе - предела нет вовсе (основатель).
    my_taken: int = 0
    my_limit: int | None = 0
    #: Что лежит в хранилище: (вещь, имя, сколько), и то же по вещам (ADR 0077).
    stored: tuple[tuple[str, str, int], ...] = ()
    my_items_taken: int = 0
    my_items_limit: int | None = 0
    #: Что делают с вещью на экране количества: ``stow`` или ``take``.
    vault_action: str = ""
    #: Подряд на этот переворот и счёт по каждому делу - строка в строку.
    contracts: tuple[Contract, ...] = ()
    contract_progress: tuple[int, ...] = ()
    #: Война: с кем, с каким счётом, сколько ей осталось и во что она станет.
    at_war: bool = False
    war_foe: str = ""
    war_mine: int = 0
    war_theirs: int = 0
    war_left: int = 0
    war_stake: int = 0
    #: Гильдия, которая зовёт эту на войну. Пусто - никто не зовёт.
    war_caller: str = ""

    @property
    def joined(self) -> bool:
        return self.my_rank is not None

    @property
    def founder(self) -> bool:
        return self.my_rank is GuildRank.FOUNDER

    @property
    def can_invite(self) -> bool:
        return self.my_rank is not None and self.my_rank >= INVITE_RANK

    @property
    def can_kick(self) -> bool:
        return self.my_rank is not None and self.my_rank >= KICK_RANK

    @property
    def can_withdraw(self) -> bool:
        """Есть ли смысл рисовать ряд выемки: кнопка, которая всегда откажет, - баг."""
        return self.my_limit is None or self.my_limit > 0

    @property
    def can_take_items(self) -> bool:
        """То же для хранилища: новику кнопок «Взять» не рисуют вовсе."""
        return self.my_items_limit is None or self.my_items_limit > 0

    @property
    def war_rotations(self) -> int:
        return WAR_ROTATIONS


def tier_gain(tier: GuildTier) -> str:
    """Что даёт ступень, одной строкой и ровно то, что считает движок."""
    parts = [f"мест {tier.seats}", f"в хранилище {tier.store_slots}"]
    if tier.exp_percent:
        parts.append(f"опыта за бой больше на {tier.exp_percent:g} процентов")
    if tier.gold_percent:
        parts.append(f"золота за бой больше на {tier.gold_percent:g} процентов")
    return ", ".join(parts)


def _place_lines(view: GuildView) -> list[str]:
    """Строки о ступени: где гильдия стоит и сколько до следующей."""
    place = view.place
    lines: list[str] = []
    if place.name:
        lines.append(f"Ступень {place.level}: {place.name}. Деяний: {place.deeds}.")
    else:
        lines.append(f"Деяний: {place.deeds}.")
    lines.append(f"В гильдии: {amount(len(view.members), place.seats, with_percent=False)}.")
    if place.pays:
        gains = []
        if place.exp_percent:
            gains.append(f"опыта больше на {place.exp_percent:g} процентов")
        if place.gold_percent:
            gains.append(f"золота больше на {place.gold_percent:g} процентов")
        lines.append(f"Своим за выигранный бой: {', '.join(gains)}.")
    if place.next_tier is not None:
        lines.append(
            f"До ступени «{place.next_tier.name}» - {place.deeds_left} деяний. "
            "Деяние - выигранный бой её человека или внесённое в казну золото."
        )
    else:
        lines.append("Выше ступеней нет: гильдия выросла целиком.")
    return lines


def guild_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [*head("Гильдия.", notice)]
    rows: list[tuple[Label, ...]] = []

    if view.my_rank is not None:
        lines.append(f"«{view.name}». Ваше звание: {view.my_rank.title}.")
        lines.extend(_place_lines(view))
        lines.append(f"В казне: {gold(view.vault_gold)}.")
        if view.at_war:
            lines.append(
                f"Война с гильдией «{view.war_foe}»: наших очков {view.war_mine}, "
                f"у них {view.war_theirs}."
            )
        elif view.war_caller:
            lines.append(f"Гильдия «{view.war_caller}» зовёт вас на войну.")
        rows.append((labels.GUILD_ROSTER, labels.GUILD_VAULT))
        rows.append((labels.GUILD_STORE, labels.GUILD_CONTRACT))
        rows.append((labels.GUILD_TIERS, labels.GUILD_WAR))
        if len(view.members) > 1:
            rows.append((labels.GUILD_TRANSFER,))
        if view.can_invite:
            rows.append((labels.GUILD_INVITE,))
        if view.founder:
            rows.append((labels.GUILD_SUCCEED, labels.GUILD_DISBAND))
        else:
            rows.append((labels.GUILD_LEAVE,))
    else:
        lines.append("Вы не в гильдии.")
        lines.append(
            "Гильдия - это надолго: десятки человек, пять званий и общая казна, из "
            "которой берут по званию. Отряд собирают на бой, гильдию - на месяцы."
        )
        lines.append(
            "Гильдия растёт от того, что делают её люди: выигранный бой и внесённое в "
            "казну золото поднимают её на ступень, а ступень даёт места и надбавку своим."
        )
        lines.append(
            f"Основать свою можно с {FOUND_LEVEL} уровня, грамота стоит {FOUND_COST} золота."
        )
        rows.append((labels.GUILD_FOUND,))

    if view.caller:
        lines.append(f"Гильдия «{view.caller}» зовёт вас к себе.")
        rows.append((labels.GUILD_ACCEPT, labels.GUILD_DECLINE))

    return Screen(id=ScreenId.GUILD, lines=tuple(lines), rows=tuple(rows))


def found_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head("Основать гильдию.", notice),
        "Напишите имя гильдии одним сообщением.",
        f"Нужен {FOUND_LEVEL} уровень и {FOUND_COST} золота на грамоту. "
        f"У вас {gold(view.my_gold)}.",
        "Вы станете основателем: только он раздаёт звания, распускает гильдию и передаёт её.",
    ]
    return Screen(id=ScreenId.GUILD_FOUND, lines=tuple(lines), rows=())


def invite_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head("Позвать в гильдию.", notice),
        "Напишите имя того, кого зовёте, одним сообщением.",
        f"Звать может {INVITE_RANK.title} и выше. Позванный соглашается сам и встаёт новиком.",
    ]
    if view.members:
        lines.append(
            f"Сейчас в гильдии: {amount(len(view.members), view.place.seats, with_percent=False)}."
        )
    return Screen(id=ScreenId.GUILD_INVITE, lines=tuple(lines), rows=())


def succeed_screen(view: GuildView, notice: str = "") -> Screen:
    """Передача гильдии: основатель называет того, кто её примет (ADR 0076)."""
    lines = [
        *head("Передать гильдию.", notice),
        "Напишите имя того, кто станет основателем, одним сообщением.",
        "Он получит гильдию целиком: звания, казну и роспуск. Вы останетесь в ней "
        f"{GuildRank.ELDER.title}ой и сможете из неё выйти.",
    ]
    if view.name:
        lines.append(f"Гильдия: «{view.name}».")
    return Screen(id=ScreenId.GUILD_SUCCEED, lines=tuple(lines), rows=())


def tiers_screen(view: GuildView, notice: str = "") -> Screen:
    """Возвышение: вся лестница ступеней и то, где гильдия стоит сейчас.

    Список короткий и целиком помещается в сообщение: ступеней у гильдии
    единицы, и резать их страницами нечего.
    """
    lines = [*head("Возвышение гильдии.", notice)]
    if view.name:
        lines.append(f"«{view.name}»: ступень {view.place.level}, деяний {view.place.deeds}.")
    lines.append(
        "Деяние - выигранный бой человека гильдии или внесённое в казну золото: "
        "столько деяний, сколько это боёв его уровня."
    )
    for tier in view.tiers:
        mark = "взята" if tier.deeds <= view.place.deeds else f"с {tier.deeds} деяний"
        lines.append(f"{tier.level}. {tier.name} ({mark}): {tier_gain(tier)}.")
    if not view.tiers:
        lines.append("Ступеней в игре сейчас нет: гильдия держит состав и казну.")
    return Screen(id=ScreenId.GUILD_TIERS, lines=tuple(lines), rows=())


#: Сколько человек показывает одна страница состава. В гильдии их до тридцати, а
#: одним сообщением тридцать имён со званиями и тридцатью рядами кнопок не
#: читаются: список режется, как режется всякий длинный список в игре
#: (``docs/accessibility.md``, правило 7).
ROSTER_PAGE = 8


def roster_screen(view: GuildView, page: PageState | None = None, notice: str = "") -> Screen:
    """Состав гильдии, страницами. Кнопки - только у тех, кто на этой странице.

    Звание раздаёт основатель, выгоняет старейшина; кнопки несут имя, поэтому
    страница их не путает: человек остаётся собой на любой странице. Рядом с
    именем стоит вклад - сколько этот человек принёс гильдии (ADR 0076).
    """
    pages = total_pages(len(view.members), ROSTER_PAGE)
    state = (page or PageState()).clamped(pages)
    first = (state.page - 1) * ROSTER_PAGE
    visible = view.members[first : first + ROSTER_PAGE]

    lines = [*head(f"Состав гильдии «{view.name}».", notice)]
    counted = amount(len(view.members), view.place.seats, with_percent=False)
    lines.append(
        f"В гильдии: {counted}, страница {state.page} из {pages}."
        if pages > 1
        else f"В гильдии: {counted}."
    )
    rows: list[tuple[Label, ...]] = []
    for name, rank, contributed in visible:
        lines.append(f"{name} — {rank.title}, вклад {contributed}.")
    if view.founder:
        lines.append("Вы основатель: можно поднять званием, опустить и выгнать.")
        for name, rank, _ in visible:
            if rank is GuildRank.FOUNDER:
                continue
            controls: list[Label] = []
            if rank < GuildRank.ELDER:
                controls.append(labels.guild_promote_label(name))
            if rank > GuildRank.RECRUIT:
                controls.append(labels.guild_demote_label(name))
            controls.append(labels.guild_kick_label(name))
            rows.append(tuple(controls))
    elif view.can_kick and view.my_rank is not None:
        lines.append(f"Вы {view.my_rank.title}: можно выгнать того, кто ниже вас званием.")
        for name, rank, _ in visible:
            if rank < view.my_rank:
                rows.append((labels.guild_kick_label(name),))
    else:
        lines.append("Звания раздаёт основатель.")
    if pages > 1:
        rows.append(paging_row(state.page, pages))
    # Число страниц объявляет сам экран: по нему их листает общий разбор
    # (``flows/play.advance``, ``LIST_PAGE_FIELD``), и второй раз его никто не считает.
    return Screen(
        id=ScreenId.GUILD_ROSTER,
        lines=tuple(lines),
        rows=tuple(rows),
        metadata={"page": str(state.page), "pages": str(pages), "count": str(len(view.members))},
    )


def vault_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head(f"Казна гильдии «{view.name}».", notice),
        f"В казне: {gold(view.vault_gold)}. У вас на руках: {gold(view.my_gold)}.",
        "Класть может каждый в гильдии, и внесённое идёт гильдии в деяния.",
    ]
    if view.my_limit is None:
        lines.append("Вы основатель: берёте из казны без предела.")
    elif view.my_limit > 0:
        left = max(0, view.my_limit - view.my_taken)
        lines.append(
            f"Вам положено за переворот: {view.my_limit}. "
            f"Уже взято: {view.my_taken}. Осталось: {left}."
        )
    else:
        title = view.my_rank.title if view.my_rank is not None else "новик"
        lines.append(f"{title.capitalize()} из казны не берёт: берут званием выше.")
    rows: list[tuple[Label, ...]] = [
        tuple(labels.guild_deposit_label(step) for step in VAULT_STEPS)
    ]
    if view.can_withdraw:
        rows.append(tuple(labels.guild_withdraw_label(step) for step in VAULT_STEPS))
    return Screen(id=ScreenId.GUILD_VAULT, lines=tuple(lines), rows=tuple(rows))


# --- хранилище гильдии (ADR 0077) ------------------------------------
#
# Сумка на всю гильдию: кладёт каждый, берут званием и не больше предела за
# переворот. Место считается видами, а не штуками, и сколько его - решает
# ступень, поэтому «мест 12 из 18» стоит первой строкой: это то, чем хранилище
# кончается.


def stored_button_text(name: str, quantity: int) -> str:
    """Текст кнопки вещи, лежащей в хранилище."""
    return f"{name}, штук {quantity}"


def stored_from_button(view: GuildView, text: str) -> str:
    """Свести нажатую кнопку обратно к вещи из хранилища. Пусто - не та кнопка."""
    for item_id, name, quantity in view.stored:
        if text == stored_button_text(name, quantity):
            return item_id
    return ""


def store_screen(view: GuildView, page: PageState | None = None, notice: str = "") -> Screen:
    """Что лежит в хранилище гильдии. Кнопка вещи - взять её."""
    entries = [
        ListEntry(key=item_id, text=stored_button_text(name, quantity))
        for item_id, name, quantity in view.stored
    ]
    lead = [notice] if notice else []
    lead.append(
        f"Мест: {amount(len(view.stored), view.place.store_slots, with_percent=False)}. "
        "Место занимает вид вещи, а не штука."
    )
    if view.my_items_limit is None:
        lead.append("Вы основатель: берёте из хранилища без предела.")
    elif view.my_items_limit > 0:
        left = max(0, view.my_items_limit - view.my_items_taken)
        lead.append(
            f"Вам положено за переворот: {view.my_items_limit} вещей. "
            f"Уже взято: {view.my_items_taken}. Осталось: {left}."
        )
    else:
        title = view.my_rank.title if view.my_rank is not None else "новик"
        lead.append(f"{title.capitalize()} из хранилища не берёт: берут званием выше.")
    rows: list[tuple[Label, ...]] = [(labels.GUILD_STOW,)]
    return paginated_screen(
        screen_id=ScreenId.GUILD_STORE,
        title=f"Хранилище гильдии «{view.name}»",
        entries=entries if view.can_take_items else [],
        state=page or PageState(),
        lead_lines=tuple(lead),
        empty_text=(
            "В хранилище пусто: положить может каждый в гильдии."
            if view.can_take_items
            else "Взять отсюда нельзя, а положить - можно."
        ),
        # Отбора у общего добра нет: в хранилище десятки видов, а не тысячи, и
        # кнопка «Поиск», которую некому разобрать, - баг (``Claude.md``, правило 9).
        show_filters=False,
        extra_rows=tuple(rows),
    )


def stow_screen(
    content: GameContent,
    owned: Sequence[OwnedItem],
    view: GuildView,
    page: PageState | None = None,
    notice: str = "",
) -> Screen:
    """Что положить в хранилище: вещи из своей сумки."""
    entries = [
        ListEntry(
            key=content.item(held.item_id).id,
            text=stored_button_text(content.item(held.item_id).name, held.quantity),
            detail=f"уровень {content.item(held.item_id).level}",
        )
        for held in owned
        if content.has_item(held.item_id)
    ]
    return paginated_screen(
        screen_id=ScreenId.GUILD_STORE_PUT,
        title="Что положить в хранилище",
        entries=entries,
        state=page or PageState(),
        lead_lines=(
            notice or "Положить может каждый в гильдии, и это идёт всей гильдии.",
            f"Занято мест: {amount(len(view.stored), view.place.store_slots, with_percent=False)}.",
        ),
        empty_text="В сумке пусто: класть нечего.",
        show_filters=False,
    )


def store_amount_screen(view: GuildView, item_name: str, held: int, notice: str = "") -> Screen:
    """Сколько положить или взять. Число вводят сообщением; «всё» - быстрый путь."""
    taking = view.vault_action == "take"
    what = "взять" if taking else "положить"
    where = "В хранилище" if taking else "В сумке"
    lines = [
        *head(f"Сколько {what}: {item_name}.", notice),
        f"{where} {held} {plural(held, 'штука', 'штуки', 'штук')}.",
        f"Наберите число сообщением или нажмите «{'Взять всё' if taking else 'Положить всё'}».",
    ]
    rows: tuple[tuple[Label, ...], ...] = (
        (labels.GUILD_TAKE_ALL if taking else labels.GUILD_STOW_ALL,),
    )
    return Screen(id=ScreenId.GUILD_STORE_AMOUNT, lines=tuple(lines), rows=rows)


# --- подряд гильдии (ADR 0077) ---------------------------------------


def contract_screen(view: GuildView, notice: str = "") -> Screen:
    """Подряд: три дела на переворот, счёт по каждому и что за них гильдии.

    Кнопок тут нет нарочно: подряд закрывается сам, как только счёт дошёл до
    нужного (``Claude.md``, правило 9).
    """
    lines = [*head(f"Подряд гильдии «{view.name}».", notice)]
    lines.append(
        "Застава просит у гильдии три дела на переворот прилавка. Считается всё, "
        "что за переворот сделали её люди, и платят за них гильдии - в казну и деяниями."
    )
    for contract, done in zip(view.contracts, view.contract_progress, strict=False):
        mark = "закрыто" if contract.done(done) else amount(done, contract.target)
        lines.append(f"{contract.line} Сделано: {mark}. За дело: {contract.pay}.")
    if not view.contracts:
        lines.append("Подряда сейчас нет: гильдия растёт боями и вкладами своих людей.")
    return Screen(id=ScreenId.GUILD_CONTRACT, lines=tuple(lines), rows=())


# --- война гильдий (ADR 0077) ----------------------------------------


def war_screen(view: GuildView, notice: str = "") -> Screen:
    """Война: с кем, счёт и сколько ей осталось. Или вызов, который ждёт ответа."""
    lines = [*head("Война гильдий.", notice)]
    rows: list[tuple[Label, ...]] = []
    if view.at_war:
        lines.append(f"«{view.name}» воюет с гильдией «{view.war_foe}».")
        lines.append(f"Счёт: наших очков {view.war_mine}, у них {view.war_theirs}.")
        lines.append(
            f"Осталось переворотов прилавка: {view.war_left}."
            if view.war_left
            else "Срок вышел: война подводится при первом же взгляде на неё."
        )
        lines.append(
            f"Ставка с каждой стороны: {gold(view.war_stake)}. "
            "Обе достаются той, чей счёт выше; поровну - каждой своя."
        )
        lines.append(
            "Очко берут за выигранный поединок с человеком враждебной гильдии, и за "
            "одного и того же побеждённого - раз за переворот."
        )
    elif view.war_caller:
        lines.append(f"Гильдия «{view.war_caller}» зовёт вас на войну.")
        lines.append(
            f"Ставка - {gold(view.war_stake)} из казны с каждой стороны. "
            f"Война идёт {view.war_rotations} переворота прилавка."
        )
        if view.founder:
            rows.append((labels.GUILD_WAR_ACCEPT, labels.GUILD_WAR_DECLINE))
        else:
            lines.append("Отвечает основатель: ставку кладут из казны.")
    else:
        lines.append(f"«{view.name}» сейчас ни с кем не воюет.")
        lines.append(
            "Война - это счёт: несколько переворотов всякий выигранный поединок с "
            "человеком враждебной гильдии идёт очком своей стороне."
        )
        lines.append(
            f"Ставка - {gold(view.war_stake)} из казны с каждой стороны; в казне "
            f"{gold(view.vault_gold)}."
        )
        if view.founder:
            rows.append((labels.GUILD_WAR_DECLARE,))
        else:
            lines.append("Войну объявляет основатель: ставку кладут из казны.")
    return Screen(id=ScreenId.GUILD_WAR, lines=tuple(lines), rows=tuple(rows))


def war_declare_screen(view: GuildView, notice: str = "") -> Screen:
    """Объявление войны: основатель называет гильдию, которую вызывает."""
    lines = [
        *head("Объявить войну.", notice),
        "Напишите имя гильдии, которую вызываете, одним сообщением.",
        f"Ставка - {gold(view.war_stake)} из казны с каждой стороны, и снимут её, "
        "когда та согласится. В казне сейчас " + f"{gold(view.vault_gold)}.",
        f"Война идёт {view.war_rotations} переворота прилавка, а потом сама подводит итог.",
    ]
    return Screen(id=ScreenId.GUILD_WAR_DECLARE, lines=tuple(lines), rows=())
