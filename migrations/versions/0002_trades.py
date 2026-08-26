"""Сделки: эскроу, держащий стоящее предложение, и журнал закрытых.

До сих пор предложение жило только в Redis и не двигало ничего, пока на него не
ответят. Теперь оно держит вещь или золото автора с той минуты, как объявлено, а
ценность, с которой игрок расстался, не вправе зависеть от кэша, истекающего
самого по себе, - поэтому у сделок появилась таблица (Roadmap 2.3).

Колонки времени - unix-секунды, а не TIMESTAMPTZ. Правила принимают ``now``
аргументом и ничего не знают о часах (Claude.md, правило 1), а срок обязан
значить ровно одно и то же здесь и в адаптере в памяти. ``logged_at`` -
единственная колонка живого времени, и она существует для того, кто читает этот
журнал после спора; игра её не читает никогда.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trades (
            id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            -- The group the offer was made in: two groups never fight over the
            -- short numbers players type at each other.
            scope        TEXT NOT NULL,
            number       SMALLINT NOT NULL,
            kind         TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            price        BIGINT NOT NULL,
            -- The duty, charged once on settlement and credited to nobody.
            tax          BIGINT NOT NULL DEFAULT 0,
            item_id      TEXT NOT NULL,
            -- The name as it was shown, so the journal still reads correctly
            -- after the content files are renamed (Roadmap 1.4).
            item_name    TEXT NOT NULL,
            quantity     INTEGER NOT NULL DEFAULT 1,
            author_user_id      BIGINT NOT NULL,
            author_character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            author_name         TEXT NOT NULL,
            target_user_id      BIGINT NOT NULL,
            target_character_id BIGINT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            target_name         TEXT NOT NULL,
            created_at   BIGINT NOT NULL,
            settled_at   BIGINT,
            logged_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT trades_kind CHECK (kind IN ('sell', 'buy')),
            CONSTRAINT trades_status
                CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
            CONSTRAINT trades_number_range CHECK (number BETWEEN 1 AND 999),
            CONSTRAINT trades_price_non_negative CHECK (price >= 0 AND tax >= 0),
            CONSTRAINT trades_quantity_positive CHECK (quantity > 0)
        )
        """
    )

    # Разом одно предложение на номер, и не больше: этот указатель и превращает гонку
    # двух предложений в отвергнутую вставку вместо двух живых предложений, которые
    # игрок не отличит. Закрытые строки держат свой номер ради журнала, поэтому
    # указатель частичный.
    op.execute(
        "CREATE UNIQUE INDEX trades_pending_number_key ON trades (scope, number)"
        " WHERE status = 'pending'"
    )
    # Уборка, возвращающая ставки, читает только стоящие строки, старые сверху.
    op.execute(
        "CREATE INDEX trades_pending_age_idx ON trades (scope, created_at) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX trades_author_idx ON trades (author_character_id)")
    op.execute("CREATE INDEX trades_target_idx ON trades (target_character_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trades")
