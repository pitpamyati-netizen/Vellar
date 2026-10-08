"""Износ принадлежит физическому экземпляру; старые количества не теряются."""

import re

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = """
        CREATE TABLE item_instances (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)
        );
        CREATE TEMP TABLE old_item_wear AS SELECT id, wear FROM characters;
        CREATE FUNCTION migrate_item_refs(template TEXT, amount BIGINT, spent INTEGER)
        RETURNS TEXT LANGUAGE plpgsql AS $$
        DECLARE refs TEXT[] := '{}'; number BIGINT; i INTEGER;
        BEGIN
            FOR i IN 1..amount LOOP
                INSERT INTO item_instances(used) VALUES(greatest(0, spent))
                    RETURNING id INTO number;
                refs := array_append(refs, number::text);
            END LOOP;
            RETURN template || '!' || array_to_string(refs, ',');
        END $$;
        DO $$ DECLARE r RECORD; ref TEXT; part TEXT; gear JSONB;
        BEGIN
            FOR r IN SELECT inventory.*, coalesce((w.wear->>item_id)::integer, 0) AS spent
                FROM inventory JOIN old_item_wear w ON w.id=inventory.character_id
                WHERE item_id ~ '^[^!]+@[0-9]+#[^!]+$' AND quantity > 0
            LOOP
                ref := migrate_item_refs(r.item_id, r.quantity, r.spent);
                DELETE FROM inventory WHERE character_id=r.character_id AND item_id=r.item_id;
                FOREACH part IN ARRAY string_to_array(split_part(ref, '!', 2), ',') LOOP
                    INSERT INTO inventory(character_id,item_id,quantity)
                    VALUES(r.character_id, r.item_id || '!' || part, 1);
                END LOOP;
            END LOOP;
            FOR r IN SELECT c.id, c.equipment, w.wear FROM characters c
                JOIN old_item_wear w USING(id)
            LOOP
                gear := r.equipment;
                FOR part, ref IN SELECT key, value FROM jsonb_each_text(r.equipment) LOOP
                    IF ref ~ '^[^!]+@[0-9]+#[^!]+$' THEN
                        gear := jsonb_set(gear, ARRAY[part], to_jsonb(migrate_item_refs(
                            ref, 1, coalesce((r.wear->>ref)::integer, 0))));
                    END IF;
                END LOOP;
                UPDATE characters SET equipment=gear, wear=(
                    SELECT coalesce(jsonb_object_agg(key, value), '{}') FROM jsonb_each(r.wear)
                    WHERE key !~ '^[^!]+@[0-9]+#[^!]+$'
                ) WHERE id=r.id;
            END LOOP;
            FOR r IN SELECT * FROM guild_items WHERE item_id ~ '^[^!]+@[0-9]+#[^!]+$' AND quantity>0
            LOOP
                ref := migrate_item_refs(r.item_id, r.quantity, coalesce((
                    SELECT max((wear->>r.item_id)::integer) FROM old_item_wear), 0));
                DELETE FROM guild_items WHERE guild_id=r.guild_id AND item_id=r.item_id;
                FOREACH part IN ARRAY string_to_array(split_part(ref, '!', 2), ',') LOOP
                    INSERT INTO guild_items(guild_id,item_id,quantity)
                    VALUES(r.guild_id, r.item_id || '!' || part, 1);
                END LOOP;
            END LOOP;
            FOR r IN SELECT * FROM trades WHERE status='pending' AND kind='sell'
                AND item_id ~ '^[^!]+@[0-9]+#[^!]+$'
            LOOP
                ref := migrate_item_refs(r.item_id, r.quantity, coalesce((
                    SELECT (wear->>r.item_id)::integer FROM old_item_wear
                    WHERE id=r.author_character_id), 0));
                UPDATE trades SET item_id=ref WHERE id=r.id;
            END LOOP;
            FOR r IN SELECT * FROM trades WHERE status='pending' AND kind='buy'
                AND item_id ~ '^[^!]+@[0-9]+#[^!]+$'
            LOOP
                SELECT string_agg(split_part(item_id, '!', 2), ',' ORDER BY item_id) INTO ref
                FROM (SELECT item_id FROM inventory WHERE character_id=r.target_character_id
                    AND split_part(item_id, '!', 1)=r.item_id AND quantity>0
                    ORDER BY item_id LIMIT r.quantity) selected;
                IF ref IS NOT NULL AND cardinality(string_to_array(ref, ','))=r.quantity THEN
                    UPDATE trades SET item_id=r.item_id || '!' || ref WHERE id=r.id;
                END IF;
            END LOOP;
        END $$;
        DROP FUNCTION migrate_item_refs(TEXT, BIGINT, INTEGER);
        DROP TABLE old_item_wear;
        CREATE UNIQUE INDEX inventory_instance_unique ON inventory ((split_part(item_id, '!', 2)))
            WHERE strpos(item_id, '!')>0 AND quantity>0;
        CREATE UNIQUE INDEX guild_instance_unique ON guild_items ((split_part(item_id, '!', 2)))
            WHERE strpos(item_id, '!')>0 AND quantity>0;
        ALTER TABLE inventory ADD CONSTRAINT inventory_instance_single CHECK
            (strpos(item_id, '!')=0 OR (quantity IN (0,1) AND item_id ~ '^[^!]+![1-9][0-9]*$'));
        ALTER TABLE guild_items ADD CONSTRAINT guild_instance_single CHECK
            (strpos(item_id, '!')=0 OR (quantity IN (0,1) AND item_id ~ '^[^!]+![1-9][0-9]*$'));
        CREATE FUNCTION save_instance_wear() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE ref TEXT;
        BEGIN
            FOR ref IN SELECT value FROM jsonb_each_text(NEW.equipment)
                UNION SELECT item_id FROM inventory WHERE character_id=NEW.id AND quantity>0
            LOOP
                IF strpos(ref, '!')>0 THEN
                    UPDATE item_instances SET used=CASE WHEN NEW.wear ? ref THEN
                        CASE WHEN TG_OP='INSERT' OR NEW.wear->ref IS DISTINCT FROM OLD.wear->ref
                            THEN greatest(0, (NEW.wear->>ref)::integer) ELSE used END
                        WHEN OLD.wear ? ref THEN 0 ELSE used END
                    WHERE id=split_part(ref, '!', 2)::bigint;
                END IF;
            END LOOP;
            RETURN NEW;
        END $$;
        CREATE TRIGGER character_instance_wear AFTER INSERT OR UPDATE OF wear ON characters
            FOR EACH ROW EXECUTE FUNCTION save_instance_wear();
        CREATE FUNCTION check_instance_location() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE ref TEXT; number TEXT; occurrences INTEGER; data JSONB;
        BEGIN
            FOR data IN SELECT to_jsonb(NEW) UNION SELECT to_jsonb(OLD) LOOP
                FOR ref IN SELECT data->>'item_id' WHERE data ? 'item_id'
                    UNION SELECT value FROM jsonb_each_text(coalesce(data->'equipment','{}'))
                LOOP
                    IF strpos(ref, '!')>0 THEN
                        FOREACH number IN ARRAY string_to_array(split_part(ref, '!', 2), ',') LOOP
                            IF NOT EXISTS(SELECT 1 FROM item_instances WHERE id=number::bigint) THEN
                                RAISE EXCEPTION 'Unknown item instance';
                            END IF;
                            SELECT count(*) INTO occurrences FROM (
                                SELECT item_id FROM inventory WHERE quantity>0
                                UNION ALL SELECT item_id FROM guild_items WHERE quantity>0
                                UNION ALL SELECT value FROM characters c,
                                    LATERAL jsonb_each_text(c.equipment)
                                UNION ALL SELECT item_id FROM trades
                                    WHERE status='pending' AND kind='sell'
                            ) locations WHERE number=ANY(
                                string_to_array(split_part(item_id, '!', 2), ','));
                            IF occurrences>1 THEN
                                RAISE EXCEPTION 'Item has multiple owners'; END IF;
                        END LOOP;
                    END IF;
                END LOOP;
            END LOOP;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER inventory_instance_location AFTER INSERT OR UPDATE OR DELETE
            ON inventory DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION check_instance_location();
        CREATE CONSTRAINT TRIGGER guild_instance_location AFTER INSERT OR UPDATE OR DELETE
            ON guild_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION check_instance_location();
        CREATE CONSTRAINT TRIGGER equipment_instance_location AFTER INSERT OR UPDATE OR DELETE
            ON characters DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION check_instance_location();
        CREATE CONSTRAINT TRIGGER trade_instance_location AFTER INSERT OR UPDATE OR DELETE
            ON trades DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION check_instance_location();
    """
    for statement in re.findall(r"(?:\$\$.*?\$\$|[^;])+;", sql, flags=re.DOTALL):
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Экземпляры нельзя объединить без потери износа; восстановите проверенную копию"
    )
