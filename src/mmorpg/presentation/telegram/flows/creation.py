"""The character creation flow as a pure state machine.

``advance`` takes the current state and one incoming message and returns the next
state. It touches no database, no aiogram and no clock, so the whole flow -
including every "back" path - is tested directly, without a bot token.

The handler layer does exactly three things: load the state from FSM data, call
``advance``, render and send the screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from mmorpg.application.dto.creation import CharacterDraft, validate_name
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Command, Intent, resolve
from mmorpg.presentation.telegram.screens import creation as screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    SEARCH_PROMPT,
    ListFilters,
    PageState,
    total_pages,
)
from mmorpg.presentation.telegram.states.screens import NavigationStack

SELECTED_PREFIX = "Выбрано: "


@dataclass(frozen=True, slots=True)
class CreationState:
    """Where the player is in creation, plus everything they have chosen."""

    screen: ScreenId = ScreenId.CREATE_NAME
    draft: CharacterDraft = field(default_factory=CharacterDraft)
    stack: NavigationStack = field(default_factory=lambda: NavigationStack((ScreenId.CREATE_NAME,)))
    race_page: PageState = field(default_factory=PageState)
    trait_page: PageState = field(default_factory=PageState)
    notice: str = ""
    #: Набранное сообщение сейчас означает не имя, а строку поиска по списку.
    searching: bool = False
    finished: bool = False
    exited: bool = False

    def at(self, screen: ScreenId) -> CreationState:
        """Move to a screen, remembering the step we came from."""
        return replace(self, screen=screen, stack=self.stack.push(screen), notice="")

    def with_notice(self, notice: str) -> CreationState:
        return replace(self, notice=notice)

    # --- FSM data round trip ---------------------------------------

    def serialise(self) -> str:
        return json.dumps(
            {
                "screen": self.screen.value,
                "stack": self.stack.serialise(),
                "name": self.draft.name,
                "race": self.draft.race_id,
                "class": self.draft.class_id,
                "traits": list(self.draft.trait_ids),
                "allocated": {code.value: value for code, value in self.draft.allocated},
                "race_page": self.race_page.page,
                "trait_page": self.trait_page.page,
                "trait_category": self.trait_page.filters.category,
                "trait_query": self.trait_page.filters.query,
                "searching": self.searching,
            },
            ensure_ascii=False,
        )

    @classmethod
    def deserialise(cls, raw: str) -> CreationState:
        data = json.loads(raw)
        return cls(
            screen=ScreenId(data["screen"]),
            stack=NavigationStack.deserialise(data.get("stack", "")),
            draft=CharacterDraft(
                name=data.get("name", ""),
                race_id=data.get("race", ""),
                class_id=data.get("class", ""),
                trait_ids=tuple(data.get("traits", ())),
                allocated=StatBlock.from_mapping(data.get("allocated", {})),
            ),
            race_page=PageState(page=int(data.get("race_page", 1))),
            trait_page=PageState(
                page=int(data.get("trait_page", 1)),
                filters=ListFilters(
                    category=data.get("trait_category", ""),
                    query=data.get("trait_query", ""),
                ),
            ),
            searching=bool(data.get("searching", False)),
        )


def begin() -> CreationState:
    return CreationState()


def render(content: GameContent, state: CreationState) -> Screen:
    """The screen for the current step."""
    match state.screen:
        case ScreenId.CREATE_NAME:
            return screens.name_screen(state.draft, state.notice)
        case ScreenId.CREATE_RACE:
            return screens.race_screen(content, state.draft, state.race_page, state.notice)
        case ScreenId.CREATE_RACE_DETAILS:
            return screens.race_details_screen(content, state.draft.race_id)
        case ScreenId.CREATE_CLASS:
            return screens.class_screen(content, state.draft, state.notice)
        case ScreenId.CREATE_CLASS_DETAILS:
            return screens.class_details_screen(content, state.draft.class_id)
        case ScreenId.CREATE_TRAITS:
            return screens.traits_screen(content, state.draft, state.trait_page, state.notice)
        case ScreenId.CREATE_TRAIT_FILTERS:
            return screens.trait_filters_screen(content, state.trait_page)
        case ScreenId.CREATE_POINTS:
            return screens.points_screen(content, state.draft, state.notice)
        case _:
            return screens.confirm_screen(content, state.draft)


def advance(
    content: GameContent,
    state: CreationState,
    text: str,
    *,
    name_taken: bool = False,
) -> CreationState:
    """Apply one incoming message. Never raises, never returns silence."""
    screen = render(content, state)
    command = resolve(text, screen)

    # Набранное на экране списка - это ответ на «что искать», а не незнакомая
    # кнопка. Разбирает его та ветка, что вопрос задала.
    if state.searching and command.intent is Intent.UNKNOWN:
        return _searched(state, text)

    if command.intent is Intent.LOOK:
        # "Осмотреться" re-sends the current screen and changes nothing.
        return replace(state, notice="")
    if command.intent is Intent.BACK:
        return _go_back(state)
    if command.intent is Intent.MAIN_MENU:
        return replace(state, exited=True)

    match state.screen:
        case ScreenId.CREATE_NAME:
            return _handle_name(state, text, command, name_taken=name_taken)
        case ScreenId.CREATE_RACE:
            return _handle_race(content, state, command)
        case ScreenId.CREATE_RACE_DETAILS | ScreenId.CREATE_CLASS_DETAILS:
            return _go_back(state)
        case ScreenId.CREATE_CLASS:
            return _handle_class(content, state, command)
        case ScreenId.CREATE_TRAITS:
            return _handle_traits(content, state, command)
        case ScreenId.CREATE_TRAIT_FILTERS:
            return _handle_trait_filters(content, state, command)
        case ScreenId.CREATE_POINTS:
            return _handle_points(content, state, command)
        case _:
            return _handle_confirm(content, state, command)


def _go_back(state: CreationState) -> CreationState:
    """One step back, keeping every choice. From the first step it leaves creation."""
    stack, previous = state.stack.pop()
    if previous is None:
        return replace(state, exited=True)
    return replace(state, screen=previous, stack=stack, notice="")


def _handle_name(
    state: CreationState, text: str, command: Command, *, name_taken: bool
) -> CreationState:
    if command.intent is Intent.SELECT and labels.CONTINUE.matches(text):
        if not state.draft.name:
            return state.with_notice("Сначала напишите имя сообщением.")
        return state.at(ScreenId.CREATE_RACE)

    check = validate_name(text)
    if not check.ok:
        return state.with_notice(check.problem)
    if name_taken:
        return state.with_notice(f"Имя {text.strip()} уже занято, выберите другое.")

    named = replace(state, draft=state.draft.with_name(text))
    return named.at(ScreenId.CREATE_RACE).with_notice(
        f"Имя {named.draft.name} принято. Теперь выберите расу."
    )


def _handle_race(content: GameContent, state: CreationState, command: Command) -> CreationState:
    if command.intent is Intent.NEXT_PAGE or command.intent is Intent.PREVIOUS_PAGE:
        pages = total_pages(len(content.races))
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, race_page=state.race_page.moved(delta, pages), notice="")
    if command.intent is Intent.PAGE and command.number is not None:
        pages = total_pages(len(content.races))
        return replace(state, race_page=state.race_page.jumped(command.number, pages), notice="")

    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите расу из списка или «Подробно о расе».")

    if labels.RACE_DETAILS.matches(command.argument):
        if not state.draft.race_id:
            return state.with_notice("Сначала выберите расу, потом смотрите подробности.")
        return state.at(ScreenId.CREATE_RACE_DETAILS)
    if labels.CONTINUE.matches(command.argument):
        if not state.draft.race_id:
            return state.with_notice("Сначала выберите расу.")
        return state.at(ScreenId.CREATE_CLASS)

    for race in content.races:
        if race.name == command.argument:
            chosen = replace(state, draft=state.draft.with_race(race.id))
            # Changing the race re-states its bonuses, so the effect of going
            # back and switching is never a surprise (spec section 12).
            return chosen.with_notice(
                f"Раса: {race.name}, {screens.describe_bonuses(race.bonuses)}. "
                "Нажмите «Продолжить» или выберите другую расу."
            )
    return state.with_notice("Не узнал расу. Нажмите расу из списка.")


def _handle_class(content: GameContent, state: CreationState, command: Command) -> CreationState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите класс из списка.")

    if labels.CLASS_DETAILS.matches(command.argument):
        if not state.draft.class_id:
            return state.with_notice("Сначала выберите класс, потом смотрите подробности.")
        return state.at(ScreenId.CREATE_CLASS_DETAILS)
    if labels.CONTINUE.matches(command.argument):
        if not state.draft.class_id:
            return state.with_notice("Сначала выберите класс.")
        return state.at(ScreenId.CREATE_TRAITS)

    for klass in content.classes:
        if command.argument.startswith(klass.name):
            chosen = replace(state, draft=state.draft.with_class(klass.id))
            return chosen.with_notice(
                f"Класс {klass.name}: {klass.role.lower()}, ресурс {klass.resource.name}. "
                "Нажмите «Продолжить» или выберите другой класс."
            )
    return state.with_notice("Не узнал класс. Нажмите класс из списка.")


def _searched(state: CreationState, text: str) -> CreationState:
    """Строка поиска, набранная сообщением. Пустая строка снимает поиск."""
    query = text.strip()
    filters = replace(state.trait_page.filters, query=query)
    found = replace(state, searching=False, trait_page=PageState(filters=filters))
    if not query:
        return found.with_notice("Поиск снят, список показан целиком.")
    return found.with_notice(f"Ищу «{query}».")


def _reset_traits(state: CreationState) -> CreationState:
    return replace(
        state,
        searching=False,
        screen=ScreenId.CREATE_TRAITS,
        trait_page=PageState(filters=state.trait_page.filters.cleared()),
    ).with_notice("Фильтры сняты, список показан целиком.")


def _handle_trait_filters(
    content: GameContent, state: CreationState, command: Command
) -> CreationState:
    """Раздел особенностей: одна кнопка - один раздел, и назад к списку."""
    if command.intent is Intent.RESET_FILTERS:
        return _go_back(_reset_traits(state))
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите раздел или «Назад».")
    for name in screens.trait_categories(content):
        if name != command.argument:
            continue
        chosen = replace(state.trait_page.filters, category=name)
        return _go_back(replace(state, trait_page=PageState(filters=chosen))).with_notice(
            f"Раздел «{name}»."
        )
    return state.with_notice("Нажмите раздел или «Назад».")


def _visible_traits(content: GameContent, state: CreationState) -> tuple[str, ...]:
    return tuple(trait.id for trait in screens.matching_traits(content, state.trait_page))


def _handle_traits(content: GameContent, state: CreationState, command: Command) -> CreationState:
    required = content.rules.traits_at_creation
    pool = _visible_traits(content, state)
    pages = total_pages(len(pool))

    if command.intent is Intent.NEXT_PAGE or command.intent is Intent.PREVIOUS_PAGE:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, trait_page=state.trait_page.moved(delta, pages), notice="")
    if command.intent is Intent.PAGE and command.number is not None:
        return replace(state, trait_page=state.trait_page.jumped(command.number, pages), notice="")
    if command.intent is Intent.RESET_FILTERS:
        return _reset_traits(state)
    if command.intent is Intent.FILTERS:
        return state.at(ScreenId.CREATE_TRAIT_FILTERS)
    if command.intent is Intent.SEARCH:
        return replace(state, searching=True).with_notice(SEARCH_PROMPT)
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите особенность из списка.")

    argument = command.argument
    if labels.CONTINUE.matches(argument):
        if len(state.draft.trait_ids) != required:
            return state.with_notice(f"Нужно выбрать ровно {required}.")
        return state.at(ScreenId.CREATE_POINTS)

    name = argument.removeprefix(SELECTED_PREFIX)
    for trait in content.traits:
        if trait.name == name:
            updated = state.draft.toggle_trait(trait.id, required)
            if updated == state.draft:
                return state.with_notice(
                    f"Уже выбрано {required}. Снимите одну особенность, чтобы выбрать другую."
                )
            picked = trait.id in updated.trait_ids
            verb = "выбрана" if picked else "снята"
            return replace(state, draft=updated).with_notice(
                f"Особенность {trait.name} {verb}. {trait.text}"
            )
    return state.with_notice("Не узнал особенность. Нажмите её в списке.")


def _handle_points(content: GameContent, state: CreationState, command: Command) -> CreationState:
    budget = content.rules.free_points_at_creation
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите характеристику, чтобы вложить очко.")

    argument = command.argument
    if screens.RESET_POINTS.matches(argument):
        return replace(state, draft=state.draft.reset_points()).with_notice(
            f"Очки сброшены. Осталось распределить {budget}."
        )
    if labels.CONTINUE.matches(argument):
        if state.draft.points_left(budget):
            return state.with_notice(
                f"Сначала распределите все очки, осталось {state.draft.points_left(budget)}."
            )
        return state.at(ScreenId.CREATE_CONFIRM)

    for code, name in screens.STAT_NAMES.items():
        if argument.startswith(name):
            updated = state.draft.spend_point(StatCode(code), budget)
            if updated == state.draft:
                return state.with_notice("Свободных очков не осталось.")
            left = updated.points_left(budget)
            return replace(state, draft=updated).with_notice(
                f"{name} повышена. Осталось распределить {left}."
            )
    return state.with_notice("Не узнал характеристику.")


def _handle_confirm(content: GameContent, state: CreationState, command: Command) -> CreationState:
    if command.intent is Intent.SELECT and labels.CONFIRM.matches(command.argument):
        if not state.draft.is_complete(content):
            return state.with_notice("Не все шаги пройдены, вернитесь назад.")
        return replace(state, finished=True)
    return state.with_notice("Нажмите «Подтвердить» или «Назад».")
