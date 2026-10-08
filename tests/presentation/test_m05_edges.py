"""Длинная доска и устаревшее согласие не меняют другого отряда."""

from dataclasses import replace

from mmorpg.application.services.recruitment import Recruitment
from mmorpg.domain.rules.recruitment import PACES, goals
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.handlers import play as play_handler
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.presentation.test_party_flow import buttons
from tests.presentation.test_party_flow import cache as cache
from tests.presentation.test_party_flow import characters as characters
from tests.presentation.test_party_flow import guilds as guilds
from tests.presentation.test_party_flow import parties as parties
from tests.presentation.test_party_flow import registry as registry
from tests.presentation.test_party_flow import sent as sent
from tests.presentation.test_party_flow import table as table


async def test_many_listings_keep_every_entry_and_clamp_saved_page(table, monkeypatch):
    player, _, hero, _ = table
    monkeypatch.setattr(play_handler.time, "time", lambda: 10000)
    service = Recruitment(player.deps["parties"], player.deps["characters"]).with_content(
        player.deps["registry"].current
    )
    for index in range(27):
        owner = await service.characters.create(
            replace(hero, id=0, user_id=720000 + index, name=f"Идущий{index}")
        )
        listing, _ = await service.publish(
            owner, goals(service.content, owner)[0].key, PACES[0], now=10000
        )
        assert listing
    first = await player.press("/набор")
    assert first.id is ScreenId.RECRUITMENT and labels.NEXT_PAGE.text in buttons(first)
    visited = []
    screen = first
    while True:
        assert len(screen.body()) <= 2000
        assert screen.all_rows()[-1] == labels.SERVICE_ROW
        assert all(len(one.text) <= 128 for row in screen.all_rows() for one in row)
        state = PlayState.deserialise((await player.state.get_data())["play"])
        visited.extend(key for _, key in state.recruitment_choices)
        if labels.NEXT_PAGE.text not in buttons(screen):
            break
        screen = await player.press(labels.NEXT_PAGE.text)
    assert len(set(visited)) == 27
    last = await player.press("/набор страница 999")
    state = PlayState.deserialise((await player.state.get_data())["play"])
    assert state.recruitment_page == int(last.metadata["pages"])
    previous = await player.press(labels.PREVIOUS_PAGE.text)
    assert int(previous.metadata["page"]) == state.recruitment_page - 1


async def test_late_acceptance_previews_replacement_invitation(table, monkeypatch):
    owner, guest, owner_char, guest_char = table
    clock = [10000]
    monkeypatch.setattr(play_handler.time, "time", lambda: clock[0])
    parties = owner.deps["parties"]
    await parties.create(owner_char.id)
    await parties.call(leader_id=owner_char.id, invitee_id=guest_char.id)
    await guest.press("/отряд")
    other = await owner.deps["characters"].create(
        replace(owner_char, id=0, user_id=730000, name="Довен")
    )
    await parties.create(other.id)
    clock[0] += 86400
    await parties.call(leader_id=other.id, invitee_id=guest_char.id)
    reply = await guest.press(labels.PARTY_ACCEPT.text)
    assert "сменилось" in reply.body() and "Довен" in reply.body()
    assert await parties.of(guest_char.id) is None
    await guest.press(labels.PARTY_ACCEPT.text)
    assert (await parties.of(guest_char.id)).leader_id == other.id


async def test_expired_guild_call_has_same_clear_repeat_and_block(table, monkeypatch):
    owner, guest, owner_char, guest_char = table
    clock = [10000]
    monkeypatch.setattr(play_handler.time, "time", lambda: clock[0])
    guilds = owner.deps["guilds"]
    guild = await guilds.create("Попутчики", owner_char.id)
    await guilds.call(guild_id=guild.id, invitee_id=guest_char.id, inviter_id=owner_char.id)
    clock[0] += 86400
    reply = await guest.press("/гильдия")
    assert "истёк" in reply.body() and "Повторить зов в гильдию" in buttons(reply)
    assert "истёк" in (await guest.press("/гильдия принять")).body()
    await guest.press("Повторить зов в гильдию")
    assert labels.GUILD_ACCEPT.text in buttons(await guest.press("/гильдия"))
    assert "заблокирован" in (await guest.press("Блокировать зов в гильдию")).body()
    assert await guilds.accept(guest_char.id, owner.deps["registry"].current) is None
