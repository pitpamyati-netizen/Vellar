"""Хранилища на PostgreSQL.

Явный SQL поверх asyncpg, без ORM (``docs/adr/0001-no-orm.md``). Каждый запрос
здесь трогает одну-две строки по первичному ключу или по уникальному указателю,
и это то, что держит обновление внутри бюджета в 100 мс.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from mmorpg.domain.entities.character import (
    Character,
    Equipment,
    InventoryEntry,
    SkillLoadout,
    ToolWear,
)
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import (
    AccessibilitySettings,
    Census,
    GoldFlowSlice,
    PlayerFilter,
    User,
)
from mmorpg.domain.rules.group_offers import MAX_OFFER_NUMBER
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank
from mmorpg.domain.rules.party import Party as PlayerParty
from mmorpg.domain.rules.turning import COUNCIL_VOTE_CAP

if TYPE_CHECKING:  # pragma: no cover - только для типов
    import asyncpg

CHARACTER_COLUMNS = """
    id, user_id, name, race_id, class_id, level, experience, gold,
    stat_str, stat_agi, stat_end, stat_int, stat_wis, stat_cha, stat_lck,
    trait_ids, loadout, equipment, city_id, unspent_stat_points, unspent_skill_points,
    health, bank_gold, quests, crafts, tools, tutorial, arena_wins, arena_losses,
    arena_credit, remorts, turning_cycle, turning_answer, house_id, is_admin
"""

TRADE_COLUMNS = """
    id, scope, number, kind, status, tax, created_at, settled_at,
    author_user_id, author_character_id, author_name,
    target_user_id, target_character_id, target_name,
    item_id, item_name, quantity, price
"""


def _character_from_row(row: Any) -> Character:
    loadout_raw = json.loads(row["loadout"]) if row["loadout"] else {}
    equipment_raw = json.loads(row["equipment"]) if row["equipment"] else {}
    return Character(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        race_id=row["race_id"],
        class_id=row["class_id"],
        level=row["level"],
        experience=row["experience"],
        gold=row["gold"],
        allocated=StatBlock(
            STR=row["stat_str"],
            AGI=row["stat_agi"],
            END=row["stat_end"],
            INT=row["stat_int"],
            WIS=row["stat_wis"],
            CHA=row["stat_cha"],
            LCK=row["stat_lck"],
        ),
        trait_ids=tuple(row["trait_ids"] or ()),
        loadout=SkillLoadout(
            actives=tuple(loadout_raw.get("actives", [None] * 6)),
            racial=loadout_raw.get("racial"),
            ranks=MappingProxyType(dict(loadout_raw.get("ranks", {}))),
            edges=MappingProxyType(dict(loadout_raw.get("edges", {}))),
        ),
        equipment=Equipment(MappingProxyType(dict(equipment_raw))),
        city_id=row["city_id"],
        unspent_stat_points=row["unspent_stat_points"],
        unspent_skill_points=row["unspent_skill_points"],
        health=row["health"],
        bank_gold=row["bank_gold"],
        quests=_quests_from_json(row["quests"]),
        crafts=_crafts_from_json(row["crafts"]),
        tools=_tools_from_json(row["tools"]),
        tutorial=row["tutorial"],
        arena_wins=row["arena_wins"],
        arena_losses=row["arena_losses"],
        arena_credit=row["arena_credit"],
        remorts=row["remorts"],
        turning_cycle=row["turning_cycle"],
        turning_answer=row["turning_answer"],
        house_id=row["house_id"],
        is_admin=bool(row["is_admin"]),
    )


def _quests_from_json(raw: str | None) -> QuestLog:
    """Журнал заданий, прочитанный целиком. Пустая колонка - пустой журнал."""
    data = json.loads(raw) if raw else {}
    taken = {str(key): int(value) for key, value in dict(data.get("taken", {})).items()}
    return QuestLog(taken=MappingProxyType(taken), done=tuple(data.get("done", ())))


def _quests_to_json(log: QuestLog) -> str:
    return json.dumps({"taken": dict(log.taken), "done": list(log.done)}, ensure_ascii=False)


def _crafts_from_json(raw: str | None) -> CraftLog:
    """Сделанная в ремёслах работа, прочитанная целиком. Пустая колонка - ничего не изучено."""
    data = json.loads(raw) if raw else {}
    entries = {
        str(craft_id): CraftProgress(experience=int(progress.get("experience", 0)))
        for craft_id, progress in dict(data).items()
    }
    return CraftLog(MappingProxyType(entries))


def _crafts_to_json(log: CraftLog) -> str:
    return json.dumps(
        {
            craft_id: {"experience": progress.experience}
            for craft_id, progress in log.entries.items()
        },
        ensure_ascii=False,
    )


def _tools_from_json(raw: str | None) -> ToolWear:
    """Износ инструментов, прочитанный целиком. Пустая колонка - всё как новое."""
    data = json.loads(raw) if raw else {}
    return ToolWear(
        MappingProxyType({str(item_id): int(spent) for item_id, spent in dict(data).items()})
    )


def _tools_to_json(wear: ToolWear) -> str:
    return json.dumps(dict(wear.used), ensure_ascii=False)


def _loadout_to_json(loadout: SkillLoadout) -> str:
    return json.dumps(
        {
            "actives": list(loadout.actives),
            "racial": loadout.racial,
            "ranks": dict(loadout.ranks),
            "edges": dict(loadout.edges),
        },
        ensure_ascii=False,
    )


class PostgresUserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, telegram_id: int) -> User | None:
        row = await self._pool.fetchrow(
            "SELECT telegram_id, username, emoji, verbose_descriptions, page_size, keeper,"
            " banned_until, ban_reason, warnings, muted_until, mute_reason"
            " FROM users WHERE telegram_id = $1",
            telegram_id,
        )
        if row is None:
            return None
        return User(
            telegram_id=row["telegram_id"],
            username=row["username"] or "",
            settings=AccessibilitySettings(
                emoji=row["emoji"],
                # Колонку нельзя назвать «verbose»: это зарезервированное слово
                # PostgreSQL.
                verbose=row["verbose_descriptions"],
                page_size=row["page_size"],
            ),
            keeper=row["keeper"],
            ban=Ban(until=row["banned_until"], reason=row["ban_reason"] or ""),
            warnings=row["warnings"],
            mute=Ban(until=row["muted_until"], reason=row["mute_reason"] or ""),
        )

    async def upsert(self, user: User) -> User:
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, username, emoji, verbose_descriptions, page_size)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
            """,
            user.telegram_id,
            user.username,
            user.settings.emoji,
            user.settings.verbose,
            user.settings.page_size,
        )
        stored = await self.get(user.telegram_id)
        return stored if stored is not None else user

    async def save_settings(self, telegram_id: int, settings: AccessibilitySettings) -> None:
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, emoji, verbose_descriptions, page_size)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id) DO UPDATE
                SET emoji = EXCLUDED.emoji,
                    verbose_descriptions = EXCLUDED.verbose_descriptions,
                    page_size = EXCLUDED.page_size
            """,
            telegram_id,
            settings.emoji,
            settings.verbose,
            settings.page_size,
        )

    async def set_keeper(self, telegram_id: int, keeper: bool) -> None:
        """Право выдают и тому, кто ни разу не трогал настройки, поэтому upsert."""
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, keeper)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET keeper = EXCLUDED.keeper
            """,
            telegram_id,
            keeper,
        )

    async def unchecked(self, *, limit: int, before: int) -> tuple[int, ...]:
        """Кого давно не спрашивали. Уже заблокировавшие второй раз не спрашиваются."""
        rows = await self._pool.fetch(
            "SELECT telegram_id FROM users"
            " WHERE blocked_at = 0 AND checked_at < $1 ORDER BY checked_at, telegram_id LIMIT $2",
            before,
            limit,
        )
        return tuple(row["telegram_id"] for row in rows)

    async def mark_checked(self, telegram_id: int, *, at: int, blocked: bool) -> None:
        await self._pool.execute(
            "UPDATE users SET checked_at = $2, blocked_at = $3 WHERE telegram_id = $1",
            telegram_id,
            at,
            at if blocked else 0,
        )

    async def blocked_count(self) -> int:
        value = await self._pool.fetchval("SELECT count(*) FROM users WHERE blocked_at > 0")
        return int(value or 0)

    async def set_ban(self, telegram_id: int, ban: Ban) -> None:
        """Блокируют и того, кто ни разу не трогал настройки, поэтому upsert."""
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, banned_until, ban_reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE
                SET banned_until = EXCLUDED.banned_until,
                    ban_reason = EXCLUDED.ban_reason
            """,
            telegram_id,
            ban.until,
            ban.reason,
        )

    async def set_mute(self, telegram_id: int, mute: Ban) -> None:
        """Замолчать в группе и того, кто ни разу не трогал настройки, поэтому upsert."""
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, muted_until, mute_reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE
                SET muted_until = EXCLUDED.muted_until,
                    mute_reason = EXCLUDED.mute_reason
            """,
            telegram_id,
            mute.until,
            mute.reason,
        )

    async def banned_count(self, *, now: int) -> int:
        """Истёкший срок никто не снимает: он просто перестаёт считаться."""
        value = await self._pool.fetchval(
            "SELECT count(*) FROM users WHERE banned_until < 0 OR banned_until > $1", now
        )
        return int(value or 0)

    async def banned_ids(self, *, now: int) -> frozenset[int]:
        rows = await self._pool.fetch(
            "SELECT telegram_id FROM users WHERE banned_until < 0 OR banned_until > $1", now
        )
        return frozenset(row["telegram_id"] for row in rows)

    async def warn(self, telegram_id: int, *, delta: int = 1) -> int:
        """Счётчик двигается условным ``UPDATE`` и не уходит в минус."""
        value = await self._pool.fetchval(
            """
            INSERT INTO users (telegram_id, warnings)
            VALUES ($1, GREATEST(0, $2))
            ON CONFLICT (telegram_id) DO UPDATE
                SET warnings = GREATEST(0, users.warnings + $2)
            RETURNING warnings
            """,
            telegram_id,
            delta,
        )
        return int(value or 0)

    async def purge_blocked(self) -> int:
        """Персонажи и сумки уходят каскадом с аккаунтом - так объявлено в 0001."""
        value = await self._pool.fetchval(
            "WITH gone AS (DELETE FROM users WHERE blocked_at > 0 RETURNING 1)"
            " SELECT count(*) FROM gone"
        )
        return int(value or 0)


class PostgresKeeperLogRepository:
    """Журнал смотрителя: дописывается и читается с конца, больше ничего."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(self, entry: KeeperEntry) -> None:
        await self._pool.execute(
            """
            INSERT INTO keeper_log (at, keeper_id, keeper_name, action, target, detail)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            entry.at,
            entry.keeper_id,
            entry.keeper_name,
            entry.action.value,
            entry.target,
            entry.detail,
        )

    async def latest(
        self, *, limit: int = 20, offset: int = 0, target: str = ""
    ) -> tuple[KeeperEntry, ...]:
        if target:
            rows = await self._pool.fetch(
                "SELECT at, keeper_id, keeper_name, action, target, detail"
                " FROM keeper_log WHERE lower(target) = lower($1)"
                " ORDER BY at DESC, id DESC LIMIT $2 OFFSET $3",
                target,
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT at, keeper_id, keeper_name, action, target, detail"
                " FROM keeper_log ORDER BY at DESC, id DESC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
        return tuple(
            KeeperEntry(
                at=row["at"],
                keeper_id=row["keeper_id"],
                keeper_name=row["keeper_name"] or "",
                action=KeeperAction(row["action"]),
                target=row["target"] or "",
                detail=row["detail"] or "",
            )
            for row in rows
        )

    async def count(self, *, target: str = "") -> int:
        if target:
            value = await self._pool.fetchval(
                "SELECT count(*) FROM keeper_log WHERE lower(target) = lower($1)", target
            )
        else:
            value = await self._pool.fetchval("SELECT count(*) FROM keeper_log")
        return int(value or 0)


class PostgresGoldFlowRepository:
    """Денежный журнал в базе: строка на движение, срез по одному игроку (ADR 0044)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self, *, at: int, flow: str, amount: int, character_id: int, detail: str = ""
    ) -> None:
        await self._pool.execute(
            "INSERT INTO gold_flow (at, flow, amount, character_id, detail)"
            " VALUES ($1, $2, $3, $4, $5)",
            at,
            flow,
            amount,
            character_id,
            detail,
        )

    async def slice(self, character_id: int, *, since: int = 0) -> GoldFlowSlice:
        rows = await self._pool.fetch(
            "SELECT flow, sum(amount) AS total, count(*) AS n"
            " FROM gold_flow WHERE character_id = $1 AND at >= $2"
            " GROUP BY flow ORDER BY sum(abs(amount)) DESC",
            character_id,
            since,
        )
        by_flow = {row["flow"]: int(row["total"] or 0) for row in rows}
        return GoldFlowSlice(
            by_flow=by_flow,
            rows=sum(int(row["n"]) for row in rows),
            net=sum(by_flow.values()),
            since=since,
        )


class PostgresPrivacyRepository:
    """Видимость карточки и чёрные списки (Roadmap 2.5).

    И то и другое лежит по аккаунту Telegram, а не по персонажу. Признак видимости
    живёт в строке ``users``, которая и так есть у каждого игрока; чёрный список -
    собственная таблица, потому что это растущее множество.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def profile_visible(self, telegram_id: int) -> bool:
        row = await self._pool.fetchrow(
            "SELECT show_profile FROM users WHERE telegram_id = $1", telegram_id
        )
        # Нет строки - значит, игрок ни разу не открывал бота: прятать всё равно нечего.
        return True if row is None else bool(row["show_profile"])

    async def set_profile_visible(self, telegram_id: int, visible: bool) -> None:
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, show_profile) VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET show_profile = EXCLUDED.show_profile
            """,
            telegram_id,
            visible,
        )

    async def blocks(self, telegram_id: int, other_id: int) -> bool:
        row = await self._pool.fetchrow(
            "SELECT 1 FROM blocks WHERE owner_id = $1 AND blocked_id = $2",
            telegram_id,
            other_id,
        )
        return row is not None

    async def block(self, telegram_id: int, other_id: int, *, at: int) -> bool:
        # DO NOTHING не возвращает строки, когда пара уже в списке, - а это ровно тот
        # ответ «он уже был заблокирован», который нужен вызывающему.
        row = await self._pool.fetchrow(
            """
            INSERT INTO blocks (owner_id, blocked_id, created_at) VALUES ($1, $2, $3)
            ON CONFLICT (owner_id, blocked_id) DO NOTHING
            RETURNING owner_id
            """,
            telegram_id,
            other_id,
            at,
        )
        return row is not None

    async def unblock(self, telegram_id: int, other_id: int) -> bool:
        row = await self._pool.fetchrow(
            "DELETE FROM blocks WHERE owner_id = $1 AND blocked_id = $2 RETURNING owner_id",
            telegram_id,
            other_id,
        )
        return row is not None


class PostgresCharacterRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, character_id: int) -> Character | None:
        row = await self._pool.fetchrow(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE id = $1",
            character_id,
        )
        return _character_from_row(row) if row else None

    async def get_active(self, telegram_id: int) -> Character | None:
        row = await self._pool.fetchrow(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE user_id = $1 ORDER BY id LIMIT 1",
            telegram_id,
        )
        return _character_from_row(row) if row else None

    async def list_for_user(self, telegram_id: int) -> tuple[Character, ...]:
        rows = await self._pool.fetch(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE user_id = $1 ORDER BY id",
            telegram_id,
        )
        return tuple(_character_from_row(row) for row in rows)

    async def create(self, character: Character) -> Character:
        row = await self._pool.fetchrow(
            """
            INSERT INTO characters (
                user_id, name, race_id, class_id, level, experience, gold,
                stat_str, stat_agi, stat_end, stat_int, stat_wis, stat_cha, stat_lck,
                trait_ids, loadout, equipment, city_id,
                unspent_stat_points, unspent_skill_points,
                health, bank_gold, quests, crafts, tools, tutorial, arena_wins,
                arena_losses, arena_credit, remorts, turning_cycle, turning_answer,
                house_id, is_admin
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16::jsonb, $17::jsonb, $18, $19, $20, $21, $22, $23::jsonb,
                    $24::jsonb, $25::jsonb, $26, $27, $28, $29, $30, $31, $32, $33, $34)
            RETURNING id
            """,
            character.user_id,
            character.name,
            character.race_id,
            character.class_id,
            character.level,
            character.experience,
            character.gold,
            character.allocated.STR,
            character.allocated.AGI,
            character.allocated.END,
            character.allocated.INT,
            character.allocated.WIS,
            character.allocated.CHA,
            character.allocated.LCK,
            list(character.trait_ids),
            _loadout_to_json(character.loadout),
            json.dumps(dict(character.equipment.items), ensure_ascii=False),
            character.city_id,
            character.unspent_stat_points,
            character.unspent_skill_points,
            character.health,
            character.bank_gold,
            _quests_to_json(character.quests),
            _crafts_to_json(character.crafts),
            _tools_to_json(character.tools),
            character.tutorial,
            character.arena_wins,
            character.arena_losses,
            character.arena_credit,
            character.remorts,
            character.turning_cycle,
            character.turning_answer,
            character.house_id,
            character.is_admin,
        )
        return replace(character, id=row["id"])

    async def save(self, character: Character) -> None:
        await self._pool.execute(
            """
            UPDATE characters SET
                level = $2, experience = $3, gold = $4,
                stat_str = $5, stat_agi = $6, stat_end = $7, stat_int = $8,
                stat_wis = $9, stat_cha = $10, stat_lck = $11,
                trait_ids = $12, loadout = $13::jsonb, equipment = $14::jsonb,
                city_id = $15, unspent_stat_points = $16, unspent_skill_points = $17,
                health = $18, bank_gold = $19, quests = $20::jsonb,
                crafts = $21::jsonb, tools = $22::jsonb, tutorial = $23,
                arena_wins = $24, arena_losses = $25, arena_credit = $26,
                remorts = $27, turning_cycle = $28, turning_answer = $29,
                is_admin = $30, house_id = $31, updated_at = now()
            WHERE id = $1
            """,
            character.id,
            character.level,
            character.experience,
            character.gold,
            character.allocated.STR,
            character.allocated.AGI,
            character.allocated.END,
            character.allocated.INT,
            character.allocated.WIS,
            character.allocated.CHA,
            character.allocated.LCK,
            list(character.trait_ids),
            _loadout_to_json(character.loadout),
            json.dumps(dict(character.equipment.items), ensure_ascii=False),
            character.city_id,
            character.unspent_stat_points,
            character.unspent_skill_points,
            character.health,
            character.bank_gold,
            _quests_to_json(character.quests),
            _crafts_to_json(character.crafts),
            _tools_to_json(character.tools),
            character.tutorial,
            character.arena_wins,
            character.arena_losses,
            character.arena_credit,
            character.remorts,
            character.turning_cycle,
            character.turning_answer,
            character.is_admin,
            character.house_id,
        )

    async def spend_gold(self, character_id: int, amount: int) -> bool:
        """Один UPDATE решает и то, есть ли золото, и то, что его больше нет.

        ``save`` пишет кошелёк, прочитанный несколько шагов назад; здесь пишется
        разница, поэтому два закрытия, гоняющихся за одним кошельком, не могут удаться
        оба.
        """
        if amount < 0:
            return False
        row = await self._pool.fetchval(
            "UPDATE characters SET gold = gold - $2, updated_at = now()"
            " WHERE id = $1 AND gold >= $2 RETURNING id",
            character_id,
            amount,
        )
        return row is not None

    async def grant_gold(self, character_id: int, amount: int) -> None:
        if amount <= 0:
            return
        await self._pool.execute(
            "UPDATE characters SET gold = gold + $2, updated_at = now() WHERE id = $1",
            character_id,
            amount,
        )

    async def name_taken(self, name: str) -> bool:
        row = await self._pool.fetchval(
            "SELECT 1 FROM characters WHERE lower(name) = lower($1) LIMIT 1", name
        )
        return row is not None

    async def arena_opponent(self, *, level: int, window: int, exclude_id: int) -> Character | None:
        """Один персонаж примерно этого уровня, выбранный случайно.

        Случайно, а не ближайший: закреплённая пара позволила бы двоим фармить друг
        друга, а арена платит из своего кошелька (``domain/rules/arena``).
        """
        row = await self._pool.fetchrow(
            f"""
            SELECT {CHARACTER_COLUMNS} FROM characters
            WHERE id <> $1 AND abs(level - $2) <= $3
            ORDER BY random()
            LIMIT 1
            """,
            exclude_id,
            level,
            window,
        )
        return _character_from_row(row) if row else None

    async def arena_table(self, *, limit: int = 10) -> tuple[Character, ...]:
        rows = await self._pool.fetch(
            f"""
            SELECT {CHARACTER_COLUMNS} FROM characters
            WHERE arena_wins > 0
            ORDER BY arena_wins DESC, level DESC, name
            LIMIT $1
            """,
            limit,
        )
        return tuple(_character_from_row(row) for row in rows)

    async def turning_tally(self, cycle_id: str) -> Mapping[str, int]:
        """Голоса за открытый вопрос: ответ и сколько уходов за ним стоит.

        Считается запросом, а не счётчиком: счётчик, живущий отдельно от того,
        что он считает, однажды с ним расходится (``Claude.md``, правило 8).
        Вес голоса зажат потолком совета, как ``turning.voice``.
        """
        rows = await self._pool.fetch(
            f"""
            SELECT turning_answer AS option,
                   coalesce(sum(least(remorts, {COUNCIL_VOTE_CAP})), 0)::int AS voices
            FROM characters
            WHERE turning_cycle = $1 AND turning_answer <> '' AND remorts > 0
            GROUP BY turning_answer
            """,
            cycle_id,
        )
        return MappingProxyType({row["option"]: int(row["voices"]) for row in rows})

    async def find_by_name(self, name: str) -> Character | None:
        row = await self._pool.fetchrow(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE lower(name) = lower($1)",
            name.strip(),
        )
        return _character_from_row(row) if row else None

    async def newest(self, *, limit: int = 8) -> tuple[Character, ...]:
        rows = await self._pool.fetch(
            f"SELECT {CHARACTER_COLUMNS} FROM characters ORDER BY id DESC LIMIT $1",
            limit,
        )
        return tuple(_character_from_row(row) for row in rows)

    async def search(self, criteria: PlayerFilter, *, limit: int = 24) -> tuple[Character, ...]:
        clauses: list[str] = []
        args: list[object] = []
        if criteria.level_min:
            args.append(criteria.level_min)
            clauses.append(f"level >= ${len(args)}")
        if criteria.level_max:
            args.append(criteria.level_max)
            clauses.append(f"level <= ${len(args)}")
        if criteria.city_id:
            args.append(criteria.city_id)
            clauses.append(f"city_id = ${len(args)}")
        if criteria.active_since:
            args.append(criteria.active_since)
            clauses.append(f"updated_at >= to_timestamp(${len(args)})")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        rows = await self._pool.fetch(
            f"SELECT {CHARACTER_COLUMNS} FROM characters{where}"
            f" ORDER BY level DESC, name LIMIT ${len(args)}",
            *args,
        )
        return tuple(_character_from_row(row) for row in rows)

    async def census(self, *, day: int, week: int, stale: int) -> Census:
        """Игра в числах: один проход по таблице и короткий список сильнейших."""
        row = await self._pool.fetchrow(
            """
            SELECT
                count(*)                                        AS characters,
                count(DISTINCT user_id)                         AS accounts,
                count(*) FILTER (WHERE updated_at >= to_timestamp($1)) AS fresh_day,
                count(*) FILTER (WHERE updated_at >= to_timestamp($2)) AS fresh_week,
                count(*) FILTER (
                    WHERE level = 1 AND experience = 0 AND tutorial = 0
                      AND updated_at < to_timestamp($3)
                )                                               AS abandoned,
                coalesce(max(level), 0)                         AS top_level,
                coalesce(round(avg(level)), 0)                  AS average_level,
                coalesce(sum(gold), 0)                          AS gold_on_hand,
                coalesce(sum(bank_gold), 0)                     AS gold_in_bank,
                coalesce(sum(jsonb_array_length(quests -> 'done')), 0) AS quests_done,
                coalesce(sum(arena_wins + arena_losses), 0)     AS arena_fights
            FROM characters
            """,
            day,
            week,
            stale,
        )
        leaders = await self._pool.fetch(
            "SELECT name, level FROM characters ORDER BY level DESC, name LIMIT 5"
        )
        blocked = await self._pool.fetchval("SELECT count(*) FROM users WHERE blocked_at > 0")
        return Census(
            characters=int(row["characters"]),
            accounts=int(row["accounts"]),
            fresh_day=int(row["fresh_day"]),
            fresh_week=int(row["fresh_week"]),
            abandoned=int(row["abandoned"]),
            blocked=int(blocked or 0),
            top_level=int(row["top_level"]),
            average_level=int(row["average_level"]),
            gold_on_hand=int(row["gold_on_hand"]),
            gold_in_bank=int(row["gold_in_bank"]),
            quests_done=int(row["quests_done"]),
            arena_fights=int(row["arena_fights"]),
            leaders=tuple((entry["name"], int(entry["level"])) for entry in leaders),
        )

    async def purge_abandoned(self, *, before: int) -> int:
        value = await self._pool.fetchval(
            """
            WITH gone AS (
                DELETE FROM characters
                WHERE level = 1 AND experience = 0 AND tutorial = 0
                  AND updated_at < to_timestamp($1)
                RETURNING 1
            )
            SELECT count(*) FROM gone
            """,
            before,
        )
        return int(value or 0)

    async def delete(self, character_id: int) -> bool:
        row = await self._pool.fetchrow(
            "DELETE FROM characters WHERE id = $1 RETURNING id", character_id
        )
        return row is not None


class PostgresContentOverlayRepository:
    """Правки смотрителя: одна строка на сущность, поля - в JSONB.

    Схема нарочно не знает, какие у сущности поля: их знает
    ``domain/rules/overlay.py``, и он один. Иначе каждая новая правка была бы
    миграцией, а смысл панели в том, чтобы менять мир без выкатки.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def all(self) -> tuple[OverlayRecord, ...]:
        rows = await self._pool.fetch(
            "SELECT kind, entity_id, fields, removed, author_id, updated_at"
            " FROM content_overlay ORDER BY updated_at, entity_id"
        )
        return tuple(
            OverlayRecord(
                kind=OverlayKind(row["kind"]),
                entity_id=row["entity_id"],
                fields=MappingProxyType(
                    {str(key): str(value) for key, value in json.loads(row["fields"]).items()}
                ),
                removed=bool(row["removed"]),
                author_id=row["author_id"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def put(self, record: OverlayRecord) -> None:
        await self._pool.execute(
            """
            INSERT INTO content_overlay (
                kind, entity_id, fields, removed, author_id, updated_at
            )
            VALUES ($1, $2, $3::jsonb, $4, $5, $6)
            ON CONFLICT (kind, entity_id) DO UPDATE SET
                fields = EXCLUDED.fields,
                removed = EXCLUDED.removed,
                author_id = EXCLUDED.author_id,
                updated_at = EXCLUDED.updated_at
            """,
            record.kind.value,
            record.entity_id,
            json.dumps(dict(record.fields), ensure_ascii=False),
            record.removed,
            record.author_id,
            record.updated_at,
        )

    async def forget(self, kind: OverlayKind, entity_id: str) -> bool:
        row = await self._pool.fetchrow(
            "DELETE FROM content_overlay WHERE kind = $1 AND entity_id = $2 RETURNING entity_id",
            kind.value,
            entity_id,
        )
        return row is not None


def _trade_from_row(row: Any) -> TradeRecord:
    return TradeRecord(
        offer=Offer(
            number=row["number"],
            kind=OfferKind(row["kind"]),
            author=Party(
                user_id=row["author_user_id"],
                character_id=row["author_character_id"],
                name=row["author_name"],
            ),
            target=Party(
                user_id=row["target_user_id"],
                character_id=row["target_character_id"],
                name=row["target_name"],
            ),
            item_id=row["item_id"],
            item_name=row["item_name"],
            price=row["price"],
            quantity=row["quantity"],
            created_at=row["created_at"],
        ),
        scope=row["scope"],
        status=TradeStatus(row["status"]),
        tax=row["tax"],
        settled_at=row["settled_at"],
        id=row["id"],
    )


class PostgresTradeRepository:
    """Журнал сделок и эскроу, висящий на его стоящих строках.

    Вес несут два запроса. ``open`` берёт наименьший свободный номер и вставляет его
    одним шагом, поэтому между выбором номера и его занятием нет промежутка;
    частичный уникальный указатель превращает проигранную гонку в отвергнутую
    вставку, которую повторяют, а не показывают. ``close`` меняет только ту строку,
    которая всё ещё стоит, и возвращает то, что изменил, - именно это и делает
    «принять» случающимся не больше раза, сколько бы нажатий ни пришло разом.
    """

    # Чтобы возникла гонка, нужны два предложения в одну миллисекунду; трёх попыток куда
    # больше, чем когда-либо понадобится потоку одной группы.
    ATTEMPTS = 3

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def open(self, offer: Offer, *, scope: str) -> TradeRecord | None:
        for _ in range(self.ATTEMPTS):
            row = await self._pool.fetchrow(
                """
                INSERT INTO trades (
                    scope, number, kind, created_at,
                    author_user_id, author_character_id, author_name,
                    target_user_id, target_character_id, target_name,
                    item_id, item_name, quantity, price
                )
                SELECT $1, free.n, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                FROM (
                    SELECT n FROM generate_series(1, $14) AS n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM trades taken
                        WHERE taken.scope = $1
                          AND taken.number = n
                          AND taken.status = 'pending'
                    )
                    ORDER BY n LIMIT 1
                ) AS free
                ON CONFLICT DO NOTHING
                RETURNING id, number
                """,
                scope,
                offer.kind.value,
                offer.created_at,
                offer.author.user_id,
                offer.author.character_id,
                offer.author.name,
                offer.target.user_id,
                offer.target.character_id,
                offer.target.name,
                offer.item_id,
                offer.item_name,
                offer.quantity,
                offer.price,
                MAX_OFFER_NUMBER,
            )
            if row is not None:
                return TradeRecord(
                    offer=replace(offer, number=row["number"]), scope=scope, id=row["id"]
                )
        return None

    async def pending(self, number: int, *, scope: str) -> TradeRecord | None:
        row = await self._pool.fetchrow(
            f"SELECT {TRADE_COLUMNS} FROM trades"
            " WHERE scope = $1 AND number = $2 AND status = 'pending'",
            scope,
            number,
        )
        return _trade_from_row(row) if row else None

    async def close(
        self,
        number: int,
        *,
        scope: str,
        status: TradeStatus,
        settled_at: int,
        tax: int = 0,
    ) -> TradeRecord | None:
        """Закрыть стоящую сделку. ``None`` значит, что она уже была закрыта."""
        row = await self._pool.fetchrow(
            f"""
            UPDATE trades SET status = $3, tax = $4, settled_at = $5
            WHERE scope = $1 AND number = $2 AND status = 'pending'
            RETURNING {TRADE_COLUMNS}
            """,
            scope,
            number,
            status.value,
            tax,
            settled_at,
        )
        return _trade_from_row(row) if row else None

    async def expire(self, *, before: int, scope: str | None = None) -> tuple[TradeRecord, ...]:
        rows = await self._pool.fetch(
            f"""
            UPDATE trades SET status = 'expired', settled_at = $1
            WHERE status = 'pending' AND created_at <= $1
              AND ($2::text IS NULL OR scope = $2)
            RETURNING {TRADE_COLUMNS}
            """,
            before,
            scope,
        )
        return tuple(_trade_from_row(row) for row in rows)

    async def revert(self, trade_id: int) -> TradeRecord | None:
        """Откатить закрытую сделку, один раз. ``None`` - она не закрывалась или уже откачена."""
        row = await self._pool.fetchrow(
            f"""
            UPDATE trades SET status = 'reverted'
            WHERE id = $1 AND status = 'accepted'
            RETURNING {TRADE_COLUMNS}
            """,
            trade_id,
        )
        return _trade_from_row(row) if row else None

    async def journal(self, character_id: int, *, limit: int = 20) -> tuple[TradeRecord, ...]:
        rows = await self._pool.fetch(
            f"SELECT {TRADE_COLUMNS} FROM trades"
            " WHERE author_character_id = $1 OR target_character_id = $1"
            " ORDER BY id DESC LIMIT $2",
            character_id,
            limit,
        )
        return tuple(_trade_from_row(row) for row in rows)


class PostgresInventoryRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_items(self, character_id: int) -> tuple[InventoryEntry, ...]:
        rows = await self._pool.fetch(
            "SELECT item_id, quantity FROM inventory"
            " WHERE character_id = $1 AND quantity > 0 ORDER BY item_id",
            character_id,
        )
        return tuple(
            InventoryEntry(item_id=row["item_id"], quantity=row["quantity"]) for row in rows
        )

    async def add(self, character_id: int, item_id: str, quantity: int = 1) -> None:
        await self._pool.execute(
            """
            INSERT INTO inventory (character_id, item_id, quantity)
            VALUES ($1, $2, $3)
            ON CONFLICT (character_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
            """,
            character_id,
            item_id,
            quantity,
        )

    async def remove(self, character_id: int, item_id: str, quantity: int = 1) -> bool:
        """Неделимо: строку трогают, только когда в ней хватает."""
        updated = await self._pool.fetchval(
            """
            UPDATE inventory SET quantity = quantity - $3
            WHERE character_id = $1 AND item_id = $2 AND quantity >= $3
            RETURNING quantity
            """,
            character_id,
            item_id,
            quantity,
        )
        return updated is not None

    async def count(self, character_id: int, item_id: str) -> int:
        value = await self._pool.fetchval(
            "SELECT quantity FROM inventory WHERE character_id = $1 AND item_id = $2",
            character_id,
            item_id,
        )
        return int(value or 0)


class PostgresPartyRepository:
    """Состав отряда: строка на собравшего в ``parties`` и строка на каждого
    участника в ``party_members`` (``migrations/0016``, ADR 0029).

    Приглашения здесь не лежат: они висят в кэше со сроком.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def by_leader(self, leader_id: int) -> PlayerParty | None:
        rows = await self._pool.fetch(
            "SELECT character_id FROM party_members WHERE leader_id = $1 ORDER BY character_id",
            leader_id,
        )
        if not rows:
            return None
        members = tuple(row["character_id"] for row in rows if row["character_id"] != leader_id)
        return PlayerParty(leader_id=leader_id, members=members)

    async def of(self, character_id: int) -> PlayerParty | None:
        leader_id = await self._pool.fetchval(
            "SELECT leader_id FROM party_members WHERE character_id = $1",
            character_id,
        )
        return await self.by_leader(int(leader_id)) if leader_id is not None else None

    async def save(self, party: PlayerParty) -> None:
        if party.disbanded:
            await self.disband(party.leader_id)
            return
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO parties (leader_id) VALUES ($1) ON CONFLICT (leader_id) DO NOTHING",
                party.leader_id,
            )
            # Индекс по character_id держит «один отряд на человека»; вычищаем
            # участников из любого другого отряда, прежде чем записать этот.
            await connection.execute(
                "DELETE FROM party_members WHERE character_id = ANY($1::bigint[])"
                " AND leader_id <> $2",
                list(party.members),
                party.leader_id,
            )
            await connection.execute(
                "DELETE FROM party_members WHERE leader_id = $1"
                " AND character_id <> ALL($2::bigint[])",
                party.leader_id,
                list(party.members),
            )
            await connection.executemany(
                "INSERT INTO party_members (leader_id, character_id) VALUES ($1, $2)"
                " ON CONFLICT DO NOTHING",
                [(party.leader_id, member) for member in party.members],
            )

    async def disband(self, leader_id: int) -> None:
        await self._pool.execute("DELETE FROM parties WHERE leader_id = $1", leader_id)


class PostgresGuildRepository:
    """Гильдия: строка в ``guilds`` (имя, основатель, казна) и строка на каждого
    участника в ``guild_members`` со званием (``migrations/0017``, ADR 0030).

    Казна двигается условным ``UPDATE``: ``withdraw`` не уходит в минус, даже
    если два офицера нажали разом.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _assemble(self, row: Any) -> Guild:
        members = await self._pool.fetch(
            "SELECT character_id, rank FROM guild_members WHERE guild_id = $1"
            " ORDER BY rank DESC, character_id",
            row["id"],
        )
        return Guild(
            id=row["id"],
            name=row["name"],
            founder_id=row["founder_id"],
            vault_gold=row["vault_gold"],
            members=tuple(GuildMember(m["character_id"], GuildRank(m["rank"])) for m in members),
        )

    async def by_id(self, guild_id: int) -> Guild | None:
        row = await self._pool.fetchrow(
            "SELECT id, name, founder_id, vault_gold FROM guilds WHERE id = $1", guild_id
        )
        return await self._assemble(row) if row is not None else None

    async def by_name(self, name: str) -> Guild | None:
        row = await self._pool.fetchrow(
            "SELECT id, name, founder_id, vault_gold FROM guilds WHERE lower(name) = lower($1)",
            name.strip(),
        )
        return await self._assemble(row) if row is not None else None

    async def of(self, character_id: int) -> Guild | None:
        row = await self._pool.fetchrow(
            "SELECT g.id, g.name, g.founder_id, g.vault_gold FROM guilds g"
            " JOIN guild_members m ON m.guild_id = g.id WHERE m.character_id = $1",
            character_id,
        )
        return await self._assemble(row) if row is not None else None

    async def create(self, name: str, founder_id: int) -> Guild:
        async with self._pool.acquire() as connection, connection.transaction():
            guild_id = await connection.fetchval(
                "INSERT INTO guilds (name, founder_id) VALUES ($1, $2) RETURNING id",
                name.strip(),
                founder_id,
            )
            await connection.execute(
                "INSERT INTO guild_members (guild_id, character_id, rank) VALUES ($1, $2, $3)",
                guild_id,
                founder_id,
                int(GuildRank.FOUNDER),
            )
        return Guild(
            id=int(guild_id),
            name=name.strip(),
            founder_id=founder_id,
            members=(GuildMember(founder_id, GuildRank.FOUNDER),),
        )

    async def save(self, guild: Guild) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "UPDATE guilds SET name = $2 WHERE id = $1", guild.id, guild.name.strip()
            )
            ids = [one.character_id for one in guild.members]
            # Индекс по character_id держит «одна гильдия на человека»: выметаем
            # новичков из любой другой гильдии, прежде чем записать эту.
            await connection.execute(
                "DELETE FROM guild_members WHERE character_id = ANY($1::bigint[])"
                " AND guild_id <> $2",
                ids,
                guild.id,
            )
            await connection.execute(
                "DELETE FROM guild_members WHERE guild_id = $1"
                " AND character_id <> ALL($2::bigint[])",
                guild.id,
                ids,
            )
            await connection.executemany(
                """
                INSERT INTO guild_members (guild_id, character_id, rank)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, character_id) DO UPDATE SET rank = EXCLUDED.rank
                """,
                [(guild.id, one.character_id, int(one.rank)) for one in guild.members],
            )

    async def disband(self, guild_id: int) -> None:
        await self._pool.execute("DELETE FROM guilds WHERE id = $1", guild_id)

    async def deposit(self, guild_id: int, amount: int) -> None:
        if amount > 0:
            await self._pool.execute(
                "UPDATE guilds SET vault_gold = vault_gold + $2 WHERE id = $1", guild_id, amount
            )

    async def withdraw(self, guild_id: int, amount: int) -> bool:
        updated = await self._pool.fetchval(
            "UPDATE guilds SET vault_gold = vault_gold - $2"
            " WHERE id = $1 AND vault_gold >= $2 RETURNING vault_gold",
            guild_id,
            amount,
        )
        return updated is not None
