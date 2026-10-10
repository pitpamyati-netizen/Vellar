"""Один бой — одно очко. Участники закреплены за аккаунтами при начале войны."""

from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.domain.entities.long_goal import WarBattleCredit, WarRoster
from mmorpg.domain.ports.repositories import CharacterRepository
from mmorpg.domain.rules.guild_war import War

CODEC = TypeAdapter(WarRoster)
TTL = 10**12


class WarScoring:
    def __init__(self, guilds: GuildStore, characters: CharacterRepository) -> None:
        self.guilds = guilds
        self.characters = characters
        self._cache = guilds.state

    async def load(self, war_id: int) -> WarRoster | None:
        raw = await self._cache.get(f"long-goal:war:{war_id}")
        return CODEC.validate_json(raw) if raw else None

    async def _save(self, war_id: int, state: WarRoster) -> None:
        await self._cache.set(f"long-goal:war:{war_id}", CODEC.dump_json(state).decode(), TTL)

    @atomic_action
    async def begin(self, war: War, *, enabled: bool = True) -> WarRoster:
        if saved := await self.load(war.id):
            return saved
        members = []
        for guild_id in (war.challenger_id, war.defender_id):
            guild = await self.guilds.by_id(guild_id)
            if guild is not None:
                for member in guild.members:
                    actor = await self.characters.get(member.character_id)
                    if actor is not None:
                        members.append((guild_id, actor.id, actor.user_id))
        state = WarRoster(enabled=enabled, members=tuple(members))
        await self._save(war.id, state)
        return state

    @atomic_action
    async def score(
        self,
        war: War,
        guild_id: int,
        winners: tuple[int, ...],
        losers: tuple[int, ...],
        battle_id: str,
        *,
        now: int,
        account_seconds: int,
    ) -> str:
        current = await self.guilds.war_of(guild_id)
        if (
            not battle_id
            or current is None
            or current.id != war.id
            or current.over
            or (current.clock_seconds and now >= current.ends)
            or not current.has(guild_id)
        ):
            return "Бой не относится к действующей войне."
        state = await self.load(war.id)
        if state is None or not state.enabled:
            # Прежние войны получают состав при первом обращении, но до завершения
            # этой войны не получают новых очков: прошлого состава доказать нельзя.
            await self.begin(current, enabled=False)
            return (
                "Для прежней войны новый зачёт закрыт: состав при начале "
                "неизвестен. Ставки и прежний счёт сохранены."
            )
        if any(one.battle_id == battle_id for one in state.battles):
            return "Этот общий бой уже учтён."
        reason = ""
        sides = ((guild_id, winners), (current.foe_of(guild_id), losers))
        accounts: list[int] = []
        if not winners or not losers:
            reason = "Для очка должны действовать игроки обеих сторон."
        for side, ids in sides:
            for key in ids:
                actor = await self.characters.get(key)
                guild = await self.guilds.of(key)
                if (
                    actor is None
                    or guild is None
                    or guild.id != side
                    or (side, key, actor.user_id) not in state.members
                ):
                    reason = (
                        "Состав изменился: очко доступно участникам, закреплённым при начале войны."
                    )
                    continue
                accounts.append(actor.user_id)
        if len(accounts) != len(set(accounts)):
            reason = "Один аккаунт не может выступать несколькими персонажами в зачтённом бою."
        for account in set(accounts):
            last = await self._cache.get(f"long-goal:war-account:{account}")
            if account in state.used_accounts or (last is not None and now < int(last)):
                reason = (
                    "Этот аккаунт уже участвовал в зачтённом бою войны или ещё "
                    "действует перерыв между зачётами."
                )
        if not reason:
            await self.guilds.record_war_point(war.id, guild_id)
            for account in set(accounts):
                await self._cache.set(
                    f"long-goal:war-account:{account}", str(now + account_seconds), TTL
                )
        await self._save(
            war.id,
            replace(
                state,
                used_accounts=state.used_accounts
                if reason
                else (*state.used_accounts, *sorted(set(accounts))),
                battles=(
                    *state.battles,
                    WarBattleCredit(battle_id, guild_id, winners, losers, not reason, reason, now),
                ),
            ),
        )
        return reason or (
            "Один общий бой: одно очко вашей гильдии. Участие всех действовавших игроков записано."
        )
