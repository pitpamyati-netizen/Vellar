"""Резерв рынка входит в тот же журнал, что кошельки и сумки."""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION market_escrow(value jsonb)
        RETURNS TABLE(owner_id bigint, resource text, item_id text, amount bigint)
        LANGUAGE sql IMMUTABLE AS $$
          SELECT (lot->>'seller_id')::bigint, 'item', lot->>'item_id',
                 (lot->>'quantity')::bigint
          FROM jsonb_array_elements(coalesce(value->'lots', '[]')) lot
          WHERE lot->>'status' IN ('open', 'reserved')
          UNION ALL
          SELECT (lot->>'buyer_id')::bigint, 'gold', '', (lot->>'price')::bigint
          FROM jsonb_array_elements(coalesce(value->'lots', '[]')) lot
          WHERE lot->>'status' = 'reserved'
          UNION ALL
          SELECT (ord->>'buyer_id')::bigint, 'gold', '', (ord->>'price')::bigint
          FROM jsonb_array_elements(coalesce(value->'orders', '[]')) ord
          WHERE ord->>'status' = 'open'
        $$
    """)
    op.execute("""
        CREATE FUNCTION market_economic_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE before_state jsonb := '{}'; after_state jsonb := '{}'; row record;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.key <> 'market:board' THEN RETURN NULL; END IF;
          ELSE
            IF NEW.key <> 'market:board' THEN RETURN NULL; END IF;
          END IF;
          IF TG_OP <> 'INSERT' THEN
            before_state := coalesce(nullif(OLD.value, ''), '{}')::jsonb;
          END IF;
          IF TG_OP <> 'DELETE' THEN
            after_state := coalesce(nullif(NEW.value, ''), '{}')::jsonb;
          END IF;
          FOR row IN
            SELECT owner_id, resource, item_id, sum(amount)::bigint AS delta FROM (
              SELECT * FROM market_escrow(after_state)
              UNION ALL
              SELECT owner_id, resource, item_id, -amount FROM market_escrow(before_state)
            ) changes GROUP BY owner_id, resource, item_id HAVING sum(amount) <> 0
          LOOP
            PERFORM economic_entry('character', row.owner_id, 'market_escrow',
                                   row.resource, row.item_id, row.delta);
          END LOOP;
          RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER market_economic_audit AFTER INSERT OR UPDATE OR DELETE
        ON gameplay_state FOR EACH ROW
        EXECUTE FUNCTION market_economic_audit()
    """)
    op.execute("""
        CREATE FUNCTION protect_market_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM gameplay_state g,
                     LATERAL market_escrow(coalesce(nullif(g.value, ''), '{}')::jsonb) e
                     WHERE g.key='market:board' AND e.owner_id=OLD.id) THEN
            RAISE EXCEPTION 'Character has active market escrow'
              USING ERRCODE = 'foreign_key_violation';
          END IF;
          RETURN OLD;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER protect_market_owner BEFORE DELETE ON characters
        FOR EACH ROW EXECUTE FUNCTION protect_market_owner()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER protect_market_owner ON characters")
    op.execute("DROP FUNCTION protect_market_owner()")
    op.execute("DROP TRIGGER market_economic_audit ON gameplay_state")
    op.execute("DROP FUNCTION market_economic_audit()")
    op.execute("DROP FUNCTION market_escrow(jsonb)")
