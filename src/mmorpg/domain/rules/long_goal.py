"""Счёт проекта и права награды. Время и результаты действий приходят явно."""

from dataclasses import replace

from mmorpg.domain.entities.long_goal import GoalState, LongGoalRules, ProjectState

KINDS = ("cull", "delve", "tithe")
TRACKS = ("trial", "craft", "chronicle")


def targets(rules: LongGoalRules) -> dict[str, int]:
    return dict(
        zip(KINDS, (rules.project_battles, rules.project_descents, rules.project_gold), strict=True)
    )


def progress(state: ProjectState, kind: str) -> int:
    return sum(amount for _, activity, amount in state.contributions if activity == kind)


def contribute(
    state: ProjectState, kind: str, amount: int, members: tuple[int, ...], receipt: str
) -> ProjectState:
    if state.complete or kind not in KINDS or amount <= 0 or not members or not receipt:
        return state
    if receipt in state.receipts:
        return state
    gained = min(amount, max(0, targets(state.rules)[kind] - progress(state, kind)))
    if not gained:
        return state
    # Один общий бой — одна единица. Личные доли не умножают общий счёт.
    members = tuple(sorted(set(members)))
    base, remainder = divmod(gained, len(members))
    shares = tuple(
        (member, kind, base + (index < remainder))
        for index, member in enumerate(members)
        if base + (index < remainder)
    )
    updated = replace(
        state,
        contributions=(*state.contributions, *shares),
        participation=(
            *state.participation,
            *((member, kind, gained if kind == "tithe" else 1) for member in members),
        ),
        receipts=(*state.receipts, receipt),
    )
    return replace(
        updated,
        complete=all(progress(updated, one) >= goal for one, goal in targets(state.rules).items()),
    )


def ready(rules: LongGoalRules, state: GoalState, track: str, story_done: bool) -> bool:
    if track == "trial":
        return state.trial
    if track == "craft":
        return state.crafted >= rules.craft_target
    return track == "chronicle" and state.studied and story_done
