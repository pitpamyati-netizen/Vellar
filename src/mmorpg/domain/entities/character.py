"""The character entity and the parts that are actually stored.

Only *raw* values live here: allocated stat points, level, experience, chosen
traits, the skill loadout and equipment. Totals - health, armor, damage - are never
stored; they are recomputed from these raw values by ``mmorpg.domain.rules.stats``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from mmorpg.domain.entities.craft import CraftLog
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock

ACTIVE_SLOTS = 6


@dataclass(frozen=True, slots=True)
class SkillLoadout:
    """What the player put in the fixed panel.

    ``actives`` always has 6 entries; an empty slot is ``None`` and keeps its
    number, so a skill never changes position.

    Постоянных слотов здесь нет. Постоянное умение нечем нажать, у него нет ни
    хода, ни цели, и «поместить его в слот» означало только одно: три из шести
    изученных не работали, а игрок платил очки за надпись. Изучено - значит
    работает (``domain/rules/modifiers.passive_modifiers``).
    """

    actives: tuple[str | None, ...] = (None,) * ACTIVE_SLOTS
    racial: str | None = None
    ranks: Mapping[str, int] = field(default_factory=dict)
    edges: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.actives) != ACTIVE_SLOTS:
            msg = f"the panel has exactly {ACTIVE_SLOTS} active slots"
            raise ValueError(msg)
        # A skill lying in the panel is a skill the character knows. Without this
        # the starting skill fought like rank one but the skills screen still
        # offered to learn it for a point - the same skill, known and unknown at
        # once. Normalising here fixes characters saved by older releases too.
        in_panel = self.equipped_actives()
        if self.racial is not None:
            in_panel = (*in_panel, self.racial)
        missing = [code for code in in_panel if code not in self.ranks]
        if missing:
            ranks = dict(self.ranks)
            for code in missing:
                ranks[code] = 1
            object.__setattr__(self, "ranks", MappingProxyType(ranks))

    def rank_of(self, skill_code: str) -> int:
        """Rank of a skill; 1 once it is known at all."""
        return self.ranks.get(skill_code, 1)

    def edge_of(self, skill_code: str) -> str | None:
        return self.edges.get(skill_code)

    def equipped_actives(self) -> tuple[str, ...]:
        return tuple(code for code in self.actives if code is not None)

    def with_active(self, slot: int, skill_code: str | None) -> SkillLoadout:
        actives = list(self.actives)
        actives[slot] = skill_code
        return replace(self, actives=tuple(actives))

    def with_rank(self, skill_code: str, rank: int) -> SkillLoadout:
        ranks = dict(self.ranks)
        ranks[skill_code] = rank
        return replace(self, ranks=MappingProxyType(ranks))

    def with_edge(self, skill_code: str, edge_code: str) -> SkillLoadout:
        edges = dict(self.edges)
        edges[skill_code] = edge_code
        return replace(self, edges=MappingProxyType(edges))


@dataclass(frozen=True, slots=True)
class Equipment:
    """Item ids by slot. An empty slot is simply absent."""

    items: Mapping[str, str] = field(default_factory=dict)

    def item_in(self, slot: str) -> str | None:
        return self.items.get(slot)

    def equip(self, slot: str, item_id: str) -> Equipment:
        return Equipment(MappingProxyType({**self.items, slot: item_id}))

    def unequip(self, slot: str) -> Equipment:
        remaining = {key: value for key, value in self.items.items() if key != slot}
        return Equipment(MappingProxyType(remaining))

    def item_ids(self) -> tuple[str, ...]:
        return tuple(self.items.values())


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    item_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Character:
    """A player character. Everything here is persisted verbatim."""

    id: int
    user_id: int
    name: str
    race_id: str
    class_id: str
    level: int = 1
    experience: int = 0
    gold: int = 0
    allocated: StatBlock = field(default_factory=StatBlock)
    trait_ids: tuple[str, ...] = ()
    loadout: SkillLoadout = field(default_factory=SkillLoadout)
    equipment: Equipment = field(default_factory=Equipment)
    city_id: str = "farhold"
    unspent_stat_points: int = 0
    unspent_skill_points: int = 0
    # Wounds outlive a fight: a player leaves a location as they left the last
    # node, and pays somebody to be patched up. Zero means "as good as new",
    # which is what a freshly created character is - see ``health_or``.
    health: int = 0
    bank_gold: int = 0
    quests: QuestLog = field(default_factory=QuestLog)
    # Craft work already done. A rank is never stored - it is counted back from
    # the experience here by ``mmorpg.domain.rules.crafts``.
    crafts: CraftLog = field(default_factory=CraftLog)
    # What the arena remembers: two counters for the season table, and the
    # gold it is holding of yours. A win is paid out of that hold and never out
    # of nowhere, which is what keeps the arena from minting gold
    # (``domain/rules/arena.py``).
    arena_wins: int = 0
    arena_losses: int = 0
    arena_credit: int = 0
    # The endgame, and the only thing in the game that is paid for with what a
    # character already has (``domain/rules/turning.py``). ``seals`` is how many
    # Turnings they have made; ``pledges`` is what went into them, so nothing is
    # ever pledged twice; ``turning_cycle``/``turning_answer`` is the answer they
    # gave to the question the Chamber has open, and which question it was.
    seals: int = 0
    pledges: tuple[str, ...] = ()
    turning_cycle: str = ""
    turning_answer: str = ""
    # Which introduction tasks are behind them, as a bitmask
    # (``mmorpg.domain.rules.tutorial``). Zero is a player who has just arrived.
    tutorial: int = 0
    # A keeper of the game, not a stronger character: the flag only says that the
    # keeper screen is theirs to open. Who is a keeper is decided by ADMIN_IDS in
    # the environment; this column mirrors that decision so the screens can read
    # it without the settings object.
    is_admin: bool = False

    def health_or(self, maximum: int) -> int:
        """Current health, clamped into the range the totals allow right now.

        Equipment and levels move the maximum around between fights, so the
        stored number is only ever a claim: it is trusted up to the maximum and
        never below one, because a character at zero would be unplayable.
        """
        if self.health <= 0:
            return maximum
        return max(1, min(self.health, maximum))

    def with_health(self, health: int, maximum: int) -> Character:
        return replace(self, health=max(1, min(health, maximum)))

    def with_experience(self, gained: int) -> Character:
        return replace(self, experience=self.experience + gained)

    def with_level(self, level: int, *, stat_points: int, skill_points: int) -> Character:
        return replace(
            self,
            level=level,
            unspent_stat_points=self.unspent_stat_points + stat_points,
            unspent_skill_points=self.unspent_skill_points + skill_points,
        )

    def with_gold(self, delta: int) -> Character:
        return replace(self, gold=max(0, self.gold + delta))

    def with_crafts(self, crafts: CraftLog) -> Character:
        return replace(self, crafts=crafts)

    def with_arena_result(self, *, won: bool) -> Character:
        if won:
            return replace(self, arena_wins=self.arena_wins + 1)
        return replace(self, arena_losses=self.arena_losses + 1)

    def with_arena_credit(self, held: int) -> Character:
        """Set what the arena holds of yours. Never below nothing."""
        return replace(self, arena_credit=max(0, held))

    def with_seal(self, pledge: str) -> Character:
        """Что делает совершённое перерождение: Печать прибавляется, заклад записан.

        Уровень, опыт и характеристики не трогаются вовсе - Печать открывает
        доступы, а не силу (``Narrative.md``, раздел 6).
        """
        return replace(self, seals=self.seals + 1, pledges=(*self.pledges, pledge))

    def has_pledged(self, pledge: str) -> bool:
        return pledge in self.pledges

    def with_turning_answer(self, cycle: str, option: str) -> Character:
        """Ответить на голосование. Ответ всегда назван вместе с вопросом:
        голос, поданный за прошлый цикл, в этом не считается."""
        return replace(self, turning_cycle=cycle, turning_answer=option)

    def as_admin(self, is_admin: bool) -> Character:
        return replace(self, is_admin=is_admin)
