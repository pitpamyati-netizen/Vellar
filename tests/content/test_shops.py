"""Ошибки разделения товаров отклоняются при загрузке содержимого."""

import copy
import tomllib

import pytest

from mmorpg.infrastructure.content.shops import parse_shops
from tests.conftest import CONTENT_ROOT


def test_all_written_goods_have_exactly_one_merchant(content):
    for item in content.items:
        assert sum(shop.sells(item) for shop in content.shops) == 1, item.id


@pytest.mark.parametrize(
    "mistake",
    [
        "id",
        "name",
        "description",
        "duplicate_id",
        "duplicate_name",
        "equipment",
        "duplicate_equipment",
        "empty",
        "unknown_item",
        "duplicate_item",
        "empty_family",
        "unordered_family",
        "missing_family",
    ],
)
def test_invalid_shops_are_rejected(content, mistake):
    with (CONTENT_ROOT / "shops.toml").open("rb") as handle:
        raw = copy.deepcopy(tomllib.load(handle))
    shops = raw["shop"]
    if mistake in {"id", "name", "description"}:
        shops[0][mistake] = ""
    elif mistake == "duplicate_id":
        shops[1]["id"] = shops[0]["id"]
    elif mistake == "duplicate_name":
        shops[1]["name"] = shops[0]["name"].upper()
    elif mistake == "equipment":
        shops[0]["equipment"] = "missing"
    elif mistake == "duplicate_equipment":
        shops[1]["equipment"] = "weapon"
    elif mistake == "empty":
        shops[0]["equipment"] = ""
    elif mistake == "unknown_item":
        shops[3]["families"][0].append("missing")
    elif mistake == "duplicate_item":
        shops[3]["families"].append(["small_healing_potion"])
    elif mistake == "empty_family":
        shops[3]["families"].append([])
    elif mistake == "unordered_family":
        shops[3]["families"][0].reverse()
    elif mistake == "missing_family":
        shops[3]["families"].pop()
    problems = []
    parse_shops(raw, [i for i in content.items if not i.is_equipment], problems)
    assert problems
    assert all(p.startswith("shops.toml:") for p in problems)
