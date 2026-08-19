"""Временная блокировка аккаунта и журнал смотрителя.

Блокировка лежит на аккаунте, а не на персонаже: наказание, от которого уходят,
заведя второго персонажа, наказанием не было бы (та же причина, что у чёрного
списка в 0003 и у права смотрителя в 0011). Ничего не стирается — персонаж,
вещи и золото остаются на месте, пока срок не выйдет.

``banned_until`` — момент конца срока в секундах unix. Ноль означает «не
заблокирован», отрицательное — «навсегда»: срок без конца надо было чем-то
обозначить, и число меньше любого прошлого честнее огромного, которое однажды
наступит.

``keeper_log`` только дописывается. Имена лежат в нём строками, а не ссылками:
персонажа могут переименовать или стереть, а запись о том, что с ним сделали,
должна остаться читаемой.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN banned_until BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT NOT NULL DEFAULT ''")
    # Заблокированных всегда мало, а спрашивают о них на каждом сообщении только
    # по одному ключу; индекс нужен счёту в статистике, а не поиску.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_banned_until"
        " ON users (banned_until) WHERE banned_until <> 0"
    )

    op.execute(
        """
        CREATE TABLE keeper_log (
            id          BIGSERIAL PRIMARY KEY,
            at          BIGINT NOT NULL DEFAULT 0,
            keeper_id   BIGINT NOT NULL DEFAULT 0,
            keeper_name TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL,
            target      TEXT NOT NULL DEFAULT '',
            detail      TEXT NOT NULL DEFAULT ''
        )
        """
    )
    # Журнал читают с конца и только с конца.
    op.execute("CREATE INDEX IF NOT EXISTS idx_keeper_log_at ON keeper_log (at DESC, id DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_keeper_log_at")
    op.execute("DROP TABLE IF EXISTS keeper_log")
    op.execute("DROP INDEX IF EXISTS idx_users_banned_until")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS ban_reason")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS banned_until")
