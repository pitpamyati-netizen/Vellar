"""Гильдия растёт: деяния у гильдии, вклад у каждого, пять званий вместо трёх.

Гильдия перестала быть списком с общим кошельком (ADR 0076). Три вещи в схеме:

- ``guilds.deeds`` - деяния гильдии. По ним считается её ступень
  (``content/guilds.toml``), и больше ступень нигде не хранится.
- ``guild_members.contributed`` - вклад каждого: сколько он принёс боями и
  золотом. Не тратится ни на что; это память о том, кто держал гильдию.
- Званий стало пять: новик (0), участник (1), ветеран (2), старейшина (3),
  основатель (4). Прежние три раздвигаются на месте, снизу вверх, чтобы никого
  не понизить: основатель (2) становится 4, офицер (1) - 3, участник (0) - 1.
  Новик в старой игре не существовал, и задним числом им никто не станет.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-06
"""

from __future__ import annotations

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE guilds ADD COLUMN deeds BIGINT NOT NULL DEFAULT 0 CHECK (deeds >= 0)")
    op.execute(
        "ALTER TABLE guild_members"
        " ADD COLUMN contributed BIGINT NOT NULL DEFAULT 0 CHECK (contributed >= 0)"
    )
    # Сначала снять старую границу, потом раздвинуть звания, потом поставить
    # новую: иначе основатель не доедет до четвёрки.
    op.execute("ALTER TABLE guild_members DROP CONSTRAINT IF EXISTS guild_members_rank_check")
    op.execute("UPDATE guild_members SET rank = 4 WHERE rank = 2")
    op.execute("UPDATE guild_members SET rank = 3 WHERE rank = 1")
    op.execute("UPDATE guild_members SET rank = 1 WHERE rank = 0")
    op.execute(
        "ALTER TABLE guild_members ADD CONSTRAINT guild_members_rank_check"
        " CHECK (rank BETWEEN 0 AND 4)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE guild_members DROP CONSTRAINT IF EXISTS guild_members_rank_check")
    op.execute("UPDATE guild_members SET rank = 0 WHERE rank <= 2")
    op.execute("UPDATE guild_members SET rank = 1 WHERE rank = 3")
    op.execute("UPDATE guild_members SET rank = 2 WHERE rank = 4")
    op.execute(
        "ALTER TABLE guild_members ADD CONSTRAINT guild_members_rank_check"
        " CHECK (rank BETWEEN 0 AND 2)"
    )
    op.execute("ALTER TABLE guild_members DROP COLUMN IF EXISTS contributed")
    op.execute("ALTER TABLE guilds DROP COLUMN IF EXISTS deeds")
