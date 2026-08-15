"""Право смотрителя, выданное изнутри игры.

``ADMIN_IDS`` остаётся тем, откуда право берётся: id оттуда — смотритель всегда, и
только он раздаёт право другим. Само раздаваемое право лежит здесь, на аккаунте, а
не на персонаже: право, от которого можно уйти, заведя второго персонажа, правом
не было бы (та же причина, по которой на аккаунте живёт чёрный список, 0003).

``characters.is_admin`` остаётся зеркалом обоих источников и переписывается при
загрузке персонажа его владельцем, поэтому колонка здесь нужна ровно одна.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN keeper BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS keeper")
