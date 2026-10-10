"""Постоянные цели, общая история и личный вклад; без сезонного сброса."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoryOutcome:
    id: str
    name: str
    text: str
    travel_discount: int


@dataclass(frozen=True, slots=True)
class ChronicleFragment:
    quest_id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class LongGoalRules:
    project_id: str = ""
    project_name: str = ""
    project_battles: int = 12
    project_descents: int = 3
    project_gold: int = 500
    project_deeds: int = 20
    city_id: str = ""
    story_quests: tuple[str, ...] = ()
    outcomes: tuple[StoryOutcome, ...] = ()
    fragments: tuple[ChronicleFragment, ...] = ()
    dungeon_id: str = ""
    level: int = 150
    craft_rank: int = 10
    craft_target: int = 3
    reward_gold: int = 600
    war_account_seconds: int = 7 * 86400


@dataclass(frozen=True, slots=True)
class ProjectState:
    # Условия фиксируются при начале: изменение каталога не меняет начатое дело.
    rules: LongGoalRules
    contributions: tuple[tuple[int, str, int], ...] = ()
    participation: tuple[tuple[int, str, int], ...] = ()
    receipts: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True, slots=True)
class GoalState:
    trial: bool = False
    crafted: int = 0
    studied: bool = False
    claimed: tuple[str, ...] = ()
    receipts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoryState:
    outcome: StoryOutcome
    author_id: int


@dataclass(frozen=True, slots=True)
class WarBattleCredit:
    battle_id: str
    guild_id: int
    winners: tuple[int, ...]
    losers: tuple[int, ...]
    scored: bool
    reason: str
    now: int


@dataclass(frozen=True, slots=True)
class WarRoster:
    enabled: bool = True
    members: tuple[tuple[int, int, int], ...] = ()  # гильдия, персонаж, аккаунт
    used_accounts: tuple[int, ...] = ()
    battles: tuple[WarBattleCredit, ...] = ()
