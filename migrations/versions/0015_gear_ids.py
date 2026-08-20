"""Снаряжение сменило имена: сорок написанных вещей стали собранными.

Вещи в игре больше не пишут руками — их собирают из вида, ступени и редкости
(ADR 0015), и вместе с этим у каждой сменилось имя: ``rusty_sword`` стал
``sword@1#common``. В базе имена лежат в двух местах: строками в ``inventory`` и
ключами слотов в ``characters.equipment``.

Ничего производного не хранится, поэтому пересчитается всё — кроме самих ссылок.
Их и переписывает эта миграция: без неё сумки и слоты у тех, кто уже играет,
опустели бы молча.

Соответствия подобраны по смыслу: ступень берётся ближайшая снизу к уровню старой
вещи, редкость — её собственная («эпический» стал легендарным, потому что
эпического больше нет). Что не названо здесь, того и не было.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

#: Старое имя -> собранное. Обратной дороги нет: ``downgrade`` возвращает имена,
#: но вещей под ними в содержимом уже нет, и это честнее, чем делать вид.
RENAMES: dict[str, str] = {
    "rusty_sword": "sword@1#common",
    "guard_saber": "sword@6#uncommon",
    "militia_spear": "spear@1#common",
    "hunting_bow": "bow@1#common",
    "apprentice_staff": "staff@1#common",
    "poacher_daggers": "dagger@6#uncommon",
    "grove_branch": "staff@6#uncommon",
    "dwarven_maul": "mace@14#rare",
    "silver_censer": "symbol@14#rare",
    "stormcaller_rod": "wand@26#legendary",
    "bone_dagger": "dagger@1#common",
    "sailors_shortsword": "short_sword@1#common",
    "woodcutters_axe": "axe@1#common",
    "field_mace": "mace@1#common",
    "heavy_blade": "greatsword@6#common",
    "willow_wand": "wand@1#common",
    "pilgrims_symbol": "symbol@1#common",
    "padded_jacket": "cloth_body@1#common",
    "leather_armor": "light_body@6#common",
    "chain_shirt": "medium_body@6#uncommon",
    "grove_wardens_mail": "medium_body@14#rare",
    "runed_plate": "heavy_body@26#legendary",
    "runed_robe": "cloth_body@6#uncommon",
    "iron_cuirass": "heavy_body@6#uncommon",
    "worn_hood": "cloth_head@1#common",
    "iron_helm": "medium_head@6#common",
    "seers_circlet": "cloth_head@14#rare",
    "leather_cap": "light_head@1#common",
    "work_gloves": "light_hands@1#common",
    "bracers_of_grip": "medium_hands@6#uncommon",
    "linen_wraps": "cloth_hands@1#common",
    "iron_gauntlets": "heavy_hands@6#uncommon",
    "travel_boots": "light_feet@1#common",
    "silent_steps": "light_feet@14#rare",
    "soft_slippers": "cloth_feet@1#common",
    "plate_greaves": "heavy_feet@14#rare",
    "copper_charm": "charm@1#common",
    "wolf_fang": "charm@6#uncommon",
    "tide_pearl": "pendant@14#rare",
    "ashen_signet": "ring@26#legendary",
}


def _rename(pairs: dict[str, str]) -> None:
    for old, new in pairs.items():
        # Сумка: у игрока могли лежать обе вещи разом, если он успел получить
        # новую до миграции. Складываем в одну строку, а не роняем вставку.
        op.execute(
            f"""
            INSERT INTO inventory (character_id, item_id, quantity)
            SELECT character_id, '{new}', quantity FROM inventory WHERE item_id = '{old}'
            ON CONFLICT (character_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
            """
        )
        op.execute(f"DELETE FROM inventory WHERE item_id = '{old}'")
        # Слоты: строка внутри JSON, поэтому меняется текстом и разбирается назад.
        op.execute(
            f"""
            UPDATE characters
            SET equipment = replace(equipment::text, '"{old}"', '"{new}"')::jsonb
            WHERE equipment::text LIKE '%"{old}"%'
            """
        )


def upgrade() -> None:
    _rename(RENAMES)


def downgrade() -> None:
    _rename({new: old for old, new in RENAMES.items()})
