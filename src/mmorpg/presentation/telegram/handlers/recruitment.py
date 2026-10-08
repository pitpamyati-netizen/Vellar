"""Маршрут доски; сохраняет точный выбор из ранее показанного сообщения."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.application.services.recruitment import Recruitment
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.party import LEVEL_WINDOW, MAX_MEMBERS
from mmorpg.domain.rules.recruitment import PACES, goals
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens import recruitment as ui
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import ListEntry, PageState, paginated_screen
from mmorpg.presentation.telegram.states.screens import NavigationStack

SCREENS = {ScreenId.RECRUITMENT, ScreenId.RECRUITMENT_SETUP, ScreenId.RECRUITMENT_CARD}
WORDS = {
    ui.BOARD.text: "",
    ui.CREATE.text: "создать",
    ui.OWN.text: "свой",
    ui.JOIN.text: "вступить",
    ui.READY.text: "готов",
    ui.PAUSE.text: "пауза",
    ui.CLOSE.text: "закрыть",
    ui.LEAVE.text: "выйти",
    ui.DEPART.text: "идти",
    ui.PUBLISH.text: "опубликовать",
    ui.REFRESH.text: "обновить",
}


def requested(text: str, state: PlayState) -> bool:
    return text.casefold().startswith("/набор") or text == ui.BOARD.text or state.screen in SCREENS


def snapshot(state: PlayState, screen: Screen, entries: list[ListEntry]) -> PlayState:
    visible = {one.text for row in screen.rows for one in row}
    return replace(
        state,
        recruitment_page=int(screen.metadata.get("page", state.recruitment_page)),
        recruitment_choices=tuple(
            (one.as_label().text, one.key) for one in entries if one.as_label().text in visible
        ),
    )


async def show(
    state: PlayState, actor: Character, service: Recruitment, *, now: int
) -> tuple[PlayState, Screen]:
    content = service.content
    if state.screen is ScreenId.RECRUITMENT_SETUP:
        available = goals(content, actor)
        goal = next((one for one in available if one.key == state.recruitment_goal), None)
        if goal is None:
            entries = [
                ListEntry(one.key, f"{index}. {one.name}", f"уровень {one.level}")
                for index, one in enumerate(available, 1)
            ]
            screen = paginated_screen(
                screen_id=state.screen,
                title="Выбрать цель похода",
                entries=entries,
                state=PageState(state.recruitment_page),
                show_filters=False,
                lead_lines=(state.notice, "Выбор: /набор выбрать N. Отмена: /назад."),
            )
            return snapshot(state, screen, entries), screen
        screen = ui.setup(
            goal,
            state.recruitment_pace,
            state.recruitment_low,
            state.recruitment_high,
            state.notice,
        )
        return state, screen
    if state.screen is ScreenId.RECRUITMENT_CARD:
        listing = await service.get(state.recruitment_id)
        if listing:
            party = await service.parties.by_leader(listing.leader_id)
            goal = await service.goal(listing)
            names = []
            for member_id in party.members if party else ():
                if one := await service.characters.get(member_id):
                    names.append((member_id, one.name))
            city = next(
                (one.name for one in content.cities if goal and one.id == goal.city_id),
                "место больше недоступно",
            )
            return state, ui.card(
                listing, goal, city, tuple(names), actor.id, now=now, notice=state.notice
            )
        state = replace(
            state, screen=ScreenId.RECRUITMENT, notice="Набор больше недоступен. Выберите другой."
        )
    state = replace(state, screen=ScreenId.RECRUITMENT)
    entries = []
    for listing in await service.board(now=now):
        leader = await service.characters.get(listing.leader_id)
        goal = await service.goal(listing)
        party = await service.parties.by_leader(listing.leader_id)
        if leader and goal and party:
            city_info = content.city(goal.city_id)
            entries.append(
                ListEntry(
                    listing.id,
                    f"{len(entries) + 1}. Отряд {leader.name}",
                    f"{city_info.name}, {goal.name}; с {listing.low} по {listing.high}; "
                    f"{listing.pace}; свободно {MAX_MEMBERS - len(party.members)}",
                )
            )
    screen = paginated_screen(
        screen_id=ScreenId.RECRUITMENT,
        title="Поиск отряда",
        entries=entries,
        state=PageState(state.recruitment_page),
        show_filters=False,
        extra_rows=((ui.CREATE, ui.OWN), (ui.REFRESH,)),
        lead_lines=(
            state.notice,
            "Карточка: /набор выбрать N. Свой: /набор свой.",
            "Новый набор: /набор создать. Обновить: /набор обновить.",
            "Можно продолжать играть одному через главное меню.",
        ),
    )
    return snapshot(state, screen, entries), screen


async def step(
    text: str,
    state: PlayState,
    actor: Character,
    content: GameContent,
    service: Recruitment,
    *,
    now: int,
) -> tuple[PlayState, Screen | None]:
    service.with_content(content)
    stripped = text.strip()
    command = parse_command(stripped)
    if command and command.intent in {
        Intent.MAIN_MENU,
        Intent.BACK,
        Intent.PARTY,
        Intent.PARTY_LEAVE,
        Intent.PARTY_DISBAND,
    }:
        if command.intent is Intent.BACK and state.screen in {
            ScreenId.RECRUITMENT_CARD,
            ScreenId.RECRUITMENT_SETUP,
        }:
            return await show(state.at(ScreenId.RECRUITMENT), actor, service, now=now)
        return replace(
            state,
            screen=ScreenId.PARTY,
            stack=NavigationStack((ScreenId.MAIN_MENU, ScreenId.PARTY)),
        ), None
    if labels.MAIN_MENU.matches(stripped) or labels.BACK.matches(stripped):
        return await step(
            "/меню" if labels.MAIN_MENU.matches(stripped) else "/назад",
            state,
            actor,
            content,
            service,
            now=now,
        )
    parts = stripped.split()
    word = ""
    arg = ""
    if parts and parts[0].casefold() == "/набор":
        word = parts[1].casefold() if len(parts) > 1 else ""
        arg = " ".join(parts[2:])
    elif stripped in WORDS:
        word = WORDS[stripped]
    elif stripped in PACES and state.screen is ScreenId.RECRUITMENT_SETUP:
        word, arg = "темп", stripped
    elif stripped in dict(state.recruitment_choices):
        word, arg = "выбрать", stripped
    elif labels.NEXT_PAGE.matches(stripped):
        word = "дальше"
    elif labels.PREVIOUS_PAGE.matches(stripped):
        word = "раньше"
    elif command and command.intent is Intent.PAGE:
        word, arg = "страница", str(command.number or 1)
    elif stripped.startswith("Страница "):
        return await show(
            state.with_notice("Наберите /набор страница N с номером страницы."),
            actor,
            service,
            now=now,
        )
    else:
        return await show(
            state.with_notice("Это действие сейчас недоступно. Выберите актуальную кнопку."),
            actor,
            service,
            now=now,
        )
    state = state.with_notice("")
    if word in {"", "обновить"}:
        state = state.at(ScreenId.RECRUITMENT)
    elif word == "создать":
        state = replace(
            state.at(ScreenId.RECRUITMENT_SETUP),
            recruitment_goal="",
            recruitment_pace="",
            recruitment_low=max(1, actor.level - LEVEL_WINDOW),
            recruitment_high=min(150, actor.level + LEVEL_WINDOW),
            recruitment_page=1,
        )
    elif word == "свой":
        party = await service.parties.of(actor.id)
        listing = await service.for_party(party.leader_id) if party else None
        if listing:
            state = replace(state.at(ScreenId.RECRUITMENT_CARD), recruitment_id=listing.id)
        else:
            state = state.at(ScreenId.RECRUITMENT).with_notice("У вашего отряда пока нет набора.")
    elif word in {"дальше", "раньше", "страница"}:
        page = state.recruitment_page + (1 if word == "дальше" else -1)
        if word == "страница":
            page = int(arg) if arg.isdecimal() else 1
        state = replace(state, recruitment_page=max(1, page))
    elif word == "выбрать":
        picked = dict(state.recruitment_choices).get(arg, "")
        if arg.isdecimal():
            picked = next(
                (
                    key
                    for name, key in state.recruitment_choices
                    if name.startswith(f"{int(arg)}. ")
                ),
                "",
            )
        if not picked:
            state = state.with_notice("Выберите запись из показанной страницы.")
        elif state.screen is ScreenId.RECRUITMENT_SETUP:
            state = replace(state, recruitment_goal=picked, recruitment_pace="")
        else:
            state = replace(state.at(ScreenId.RECRUITMENT_CARD), recruitment_id=picked)
    elif word == "темп" and state.screen is ScreenId.RECRUITMENT_SETUP:
        pace = PACES[int(arg) - 1] if arg in {"1", "2"} else arg
        state = replace(state, recruitment_pace=pace if pace in PACES else "")
    elif word == "уровни" and state.screen is ScreenId.RECRUITMENT_SETUP:
        values = arg.split()
        if len(values) == 2 and all(one.isdecimal() for one in values):
            state = replace(state, recruitment_low=int(values[0]), recruitment_high=int(values[1]))
        else:
            state = state.with_notice("Укажите два уровня, например: /набор уровни 5 15.")
    elif word == "опубликовать" and state.screen is ScreenId.RECRUITMENT_SETUP:
        listing, notice = await service.publish(
            actor,
            state.recruitment_goal,
            state.recruitment_pace,
            now=now,
            low=state.recruitment_low,
            high=state.recruitment_high,
        )
        if listing:
            state = replace(state.at(ScreenId.RECRUITMENT_CARD), recruitment_id=listing.id)
        state = state.with_notice(notice)
    elif word in {"блокировать", "разрешить"}:
        target = await service.characters.find_by_name(arg)
        state = state.with_notice(
            await service.block(actor, target.id, enabled=word == "блокировать")
            if target
            else "Укажите имя существующего игрока."
        )
    elif state.screen is ScreenId.RECRUITMENT_CARD:
        match word:
            case "вступить":
                notice = await service.join(state.recruitment_id, actor, now=now)
            case "готов" | "пауза":
                notice = await service.ready(state.recruitment_id, actor, enabled=word == "готов")
            case "закрыть":
                notice = await service.close(state.recruitment_id, actor.id)
            case "выйти":
                listing = await service.get(state.recruitment_id)
                party = await service.parties.of(actor.id)
                current = await service.for_party(party.leader_id) if party else None
                if listing and current and current.id == listing.id:
                    await service.leave(actor.id)
                    notice = "Вы вышли из отряда. Можно вернуться через доску, пока набор открыт."
                else:
                    notice = (
                        "Ваш отряд изменился. Откройте свой набор; прежняя кнопка его не изменяет."
                    )
            case "идти":
                listing = await service.get(state.recruitment_id)
                party = await service.parties.of(actor.id)
                goal = await service.goal(listing) if listing else None
                if listing and party and party.leader_id == listing.leader_id and goal:
                    current = await service.for_party(party.leader_id)
                    refusal = await service._refusal(listing, actor, party)
                    if current is None or current.id != listing.id or refusal:
                        notice = refusal or "Цель отряда изменилась. Откройте свой набор заново."
                    elif actor.city_id != goal.city_id:
                        notice = (
                            f"Сначала отправляйтесь в {content.city(goal.city_id).name} "
                            "через карту мира."
                        )
                    elif goal.kind == "dungeon":
                        return replace(
                            state.at(ScreenId.DUNGEON_PICK),
                            city_id=goal.city_id,
                            dungeon_pick=goal.target,
                        ), None
                    else:
                        return replace(
                            state.at(ScreenId.LOCATION),
                            city_id=goal.city_id,
                            session=LocationSession(city_id=goal.city_id, slot=int(goal.target)),
                        ), None
                else:
                    notice = "Вы больше не в этом отряде или цель недоступна."
            case _:
                notice = "Выберите актуальную кнопку или команду из карточки."
        state = state.with_notice(notice)
    else:
        state = state.with_notice("Сначала откройте карточку нужного набора.")
    return await show(state, actor, service, now=now)
