"""Гильдия: где лежит её состав, как в неё зовут, чем она растёт и как воюет.

Правила - в ``domain/rules/guild.py``, ``guild_war.py`` и ``guild_contract.py``;
здесь только хранение и оркестровка: завести, позвать, согласиться, уйти,
распустить, сменить звание, выгнать, подвинуть казну и хранилище, записать
деяния, посчитать подряд и подвести войну.

Что где лежит:

- **состав, казна, хранилище и войны** - в базе (``GuildRepository``, ADR 0030,
  0077): гильдию и её добро нельзя терять между заходами;
- **зовы** (в гильдию и на войну) - в кэше со сроком, как и зов в отряд: зов,
  который нельзя ни принять, ни отменить, хуже, чем никакого;
- **счёт переворота** - взятое из казны и из хранилища, счёт подряда и уже
  засчитанные очки войны - в кэше со сроком до конца переворота. Всё это живёт
  ровно переворот, и забыть его на перевороте - ровно то, что задумано
  (ADR 0053, 0076, 0077).
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import GuildRepository, StateCache
from mmorpg.domain.procgen.seeds import rotation_index, seconds_left_in_rotation
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import guild_war as war_rules
from mmorpg.domain.rules.guild import Guild, GuildRank
from mmorpg.domain.rules.guild_contract import Contract, ContractKind, contracts
from mmorpg.domain.rules.guild_war import War

#: Сколько ждёт ответа зов в гильдию.
CALL_TTL = 600


class GuildStore:
    """Гильдии (в базе) и незакрытые приглашения (в кэше со сроком)."""

    def __init__(
        self, roster: GuildRepository, cache: StateCache, *, call_ttl: int = CALL_TTL
    ) -> None:
        self._roster = roster
        self._cache = cache
        self._call_ttl = call_ttl

    @staticmethod
    def _call_key(character_id: int) -> str:
        return f"guild-call:{character_id}"

    @staticmethod
    def _taken_key(guild_id: int, character_id: int, rotation: int) -> str:
        return f"guild-taken:{guild_id}:{character_id}:{rotation}"

    async def of(self, character_id: int) -> Guild | None:
        return await self._roster.of(character_id)

    async def by_id(self, guild_id: int) -> Guild | None:
        return await self._roster.by_id(guild_id)

    async def by_name(self, name: str) -> Guild | None:
        return await self._roster.by_name(name)

    async def create(self, name: str, founder_id: int) -> Guild:
        return await self._roster.create(name, founder_id)

    async def disband(self, guild: Guild) -> None:
        await self._roster.disband(guild.id)

    async def save(self, guild: Guild) -> None:
        await self._roster.save(guild)

    async def deposit(self, guild: Guild, amount: int) -> None:
        await self._roster.deposit(guild.id, amount)

    async def withdraw(self, guild: Guild, amount: int) -> bool:
        return await self._roster.withdraw(guild.id, amount)

    async def record_deeds(self, guild_id: int, character_id: int, deeds: int) -> None:
        """Записать деяния гильдии и вклад того, кто их сделал (ADR 0076)."""
        await self._roster.record_deeds(guild_id, character_id, deeds)

    async def taken(
        self, guild_id: int, character_id: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Сколько этот человек уже вынес из казны за нынешний переворот прилавка.

        Счёт держит ключ со сроком до конца переворота - как разовость надбавки
        за дело сводки (``presentation/telegram/digest_claim.py``): предел
        выемки не стоит отдельной таблицы, а забыть его на перевороте - ровно то,
        что и задумано.
        """
        rotation = rotation_index(now, rotation_seconds)
        stored = await self._cache.get(self._taken_key(guild_id, character_id, rotation))
        return int(stored) if stored and stored.isdigit() else 0

    async def note_taken(
        self, guild_id: int, character_id: int, amount: int, *, now: int, rotation_seconds: int
    ) -> None:
        """Прибавить взятое к счёту переворота."""
        rotation = rotation_index(now, rotation_seconds)
        key = self._taken_key(guild_id, character_id, rotation)
        already = await self.taken(
            guild_id, character_id, now=now, rotation_seconds=rotation_seconds
        )
        await self._cache.set(
            key, str(already + max(0, amount)), seconds_left_in_rotation(now, rotation_seconds)
        )

    async def call(self, *, guild_id: int, invitee_id: int) -> None:
        await self._cache.set(self._call_key(invitee_id), str(guild_id), self._call_ttl)

    async def called_to(self, invitee_id: int) -> int:
        guild_id = await self._cache.get(self._call_key(invitee_id))
        return int(guild_id) if guild_id else 0

    async def forget_call(self, invitee_id: int) -> None:
        await self._cache.delete(self._call_key(invitee_id))

    async def accept(self, invitee_id: int, content: GameContent) -> Guild | None:
        """Согласиться вступить. ``None`` — звать уже некому или гильдии нет.

        Мест в гильдии столько, сколько даёт её ступень: пока зов висел, гильдия
        могла набраться под завязку (ADR 0076).
        """
        guild_id = await self.called_to(invitee_id)
        await self.forget_call(invitee_id)
        if not guild_id:
            return None
        guild = await self._roster.by_id(guild_id)
        if guild is None:
            return None
        seats = guild_rules.standing(content, guild).seats
        if guild.size >= seats or guild.has(invitee_id):
            return guild
        joined = guild.with_member(invitee_id, GuildRank.RECRUIT, seats=seats)
        await self._roster.save(joined)
        return joined

    async def leave(self, character_id: int) -> Guild | None:
        """Уйти из гильдии. Основатель так уйти не может — он её распускает."""
        guild = await self._roster.of(character_id)
        if guild is None or guild.founder_id == character_id:
            return None
        await self._roster.save(guild.without(character_id))
        return guild

    # --- счёт, который живёт один переворот ---------------------------
    #
    # Общий счётчик со сроком до конца переворота: им считаются и взятое из
    # казны, и вынесенное из хранилища, и сделанное по подряду. Ключей три вида,
    # а правило одно - на перевороте счёт забывается сам.

    async def _count(self, key: str) -> int:
        stored = await self._cache.get(key)
        return int(stored) if stored and stored.isdigit() else 0

    async def _add(self, key: str, amount: int, *, now: int, rotation_seconds: int) -> int:
        total = await self._count(key) + max(0, amount)
        await self._cache.set(key, str(total), seconds_left_in_rotation(now, rotation_seconds))
        return total

    @staticmethod
    def _items_key(guild_id: int, character_id: int, rotation: int) -> str:
        return f"guild-items:{guild_id}:{character_id}:{rotation}"

    @staticmethod
    def _contract_key(guild_id: int, rotation: int, kind: ContractKind) -> str:
        return f"guild-contract:{guild_id}:{rotation}:{kind.value}"

    @staticmethod
    def _paid_key(guild_id: int, rotation: int, kind: ContractKind) -> str:
        return f"guild-contract-paid:{guild_id}:{rotation}:{kind.value}"

    @staticmethod
    def _war_call_key(defender_id: int) -> str:
        return f"guild-war-call:{defender_id}"

    @staticmethod
    def _war_hit_key(war_id: int, winner_id: int, loser_id: int, rotation: int) -> str:
        return f"guild-war-hit:{war_id}:{winner_id}:{loser_id}:{rotation}"

    # --- хранилище гильдии (ADR 0077) ---------------------------------

    async def stock(self, guild_id: int) -> tuple[tuple[str, int], ...]:
        """Что лежит в хранилище: пары «вещь - сколько»."""
        return await self._roster.stock(guild_id)

    async def stow(self, guild_id: int, item_id: str, amount: int) -> None:
        await self._roster.stow(guild_id, item_id, amount)

    async def unstow(self, guild_id: int, item_id: str, amount: int) -> bool:
        return await self._roster.unstow(guild_id, item_id, amount)

    async def taken_items(
        self, guild_id: int, character_id: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Сколько вещей этот человек уже вынес из хранилища за переворот."""
        rotation = rotation_index(now, rotation_seconds)
        return await self._count(self._items_key(guild_id, character_id, rotation))

    async def note_taken_items(
        self, guild_id: int, character_id: int, amount: int, *, now: int, rotation_seconds: int
    ) -> None:
        rotation = rotation_index(now, rotation_seconds)
        await self._add(
            self._items_key(guild_id, character_id, rotation),
            amount,
            now=now,
            rotation_seconds=rotation_seconds,
        )

    # --- подряд гильдии (ADR 0077) ------------------------------------

    async def contract_progress(
        self, guild_id: int, *, now: int, rotation_seconds: int
    ) -> dict[ContractKind, int]:
        """Сколько сделано по каждому делу подряда за нынешний переворот."""
        rotation = rotation_index(now, rotation_seconds)
        return {
            kind: await self._count(self._contract_key(guild_id, rotation, kind))
            for kind in ContractKind
        }

    async def advance_contract(
        self, guild_id: int, kind: ContractKind, amount: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Записать сделанное по одному делу подряда. Ответ - сколько стало всего."""
        rotation = rotation_index(now, rotation_seconds)
        return await self._add(
            self._contract_key(guild_id, rotation, kind),
            amount,
            now=now,
            rotation_seconds=rotation_seconds,
        )

    async def claim_contract(
        self, guild_id: int, kind: ContractKind, *, now: int, rotation_seconds: int
    ) -> bool:
        """Пометить дело закрытым. Ложь - за него уже заплатили в этот переворот.

        Разовость держит ключ со сроком, как у надбавки сводки (ADR 0053): дело
        закрывается само, и заплатить за него дважды не должно выйти даже у
        двоих, добивших стаю разом.
        """
        rotation = rotation_index(now, rotation_seconds)
        key = self._paid_key(guild_id, rotation, kind)
        if await self._cache.get(key):
            return False
        await self._cache.set(key, "1", seconds_left_in_rotation(now, rotation_seconds))
        return True

    async def add_deeds(self, guild_id: int, deeds: int) -> None:
        """Деяния гильдии целиком: подряд и война (ADR 0077)."""
        await self._roster.add_deeds(guild_id, deeds)

    async def pay_vault(self, guild_id: int, amount: int) -> None:
        """Положить в казну гильдии, которую читать незачем: подряд и война."""
        await self._roster.deposit(guild_id, amount)

    async def work_on_contract(
        self,
        content: GameContent,
        *,
        guild_id: int,
        place: guild_rules.Standing,
        kind: ContractKind,
        amount: int,
        world_seed: str,
        now: int,
        rotation_seconds: int,
    ) -> Contract | None:
        """Записать сделанное по подряду и заплатить, если дело этим закрылось.

        Ответ - закрытое дело, и только тому, чьё движение его закрыло: платят
        за него один раз, а сказать о нём надо тому, кто это увидел. ``None`` -
        дело ещё идёт или за него уже заплатили (ADR 0077).
        """
        progress = await self.advance_contract(
            guild_id, kind, amount, now=now, rotation_seconds=rotation_seconds
        )
        rotation = rotation_index(now, rotation_seconds)
        deals = contracts(
            content.guild_tiers,
            place,
            world_seed=world_seed,
            guild_id=guild_id,
            rotation=rotation,
        )
        deal = next((one for one in deals if one.kind is kind), None)
        if deal is None or not deal.done(progress):
            return None
        if not await self.claim_contract(
            guild_id, kind, now=now, rotation_seconds=rotation_seconds
        ):
            return None
        await self._roster.deposit(guild_id, deal.reward_gold)
        await self._roster.add_deeds(guild_id, deal.reward_deeds)
        return deal

    # --- война гильдий (ADR 0077) -------------------------------------

    async def war_of(self, guild_id: int) -> War | None:
        return await self._roster.war_of(guild_id)

    async def call_war(self, *, challenger_id: int, defender_id: int) -> None:
        """Послать вызов. Висит час и гаснет сам: ставку снимают при согласии."""
        await self._cache.set(
            self._war_call_key(defender_id), str(challenger_id), war_rules.CALL_TTL
        )

    async def war_called_by(self, defender_id: int) -> int:
        called = await self._cache.get(self._war_call_key(defender_id))
        return int(called) if called else 0

    async def forget_war_call(self, defender_id: int) -> None:
        await self._cache.delete(self._war_call_key(defender_id))

    async def open_war(
        self, *, challenger_id: int, defender_id: int, stake: int, started: int, ends: int
    ) -> War:
        return await self._roster.open_war(
            challenger_id=challenger_id,
            defender_id=defender_id,
            stake=stake,
            started=started,
            ends=ends,
        )

    async def score_war(
        self,
        war: War,
        *,
        guild_id: int,
        winner_id: int,
        loser_id: int,
        now: int,
        rotation_seconds: int,
    ) -> bool:
        """Записать войне очко. Ложь - за этого побеждённого уже платили сегодня.

        Раз за переворот на пару «кто кого»: иначе двое сговорившихся набивают
        счёт друг об друга, не выходя из города (``rules/guild_war``).
        """
        rotation = rotation_index(now, rotation_seconds)
        key = self._war_hit_key(war.id, winner_id, loser_id, rotation)
        if await self._cache.get(key):
            return False
        await self._cache.set(key, "1", seconds_left_in_rotation(now, rotation_seconds))
        await self._roster.score_war(war.id, guild_id)
        return True

    async def settle_war(self, content: GameContent, war: War, rotation: int) -> War | None:
        """Подвести войну, если её срок вышел. ``None`` - подводить нечего.

        Расчёт ленивый: часов в игре нет, и войну закрывает тот, кто первым
        заглянул на неё после срока. Закрывается она условным движением, поэтому
        двое, заглянувшие разом, не заплатят дважды.
        """
        if not war.due(rotation):
            return None
        if not await self._roster.close_war(war.id):
            return None
        settled = replace(war, over=True)
        for guild_id, back in war_rules.spoils(settled).items():
            if back:
                await self._roster.deposit(guild_id, back)
        champion = war_rules.winner_of(settled)
        if champion:
            guild = await self._roster.by_id(champion)
            if guild is not None:
                place = guild_rules.standing(content, guild)
                await self._roster.add_deeds(champion, war_rules.war_deeds(place))
        return settled
