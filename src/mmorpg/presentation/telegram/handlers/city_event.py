"""Маршрут события использует обычные локации и общий расчёт боя."""

from dataclasses import replace

from mmorpg.application.services.city_event import CityEvents
from mmorpg.domain.entities.character import Character
from mmorpg.presentation.telegram.flows.play import begin
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState, go_back
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens import city_event as ui
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

WORDS = {
    ui.OPEN.text: "",
    ui.BATTLE.text: "бой",
    ui.SCOUT.text: "разведка",
    ui.CRAFT.text: "ремесло",
    ui.CLAIM.text: "награда",
    ui.REFRESH.text: "обновить",
}


def requested(text: str, flow: PlayState) -> bool:
    return (
        text.casefold().split(maxsplit=1)[0:1] == ["/событие"]
        or text == ui.OPEN.text
        or flow.screen is ScreenId.CITY_EVENT
    )


async def step(
    text: str, flow: PlayState, actor: Character, service: CityEvents, *, receipt: str
) -> tuple[PlayState, Screen | None]:
    command = parse_command(text)
    if (command and command.intent is Intent.BACK) or labels.BACK.matches(text):
        return go_back(flow), None
    if (command and command.intent is Intent.MAIN_MENU) or labels.MAIN_MENU.matches(text):
        return begin(actor), None
    if command and command.intent not in {Intent.LOOK, Intent.UNKNOWN}:
        return go_back(flow), None
    if text.casefold().startswith("/набор"):
        return go_back(flow), None
    if not service.content.city_events:
        return flow.at(ScreenId.CITY).with_notice("В этом мире пока нет городского дела."), None
    event = service.content.city_events[0]
    state = await service.load(event)
    action = WORDS.get(
        text,
        text.partition(" ")[2].strip().casefold()
        if text.casefold().startswith("/событие")
        else "неизвестно",
    )
    if command and command.intent is Intent.LOOK:
        action = "обновить"
    notice = ""
    if action in {"бой", "разведка"}:
        if actor.city_id != event.city_id:
            notice = (
                f"Для помощи приезжайте в город {service.content.city(event.city_id).name}. "
                "Дорога: /дорога."
            )
        elif state.stage >= len(event.stages):
            notice = "Городское дело завершено. Можно продолжать обычные занятия."
        else:
            slot = event.stages[state.stage].location_slot
            return replace(
                flow, city_id=event.city_id, session=LocationSession(event.city_id, slot)
            ).at(ScreenId.LOCATION), None
    elif action == "ремесло":
        notice = await service.craft(actor.id, event.city_id, flow.city_event_stage, receipt)
    elif action == "награда":
        notice = await service.claim(actor.id, event.city_id)
    elif action not in {"", "обновить"}:
        notice = "Выберите помощь кнопкой или командой /событие."
    state = await service.load(event)
    shown = replace(flow.at(ScreenId.CITY_EVENT), city_event_stage=state.stage).with_notice(notice)
    return shown, ui.overview(service.content, event, state, actor.id, notice)
