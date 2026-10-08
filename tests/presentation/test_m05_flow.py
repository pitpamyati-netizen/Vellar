"""Игрок проходит настоящий обработчик кнопками и текстовыми командами."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.rules.recruitment import PACES
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.handlers import play as play_handler
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens import recruitment as ui
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.presentation.test_party_flow import (
    buttons,
)
from tests.presentation.test_party_flow import (
    cache as cache,
)
from tests.presentation.test_party_flow import (
    characters as characters,
)
from tests.presentation.test_party_flow import (
    guilds as guilds,
)
from tests.presentation.test_party_flow import (
    parties as parties,
)
from tests.presentation.test_party_flow import (
    registry as registry,
)
from tests.presentation.test_party_flow import (
    sent as sent,
)
from tests.presentation.test_party_flow import (
    table as table,
)


async def publish(player):
    assert (await player.press(ui.BOARD.text)).id is ScreenId.RECRUITMENT
    assert (await player.press(ui.CREATE.text)).id is ScreenId.RECRUITMENT_SETUP
    screen = await player.press("/набор выбрать 1")
    assert PACES[0] in buttons(screen)
    await player.press(PACES[0])
    screen = await player.press(ui.PUBLISH.text)
    assert screen.id is ScreenId.RECRUITMENT_CARD
    assert "опубликован" in screen.body()
    return screen


async def test_unfamiliar_players_gather_without_messages_and_return_after_pause(table):
    owner, guest, _owner_char, _guest_char = table
    menu = await owner.press("/меню")
    assert ui.BOARD.text in buttons(menu)
    await publish(owner)
    await guest.press("/набор")
    card = await guest.press("/набор выбрать 1")
    assert "Цель:" in card.body() and "Свободно: 4" in card.body()
    assert ui.JOIN.text in buttons(card)
    joined = await guest.press(ui.JOIN.text)
    assert "вступили" in joined.body() and "2 из 5" in joined.body()
    assert "подтверждена" in (await guest.press("/набор готов")).body()
    assert "снята" in (await guest.press(ui.PAUSE.text)).body()
    assert (await guest.press("/меню")).id is ScreenId.MAIN_MENU
    await guest.press("/набор")
    assert (await guest.press(ui.OWN.text)).id is ScreenId.RECRUITMENT_CARD
    assert "вышли" in (await guest.press(ui.LEAVE.text)).body()
    assert (
        ui.JOIN.text in buttons(await guest.press("/набор обновить"))
        or (await guest.press("/набор выбрать 1")).id is ScreenId.RECRUITMENT_CARD
    )
    assert "вступили" in (await guest.press("/набор вступить")).body()
    assert (await guest.press(ui.DEPART.text)).id is ScreenId.LOCATION
    assert (await guest.press("/меню")).id is ScreenId.MAIN_MENU


async def test_stale_listing_button_never_joins_replacement(table):
    owner, guest, _owner_char, guest_char = table
    await publish(owner)
    await guest.press("/набор")
    await guest.press("/набор выбрать 1")
    await owner.press("/набор закрыть")
    await owner.press("/набор создать")
    await owner.press("/набор выбрать 2")
    await owner.press("/набор темп 2")
    await owner.press("/набор опубликовать")
    answer = await guest.press(ui.JOIN.text)
    assert "завершён" in answer.body()
    assert await guest.deps["parties"].of(guest_char.id) is None


async def test_expired_party_invitation_repeats_for_slow_reader_and_blocks(table, monkeypatch):
    owner, guest, _owner_char, guest_char = table
    clock = [10000]
    monkeypatch.setattr(play_handler.time, "time", lambda: clock[0])
    await owner.press("/отряд создать")
    await owner.press("/отряд пригласить")
    await owner.press(guest_char.name)
    clock[0] += 86400
    screen = await guest.press("/отряд")
    assert "истёк" in screen.body()
    assert "Повторить зов в отряд" in buttons(screen)
    assert "истёк" in (await guest.press("/отряд принять")).body()
    assert "возобновлено" in (await guest.press("Повторить зов в отряд")).body()
    assert "заблокирован" in (await guest.press("Блокировать зов в отряд")).body()
    await owner.press("/отряд пригласить")
    assert "блокировка" in (await owner.press(guest_char.name)).body()
    assert await guest.deps["parties"].of(guest_char.id) is None


async def test_full_party_refuses_accept_without_false_success(table):
    owner, guest, owner_char, guest_char = table
    await owner.press("/отряд создать")
    await owner.press("/отряд пригласить")
    await owner.press(guest_char.name)
    parties = owner.deps["parties"]
    party = await parties.of(owner_char.id)
    for index in range(3):
        one = await owner.deps["characters"].create(
            replace(owner_char, id=0, user_id=710000 + index, name=f"Сосед{index}")
        )
        party = party.with_member(one.id)
    # Пятый участник занимает последнее место после показанного приглашения.
    one = await owner.deps["characters"].create(
        replace(owner_char, id=0, user_id=710003, name="Пятый")
    )
    await parties.save(party.with_member(one.id))
    screen = await guest.press("/отряд принять")
    assert "не осталось мест" in screen.body()
    assert "Вы в отряде" not in screen.body()
    assert await parties.of(guest_char.id) is None


async def test_back_draft_cancel_and_bad_range_are_clear(table):
    owner, _, owner_char, _ = table
    await owner.press("/набор создать")
    assert (await owner.press("Назад")).id is ScreenId.RECRUITMENT
    assert await owner.deps["parties"].of(owner_char.id) is None
    await owner.press("/набор создать")
    await owner.press("/набор выбрать 1")
    await owner.press("/набор темп 1")
    await owner.press("/набор уровни 10 20")
    assert "ваш уровень" in (await owner.press("/набор опубликовать")).body()
    assert await owner.deps["parties"].of(owner_char.id) is None
    await owner.press("/набор уровни 1 11")
    assert "опубликован" in (await owner.press("/набор опубликовать")).body()
    assert (await owner.press("Назад")).id is ScreenId.RECRUITMENT
    assert (await owner.press(labels.MAIN_MENU.text)).id is ScreenId.MAIN_MENU


def test_selection_and_draft_survive_saved_screen():
    state = PlayState(
        screen=ScreenId.RECRUITMENT_SETUP,
        recruitment_goal="city:location:1",
        recruitment_pace=PACES[0],
        recruitment_low=1,
        recruitment_high=16,
        recruitment_page=3,
        recruitment_choices=(("Отряд Аргуса", "stable"),),
    )
    assert PlayState.deserialise(state.serialise()) == state
