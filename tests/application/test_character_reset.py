"""Полная потеря прежнего прогресса, безопасность и повтор сброса."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.application.operations import atomic_action
from mmorpg.application.services.character_reset import GOALS, CharacterReset
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.market import CODEC, KEY
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.character import Character, Equipment, SkillLoadout
from mmorpg.domain.entities.craft import CraftLog
from mmorpg.domain.entities.long_goal import GoalState
from mmorpg.domain.entities.market import CraftOrder, Lot, MarketState
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeStatus
from mmorpg.domain.rules import tutorial
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.persistence.instances import MemoryItemInstances
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
    InMemoryPartyRepository,
    InMemoryTradeRepository,
)


def draft_for(content, name="Аргус"):
    return CharacterDraft(
        name=name,
        race_id="dwarf",
        class_id="mage",
        trait_ids=tuple(one.id for one in content.traits[: content.rules.traits_at_creation]),
        allocated=StatBlock(STR=content.rules.free_points_at_creation),
    )


def rich_hero(user_id=42, name="Аргус"):
    return Character(
        0,
        user_id,
        name,
        "human",
        "warrior",
        level=150,
        experience=900000,
        gold=650,
        bank_gold=250,
        allocated=StatBlock(STR=120, AGI=90),
        trait_ids=("old_trait",),
        loadout=SkillLoadout(
            ranks={"old_skill": 5}, masteries={"old_skill": ("old_facet",)}, talents=("old_talent",)
        ),
        equipment=Equipment({"weapon": "sword@1#common"}),
        city_id="kolva",
        unspent_stat_points=65,
        unspent_skill_points=50,
        health=2,
        quests=QuestLog({"old_quest": 5}, ("old_done",)),
        crafts=CraftLog().with_experience("smith", 1200),
        arena_wins=10,
        arena_losses=5,
        arena_credit=50,
        house_id="old_house",
        subclass_ids=("old_subclass",),
        tutorial=1,
        is_admin=True,
    )


@pytest.fixture
async def restart(content):
    instances = MemoryItemInstances(content)
    chars = InMemoryCharacterRepository(instances)
    inventory = InMemoryInventoryRepository(instances)
    cache = InMemoryStateCache()
    trades = InMemoryTradeRepository()
    parties = PartyStore(InMemoryPartyRepository(), cache)
    guilds = GuildStore(InMemoryGuildRepository(), cache)
    hero = await chars.create(rich_hero())
    await inventory.add(hero.id, "iron_scrap", 4)
    await inventory.add(hero.id, "sword@1#common")
    hero = await chars.get(hero.id)
    await cache.set(
        f"long-goal:hero:{hero.id}",
        GOALS.dump_json(
            GoalState(trial=True, crafted=3, studied=True, claimed=("craft",))
        ).decode(),
        10**12,
    )
    return SimpleNamespace(
        characters=chars,
        inventory=inventory,
        cache=cache,
        trades=trades,
        parties=parties,
        guilds=guilds,
        hero=hero,
        draft=draft_for(content),
        service=CharacterReset(content, chars, inventory, cache, trades, parties, guilds),
    )


async def assert_fresh(e, content, fresh):
    expected = e.draft.to_character(content, e.hero.user_id)
    assert fresh.id == e.hero.id and fresh.user_id == e.hero.user_id
    assert fresh.name == e.hero.name and fresh.is_admin == e.hero.is_admin
    assert fresh.race_id == "dwarf" and fresh.class_id == "mage"
    assert fresh.level == 1 and fresh.experience == 0
    assert (fresh.gold, fresh.bank_gold, fresh.arena_credit) == (0, 0, 0)
    assert fresh.allocated == expected.allocated and fresh.trait_ids == expected.trait_ids
    assert fresh.loadout == expected.loadout
    assert not fresh.subclass_ids and not fresh.house_id
    assert fresh.quests == QuestLog() and fresh.crafts == CraftLog()
    assert (fresh.unspent_stat_points, fresh.unspent_skill_points) == (0, 0)
    assert (fresh.arena_wins, fresh.arena_losses, fresh.health) == (0, 0, 0)
    assert not fresh.wear.used and fresh.city_id == expected.city_id
    assert tutorial.finished(fresh)
    assert await e.inventory.list_items(fresh.id) == ()
    assert {ref.split("!")[0] for ref in fresh.equipment.item_ids()} == {
        ref.split("!")[0] for ref in expected.equipment.item_ids()
    }
    assert not set(fresh.equipment.item_ids()) & set(e.hero.equipment.item_ids())
    goals = GOALS.validate_json(await e.cache.get(f"long-goal:hero:{fresh.id}"))
    assert goals == GoalState(claimed=("craft",))


async def test_full_reset_forgets_everything_and_keeps_used_rights(restart, content):
    e = restart
    await e.cache.set("war:account:42", "used", 10**12)
    fresh, _ = await e.service.confirm(42, e.hero.revision, e.draft)
    assert fresh is not None
    await assert_fresh(e, content, fresh)
    assert await e.cache.get("war:account:42") == "used"


async def test_repeat_and_two_confirmations_do_not_reset_twice(restart):
    e = restart
    results = await asyncio.gather(
        *(e.service.confirm(42, e.hero.revision, e.draft) for _ in range(2))
    )
    assert sum(hero is not None for hero, _ in results) == 1
    fresh = await e.characters.get(e.hero.id)
    replay, _ = await e.service.confirm(42, e.hero.revision, e.draft)
    assert replay is None and await e.characters.get(e.hero.id) == fresh


async def test_failure_after_all_writes_rolls_back_everything(restart):
    e = restart
    before = await e.inventory.list_items(e.hero.id)
    goals = await e.cache.get(f"long-goal:hero:{e.hero.id}")

    @atomic_action
    async def fail(service) -> None:
        assert (await service.confirm(42, e.hero.revision, e.draft))[0]
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled failure"):
        await fail(e.service)
    assert await e.characters.get(e.hero.id) == e.hero
    assert await e.inventory.list_items(e.hero.id) == before
    assert await e.cache.get(f"long-goal:hero:{e.hero.id}") == goals


@pytest.mark.parametrize("kind", ["lot", "reservation", "order", "party", "guild", "trade"])
async def test_outstanding_assets_and_groups_block_reset_without_loss(restart, kind):
    e = restart
    if kind in {"lot", "reservation", "order"}:
        state = MarketState()
        if kind == "lot":
            state = replace(state, lots=(Lot(1, e.hero.id, "iron_scrap", 1, 20, 99999),))
        elif kind == "reservation":
            state = replace(
                state,
                lots=(
                    Lot(1, 999, "iron_scrap", 1, 20, 99999, status="reserved", buyer_id=e.hero.id),
                ),
            )
        else:
            state = replace(state, orders=(CraftOrder(1, e.hero.id, "smith_ingot@1", 20, 99999),))
        await e.cache.set(KEY, CODEC.dump_json(state).decode(), 10**12)
    elif kind == "party":
        await e.parties.create(e.hero.id)
    elif kind == "guild":
        await e.guilds._roster.create("Проверка", e.hero.id)
    else:
        await e.trades.open(
            Offer(
                0,
                OfferKind.SELL,
                Party(99, 99, "Другой"),
                Party(42, e.hero.id, e.hero.name),
                "iron_scrap",
                "Железо",
                20,
            ),
            scope="reset-test",
        )
    fresh, notice = await e.service.confirm(42, e.hero.revision, e.draft)
    assert fresh is None and "Сначала" in notice
    assert await e.characters.get(e.hero.id) == e.hero
    assert await e.inventory.count(e.hero.id, "iron_scrap") == 4


async def test_closed_offer_no_longer_blocks_reset(restart):
    e = restart
    record = await e.trades.open(
        Offer(
            0,
            OfferKind.SELL,
            Party(42, e.hero.id, e.hero.name),
            Party(99, 99, "Другой"),
            "iron_scrap",
            "Железо",
            20,
        ),
        scope="reset-test",
    )
    assert await e.trades.has_pending(e.hero.id)
    await e.trades.close(
        record.number, scope=record.scope, status=TradeStatus.DECLINED, settled_at=1
    )
    assert (await e.service.confirm(42, e.hero.revision, e.draft))[0] is not None


async def test_changed_character_and_incomplete_choice_refuse(restart):
    e = restart
    assert (await e.service.confirm(42, e.hero.revision, replace(e.draft, race_id="")))[0] is None
    await e.characters.save(e.hero.with_gold(1))
    assert (await e.service.confirm(42, e.hero.revision, e.draft))[0] is None
    assert (await e.characters.get(e.hero.id)).gold == 651
