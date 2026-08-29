"""Мьют в игровой группе — короче блокировки.

Замолчавший в группе играет как обычно: и в личке, и в бою, и в вылазке. Только
его сообщения в игровой группе бот удаляет, пока срок не выйдет. Это мягче
блокировки, которая заворачивает человека отовсюду, и нужно оно тогда, когда
мешают не игре, а разговору в комнате.

``muted_until`` устроен как ``banned_until`` (0013): ноль — не замолчан,
отрицательное — навсегда, положительное — момент конца. Причину читает не
смотритель, а сам замолчавший, если спросит в личке.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN muted_until BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN mute_reason TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS mute_reason")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS muted_until")
