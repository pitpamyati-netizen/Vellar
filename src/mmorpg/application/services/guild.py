"""Гильдия: где лежит её состав, как в неё зовут, чем она растёт и как воюет.

Правила - в ``domain/rules/guild.py``, ``guild_war.py`` и ``guild_contract.py``;
здесь только хранение и оркестровка: завести, позвать, согласиться, уйти,
распустить, сменить звание, выгнать, подвинуть казну и хранилище, записать
деяния, посчитать подряд и подвести войну.

Что где лежит:

- **состав, казна, хранилище и войны** - в базе (``GuildRepository``, ADR 0030,
  0077): гильдию и её добро нельзя терять между заходами;
- **зов в гильдию** действует сутки, а SQL сохраняет исход и возможность
  позднего повтора (ADR 0092); зов на войну сохраняет прежние правила срока;
- **счёт периода** - выемка, подряд и зачтённые очки войны сохраняются
  в PostgreSQL вместе с ценностями (M02.2). Период входит в ключ; прежний
  счёт не удаляется при потере кэша и не переносится в следующий период.
"""

from __future__ import annotations

from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.invitations import DAY, Invitations
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.long_goal import ProjectState
from mmorpg.domain.ports.repositories import GuildRepository, StateCache
from mmorpg.domain.procgen.seeds import seconds_left_in_rotation
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import guild_war as war_rules
from mmorpg.domain.rules import long_goal as goal_rules
from mmorpg.domain.rules.guild import Guild, GuildRank
from mmorpg.domain.rules.guild_contract import Contract, ContractKind, contracts
from mmorpg.domain.rules.guild_war import War

#: Сколько ждёт ответа зов в гильдию.
CALL_TTL = DAY


class GuildStore:
    """Гильдии и приглашения с явным сроком и сохранённым исходом."""

    def __init__(
        self, roster: GuildRepository, cache: StateCache, *, call_ttl: int = CALL_TTL
    ) -> None:
        self._roster = roster
        self._cache = cache
        self._call_ttl = call_ttl

    @property
    def state(self) -> StateCache:
        return self._cache

    @property
    def invitations(self) -> Invitations:
        return Invitations(self._cache, "guild", self._call_ttl)

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

    @atomic_action
    async def disband(self, guild: Guild) -> bool:
        return await self._roster.disband(guild.id)

    @atomic_action
    async def credit_deposit(self, guild: Guild, amount: int) -> int:
        return await self._roster.credit_deposit(guild.id, amount)

    async def recycle_withdrawal(self, guild: Guild, amount: int) -> None:
        await self._roster.recycle_withdrawal(guild.id, amount)

    async def save(self, guild: Guild) -> None:
        await self._roster.save(guild)

    async def deposit(self, guild: Guild, amount: int) -> None:
        await self._roster.deposit(guild.id, amount)

    async def withdraw(self, guild: Guild, amount: int) -> bool:
        return await self._roster.withdraw(guild.id, amount)

    async def record_deeds(self, guild_id: int, character_id: int, deeds: int) -> None:
        """Записать деяния гильдии и вклад того, кто их сделал (ADR 0076)."""
        await self._roster.record_deeds(guild_id, character_id, deeds)

    async def record_group_deed(self, guild_id: int, character_ids: tuple[int, ...]) -> None:
        await self._roster.record_group_deed(guild_id, character_ids)

    @atomic_action
    async def period(
        self, guild_id: int, purpose: str, *, now: int, seconds: int
    ) -> tuple[int, int]:
        """Сохранённая граница; новая настройка действует после прежнего срока."""
        if seconds <= 0:
            raise ValueError("Period duration must be positive")
        prefix = f"guild-period:{guild_id}:{purpose}"
        end = await self._count(f"{prefix}:end")
        index = await self._count(f"{prefix}:index")
        if end and now < end:
            return index, end
        if end:
            skipped = max(0, (now - end) // seconds)
            index += skipped + 1
            end += (skipped + 1) * seconds
        else:
            end = now + seconds
        await self._cache.set(f"{prefix}:end", str(end), max(1, end - now))
        await self._cache.set(f"{prefix}:index", str(index), max(1, end - now))
        return index, end

    @atomic_action
    async def current_contracts(
        self,
        content: GameContent,
        place: guild_rules.Standing,
        *,
        guild_id: int,
        world_seed: str,
        now: int,
        seconds: int,
    ) -> tuple[Contract, ...]:
        index, end = await self.period(guild_id, "contract", now=now, seconds=seconds)
        seed = await self._count(f"guild-period:{guild_id}:contract:seed") if index == 0 else index
        deals = contracts(
            content.guild_tiers, place, world_seed=world_seed, guild_id=guild_id, rotation=seed
        )
        saved: list[Contract] = []
        for deal in deals:
            prefix = f"guild-contract-snapshot:{guild_id}:{index}:{deal.kind.value}"
            values: dict[str, int] = {}
            for name in ("target", "level", "reward_gold", "reward_deeds"):
                raw = await self._cache.get(f"{prefix}:{name}")
                if raw is None:
                    raw = str(getattr(deal, name))
                    await self._cache.set(f"{prefix}:{name}", raw, max(1, end - now))
                values[name] = int(raw)
            saved.append(
                replace(
                    deal,
                    line=deal.line.replace(str(deal.target), str(values["target"])),
                    target=values["target"],
                    level=values["level"],
                    reward_gold=values["reward_gold"],
                    reward_deeds=values["reward_deeds"],
                )
            )
        return tuple(saved)

    async def taken(
        self, guild_id: int, character_id: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Сколько этот человек уже вынес из казны за нынешний период.

        Счёт постоянный, ключ включает период. Потеря кэша его не обнуляет.
        """
        rotation, _ = await self.period(guild_id, "limits", now=now, seconds=rotation_seconds)
        stored = await self._cache.get(f"guild-taken-v2:{guild_id}:{character_id}:{rotation}")
        return int(stored) if stored and stored.isdigit() else 0

    async def note_taken(
        self, guild_id: int, character_id: int, amount: int, *, now: int, rotation_seconds: int
    ) -> None:
        """Прибавить взятое к счёту периода."""
        rotation, end = await self.period(guild_id, "limits", now=now, seconds=rotation_seconds)
        key = f"guild-taken-v2:{guild_id}:{character_id}:{rotation}"
        already = await self.taken(
            guild_id, character_id, now=now, rotation_seconds=rotation_seconds
        )
        await self._cache.set(key, str(already + max(0, amount)), max(1, end - now))

    @atomic_action
    async def call(
        self, *, guild_id: int, invitee_id: int, inviter_id: int = 0, now: int | None = None
    ) -> str:
        guild = await self.by_id(guild_id)
        issuer = inviter_id or (guild.founder_id if guild else 0)
        return await self.invitations.send(guild_id, issuer, invitee_id, now=now)

    async def called_to(self, invitee_id: int) -> int:
        return await self.invitations.current(invitee_id)

    async def forget_call(self, invitee_id: int) -> None:
        await self.invitations.finish(invitee_id)

    @atomic_action
    async def accept(self, invitee_id: int, content: GameContent) -> Guild | None:
        """Согласиться вступить. ``None`` — звать уже некому или гильдии нет.

        Мест в гильдии столько, сколько даёт её ступень: пока зов висел, гильдия
        могла набраться под завязку (ADR 0076).
        """
        guild_id = await self.called_to(invitee_id)
        if not guild_id:
            return None
        if await self._roster.of(invitee_id) is not None:
            return None
        guild = await self._roster.by_id(guild_id)
        if guild is None:
            return None
        seats = guild_rules.standing(content, guild).seats
        if guild.size >= seats or guild.has(invitee_id):
            return guild
        call = await self.invitations.get(invitee_id)
        if (
            call
            and call.inviter_id
            and (
                not guild.can_invite(call.inviter_id)
                or await self.invitations.blocked(call.inviter_id, invitee_id)
            )
        ):
            return None
        joined = guild.with_member(invitee_id, GuildRank.RECRUIT, seats=seats)
        await self._roster.save(joined)
        await self.invitations.finish(invitee_id, "accepted")
        return joined

    @atomic_action
    async def leave(self, character_id: int) -> Guild | None:
        """Уйти из гильдии. Основатель так уйти не может — он её распускает."""
        guild = await self._roster.of(character_id)
        if guild is None or guild.founder_id == character_id:
            return None
        await self._roster.save(guild.without(character_id))
        return guild

    # --- постоянный счёт по периодам ------------------------------

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
        """Сколько вещей этот человек уже вынес из хранилища за период."""
        rotation, _ = await self.period(guild_id, "limits", now=now, seconds=rotation_seconds)
        return await self._count(f"guild-items-v2:{guild_id}:{character_id}:{rotation}")

    async def note_taken_items(
        self, guild_id: int, character_id: int, amount: int, *, now: int, rotation_seconds: int
    ) -> None:
        rotation, _ = await self.period(guild_id, "limits", now=now, seconds=rotation_seconds)
        await self._add(
            f"guild-items-v2:{guild_id}:{character_id}:{rotation}",
            amount,
            now=now,
            rotation_seconds=rotation_seconds,
        )

    # --- подряд гильдии (ADR 0077) ------------------------------------

    async def contract_progress(
        self, guild_id: int, *, now: int, rotation_seconds: int
    ) -> dict[ContractKind, int]:
        """Сколько сделано по каждому делу подряда за нынешний период."""
        rotation, _ = await self.period(guild_id, "contract", now=now, seconds=rotation_seconds)
        return {
            kind: await self._count(f"guild-contract-v2:{guild_id}:{rotation}:{kind.value}")
            for kind in ContractKind
        }

    async def advance_contract(
        self, guild_id: int, kind: ContractKind, amount: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Записать сделанное по одному делу подряда. Ответ - сколько стало всего."""
        rotation, _ = await self.period(guild_id, "contract", now=now, seconds=rotation_seconds)
        return await self._add(
            f"guild-contract-v2:{guild_id}:{rotation}:{kind.value}",
            amount,
            now=now,
            rotation_seconds=rotation_seconds,
        )

    async def claim_contract(
        self, guild_id: int, kind: ContractKind, *, now: int, rotation_seconds: int
    ) -> bool:
        """Пометить дело закрытым. Ложь - за него уже заплатили в этот период.

        Отметка сохраняется в одной операции с выплатой (M02.2).
        """
        rotation, end = await self.period(guild_id, "contract", now=now, seconds=rotation_seconds)
        key = f"guild-contract-paid-v2:{guild_id}:{rotation}:{kind.value}"
        if await self._cache.get(key):
            return False
        await self._cache.set(key, "1", max(1, end - now))
        return True

    async def add_deeds(self, guild_id: int, deeds: int) -> None:
        """Деяния гильдии целиком: подряд и война (ADR 0077)."""
        await self._roster.add_deeds(guild_id, deeds)

    async def pay_vault(self, guild_id: int, amount: int) -> None:
        """Положить в казну гильдии, которую читать незачем: подряд и война."""
        await self._roster.deposit(guild_id, amount)

    @atomic_action
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
        contributors: tuple[int, ...] = (),
        receipt: str = "",
    ) -> Contract | None:
        """Записать сделанное по подряду и заплатить, если дело этим закрылось.

        Ответ - закрытое дело, и только тому, чьё движение его закрыло: платят
        за него один раз, а сказать о нём надо тому, кто это увидел. ``None`` -
        дело ещё идёт или за него уже заплатили (ADR 0077).
        """
        if contributors and receipt:
            await self.work_on_project(content, guild_id, kind, amount, contributors, receipt)
        progress = await self.advance_contract(
            guild_id, kind, amount, now=now, rotation_seconds=rotation_seconds
        )
        deals = await self.current_contracts(
            content,
            place,
            world_seed=world_seed,
            guild_id=guild_id,
            now=now,
            seconds=rotation_seconds,
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

    @atomic_action
    async def work_on_project(
        self,
        content: GameContent,
        guild_id: int,
        kind: ContractKind,
        amount: int,
        contributors: tuple[int, ...],
        receipt: str,
    ) -> None:
        key = f"long-goal:project:{guild_id}"
        raw = await self._cache.get(key)
        guild = await self.by_id(guild_id)
        if not raw or guild is None:
            return
        codec = TypeAdapter(ProjectState)
        previous = codec.validate_json(raw)
        members = tuple(one for one in contributors if guild.has(one))
        state = goal_rules.contribute(
            previous, kind.value, amount, members, f"{kind.value}:{receipt}"
        )
        if state == previous:
            return
        await self._cache.set(key, codec.dump_json(state).decode(), 10**12)
        if state.complete and not previous.complete:
            await self.add_deeds(guild_id, state.rules.project_deeds)

    async def record_war_point(self, war_id: int, guild_id: int) -> None:
        await self._roster.score_war(war_id, guild_id)

    # --- война гильдий (ADR 0077) -------------------------------------

    async def war_of(self, guild_id: int) -> War | None:
        return await self._roster.war_of(guild_id)

    @atomic_action
    async def timed_war(
        self, guild_id: int, *, now: int, legacy_seconds: int, duration: int
    ) -> War | None:
        war = await self.war_of(guild_id)
        if war is not None and not war.clock_seconds:
            started = war.started * legacy_seconds
            end = max(war.ends * legacy_seconds, now + duration)
            await self._roster.set_war_clock(war.id, started, end)
            war = replace(war, started=started, ends=end, clock_seconds=True)
        return war

    async def call_war(
        self, *, challenger_id: int, defender_id: int, seconds: int = war_rules.CALL_TTL
    ) -> None:
        """Послать вызов. Висит час и гаснет сам: ставку снимают при согласии."""
        await self._cache.set(self._war_call_key(defender_id), str(challenger_id), seconds)

    async def war_called_by(self, defender_id: int) -> int:
        called = await self._cache.get(self._war_call_key(defender_id))
        return int(called) if called else 0

    async def forget_war_call(self, defender_id: int) -> None:
        await self._cache.delete(self._war_call_key(defender_id))

    @atomic_action
    async def open_war(
        self,
        *,
        challenger_id: int,
        defender_id: int,
        stake: int,
        started: int,
        ends: int,
        clock_seconds: bool = False,
    ) -> War:
        war = await self._roster.open_war(
            challenger_id=challenger_id,
            defender_id=defender_id,
            stake=stake,
            started=started,
            ends=ends,
        )
        if clock_seconds:
            await self._roster.set_war_clock(war.id, started, ends)
            war = replace(war, clock_seconds=True)
        return war

    @atomic_action
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
        """Прежняя парная запись закрыта. Используйте WarScoring с аккаунтами."""
        return False

    @atomic_action
    async def settle_war(self, content: GameContent, war: War, rotation: int) -> War | None:
        """Подвести войну, если её срок вышел. ``None`` - подводить нечего.

        Расчёт ленивый: часов в игре нет, и войну закрывает тот, кто первым
        заглянул на неё после срока. Закрывается она условным движением, поэтому
        двое, заглянувшие разом, не заплатят дважды.
        """
        current = await self._roster.war_of(war.challenger_id)
        if current is None or current.id != war.id:
            return None
        war = current
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
