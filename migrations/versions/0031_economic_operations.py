"""M01: операции ценностей, версии персонажей и связанный журнал.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE characters ADD COLUMN revision BIGINT NOT NULL DEFAULT 0")
    op.execute("""
        CREATE TABLE economic_operations (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL DEFAULT '',
            result JSONB NOT NULL DEFAULT 'null',
            completed BOOLEAN NOT NULL DEFAULT false,
            cache_changes JSONB NOT NULL DEFAULT '[]',
            cache_applied BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX economic_operations_pending ON economic_operations (created_at, id)"
        " WHERE completed AND NOT cache_applied"
    )
    op.execute("""
        CREATE TABLE economic_entries (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            operation_id TEXT NOT NULL REFERENCES economic_operations(id),
            owner_kind TEXT NOT NULL CHECK (owner_kind IN ('character', 'guild')),
            owner_id BIGINT NOT NULL,
            container TEXT NOT NULL,
            resource TEXT NOT NULL CHECK (resource IN ('gold', 'item')),
            item_id TEXT NOT NULL DEFAULT '',
            amount BIGINT NOT NULL CHECK (amount <> 0)
        )
    """)
    op.execute("CREATE INDEX economic_entries_operation ON economic_entries (operation_id, id)")
    op.execute("CREATE INDEX economic_entries_owner ON economic_entries (owner_kind, owner_id, id)")
    op.execute(
        "ALTER TABLE gold_flow ADD COLUMN operation_id TEXT REFERENCES economic_operations(id)"
    )
    op.execute("""
        CREATE FUNCTION character_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.revision := OLD.revision + 1;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER character_revision BEFORE UPDATE ON characters
        FOR EACH ROW EXECUTE FUNCTION character_revision()
    """)
    op.execute("""
        CREATE FUNCTION economic_entry(
            who TEXT, owner BIGINT, place TEXT, what TEXT, item TEXT, delta BIGINT
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE operation TEXT;
        BEGIN
            IF delta = 0 THEN RETURN; END IF;
            operation := nullif(current_setting('vellar.operation_id', true), '');
            IF operation IS NULL THEN
                operation := 'sql:' || txid_current()::text;
                INSERT INTO economic_operations (id, kind, completed)
                VALUES (operation, 'sql', true) ON CONFLICT DO NOTHING;
            END IF;
            INSERT INTO economic_entries
                (operation_id, owner_kind, owner_id, container, resource, item_id, amount)
            VALUES (operation, who, owner, place, what, item, delta);
        END $$
    """)
    op.execute("""
        CREATE FUNCTION economic_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            before_row JSONB := '{}'; after_row JSONB := '{}';
            owner BIGINT; item TEXT; delta BIGINT;
        BEGIN
            IF TG_OP <> 'INSERT' THEN before_row := to_jsonb(OLD); END IF;
            IF TG_OP <> 'DELETE' THEN after_row := to_jsonb(NEW); END IF;
            IF TG_TABLE_NAME = 'characters' THEN
                owner := coalesce(after_row->>'id', before_row->>'id')::bigint;
                FOREACH item IN ARRAY ARRAY['gold', 'bank_gold', 'arena_credit'] LOOP
                    delta := coalesce((after_row->>item)::bigint, 0)
                           - coalesce((before_row->>item)::bigint, 0);
                    PERFORM economic_entry('character', owner, item, 'gold', '', delta);
                END LOOP;
                FOR item IN
                    SELECT DISTINCT value FROM (
                        SELECT value FROM jsonb_each_text(coalesce(before_row->'equipment', '{}'))
                        UNION ALL
                        SELECT value FROM jsonb_each_text(coalesce(after_row->'equipment', '{}'))
                    ) AS equipment
                LOOP
                    SELECT count(*) INTO delta
                    FROM jsonb_each_text(coalesce(after_row->'equipment', '{}')) e
                    WHERE e.value = item;
                    SELECT delta - count(*) INTO delta
                    FROM jsonb_each_text(coalesce(before_row->'equipment', '{}')) e
                    WHERE e.value = item;
                    PERFORM economic_entry('character', owner, 'equipment', 'item', item, delta);
                END LOOP;
            ELSIF TG_TABLE_NAME = 'inventory' THEN
                owner := coalesce(after_row->>'character_id', before_row->>'character_id')::bigint;
                item := coalesce(after_row->>'item_id', before_row->>'item_id');
                delta := coalesce((after_row->>'quantity')::bigint, 0)
                       - coalesce((before_row->>'quantity')::bigint, 0);
                PERFORM economic_entry('character', owner, 'bag', 'item', item, delta);
            ELSIF TG_TABLE_NAME = 'guilds' THEN
                owner := coalesce(after_row->>'id', before_row->>'id')::bigint;
                delta := coalesce((after_row->>'vault_gold')::bigint, 0)
                       - coalesce((before_row->>'vault_gold')::bigint, 0);
                PERFORM economic_entry('guild', owner, 'vault', 'gold', '', delta);
            ELSIF TG_TABLE_NAME = 'guild_items' THEN
                owner := coalesce(after_row->>'guild_id', before_row->>'guild_id')::bigint;
                item := coalesce(after_row->>'item_id', before_row->>'item_id');
                delta := coalesce((after_row->>'quantity')::bigint, 0)
                       - coalesce((before_row->>'quantity')::bigint, 0);
                PERFORM economic_entry('guild', owner, 'store', 'item', item, delta);
            ELSIF TG_TABLE_NAME = 'trades' THEN
                -- Открытое предложение хранит резерв автора. Закрытие освобождает его.
                owner := coalesce(after_row->>'author_character_id',
                                  before_row->>'author_character_id')::bigint;
                item := coalesce(after_row->>'item_id', before_row->>'item_id');
                delta := (CASE WHEN after_row->>'status' = 'pending' THEN 1 ELSE 0 END)
                       - (CASE WHEN before_row->>'status' = 'pending' THEN 1 ELSE 0 END);
                IF coalesce(after_row->>'kind', before_row->>'kind') = 'sell' THEN
                    delta := delta * coalesce((after_row->>'quantity')::bigint,
                                              (before_row->>'quantity')::bigint);
                    PERFORM economic_entry('character', owner, 'escrow', 'item', item, delta);
                ELSE
                    delta := delta * coalesce((after_row->>'price')::bigint,
                                              (before_row->>'price')::bigint);
                    PERFORM economic_entry('character', owner, 'escrow', 'gold', '', delta);
                END IF;
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ("characters", "inventory", "guilds", "guild_items", "trades"):
        op.execute(
            f"CREATE TRIGGER economic_audit AFTER INSERT OR UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION economic_audit()"
        )


def downgrade() -> None:
    for table in ("characters", "inventory", "guilds", "guild_items", "trades"):
        op.execute(f"DROP TRIGGER economic_audit ON {table}")
    op.execute("DROP FUNCTION economic_audit()")
    op.execute("DROP FUNCTION economic_entry(TEXT, BIGINT, TEXT, TEXT, TEXT, BIGINT)")
    op.execute("DROP TRIGGER character_revision ON characters")
    op.execute("DROP FUNCTION character_revision()")
    op.execute("ALTER TABLE characters DROP COLUMN revision")
    op.execute("ALTER TABLE gold_flow DROP COLUMN operation_id")
    op.execute("DROP TABLE economic_entries")
    op.execute("DROP TABLE economic_operations")
