"""Долгие цели доступны кнопками и текстом, включая позднее подтверждение."""

from dataclasses import replace

from mmorpg.application.services.long_goal import LongGoals
from mmorpg.domain.entities.character import Character
from mmorpg.presentation.telegram.flows.play import begin
from mmorpg.presentation.telegram.flows.state import PlayState, go_back
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens import long_goal as ui
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

SCREENS = {ScreenId.LONG_GOALS, ScreenId.LONG_PROJECT, ScreenId.LONG_STORY}
OPENERS = {ui.OPEN.text: "/цели", ui.PROJECT.text: "/проект", ui.STORY.text: "/историямира"}


def requested(text: str, flow: PlayState) -> bool:
    return (
        text.casefold().split(maxsplit=1)[0:1] in [["/цели"], ["/проект"], ["/историямира"]]
        or text in OPENERS
        or flow.screen in SCREENS
    )


async def step(
    text: str, flow: PlayState, actor: Character, service: LongGoals, *, receipt: str
) -> tuple[PlayState, Screen | None]:
    command = parse_command(text)
    if labels.BACK.matches(text) or (command and command.intent is Intent.BACK):
        return go_back(flow), None
    if labels.MAIN_MENU.matches(text) or (command and command.intent is Intent.MAIN_MENU):
        return begin(actor), None
    if command and command.intent not in {Intent.UNKNOWN, Intent.LOOK, Intent.REFRESH}:
        return go_back(flow), None
    text = OPENERS.get(text, text)
    notice = ""
    screen = flow.screen if flow.screen in SCREENS else ScreenId.LONG_GOALS
    if text == ui.START.text:
        text = "/проект начать"
    if text == ui.STUDY.text:
        text = "/историямира изучить"
    if text == ui.TRIAL.text:
        text = "/цели испытание"
    if text == ui.CHRONICLE.text:
        text = "/цели хроника"
    if text == ui.REFRESH.text or (command and command.intent in {Intent.LOOK, Intent.REFRESH}):
        text = {ScreenId.LONG_PROJECT: "/проект", ScreenId.LONG_STORY: "/историямира"}.get(
            screen, "/цели"
        )
    for track, button in ui.CLAIMS.items():
        if text == button.text:
            text = (
                "/цели награда "
                + {"trial": "испытание", "craft": "мастерство", "chronicle": "хроника"}[track]
            )
    for index, recipe in enumerate(service.goal_recipes(), 1):
        from mmorpg.presentation.telegram.screens.crafts import output_name

        if labels.label(
            f"Изготовить для цели {index}: {output_name(service.content, recipe)}"
        ).matches(text):
            text = f"/цели изготовить {index}"
    for index, outcome in enumerate(service.content.long_goals.outcomes, 1):
        for word, verb in (("Выбрать", "выбрать"), ("Подтвердить", "подтвердить")):
            if text == f"{word}: {outcome.name}":
                text = f"/историямира {verb} {index}"
    words = text.casefold().split()
    if not words:
        notice = "Откройте нужный раздел: /цели, /проект или /историямира."
    elif words[0] == "/проект":
        screen = ScreenId.LONG_PROJECT
        if words[1:] == ["начать"]:
            notice = await service.start_project(actor.id)
        elif words[1:]:
            notice = "Начало проекта: /проект начать. Просмотр: /проект."
    elif words[0] == "/историямира":
        screen = ScreenId.LONG_STORY
        if words[1:] == ["изучить"]:
            notice = await service.study(actor.id)
        elif len(words) == 3 and words[1] in {"выбрать", "подтвердить"} and words[2] in {"1", "2"}:
            outcome = service.content.long_goals.outcomes[int(words[2]) - 1]
            notice = await service.choose(actor.id, outcome.id, confirm=words[1] == "подтвердить")
        elif words[1:]:
            notice = "Выберите путь 1 или 2: /историямира выбрать 1."
    elif words[0] == "/цели":
        screen = ScreenId.LONG_GOALS
        if words[1:] == ["испытание"]:
            config = service.content.long_goals
            if actor.level < config.level or actor.city_id != config.city_id:
                notice = (
                    "На уровне 150 приезжайте в Колву, соберите отряд через /набор и "
                    "выберите Основание Маяка."
                )
            else:
                return replace(flow.at(ScreenId.DUNGEON), city_id=actor.city_id), None
        elif len(words) == 3 and words[1] == "изготовить" and words[2].isdigit():
            notice = await service.craft(actor.id, int(words[2]), receipt)
        elif len(words) == 3 and words[1] == "награда":
            track = {"испытание": "trial", "мастерство": "craft", "хроника": "chronicle"}.get(
                words[2], ""
            )
            notice = await service.claim(actor.id, track)
        elif words[1:] not in ([], ["обновить"], ["хроника"]):
            notice = "Условия и команды перечислены на экране /цели."
    else:
        notice = "Действие не распознано. Выберите кнопку или команду из условий."
    flow = flow.at(screen) if flow.screen is not screen else flow
    if screen is ScreenId.LONG_PROJECT:
        guild = await service.guilds.of(actor.id)
        project = await service.project(guild.id) if guild else None
        names = {}
        if project:
            for key in {member for member, _, _ in project.participation}:
                person = await service.characters.get(key)
                if person:
                    names[key] = person.name
        view = ui.project(service.content, actor, guild, project, notice, names)

    elif screen is ScreenId.LONG_STORY:
        view = ui.story(
            service.content,
            await service.story(),
            await service.offer(actor.id),
            notice,
        )
    else:
        view = ui.goals(
            service.content,
            actor,
            await service.goals(actor.id),
            notice,
            chronicle=words == ["/цели", "хроника"],
        )
    return flow.with_notice(notice), view
