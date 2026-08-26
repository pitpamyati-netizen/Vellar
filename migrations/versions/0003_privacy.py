"""Приватность: карточка, которую игрок вправе закрыть, и те, с кем он не имеет дела.

И то и другое принадлежит аккаунту, а не персонажу. Чёрный список, который
обходится заведением второго персонажа, списком не является, как не является
закрытой и карточка, открывающаяся на следующем персонаже (Roadmap 2.5).

``blocks`` держит по строке на направление. Пара закрывается в обе стороны
правилами, а не схемой, поэтому снять блокировку - всегда решение того, кто её
поставил.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Открыто по умолчанию: приватность - это выбор, который игрок делает, а не
    # состояние, из которого он обязан выйти, чтобы его вообще увидели.
    op.execute("ALTER TABLE users ADD COLUMN show_profile BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute(
        """
        CREATE TABLE blocks (
            owner_id   BIGINT NOT NULL,
            blocked_id BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            PRIMARY KEY (owner_id, blocked_id),
            CONSTRAINT blocks_not_self CHECK (owner_id <> blocked_id)
        )
        """
    )
    # Каждая команда в группе читает список с обоих концов: блокирует ли автор адресата
    # и блокирует ли адресат автора. Первому вопросу служит первичный ключ, второму -
    # этот указатель.
    op.execute("CREATE INDEX blocks_blocked_idx ON blocks (blocked_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS blocks")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS show_profile")
