"""Каждый зачтённый вклад равен одному; сроки и случайность отсутствуют."""

from dataclasses import replace

from mmorpg.domain.entities.city_event import CityContribution, CityEvent, CityEventState

KINDS = ("battle", "scout", "craft")


def progress(state: CityEventState, stage: int) -> int:
    return sum(one.stage == stage for one in state.contributions)


def used(state: CityEventState, stage: int, character_id: int, kind: str) -> int:
    return sum(
        one.stage == stage and one.character_id == character_id and one.kind == kind
        for one in state.contributions
    )


def refusal(
    event: CityEvent, state: CityEventState, character_id: int, kind: str, receipt: str
) -> str:
    if kind not in KINDS or not receipt:
        return "Такой вклад не предусмотрен. Откройте городское дело заново."
    if any(
        one.character_id == character_id and one.kind == kind and one.receipt == receipt
        for one in state.contributions
    ):
        return "Этот вклад уже сохранён."
    if state.stage >= len(event.stages):
        return "Городское дело завершено. Можно забрать награду и продолжать путешествие."
    if used(state, state.stage, character_id, kind) >= event.stages[state.stage].cap:
        return "Ваш предел этого вида помощи достигнут. Выберите другой способ или занятие."
    return ""


def contribute(
    event: CityEvent, state: CityEventState, character_id: int, kind: str, receipt: str
) -> tuple[CityEventState, str]:
    if reason := refusal(event, state, character_id, kind, receipt):
        return state, reason
    stage = event.stages[state.stage]
    updated = replace(
        state,
        contributions=(
            *state.contributions,
            CityContribution(state.stage, character_id, kind, receipt),
        ),
    )
    notice = (
        f"Городское дело «{stage.name}»: вклад сохранён, "
        f"{progress(updated, state.stage)} из {stage.target}."
    )
    if progress(updated, state.stage) >= stage.target:
        counts = {
            key: sum(one.stage == state.stage and one.kind == key for one in updated.contributions)
            for key in KINDS
        }
        leaders = [key for key in KINDS if counts[key] == max(counts.values())]
        outcome = leaders[0] if len(leaders) == 1 else "balanced"
        updated = replace(updated, stage=state.stage + 1, outcomes=(*state.outcomes, outcome))
        notice += " Общее дело завершено."
        if updated.stage < len(event.stages):
            notice += f" Открыто следующее: {event.stages[updated.stage].name}."
        else:
            notice += " Переправа работает. Итог и награда: /событие."
    return updated, notice


def reward_due(
    event: CityEvent, state: CityEventState, character_id: int
) -> tuple[int, tuple[int, ...]]:
    stages = tuple(
        index
        for index in range(state.stage)
        if (index, character_id) not in state.claimed
        and any(
            one.stage == index and one.character_id == character_id for one in state.contributions
        )
    )
    return sum(event.stages[index].reward_gold for index in stages), stages


def discount(event: CityEvent, state: CityEventState) -> int:
    if state.stage != len(event.stages):
        return 0
    return next(one.travel_discount for one in event.outcomes if one.kind == state.outcomes[0])
