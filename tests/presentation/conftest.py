"""Приспособления экранов.

``all_screens`` собирает каждый экран, который игра умеет показать, чтобы тесты
доступности могли осмотреть их все разом. Каждый новый экран обязан быть добавлен
сюда, и это нарочно: экран, которого никто не перечислил, - это экран, которого
никто не проверил.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities import (
    Character,
    Equipment,
    GameContent,
    GeneratedLocation,
    InventoryEntry,
    QuestLog,
    SkillLoadout,
    StatBlock,
)
from mmorpg.domain.entities.combat import ActionTag, BattleState, Trace
from mmorpg.domain.entities.content import Item
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.location import (
    Enemy,
    EnemyKind,
    EnemyRank,
    LocationState,
    NodeState,
    Roamer,
)
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import Census
from mmorpg.domain.procgen import generate_location, location_seed
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules.combat import hero_combatant, monster_combatant, open_battle
from mmorpg.domain.rules.economy import buy_price, roll_assortment
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank
from mmorpg.domain.rules.party import Party as PlayerParty
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.handlers import creation as handlers_creation
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens import arena as arena_screens
from mmorpg.presentation.telegram.screens import chamber as chamber_screens
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import crafts as craft_screens
from mmorpg.presentation.telegram.screens import creation, play, shop
from mmorpg.presentation.telegram.screens import guild as guild_screens
from mmorpg.presentation.telegram.screens import items as item_screens
from mmorpg.presentation.telegram.screens import keeper as keeper_screens
from mmorpg.presentation.telegram.screens import party as party_screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens import transfer as transfer_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    ListFilters,
    PageState,
    filters_screen,
    paginated_screen,
)


@pytest.fixture(scope="session")
def hero() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


@pytest.fixture(scope="session")
def sample_location() -> GeneratedLocation:
    return generate_location(
        world_seed="vellar-test",
        city_id="farhold",
        slot=1,
        name="Луга у Заставы",
        biome="луга",
        level_min=1,
        level_max=4,
    )


def full_location(location: GeneratedLocation) -> dict[int, node_rules.Standing]:
    """Локация, в которой ещё никто не был: в каждом узле стоит полная волна."""
    return node_rules.standing(
        location_seed("vellar-test", location.city_id, location.slot),
        location,
        LocationState(),
        now=0,
    )


def emptied_location(location: GeneratedLocation) -> dict[int, node_rules.Standing]:
    """Локация, из которой всё вынесли минуту назад: узлы ждут новой волны."""
    emptied = LocationState(
        nodes={node.index: NodeState(taken=99, emptied_at=1) for node in location.nodes}
    )
    return node_rules.standing(
        location_seed("vellar-test", location.city_id, location.slot),
        location,
        emptied,
        now=60,
    )


def _with_tutorial_hint(screen: Screen, screen_id: ScreenId, character: Character) -> Screen:
    """Экран шага с приклеенной подсказкой обучения — так его дописывает ``render`` (ADR 0038)."""
    hint = tutorial_screens.hint_line(screen_id, character)
    assert hint, "фикстуре нужен незакрытый шаг на этом экране"
    return replace(screen, lines=(*screen.lines, hint))


@pytest.fixture(scope="session")
def fighter(content: GameContent) -> Character:
    return Character(
        id=2,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        gold=400,
        health=90,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", "warrior_provokatsiya", None, None, None, None),
            racial="race_human_second_wind",
            # Постоянное умение слота не занимает: изучено - значит работает.
            ranks=MappingProxyType(
                {"warrior_rassechenie": 3, "warrior_provokatsiya": 1, "warrior_stoykost": 1}
            ),
        ),
        quests=QuestLog(taken=MappingProxyType({"farhold_tallies": 2})),
    )


def build_battle(
    content: GameContent,
    character: Character,
    enemies: tuple[Enemy, ...],
    *,
    seed: bytes = b"battle-seed",
) -> BattleState:
    """Бой одного героя против стаи - то, что рисует экран боя."""
    hero = hero_combatant(content, character, combatant_id=1, side=0, live=True)
    pack = [
        monster_combatant(enemy, combatant_id=index + 2, side=1)
        for index, enemy in enumerate(enemies)
    ]
    return open_battle(content, {1: character}, [hero, *pack], seed)


@pytest.fixture(scope="session")
def sample_fight(content: GameContent, fighter: Character) -> BattleState:
    enemy = Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=4,
        max_health=120,
        damage=9,
        armor=3,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=14,
    )
    return build_battle(content, fighter, (enemy,))


@pytest.fixture(scope="session")
def crowded_fight(content: GameContent, fighter: Character) -> BattleState:
    """Самый длинный вид боевого экрана: три противника, каждый объявляет намерение,
    и след, о котором есть что сказать.
    """
    pack = tuple(
        Enemy(
            archetype_id="grey_wolf",
            name=name,
            kind=EnemyKind.BEAST,
            level=4,
            max_health=120,
            damage=9,
            armor=3,
            initiative=9.0 + index,
            loot=("wolf_pelt",),
            gold=14,
        )
        for index, name in enumerate(("Серый волк", "Волчица", "Вожак стаи"))
    )
    state = build_battle(content, fighter, pack)
    hero = state.by_id(1)
    assert hero is not None
    marked = state.replace_combatant(replace(hero, trace=Trace((ActionTag.GUARD, ActionTag.PRESS))))
    return replace(marked, round=7)


@pytest.fixture(scope="session")
def duel_fight(content: GameContent, fighter: Character) -> BattleState:
    """Поединок двоих живых: у обоих панель, ход у того, кто быстрее.

    Экран боя обязан звучать и с этой стороны: «Против вас» здесь - человек, а
    намерения у человека нет, есть след (ADR 0021).
    """
    other = replace(fighter, id=77, user_id=99, name="Мирна")
    left = hero_combatant(content, fighter, combatant_id=1, side=0, live=True)
    right = hero_combatant(content, other, combatant_id=2, side=1, live=True)
    return open_battle(content, {1: fighter, 2: other}, [left, right], b"duel-seed")


@pytest.fixture(scope="session")
def boss_fight(content: GameContent, fighter: Character) -> BattleState:
    """Ступень, объявляющая себя: строка противника обязана сказать, насколько это
    надолго, до того как игрок потратит на неё ход.
    """
    boss = Enemy(
        archetype_id="grey_wolf",
        name="Владыка серого волка",
        kind=EnemyKind.BEAST,
        level=12,
        max_health=900,
        damage=22,
        armor=18,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=140,
        rank=EnemyRank.BOSS,
    )
    return build_battle(content, fighter, (boss,))


@pytest.fixture(scope="session")
def sealbearer(fighter: Character) -> Character:
    """Тот, кто дошёл до конца: триста уровней, одна Печать и есть что заложить.

    На нём надета вещь выше запроса Палаты и доведено до полного ранга умение с
    выбранной гранью, поэтому экран заклада показывает оба вида заклада разом.
    """
    return replace(
        fighter,
        level=300,
        seals=1,
        pledges=("item:seers_circlet",),
        turning_cycle="toll",
        turning_answer="toll_keep",
        equipment=Equipment(
            MappingProxyType({"trinket": "ring@26#legendary", "body": "heavy_body@26#legendary"})
        ),
        loadout=replace(
            fighter.loadout,
            ranks=MappingProxyType({"warrior_rassechenie": 5, "warrior_provokatsiya": 5}),
            edges=MappingProxyType({"warrior_rassechenie": "warrior_rassechenie_a"}),
        ),
    )


@pytest.fixture(scope="session")
def craftsman(fighter: Character) -> Character:
    """Тот, кто уже вложил стражу-другую в два ремесла."""
    return replace(
        fighter,
        crafts=CraftLog(
            MappingProxyType(
                {
                    "mining": CraftProgress(experience=260, gathered_at=1_700_000_000),
                    "smithing": CraftProgress(experience=140),
                }
            )
        ),
    )


@pytest.fixture(scope="session")
def keeper(fighter: Character) -> Character:
    """Тот, чей Telegram-id стоит в ADMIN_IDS: один лишний ряд, и больше ничего."""
    return replace(fighter, is_admin=True)


@pytest.fixture(scope="session")
def edits() -> tuple[OverlayRecord, ...]:
    """Две правки: заведённый житель и его задание, одно из них недописанное."""
    return (
        OverlayRecord(
            kind=OverlayKind.NPC,
            entity_id="keeper_npc_1",
            fields=MappingProxyType(
                {
                    "name": "Довен",
                    "city": "farhold",
                    "role": "писарь заставы",
                    "text": "Водит пальцем по строкам сводки и не поднимает головы.",
                }
            ),
            author_id=42,
        ),
        OverlayRecord(
            kind=OverlayKind.QUEST,
            entity_id="keeper_quest_1",
            fields=MappingProxyType({"city": "farhold", "objective": "kill"}),
            author_id=42,
        ),
    )


#: Момент, которым меряются сроки на снимках экранов. Число, а не часы: экран
#: должен рисоваться одинаково в любой день.
NOW = 1_700_000_000


@pytest.fixture(scope="session")
def edited(content: GameContent, edits: tuple[OverlayRecord, ...]) -> GameContent:
    """Мир, в котором правки смотрителя уже стоят."""
    return overlay_rules.apply(content, edits)


def _trade(
    trade_id: int,
    kind: OfferKind,
    status: TradeStatus,
    *,
    item: str,
    price: int,
) -> TradeRecord:
    return TradeRecord(
        offer=Offer(
            number=trade_id,
            kind=kind,
            author=Party(user_id=42, character_id=2, name="Аргус"),
            target=Party(user_id=900, character_id=3, name="Мерла"),
            item_id="sword",
            item_name=item,
            price=price,
            created_at=NOW - 3600,
        ),
        scope="group",
        status=status,
        tax=price // 20,
        settled_at=NOW - 3500,
        id=trade_id,
    )


#: Журнал сделок для карточки: расчёт, отказ и уже откаченное - чтобы экран
#: проверялся на всех трёх, а не только на том, что можно нажать.
SAMPLE_TRADES: tuple[TradeRecord, ...] = (
    _trade(3, OfferKind.SELL, TradeStatus.ACCEPTED, item="Короткий меч", price=120),
    _trade(2, OfferKind.BUY, TradeStatus.DECLINED, item="Простая куртка", price=80),
    _trade(1, OfferKind.SELL, TradeStatus.REVERTED, item="Кольцо", price=400),
)


#: Карточка игрока, который и в отряде, и в гильдии: чтобы экраны отряда и
#: гильдии с карточки собрались вместе со всеми прочими.
_group_view = keeper_screens.KeeperView(
    target_party=PlayerParty(leader_id=7, members=(7, 8, 9)),
    target_party_members=((7, "Мерла"), (8, "Аргус"), (9, "Довен")),
    target_guild=Guild(
        id=1,
        name="Клинки",
        founder_id=7,
        members=(
            GuildMember(7, GuildRank.FOUNDER),
            GuildMember(8, GuildRank.OFFICER),
            GuildMember(9, GuildRank.MEMBER),
        ),
        vault_gold=400,
    ),
    target_guild_members=(
        (7, "Мерла", GuildRank.FOUNDER),
        (8, "Аргус", GuildRank.OFFICER),
        (9, "Довен", GuildRank.MEMBER),
    ),
    now=NOW,
)


@pytest.fixture(scope="session")
def keeper_view(edits: tuple[OverlayRecord, ...], fighter: Character) -> keeper_screens.KeeperView:
    return keeper_screens.KeeperView(
        records=edits,
        players=(fighter, replace(fighter, id=3, name="Мерла", level=22)),
        target=fighter,
        census=Census(
            characters=128,
            accounts=97,
            fresh_day=14,
            fresh_week=61,
            abandoned=9,
            blocked=3,
            top_level=41,
            average_level=7,
            gold_on_hand=812_400,
            gold_in_bank=310_000,
            quests_done=402,
            arena_fights=88,
            banned=2,
            leaders=(("Мерла", 41), ("Аргус", 22)),
        ),
        now=NOW,
        trades=SAMPLE_TRADES,
    )


@pytest.fixture(scope="session")
def banned_view(keeper_view: keeper_screens.KeeperView) -> keeper_screens.KeeperView:
    """Та же панель, но открытый игрок заблокирован, и журнал не пуст."""
    return replace(
        keeper_view,
        target_ban=Ban(until=NOW + 2 * 24 * 60 * 60, reason="ругался в группе"),
        log=(
            KeeperEntry(
                at=NOW - 600,
                keeper_id=1,
                keeper_name="Смотритель",
                action=KeeperAction.BAN,
                target="Мерла",
                detail="ругался в группе",
            ),
            KeeperEntry(
                at=NOW - 7200,
                keeper_id=1,
                keeper_name="Смотритель",
                action=KeeperAction.GOLD,
                target="Аргус",
            ),
        ),
    )


@pytest.fixture(scope="session")
def sample_stock(content: GameContent) -> tuple[Item, ...]:
    return roll_assortment(
        content, world_seed="vellar-test", city_id="farhold", rotation=100, character_level=8
    )


@pytest.fixture
def complete_draft() -> CharacterDraft:
    return CharacterDraft(
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        trait_ids=("berserker", "duelist"),
        allocated=StatBlock(STR=3, END=2),
    )


@pytest.fixture
def all_screens(
    content: GameContent,
    hero: Character,
    complete_draft: CharacterDraft,
    sample_location: GeneratedLocation,
    fighter: Character,
    sample_fight: BattleState,
    crowded_fight: BattleState,
    boss_fight: BattleState,
    duel_fight: BattleState,
    sample_stock: tuple[Item, ...],
    craftsman: Character,
    sealbearer: Character,
    keeper: Character,
    edited: GameContent,
    keeper_view: keeper_screens.KeeperView,
    banned_view: keeper_screens.KeeperView,
) -> list[Screen]:
    """Каждый экран игры, нарисованный на образцовых данных.

    Новые экраны обязаны попадать сюда: экран, которого никто не перечислил, - это
    экран, которого никто не сверял с правилами доступности.
    """
    empty = CharacterDraft()
    screens: list[Screen] = [
        creation.name_screen(empty),
        creation.name_screen(complete_draft, notice="Имя Аргус уже занято."),
        creation.race_screen(content, empty, PageState()),
        creation.race_screen(content, complete_draft, PageState(page=2)),
        creation.race_details_screen(content, "dwarf"),
        creation.class_screen(content, complete_draft),
        creation.class_details_screen(content, "warrior"),
        creation.traits_screen(content, complete_draft, PageState()),
        creation.traits_screen(content, empty, PageState(page=3)),
        creation.traits_screen(
            content, empty, PageState(filters=ListFilters(query="ничего такого нет"))
        ),
        creation.trait_filters_screen(content, PageState()),
        creation.trait_filters_screen(
            content, PageState(filters=ListFilters(category="Боевые", query="меч"))
        ),
        creation.points_screen(content, complete_draft),
        creation.confirm_screen(content, complete_draft),
        handlers_creation.welcome_screen(),
        handlers_creation.created_screen("Аргус", "Дубно"),
        play.main_menu_screen(content, hero, derived_stats(content, hero)),
        play.main_menu_screen(content, keeper, derived_stats(content, keeper)),
        keeper_screens.keeper_screen(content, keeper, derived_stats(content, keeper)),
        keeper_screens.keeper_screen(
            edited,
            keeper,
            derived_stats(content, keeper),
            keeper_view,
            notice="Выдано 1000 золота.",
        ),
        keeper_screens.content_screen(edited, keeper_view),
        *(
            keeper_screens.list_screen(edited, kind, PageState(), keeper_view)
            for kind in keeper_screens.KINDS
        ),
        keeper_screens.list_screen(edited, OverlayKind.QUEST, PageState(page=2), keeper_view),
        *(
            keeper_screens.entity_screen(
                edited,
                overlay_rules.effective(edited, keeper_view.records, kind, entity_id),
                PageState(page=page),
                keeper_view,
            )
            for kind, entity_id, page in (
                (OverlayKind.NPC, "keeper_npc_1", 1),
                # Пустая запись: у неё отказ на каждом обязательном поле, и
                # карточка всё равно обязана поместиться в сообщение.
                (OverlayKind.QUEST, "нет такого", 1),
                (OverlayKind.QUEST, "keeper_quest_1", 1),
                (OverlayKind.QUEST, "keeper_quest_1", 2),
                (OverlayKind.QUEST, "farhold_tallies", 1),
                (OverlayKind.LOCATION, "quiet_meadows", 1),
                (OverlayKind.ENEMY, "grey_wolf", 1),
                (OverlayKind.CITY, "farhold", 1),
            )
        ),
        keeper_screens.entity_screen(
            edited,
            replace(
                overlay_rules.effective(
                    edited, keeper_view.records, OverlayKind.ENEMY, "grey_wolf"
                ),
                removed=True,
            ),
            PageState(),
            keeper_view,
        ),
        # Самая длинная карточка, какую смотритель может набрать: у задания
        # четырнадцать полей, и каждое заполнено до предела.
        *(
            keeper_screens.entity_screen(
                edited,
                OverlayRecord(
                    kind=OverlayKind.QUEST,
                    entity_id="keeper_quest_9",
                    fields=MappingProxyType(
                        {
                            spec.key: "а" * overlay_rules.MAX_TEXT
                            for spec in overlay_rules.FIELDS[OverlayKind.QUEST]
                        }
                    ),
                ),
                PageState(page=page),
                keeper_view,
            )
            for page in (1, 2)
        ),
        *(
            keeper_screens.field_screen(
                edited,
                overlay_rules.effective(edited, keeper_view.records, OverlayKind.QUEST, key),
                spec,
                PageState(),
            )
            for key in ("keeper_quest_1",)
            for spec in overlay_rules.FIELDS[OverlayKind.QUEST]
        ),
        *(
            keeper_screens.field_screen(
                edited,
                overlay_rules.effective(
                    edited, keeper_view.records, OverlayKind.ENEMY, "grey_wolf"
                ),
                spec,
                PageState(),
            )
            for spec in overlay_rules.FIELDS[OverlayKind.ENEMY]
        ),
        keeper_screens.field_screen(
            edited,
            overlay_rules.effective(
                edited, keeper_view.records, OverlayKind.LOCATION, "quiet_meadows"
            ),
            overlay_rules.FIELDS[OverlayKind.LOCATION][-1],
            PageState(),
        ),
        keeper_screens.players_screen(edited, keeper_view, PageState()),
        keeper_screens.players_screen(
            edited, keeper_screens.KeeperView(), PageState(), notice="Никого не нашли."
        ),
        keeper_screens.player_screen(edited, fighter, derived_stats(content, fighter)),
        keeper_screens.player_screen(
            edited, fighter, derived_stats(content, fighter), view=banned_view
        ),
        keeper_screens.ban_screen(fighter, keeper_view),
        keeper_screens.ban_screen(fighter, banned_view, "ругался в группе"),
        keeper_screens.log_screen(banned_view),
        keeper_screens.log_screen(keeper_view),
        keeper_screens.trades_screen(fighter, keeper_view),
        keeper_screens.trades_screen(fighter, keeper_screens.KeeperView()),
        keeper_screens.tune_screen(fighter, derived_stats(content, fighter), own=True),
        keeper_screens.tune_screen(
            replace(fighter, unspent_stat_points=4, bank_gold=200),
            derived_stats(content, fighter),
            own=False,
            notice="Золото: 1400.",
        ),
        *(
            keeper_screens.amount_screen(
                key, fighter, derived_stats(content, fighter), own=(key == "gold")
            )
            for key in keeper_screens.TUNE_KEYS
        ),
        keeper_screens.give_screen(content, fighter, PageState()),
        keeper_screens.give_screen(content, fighter, PageState(page=3), notice="Кому: Аргус."),
        keeper_screens.give_gear_screen(
            content, fighter, content.gear_archetype("sword"), fighter.level
        ),
        keeper_screens.give_gear_screen(
            content, fighter, content.gear_archetype("heavy_body"), 1, notice="Ступень по уровню 1."
        ),
        keeper_screens.give_item_screen(content.item("small_healing_potion"), fighter),
        keeper_screens.player_skills_screen(content, fighter, PageState()),
        keeper_screens.player_skills_screen(content, hero, PageState(), notice="Дерево сброшено."),
        keeper_screens.skill_learn_screen(content, fighter, PageState()),
        keeper_screens.skill_learn_screen(content, hero, PageState(page=2)),
        keeper_screens.keeper_skill_screen(content, fighter, "warrior_rassechenie"),
        keeper_screens.keeper_skill_screen(content, fighter, "race_human_second_wind"),
        keeper_screens.skill_edge_screen(content, fighter, "warrior_rassechenie"),
        keeper_screens.skill_slot_screen(content, fighter, "warrior_rassechenie"),
        keeper_screens.stats_edit_screen(replace(fighter, unspent_stat_points=6)),
        keeper_screens.stats_edit_screen(fighter, chosen="STR", notice="Сила: 4."),
        keeper_screens.bag_screen(
            content,
            replace(fighter, equipment=fighter.equipment.equip("weapon", "sword@1#common")),
            (InventoryEntry("axe@1#common", 1), InventoryEntry("small_healing_potion", 3)),
            PageState(),
        ),
        keeper_screens.bag_screen(content, hero, (), PageState(), notice="Сумка пуста."),
        keeper_screens.player_quests_screen(content, fighter, PageState()),
        keeper_screens.keeper_quest_screen(content, fighter, "farhold_tallies"),
        keeper_screens.keeper_quest_screen(
            content, hero, "farhold_tallies", counting=True, notice="Наберите число."
        ),
        keeper_screens.keeper_party_screen(_group_view),
        keeper_screens.keeper_guild_screen(_group_view),
        keeper_screens.keeper_guild_screen(_group_view, counting=True, notice="Казна: 500."),
        keeper_screens.player_screen(
            edited, fighter, derived_stats(content, fighter), view=_group_view
        ),
        keeper_screens.stats_screen(keeper_view.census),
        keeper_screens.stats_screen(Census()),
        keeper_screens.service_screen(keeper_view),
        city_screens.npcs_screen(edited, edited.city("farhold")),
        city_screens.npcs_screen(edited, edited.city("dusk_harbor")),
        city_screens.npc_screen(edited, fighter, edited.npc("keeper_npc_1")),
        play.city_screen(edited, edited.city("farhold"), hero),
        play.world_screen(content, hero, PageState()),
        play.world_screen(content, keeper, PageState()),
        play.city_screen(content, content.city("farhold"), hero),
        play.location_list_screen(content, content.city("farhold"), hero, PageState()),
        play.location_screen(
            sample_location,
            sample_location.entrance,
            standing=full_location(sample_location),
        ),
        _with_tutorial_hint(
            play.location_list_screen(content, content.city("farhold"), hero, PageState()),
            ScreenId.LOCATION_LIST,
            hero,
        ),
        play.location_screen(
            sample_location,
            sample_location.exit_node,
            standing=emptied_location(sample_location),
            notice="Узел вычищен.",
        ),
        play.location_screen(
            sample_location,
            sample_location.node(1),
            standing=emptied_location(sample_location),
            roamer=Roamer(node=1, group=True, difficulty="delve", level=3, stamp=1),
        ),
        play.location_screen(
            sample_location,
            sample_location.entrance,
            standing=emptied_location(sample_location),
            roamer=Roamer(node=1, group=False, difficulty="grim", level=3, stamp=1, holder=42),
        ),
        play.character_screen(content, hero, derived_stats(content, hero)),
        play.character_screen(content, fighter, derived_stats(content, fighter)),
        play.character_screen(
            content,
            replace(fighter, unspent_stat_points=3, unspent_skill_points=1),
            derived_stats(content, fighter),
        ),
        play.stats_screen(content, hero, derived_stats(content, hero)),
        play.stats_screen(
            content,
            replace(fighter, unspent_stat_points=4),
            derived_stats(content, fighter),
        ),
        party_screens.party_screen(party_screens.PartyView()),
        party_screens.party_screen(
            party_screens.PartyView(members=("Аргус", "Мирна"), leader=True),
            notice="Отряд создан.",
        ),
        party_screens.party_screen(
            party_screens.PartyView(members=("Мирна", "Аргус"), caller="Тьен")
        ),
        party_screens.invite_screen(party_screens.PartyView(members=("Аргус",), leader=True)),
        party_screens.invite_screen(party_screens.PartyView()),
        guild_screens.guild_screen(guild_screens.GuildView(my_gold=900)),
        guild_screens.guild_screen(guild_screens.GuildView(my_gold=50, caller="Медный Крест")),
        guild_screens.guild_screen(
            guild_screens.GuildView(
                name="Стая",
                my_rank=GuildRank.FOUNDER,
                members=(("Аргус", GuildRank.FOUNDER), ("Мирна", GuildRank.OFFICER)),
                vault_gold=1200,
                my_gold=300,
            ),
            notice="Гильдия основана.",
        ),
        guild_screens.guild_screen(
            guild_screens.GuildView(
                name="Стая",
                my_rank=GuildRank.MEMBER,
                members=(("Аргус", GuildRank.FOUNDER), ("Мирна", GuildRank.MEMBER)),
                vault_gold=1200,
                my_gold=300,
            ),
        ),
        guild_screens.found_screen(guild_screens.GuildView(my_gold=900)),
        guild_screens.invite_screen(
            guild_screens.GuildView(
                name="Стая", my_rank=GuildRank.OFFICER, members=(("Аргус", GuildRank.FOUNDER),)
            )
        ),
        guild_screens.roster_screen(
            guild_screens.GuildView(
                name="Стая",
                my_rank=GuildRank.FOUNDER,
                members=(
                    ("Аргус", GuildRank.FOUNDER),
                    ("Мирна", GuildRank.OFFICER),
                    ("Тьен", GuildRank.MEMBER),
                ),
            )
        ),
        guild_screens.roster_screen(
            guild_screens.GuildView(
                name="Стая",
                my_rank=GuildRank.MEMBER,
                members=(("Аргус", GuildRank.FOUNDER), ("Мирна", GuildRank.MEMBER)),
            )
        ),
        guild_screens.vault_screen(
            guild_screens.GuildView(
                name="Стая", my_rank=GuildRank.OFFICER, vault_gold=800, my_gold=250
            )
        ),
        guild_screens.vault_screen(
            guild_screens.GuildView(
                name="Стая", my_rank=GuildRank.MEMBER, vault_gold=800, my_gold=250
            )
        ),
        transfer_screens.recipients_screen("party", ("Мирна", "Тьен"), PageState()),
        transfer_screens.recipients_screen("guild", (), PageState()),
        transfer_screens.recipients_screen(
            "guild",
            tuple(f"Соклановец {n}" for n in range(20)),
            PageState(page=2),
            notice="Кому передать?",
        ),
        transfer_screens.bag_screen(
            content,
            (shop.OwnedItem("small_healing_potion", 3), shop.OwnedItem("sword@1#common", 1)),
            "Мирна",
            PageState(),
        ),
        transfer_screens.bag_screen(content, (), "Мирна", PageState()),
        transfer_screens.amount_screen(content.item("small_healing_potion"), 3, "Мирна"),
        transfer_screens.amount_screen(
            content.item("small_healing_potion"), 1, "Мирна", notice="Столько нет."
        ),
        tutorial_screens.tutorial_screen(hero),
        tutorial_screens.tutorial_screen(replace(hero, tutorial=0b000111)),
        tutorial_screens.tutorial_screen(replace(hero, tutorial=0b111111)),
        _with_tutorial_hint(skill_screens.slots_screen(content, hero), ScreenId.SKILL_SLOTS, hero),
        chamber_screens.chamber_screen(content, fighter),
        chamber_screens.chamber_screen(content, sealbearer, notice="Перерождение совершено."),
        chamber_screens.turning_screen(content, fighter),
        chamber_screens.turning_screen(content, sealbearer),
        chamber_screens.turning_screen(content, sealbearer, tally={"toll_low": 3, "toll_keep": 3}),
        chamber_screens.pledge_screen(content, sealbearer, PageState()),
        chamber_screens.pledge_screen(content, fighter, PageState()),
        arena_screens.arena_screen(fighter),
        arena_screens.arena_screen(
            replace(fighter, arena_wins=4, arena_losses=2),
            table=(
                replace(fighter, name="Мерла", arena_wins=9),
                replace(fighter, name="Довен", arena_wins=4),
            ),
        ),
        arena_screens.arena_screen(replace(hero, gold=0)),
        skill_screens.skills_screen(content, fighter, PageState()),
        skill_screens.skills_screen(content, hero, PageState(page=2)),
        # Длинный список умений - самый длинный экран игры: у разбойника на
        # трёхсотом уровне четырнадцать записей под двести знаков каждая, и
        # восемь таких в одно сообщение не влезали. Три страницы этого списка
        # стоят здесь ради правила 11, а не ради разбойника.
        *(
            skill_screens.skills_screen(
                content,
                replace(hero, class_id=class_id, level=300, unspent_skill_points=2),
                PageState(page=page),
            )
            for class_id in ("rogue", "ranger", "barbarian")
            for page in (1, 2, 3)
        ),
        skill_screens.slots_screen(content, fighter),
        skill_screens.pick_screen(content, fighter, 2, PageState()),
        skill_screens.pick_screen(content, hero, 0, PageState()),
        skill_screens.edge_screen(content, fighter, content.skill("warrior_rassechenie")),
        craft_screens.crafts_screen(content, hero),
        craft_screens.crafts_screen(content, craftsman),
        craft_screens.craft_screen(
            content, craftsman, content.craft("mining"), {}, now=1_700_000_000, cooldown=900
        ),
        craft_screens.craft_screen(
            content,
            hero,
            content.craft("mining"),
            {},
            now=1_700_000_000,
            cooldown=900,
            notice="Собрано: Железный лом.",
        ),
        craft_screens.craft_screen(
            content,
            craftsman,
            content.craft("smithing"),
            {"iron_scrap": 4, "mountain_ore": 3},
            now=1_700_000_000,
            cooldown=900,
        ),
        craft_screens.craft_screen(
            content, hero, content.craft("alchemy"), {}, now=1_700_000_000, cooldown=900
        ),
        quest_screens.journal_screen(content, fighter),
        quest_screens.journal_screen(content, hero),
        quest_screens.board_screen(content, hero, PageState()),
        quest_screens.board_screen(content, fighter, PageState()),
        quest_screens.offer_screen(content, content.quest("farhold_tallies")),
        quest_screens.offer_screen(content, content.quest("farhold_tallies"), fighter),
        quest_screens.offer_screen(content, content.quest("farhold_whetstones"), hero),
        quest_screens.offer_screen(content, content.quest("farhold_meadow_teeth"), hero),
        city_screens.tavern_screen(content, fighter, content.city("farhold")),
        city_screens.mentor_screen(content, fighter, content.city("farhold"), PageState()),
        city_screens.bank_screen(content, fighter, content.city("farhold")),
        city_screens.dungeon_screen(
            content,
            fighter,
            content.city("farhold"),
            level=12,
            deep_level=30,
            deep_open=False,
            base_depth=3,
        ),
        city_screens.dungeon_screen(
            content,
            replace(fighter, level=30),
            content.city("farhold"),
            level=22,
            deep_level=30,
            deep_open=True,
            base_depth=3,
        ),
        combat_screens.battle_screen(content, fighter, sample_fight, 1),
        combat_screens.battle_screen(content, fighter, crowded_fight, 1),
        combat_screens.battle_screen(content, fighter, boss_fight, 1),
        combat_screens.battle_screen(content, fighter, duel_fight, 1),
        combat_screens.waiting_screen(duel_fight, 2),
        combat_screens.waiting_screen(crowded_fight, 1, "Сейчас не ваш ход."),
        combat_screens.bag_screen(content, (("small_healing_potion", "Малое зелье лечения", 3),)),
        combat_screens.bag_screen(content, ()),
        combat_screens.victory_screen(sample_fight, 1, experience=40, gold=14),
        combat_screens.victory_screen(
            sample_fight,
            1,
            extra=("Уровень 11. Очков характеристик: 3, очков умений: 1.",),
            rows=((labels.ROOM_SKIRMISH,), (labels.ROOM_HOLLOW,)),
        ),
        combat_screens.defeat_screen(sample_fight, 1),
        combat_screens.defeat_screen(sample_fight, 1, gold_lost=42),
        combat_screens.escaped_screen(True, sample_fight, 1),
        combat_screens.escaped_screen(False, sample_fight, 1),
        shop.inventory_screen(
            content,
            (shop.OwnedItem("small_healing_potion", 3), shop.OwnedItem("sword@1#common", 1)),
            PageState(),
            gold=120,
        ),
        shop.inventory_screen(content, (), PageState(), gold=0),
        item_screens.item_screen(
            content, hero, content.item("sword@1#common"), quantity=1, sale=12
        ),
        item_screens.item_screen(
            content,
            replace(hero, equipment=hero.equipment.equip("body", "light_body@6#common")),
            content.item("medium_body@6#uncommon"),
            quantity=1,
            sale=12,
        ),
        item_screens.item_screen(
            content, hero, content.item("small_healing_potion"), quantity=3, sale=4
        ),
        item_screens.item_screen(content, hero, content.item("wolf_pelt"), quantity=7, sale=6),
        item_screens.shop_item_screen(
            content, hero, content.item("sword@1#common"), price=30, gold=250
        ),
        item_screens.shop_item_screen(
            content, hero, content.item("sword@1#common"), price=30, gold=2
        ),
        filters_screen(
            screen_id=ScreenId.LIST_FILTERS,
            title="Разделы списка",
            categories=shop.ITEM_SECTIONS,
            current=ListFilters(),
        ),
        filters_screen(
            screen_id=ScreenId.LIST_FILTERS,
            title="Разделы списка",
            categories=shop.ITEM_SECTIONS,
            current=ListFilters(category="Сырьё", query="шкура"),
        ),
        shop.sell_screen(
            content,
            (shop.OwnedItem("wolf_pelt", 4),),
            {"wolf_pelt": 3},
            PageState(),
            gold=10,
            city_name="Дубно",
        ),
        shop.shop_screen(
            content,
            sample_stock,
            {item.id: buy_price(content, item) for item in sample_stock},
            PageState(),
            gold=250,
            city_name="Дубно",
        ),
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[
                ListEntry(key=item.id, text=item.name, detail=f"уровень {item.level}")
                for item in content.items[:20]
            ],
            state=PageState(page=1, filters=ListFilters(category="Оружие")),
        ),
        paginated_screen(
            screen_id=ScreenId.INVENTORY,
            title="Инвентарь",
            entries=[],
            state=PageState(),
        ),
    ]
    return screens
