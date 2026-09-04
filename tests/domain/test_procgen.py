"""Процедурная сборка: определённая, связная, всегда проходимая.

Тесты свойств здесь и есть спецификация сборщика. Стоит любому из них упасть, и
игроки окажутся в локации, из которой не выйти.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mmorpg.domain.entities import GameContent, NodeKind, NodeState
from mmorpg.domain.entities.location import EnemyRank, LocationState
from mmorpg.domain.procgen import (
    DEFAULT_SHOP_ROTATION_SECONDS,
    MAX_NODES,
    MIN_NODES,
    combat_nodes,
    epoch_seed,
    generate_enemy,
    generate_group,
    generate_location,
    guaranteed_find_kinds,
    location_seed,
    rotation_ends_at,
    rotation_index,
    seconds_left_in_rotation,
    wave_seed,
)
from mmorpg.domain.procgen.seeds import enemy_seed, node_seed
from mmorpg.domain.rules import nodes as node_rules

WORLD_SEED = "vellar-test"

_COMBAT_KINDS = {NodeKind.BATTLE, NodeKind.ELITE_BATTLE, NodeKind.BOSS_BATTLE}
_FINDING_KINDS = {NodeKind.GATHER, NodeKind.CACHE, NodeKind.EVENT}


def build(city_id: str = "farhold", slot: int = 1, seed: str = WORLD_SEED, epoch: int = 0):
    return generate_location(
        world_seed=seed,
        city_id=city_id,
        slot=slot,
        name="Луга у Заставы",
        biome="луга",
        level_min=1,
        level_max=4,
        epoch=epoch,
    )


def _family(kind: NodeKind) -> str:
    """Постоянная категория узла: её смена поколения не трогает."""
    if kind in _COMBAT_KINDS:
        return "combat"
    if kind in _FINDING_KINDS:
        return "finding"
    return kind.value


# --- то единственное, что ещё на часах -------------------------------


def test_the_shop_rotates_every_half_hour() -> None:
    """Мир больше не переворачивается по страже; переворачивается только прилавок."""
    assert DEFAULT_SHOP_ROTATION_SECONDS == 1_800
    assert rotation_index(0) == 0
    assert rotation_index(1_799) == 0
    assert rotation_index(1_800) == 1
    assert rotation_index(86_400) == 48


def test_seconds_left_in_rotation_is_a_valid_ttl() -> None:
    for moment in (0, 500, 1_799, 43_200):
        left = seconds_left_in_rotation(moment)
        assert 0 < left <= DEFAULT_SHOP_ROTATION_SECONDS
        assert moment + left == rotation_ends_at(rotation_index(moment))


# --- определённость --------------------------------------------------


def test_ten_thousand_runs_are_byte_identical() -> None:
    """Определённость - весь договор: никакого глобального random, никогда."""
    reference = location_seed(WORLD_SEED, "farhold", 1)
    assert all(location_seed(WORLD_SEED, "farhold", 1) == reference for _ in range(10_000))


def test_the_same_seed_and_epoch_are_byte_identical() -> None:
    """Определённость: тот же сид и то же поколение - тот же граф."""
    assert build() == build()
    assert build(epoch=7) == build(epoch=7)


def test_a_new_epoch_relays_the_whole_layout() -> None:
    """Карта локации больше не стоит на месте: поколение перекладывает раскладку."""
    for slot in range(1, 6):
        base = build(slot=slot, epoch=0)
        links_moved = any(
            [n.links for n in build(slot=slot, epoch=e).nodes] != [n.links for n in base.nodes]
            for e in range(1, 6)
        )
        names_moved = any(
            [n.name for n in build(slot=slot, epoch=e).nodes] != [n.name for n in base.nodes]
            for e in range(1, 6)
        )
        assert links_moved, f"slot {slot}: дерево троп ни разу не переложилось"
        assert names_moved, f"slot {slot}: имена узлов ни разу не сменились"


def test_different_slots_and_cities_differ() -> None:
    assert build(slot=1) != build(slot=2)
    assert build(city_id="farhold") != build(city_id="stonedale")


def test_a_different_world_seed_changes_everything() -> None:
    assert build(seed="one") != build(seed="another")


# --- поколение округи -----------------------------------------------


def test_a_new_epoch_is_seeded_differently() -> None:
    seed = location_seed(WORLD_SEED, "farhold", 1)
    assert epoch_seed(seed, 0) != epoch_seed(seed, 1)
    assert epoch_seed(seed, 1) != epoch_seed(seed, 2)


def test_the_same_epoch_rebuilds_identically() -> None:
    assert build(epoch=3) == build(epoch=3)


def test_what_survives_every_epoch() -> None:
    """Постоянно то, чего игрок не слышит как карту: размер, набор дел, уровни.

    Раскладка (кто где стоит, тропы, имена) перекладывается поколением - это
    проверяет ``test_a_new_epoch_relays_the_whole_layout``. А число узлов, набор
    категорий среди них, набор видов находок и кривая уровней по глубине - функция
    места, и поколение их не трогает: иначе ``search``-задание на вид узла встало
    бы намертво.
    """
    finding = _FINDING_KINDS | {NodeKind.SHRINE}
    base = build(epoch=0)
    want_categories = sorted(_family(n.kind) for n in base.nodes)
    want_finding = sorted(n.kind for n in base.nodes if n.kind in finding)
    for epoch in range(1, 12):
        later = build(epoch=epoch)
        assert len(later.nodes) == len(base.nodes)
        assert [n.level for n in later.nodes] == [n.level for n in base.nodes]
        assert sorted(_family(n.kind) for n in later.nodes) == want_categories
        assert sorted(n.kind for n in later.nodes if n.kind in finding) == want_finding
        assert later.is_connected
        assert later.exit_node.index == base.exit_node.index == len(later.nodes) - 1


def test_guaranteed_find_kinds_matches_every_epoch() -> None:
    """То, что обещает ``guaranteed_find_kinds``, стоит в локации в любом поколении.

    На этом держится дело ``SEARCH`` сводки (ADR 0054): застава называет вид узла
    от места, а не от поколения.
    """
    for city_id, slot in (("farhold", 1), ("farhold", 3), ("dusk_harbor", 2)):
        promised = sorted(guaranteed_find_kinds(WORLD_SEED, city_id, slot))
        for epoch in range(12):
            here = generate_location(
                world_seed=WORLD_SEED,
                city_id=city_id,
                slot=slot,
                name="x",
                biome="forest",
                level_min=1,
                level_max=30,
                epoch=epoch,
            )
            present = sorted(n.kind for n in here.nodes if n.kind in _FINDING_KINDS)
            assert present == promised


def test_the_boss_stays_pinned_every_epoch() -> None:
    for epoch in range(8):
        location = build(epoch=epoch)
        bosses = [n for n in location.nodes if n.kind is NodeKind.BOSS_BATTLE]
        assert len(bosses) == 1
        assert bosses[0].index == len(location.nodes) - 2, "самый глубокий внутренний узел"


def _depths(location) -> dict[int, int]:
    """Сколько шагов от входа до каждого узла - по самому графу, а не по номеру."""
    seen = {0: 0}
    frontier = [0]
    while frontier:
        current = frontier.pop(0)
        for link in location.node(current).links:
            if link not in seen:
                seen[link] = seen[current] + 1
                frontier.append(link)
    return seen


def test_a_node_is_as_hard_as_it_is_deep() -> None:
    """Уровень узла считается от слоя, а не от номера в списке (ADR 0061).

    До этого «чем глубже, тем тяжелее» было обещанием: номер ничего не говорил о
    том, сколько до узла идти, и двадцатый узел мог висеть в шаге от входа.
    """
    for slot in range(1, 6):
        for epoch in range(6):
            location = build(
                slot=slot,
                epoch=epoch,
            )
            depths = _depths(location)
            assert len(depths) == len(location.nodes), "связность"
            for node in location.nodes:
                for other in location.nodes:
                    if depths[node.index] < depths[other.index]:
                        assert node.level <= other.level


def test_the_layers_run_deep_enough_to_be_a_road() -> None:
    """Локация - это дорога в глубину, а не двор: слоёв не меньше пяти."""
    for slot in range(1, 6):
        location = build(slot=slot)
        assert max(_depths(location).values()) >= 5


def test_a_dead_end_pays_for_the_turn() -> None:
    """Свернувший с дороги получает дело, а не ещё одну стычку (ADR 0061).

    Тупиков в разных поколениях разное число, и тихих узлов может не хватить на
    все, - но львиная доля тупиков обязана держать находку или святилище, иначе
    сворачивать незачем.
    """
    quiet = _FINDING_KINDS | {NodeKind.SHRINE}
    ends = paid = 0
    for slot in range(1, 6):
        for epoch in range(6):
            location = build(slot=slot, epoch=epoch)
            for node in location.nodes:
                if node.index in (0, len(location.nodes) - 1) or len(node.links) != 1:
                    continue
                if node.kind is NodeKind.BOSS_BATTLE:
                    continue
                ends += 1
                paid += node.kind in quiet
    assert ends, "тупиков нет вовсе: сворачивать некуда"
    assert paid / ends > 0.6, f"наград в тупиках {paid} из {ends}"


def test_a_shortcut_never_swallows_a_dead_end() -> None:
    """Тропа, подшитая к тупику, отменяет саму причину туда идти.

    Проверяется по-другому: у каждого узла-находки в тупике ровно одна тропа, и
    короткие тропы соединяют только соседние слои.
    """
    for slot in range(1, 6):
        for epoch in range(6):
            location = build(slot=slot, epoch=epoch)
            depths = _depths(location)
            for node in location.nodes:
                for link in node.links:
                    assert abs(depths[node.index] - depths[link]) <= 1, "тропа через слой"


def test_the_exit_is_reachable_without_the_boss() -> None:
    """Босс держит конец, но не дорогу к выходу: драться с ним - решение (ADR 0035)."""

    def reachable(location, *, without: int | None) -> set[int]:
        seen = {0}
        frontier = [0]
        while frontier:
            current = frontier.pop()
            for link in location.node(current).links:
                if link not in seen and link != without:
                    seen.add(link)
                    frontier.append(link)
        return seen

    for slot in range(1, 6):
        for epoch in range(1, 12):
            location = build(slot=slot, epoch=epoch)
            boss = len(location.nodes) - 2
            assert boss in reachable(location, without=None), "в полный граф босс входит"
            without_boss = reachable(location, without=boss)
            assert boss not in without_boss
            assert location.exit_node.index in without_boss, "к выходу путь есть мимо логова"


def test_the_finding_composition_is_permanent() -> None:
    """Сколько в локации сбора, тайников и событий - постоянно; на них завязаны задания."""
    finding = _FINDING_KINDS | {NodeKind.SHRINE}
    base = build(slot=2)
    want = sorted(n.kind for n in base.nodes if n.kind in finding)
    for epoch in range(1, 15):
        later = sorted(n.kind for n in build(slot=2, epoch=epoch).nodes if n.kind in finding)
        assert later == want


def test_the_combat_mix_relays_with_the_epoch() -> None:
    """Боевой состав локации перекладывается поколением (ADR 0035), но стая есть всегда."""
    for slot in range(1, 6):
        base = build(slot=slot)
        combat_total = sum(1 for n in base.nodes if n.kind in _COMBAT_KINDS)
        mixes = set()
        for epoch in range(30):
            combat = tuple(
                n.kind for n in build(slot=slot, epoch=epoch).nodes if n.kind in _COMBAT_KINDS
            )
            assert len(combat) == combat_total, "число боёв постоянно"
            assert NodeKind.BATTLE in combat, "хотя бы одна стая"
            mixes.add(combat)
        assert len(mixes) > 1, f"slot {slot}: боевой состав ни разу не переложился"


def test_a_search_quest_target_is_stocked_in_its_location(content: GameContent) -> None:
    """Задание «найдите четыре схрона» обязано быть выполнимым в любом поколении.

    Набор видов находок постоянен (ADR 0035), но проверить это по живому
    содержимому надёжнее, чем верить, что раздача случайно выдала достаточно узлов
    нужного вида в каждом поколении.
    """
    from mmorpg.domain.entities.quest import ObjectiveKind

    for quest in content.quests:
        if quest.objective is not ObjectiveKind.SEARCH or not quest.target_kind:
            continue
        city = content.city(quest.city_id)
        loc = city.location(quest.location_slot)
        for epoch in (0, 1, 2, 5, 11):
            built = generate_location(
                world_seed=WORLD_SEED,
                city_id=city.id,
                slot=loc.slot,
                name=loc.name,
                biome=loc.biome,
                level_min=loc.level_min,
                level_max=loc.level_max,
                epoch=epoch,
            )
            stocked = sum(1 for node in built.nodes if node.kind.value == quest.target_kind)
            assert stocked >= quest.target_count, f"{quest.id}: {stocked} узлов {quest.target_kind}"


def test_location_epoch_counts_by_wave_not_clock() -> None:
    assert node_rules.location_epoch(LocationState()) == 0
    nodes = {i: NodeState(wave=w) for i, w in enumerate([3, 3, 3, 3])}
    # 12 волн суммарно = ровно одно сменившееся поколение.
    assert node_rules.location_epoch(LocationState(nodes=nodes)) == 1
    nodes[0] = NodeState(wave=2)
    assert node_rules.location_epoch(LocationState(nodes=nodes)) == 0


# --- строение --------------------------------------------------------


@given(
    slot=st.integers(min_value=1, max_value=5),
    epoch=st.integers(min_value=0, max_value=25),
)
@settings(max_examples=400, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_structure_invariants(slot: int, epoch: int) -> None:
    location = build(slot=slot, epoch=epoch)

    assert MIN_NODES <= len(location.nodes) <= MAX_NODES
    assert location.entrance.kind is NodeKind.ENTRANCE
    assert location.exit_node.index == len(location.nodes) - 1
    assert location.is_connected, "every node must be reachable from the entrance"
    assert location.exit_node.index in location.reachable_from(0), "the exit must be reachable"
    assert combat_nodes(location), "a location always has at least one fight"

    for node in location.nodes:
        assert node.links, f"node {node.index} is isolated"
        assert node.index not in node.links, "no self links"
        for link in node.links:
            assert node.index in location.node(link).links, "links must be symmetric"
        assert location.level_min <= node.level <= location.level_max


@given(city=st.sampled_from(["farhold", "dusk_harbor", "bone_marches", "last_beacon"]))
@settings(max_examples=200)
def test_exit_is_always_reachable_across_cities(city: str) -> None:
    location = generate_location(
        world_seed=WORLD_SEED,
        city_id=city,
        slot=3,
        name="Локация",
        biome="лес",
        level_min=10,
        level_max=20,
    )
    assert location.exit_node.index in location.reachable_from(0)


def test_node_levels_increase_with_depth() -> None:
    location = generate_location(
        world_seed=WORLD_SEED,
        city_id="farhold",
        slot=5,
        name="Выработки",
        biome="подземелье",
        level_min=22,
        level_max=30,
    )
    assert location.entrance.level == 22
    assert location.exit_node.level == 30
    levels = [node.level for node in location.nodes]
    assert levels == sorted(levels)


# --- что стоит в узле и когда оно возвращается -----------------------


def test_every_node_holds_a_wave_of_its_own_size() -> None:
    """Прежняя модель была рубильником: одно нажатие, и узел кончился навсегда."""
    seed = location_seed(WORLD_SEED, "farhold", 1)
    location = build()
    for node in location.nodes:
        low, high = node_rules.WAVE_SIZE[node.kind]
        assert low <= node_rules.wave_size(seed, node.index, node.kind, 0) <= high


def test_a_battle_node_holds_more_than_one_pack() -> None:
    seed = location_seed(WORLD_SEED, "farhold", 1)
    assert node_rules.WAVE_SIZE[NodeKind.BATTLE][0] >= 2
    assert node_rules.wave_size(seed, 1, NodeKind.BATTLE, 0) >= 2


def test_taking_the_last_thing_empties_the_node_and_it_refills() -> None:
    state = NodeState()
    for _ in range(3):
        state = node_rules.taken_one(state, 3, now=1_000)
    assert node_rules.remaining(state, 3) == 0
    assert node_rules.seconds_until_refill(state, 1_000) == node_rules.RESPAWN_SECONDS

    waiting = node_rules.refreshed(state, 1_000 + node_rules.RESPAWN_SECONDS - 1)
    assert waiting == state

    filled = node_rules.refreshed(state, 1_000 + node_rules.RESPAWN_SECONDS)
    assert filled.wave == 1
    assert filled.taken == 0
    assert not filled.empty


def test_the_refill_waits_three_minutes() -> None:
    assert node_rules.RESPAWN_SECONDS == 180


def test_a_new_wave_is_seeded_differently() -> None:
    """Тот же узел, наполненный заново, - это не те же три волка снова."""
    seed = location_seed(WORLD_SEED, "farhold", 1)
    assert wave_seed(seed, 3, 0) != wave_seed(seed, 3, 1)
    assert wave_seed(seed, 3, 0) != wave_seed(seed, 4, 0)


# --- противники ------------------------------------------------------


def test_enemy_generation_is_deterministic(content: GameContent) -> None:
    seed = enemy_seed(node_seed(location_seed(WORLD_SEED, "farhold", 1), 4), 0)
    first = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    second = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    assert first == second


def test_enemy_fits_the_biome(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "winter_march", 2), 0)
    enemy = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="снега", level=180)
    archetype = next(a for a in content.enemy_archetypes if a.id == enemy.archetype_id)
    assert archetype.fits("снега")


def test_enemies_scale_with_level(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1), 1)
    low = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=2)
    high = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=200)
    assert high.max_health > low.max_health * 10
    assert high.damage > low.damage
    assert high.gold > low.gold


def test_elites_are_stronger_and_alone(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1), 2)
    normal = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="лес", level=30)
    elite = generate_enemy(
        seed,
        archetypes=content.enemy_archetypes,
        biome="лес",
        level=30,
        rank=EnemyRank.ELITE,
        elite_titles=content.elite_titles,
    )
    assert elite.is_elite
    assert elite.max_health > normal.max_health
    assert elite.gold > normal.gold
    group = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="лес",
        level=30,
        rank=EnemyRank.ELITE,
        elite_titles=content.elite_titles,
    )
    assert len(group) == 1


def test_groups_hold_between_one_and_three_enemies(content: GameContent) -> None:
    sizes = set()
    for attempt in range(200):
        seed = enemy_seed(location_seed(WORLD_SEED, f"farhold-{attempt}", 1), 0)
        group = generate_group(seed, archetypes=content.enemy_archetypes, biome="лес", level=12)
        assert 1 <= len(group) <= 3
        sizes.add(len(group))
    assert sizes == {1, 2, 3}, "all group sizes should occur across many seeds"


def test_dungeon_pool_only_yields_dungeon_archetypes(content: GameContent) -> None:
    """Заход в подземелье не выставит дорожную стаю (ADR 0042)."""
    for attempt in range(40):
        seed = enemy_seed(location_seed(WORLD_SEED, f"pit-{attempt}", 1), 0)
        group = generate_group(
            seed, archetypes=content.enemy_archetypes, biome="рудник", level=40, dungeon=True
        )
        for enemy in group:
            fits = next(a for a in content.enemy_archetypes if a.id == enemy.archetype_id)
            assert fits.dungeon, enemy.archetype_id


def test_affix_roll_is_seed_deterministic_and_optional(content: GameContent) -> None:
    seed = b"same-room"
    first = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="рудник",
        level=30,
        dungeon=True,
        affixes=content.affixes,
        affix_chance=1.0,
        affix_count=2,
    )
    second = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="рудник",
        level=30,
        dungeon=True,
        affixes=content.affixes,
        affix_chance=1.0,
        affix_count=2,
    )
    assert [e.affixes for e in first] == [e.affixes for e in second]
    assert all(e.affixes for e in first)

    plain = generate_group(
        seed, archetypes=content.enemy_archetypes, biome="рудник", level=30, dungeon=True
    )
    assert all(e.affixes == () for e in plain)


def test_affix_bakes_multipliers_and_prefixes_the_name(content: GameContent) -> None:
    seed = b"steady"
    plain = generate_group(
        seed, archetypes=content.enemy_archetypes, biome="рудник", level=30, dungeon=True
    )
    brutish = next(a for a in content.affixes if a.id == "brutish")
    named = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="рудник",
        level=30,
        dungeon=True,
        affixes=(brutish,),
        affix_chance=1.0,
        affix_count=1,
    )
    assert sum(e.max_health for e in named) > sum(e.max_health for e in plain)
    assert all(e.name.startswith("Кряжистый") for e in named)


def test_broodkeeper_grows_the_pack(content: GameContent) -> None:
    brood = next(a for a in content.affixes if a.id == "broodkeeper")
    seen_big = False
    for attempt in range(60):
        seed = enemy_seed(location_seed(WORLD_SEED, f"brood-{attempt}", 1), 0)
        group = generate_group(
            seed,
            archetypes=content.enemy_archetypes,
            biome="рудник",
            level=20,
            dungeon=True,
            affixes=(brood,),
            affix_chance=1.0,
            affix_count=1,
        )
        assert len(group) <= 5
        seen_big = seen_big or len(group) >= 4
    assert seen_big


def test_generation_never_touches_the_global_random(content: GameContent) -> None:
    """Забредший ``random.random()`` сделал бы мир невоспроизводимым."""
    import random

    random.seed(1)
    build()
    first = random.random()
    random.seed(1)
    build(slot=4)
    assert random.random() == first
