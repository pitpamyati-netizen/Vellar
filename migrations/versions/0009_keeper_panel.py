"""Панель смотрителя: правки поверх содержимого и учёт заблокировавших бота.

Две вещи, и обе - про то, чтобы игра жила без выкатки.

``content_overlay`` - единственное содержимое, которое хранится в базе. Файлы в
``content/`` остаются источником мира; правка ложится сверху и снимается целиком,
поэтому исходная строка не теряется никогда. Поля лежат в JSONB, потому что схему
полей знает домен (``domain/rules/overlay.py``), а не таблица: иначе каждая новая
разновидность правки была бы миграцией.

``users.blocked_at`` и ``users.checked_at`` - про уборку. Человек, заблокировавший
бота, ничего боту больше не скажет, и узнать об этом можно только спросив Telegram;
поэтому у аккаунта есть отметка, когда его последний раз спрашивали и что ответили.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE content_overlay (
            kind       TEXT NOT NULL,
            entity_id  TEXT NOT NULL,
            -- Поля сущности строками: запись переживает и код, и содержимое.
            fields     JSONB NOT NULL DEFAULT '{}'::jsonb,
            -- Надгробие: сущность из TOML не стирается, а перестаёт показываться.
            removed    BOOLEAN NOT NULL DEFAULT FALSE,
            author_id  BIGINT NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (kind, entity_id)
        )
        """
    )

    op.execute("ALTER TABLE users ADD COLUMN blocked_at BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN checked_at BIGINT NOT NULL DEFAULT 0")
    # Проверка идёт порциями и всегда с одного конца: сначала те, кого не
    # спрашивали дольше всех.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_checked_at ON users (checked_at) WHERE blocked_at = 0"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_checked_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS checked_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS blocked_at")
    op.execute("DROP TABLE IF EXISTS content_overlay")
