from dataclasses import replace

from mmorpg.application.services.city_event import CityEvents
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.application.test_long_goals_m09 import advanced
from tests.presentation.test_party_flow import buttons
from tests.presentation.test_party_flow import cache as cache
from tests.presentation.test_party_flow import characters as characters
from tests.presentation.test_party_flow import guilds as guilds
from tests.presentation.test_party_flow import parties as parties
from tests.presentation.test_party_flow import registry as registry
from tests.presentation.test_party_flow import sent as sent
from tests.presentation.test_party_flow import table as table


async def prepare(player, hero):
    content = player.deps["registry"].current
    await player.deps["characters"].save(advanced(content, hero))
    return LongGoals(
        content,
        player.deps["cache"],
        player.deps["characters"],
        player.deps["inventory"],
        player.deps["guilds"],
    )


async def test_main_goals_commands_back_and_real_trial_navigation(table):
    player, _, hero, _ = table
    await prepare(player, hero)
    assert "Долгие цели" in buttons(await player.press("Главное меню"))
    shown = await player.press("Долгие цели")
    assert shown.id is ScreenId.LONG_GOALS
    assert "двух разных игроков" in shown.text()
    assert shown.all_rows()[-1][0].text == "Назад"
    for row in shown.all_rows():
        assert all(len(one.text) <= 128 for one in row)
    assert (await player.press("/цели испытание")).id is ScreenId.DUNGEON
    assert (await player.press("/цели")).id is ScreenId.LONG_GOALS
    assert (await player.press("/назад")).id is ScreenId.MAIN_MENU
    assert (await player.press("/меню")).id is ScreenId.MAIN_MENU
    collected = await player.press("/цели хроника")
    assert "Письма мимо Престола" in collected.text()
    assert "Веденей не сжёг письма" in collected.text()
    assert (
        "не распознано" in (await player.press("/цели неизвестно")).text()
        or "Условия" in (await player.press("/цели неизвестно")).text()
    )


async def test_shared_story_preview_late_confirmation_and_city_consequence(table):
    player, other, hero, visitor = table
    service = await prepare(player, hero)
    await prepare(other, visitor)
    assert (await player.press("/историямира")).id is ScreenId.LONG_STORY
    preview = await player.press("Выбрать: Сигнальный двор")
    assert "10 процентов" in preview.text() and "Подтвердить: Сигнальный двор" in buttons(preview)
    assert await service.story() is None
    assert (await player.press("/меню")).id is ScreenId.MAIN_MENU
    await player.press("/историямира")
    assert "принял" in (await player.press("Подтвердить: Сигнальный двор")).text()
    city = await other.press("/город")
    assert "сигнальный двор" in city.text()
    events = CityEvents(service.content, service._cache, service.characters, service.inventory)
    assert await events.travel_discount(visitor.city_id) == 0  # original saved city was farhold
    assert await events.travel_discount("last_beacon") == 10
    assert (
        "Запись прочитана: 1 из 1"
        in (await other.press("/историямира изучить") and await other.press("/цели")).text()
    )
    assert "600 золота" in (await other.press("Награда за хронику")).text()
    assert "уже" in (await other.press("/цели награда хроника")).text()


async def test_project_rights_members_names_and_commands(table):
    player, other, hero, visitor = table
    service = await prepare(player, hero)
    await prepare(other, visitor)
    guild = await service.guilds.create("Путевой двор", hero.id)
    from mmorpg.domain.rules.guild import GuildRank

    await service.guilds.save(guild.with_member(visitor.id, GuildRank.MEMBER))
    assert "Общий проект гильдии" in buttons(await player.press("/гильдия"))
    assert "основатель" in (await other.press("/проект начать")).text()
    await player.press("/проект")
    shown = await player.press("Начать путевой двор")
    assert shown.id is ScreenId.LONG_PROJECT and "Срока сдачи нет" in shown.text()
    assert (await other.press("/проект")).id is ScreenId.LONG_PROJECT
    assert (await other.press("/гильдия казна")).id is ScreenId.GUILD_VAULT


async def test_story_does_not_accept_old_confirmation_or_changed_eligibility(table):
    player, _, hero, _ = table
    service = await prepare(player, hero)
    assert "Сначала прочитайте" in (await player.press("/историямира подтвердить 1")).text()
    await player.press("/историямира выбрать 1")
    actor = await service.characters.get(hero.id)
    await service.characters.save(replace(actor, city_id="farhold"))
    assert "вернитесь" in (await player.press("/историямира подтвердить 1")).text()
    assert await service.story() is None
