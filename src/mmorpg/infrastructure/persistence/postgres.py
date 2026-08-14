"""PostgreSQL repositories.

Explicit SQL over asyncpg, no ORM (``docs/adr/0001-no-orm.md``). Every query here
touches one or two rows by primary key or by a unique index, which is what keeps
an update inside the 100 ms budget.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from mmorpg.domain.entities.character import Character, Equipment, InventoryEntry, SkillLoadout
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import AccessibilitySettings, User
from mmorpg.domain.rules.group_offers import MAX_OFFER_NUMBER

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

CHARACTER_COLUMNS = """
    id, user_id, name, race_id, class_id, level, experience, gold, bank_gold,
    stat_str, stat_agi, stat_end, stat_int, stat_wis, stat_cha, stat_lck,
    trait_ids, loadout, equipment, city_id, unspent_stat_points, unspent_skill_points
"""

TRADE_COLUMNS = """
    scope, number, kind, status, tax, created_at, settled_at,
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
        bank_gold=row["bank_gold"],
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
            passives=tuple(loadout_raw.get("passives", [None] * 3)),
            racial=loadout_raw.get("racial"),
            ranks=MappingProxyType(dict(loadout_raw.get("ranks", {}))),
            edges=MappingProxyType(dict(loadout_raw.get("edges", {}))),
        ),
        equipment=Equipment(MappingProxyType(dict(equipment_raw))),
        city_id=row["city_id"],
        unspent_stat_points=row["unspent_stat_points"],
        unspent_skill_points=row["unspent_skill_points"],
    )


def _loadout_to_json(loadout: SkillLoadout) -> str:
    return json.dumps(
        {
            "actives": list(loadout.actives),
            "passives": list(loadout.passives),
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
            "SELECT telegram_id, username, emoji, verbose_descriptions, page_size"
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
                # The column cannot be called "verbose"; PostgreSQL reserves it.
                verbose=row["verbose_descriptions"],
                page_size=row["page_size"],
            ),
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


class PostgresPrivacyRepository:
    """Profile visibility and black lists (Roadmap 2.5).

    Both are keyed by Telegram account, not by character. The visibility flag
    lives on the ``users`` row that already exists for every player; the black
    list is its own table, because it is a set that grows.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def profile_visible(self, telegram_id: int) -> bool:
        row = await self._pool.fetchrow(
            "SELECT show_profile FROM users WHERE telegram_id = $1", telegram_id
        )
        # No row means the player never opened the bot: nothing to hide anyway.
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
        # DO NOTHING returns no row when the pair is already listed, which is
        # exactly the "they were already blocked" answer the caller needs.
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
                user_id, name, race_id, class_id, level, experience, gold, bank_gold,
                stat_str, stat_agi, stat_end, stat_int, stat_wis, stat_cha, stat_lck,
                trait_ids, loadout, equipment, city_id,
                unspent_stat_points, unspent_skill_points
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17::jsonb, $18::jsonb, $19, $20, $21)
            RETURNING id
            """,
            character.user_id,
            character.name,
            character.race_id,
            character.class_id,
            character.level,
            character.experience,
            character.gold,
            character.bank_gold,
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
        )
        return replace(character, id=row["id"])

    async def save(self, character: Character) -> None:
        await self._pool.execute(
            """
            UPDATE characters SET
                level = $2, experience = $3, gold = $4, bank_gold = $5,
                stat_str = $6, stat_agi = $7, stat_end = $8, stat_int = $9,
                stat_wis = $10, stat_cha = $11, stat_lck = $12,
                trait_ids = $13, loadout = $14::jsonb, equipment = $15::jsonb,
                city_id = $16, unspent_stat_points = $17, unspent_skill_points = $18,
                updated_at = now()
            WHERE id = $1
            """,
            character.id,
            character.level,
            character.experience,
            character.gold,
            character.bank_gold,
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
        )

    async def name_taken(self, name: str) -> bool:
        row = await self._pool.fetchval(
            "SELECT 1 FROM characters WHERE lower(name) = lower($1) LIMIT 1", name
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
    )


class PostgresTradeRepository:
    """The trade journal, and the escrow that hangs on its pending rows.

    Two statements carry the weight. ``open`` picks the lowest free number and
    inserts it in one go, so no gap exists between choosing a number and taking
    it; the partial unique index turns a lost race into a rejected insert, which
    is retried rather than reported. ``close`` updates only a row that is still
    pending and returns what it changed, which is what makes "accept" happen at
    most once no matter how many taps arrive together.
    """

    # A race needs two proposals in the same millisecond; three attempts is far
    # more than the traffic of one group will ever need.
    ATTEMPTS = 3

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def open(self, offer: Offer, *, scope: str) -> TradeRecord | None:
        for _ in range(self.ATTEMPTS):
            number = await self._pool.fetchval(
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
                RETURNING number
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
            if number is not None:
                return TradeRecord(offer=replace(offer, number=number), scope=scope)
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
        """Close a pending trade. ``None`` means it was already closed."""
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

    async def expire(self, *, scope: str, before: int) -> tuple[TradeRecord, ...]:
        rows = await self._pool.fetch(
            f"""
            UPDATE trades SET status = 'expired', settled_at = $2
            WHERE scope = $1 AND status = 'pending' AND created_at <= $2
            RETURNING {TRADE_COLUMNS}
            """,
            scope,
            before,
        )
        return tuple(_trade_from_row(row) for row in rows)

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
        """Atomic: the row is only touched when it holds enough."""
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
