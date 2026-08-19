"""Расчёт, который смотритель откатил.

Откат — не возврат сделки в ожидание, а её собственное состояние: то, что
произошло, произошло, и журнал обязан говорить об этом дальше. Поэтому у
``status`` появляется пятое значение, а не исчезает четвёртое.

Момент расчёта при этом не переписывается. Когда сделку откатили и кто это
сделал, лежит в ``keeper_log`` (0013): в журнале сделок читают, что было между
игроками, а в журнале смотрителя — что было сделано поверх.

Колонок не прибавилось: строку опознаёт ``id``, который у таблицы был с самого
начала (0002). Короткий номер, который набирают игроки, освобождается сразу
после расчёта и назвать давнюю сделку не может.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_status")
    op.execute(
        """
        ALTER TABLE trades ADD CONSTRAINT trades_status
        CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'reverted'))
        """
    )


def downgrade() -> None:
    # Откаченные строки — уже случившееся; вернуть их в 'accepted' значило бы
    # сказать, что вещи и золото лежат не там, где они лежат.
    op.execute("UPDATE trades SET status = 'declined' WHERE status = 'reverted'")
    op.execute("ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_status")
    op.execute(
        """
        ALTER TABLE trades ADD CONSTRAINT trades_status
        CHECK (status IN ('pending', 'accepted', 'declined', 'expired'))
        """
    )
