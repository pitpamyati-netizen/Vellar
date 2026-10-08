"""Подтверждение роспуска и возврат имущества одной операцией."""

from hashlib import sha256

from mmorpg.application.operations import MissingResourceError, atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository
from mmorpg.domain.rules.guild import Guild


def disband_token(guild: Guild, stock: tuple[tuple[str, int], ...]) -> str:
    return sha256(repr((guild, tuple(sorted(stock)))).encode()).hexdigest()


@atomic_action
async def dissolve(
    guilds: GuildStore,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    guild_id: int,
    *,
    actor_id: int,
    expected: str,
) -> str:
    guild = await guilds.by_id(guild_id)
    if guild is None:
        return "Эта гильдия уже распущена."
    if guild.founder_id != actor_id:
        return "Роспуск подтверждает нынешний основатель."
    if await guilds.war_of(guild.id) is not None:
        return "Гильдия воюет. Сначала дождитесь расчёта войны: обе ставки сохраняются."
    stock = await guilds.stock(guild.id)
    if not expected or expected != disband_token(guild, stock):
        return (
            "Казна, склад или состав изменились. Откройте роспуск заново и проверьте последствия."
        )
    if guild.vault_gold:
        if not await guilds.withdraw(guild, guild.vault_gold):
            raise MissingResourceError("guild vault")
        await characters.grant_gold(actor_id, guild.vault_gold)
    for item_id, quantity in stock:
        if not await guilds.unstow(guild.id, item_id, quantity):
            raise MissingResourceError("guild stock")
        await inventory.add(actor_id, item_id, quantity)
    if not await guilds.disband(guild):
        raise MissingResourceError("guild obligations")
    return "Гильдия распущена. Казна и все вещи склада переданы основателю. История сохранена."
