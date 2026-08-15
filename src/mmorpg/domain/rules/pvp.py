"""Fighting another player on the road.

Two decisions shape this module, and both come from ``docs/accessibility.md``:

- **No timers.** A fight between two live players, turn by turn, needs both of
  them at their phone and a clock to punish whoever is not. So an attack is
  fought against a **snapshot** of the other character - their stats, their
  health, their gear - driven by the ordinary combat engine. The attacker plays;
  the defender is told what happened when they next open the game.
- **Consent is not asked, but it is bounded.** In a location marked ``pvp`` any
  player standing on your node may attack you. What keeps that from being a tax
  on newcomers is the fence below: a level floor, a narrow level window, and a
  price that is paid out of what you are carrying, never out of the bank.

The stake is deliberately small. Losing costs a tenth of the gold on hand and the
wounds of the fight; it never costs a level, an item or a contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank
from mmorpg.domain.rules.stats import derived_stats

# Below this level nobody attacks and nobody is attacked. A character who has not
# yet filled their panel has nothing to defend themselves with, and losing their
# first hundred gold to somebody twice their size is how a player leaves for good.
SAFE_LEVEL = 10
# How far apart two levels may be. Wide enough that friends of different levels
# can spar, narrow enough that nobody farms the bottom of their own band.
LEVEL_WINDOW = 5
# What the winner takes: a tenth of the gold the loser is carrying. The bank is
# untouchable, which is the whole reason the bank exists.
SPOILS_PERCENT = 10


@dataclass(frozen=True, slots=True)
class Spoils:
    """What one settled attack moved between two characters."""

    gold: int = 0
    experience: int = 0


def refusal(
    attacker: Character,
    *,
    defender_name: str,
    defender_level: int,
    location_allows: bool,
) -> str:
    """Empty when the attack may happen, otherwise the reason it may not.

    The reason is a full sentence: a refusal a player cannot read is a bug that
    looks like a rule.
    """
    if not location_allows:
        return "Здесь не дерутся друг с другом. Поединки разрешены не везде."
    if attacker.level < SAFE_LEVEL:
        return f"До {SAFE_LEVEL} уровня в поединки не вступают. Ваш уровень: {attacker.level}."
    if defender_level < SAFE_LEVEL:
        return f"{defender_name} ещё под защитой: до {SAFE_LEVEL} уровня на дороге не трогают."
    if abs(attacker.level - defender_level) > LEVEL_WINDOW:
        return (
            f"Разница уровней больше {LEVEL_WINDOW}: "
            f"ваш {attacker.level}, у {defender_name} {defender_level}."
        )
    return ""


def spoils_from(loser_gold: int) -> int:
    """A tenth of what is on hand, and nothing at all from an empty purse."""
    return max(0, loser_gold) * SPOILS_PERCENT // 100


def as_enemy(content: GameContent, character: Character) -> Enemy:
    """A character as the combat engine sees an opponent.

    Everything is read from the same totals the character fights with, so the
    snapshot is exactly as strong as the player it copies - no scaling, no
    handicap. The rank is ordinary: another adventurer is not a boss, and a fight
    against one should last about as long as a fight against anything else.
    """
    stats = derived_stats(content, character)
    klass = content.character_class(character.class_id)
    # The damage of one standard blow, the same number the skill screen quotes.
    damage = 6.0 + 2.2 * character.level
    return Enemy(
        archetype_id=f"player:{character.id}",
        name=f"{character.name}, {klass.name.lower()}",
        kind=EnemyKind.HUMANOID,
        level=character.level,
        max_health=stats.max_health,
        damage=max(1, round(damage)),
        armor=stats.armor,
        initiative=stats.initiative,
        loot=(),
        gold=0,
        rank=EnemyRank.NORMAL,
    )


def settle(
    winner: Character, loser: Character, *, experience: int = 0
) -> tuple[Character, Character, Spoils]:
    """Move the stake. Returns both characters and what changed hands.

    Nothing here touches health: the fight already wrote the wounds, and the
    loser keeps everything except the coins in their pocket.
    """
    gold = spoils_from(loser.gold)
    return (
        winner.with_gold(gold),
        loser.with_gold(-gold),
        Spoils(gold=gold, experience=experience),
    )
