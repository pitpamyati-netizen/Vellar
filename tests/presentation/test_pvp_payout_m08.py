"""Личная доля PvP совпадает с кошельком, экраном и денежным журналом."""

from dataclasses import replace

import pytest

from mmorpg import economy_log
from mmorpg.application.services.battle import BattleKind, begin
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.handlers import combat


@pytest.mark.parametrize("winner_side", [0, 1])
@pytest.mark.parametrize(
    "winner_gold,loser_gold,shares,losses",
    [
        ((100, 200), (110,), (6, 5), (11,)),
        ((0, 100, 200), (50, 60), (4, 4, 3), (5, 6)),
        ((99,), (20, 30, 70), (12,), (2, 3, 7)),
        ((0, 10, 20, 30, 40), (10,), (1, 0, 0, 0, 0), (1,)),
        ((100, 200), (0, 9), (0, 0), (0, 0)),
    ],
)
async def test_personal_pvp_shares_match_wallet_screen_and_log(
    content, monkeypatch, winner_side, winner_gold, loser_gold, shares, losses
):
    heroes = tuple(
        Character(
            id=index,
            user_id=700_000 + index,
            name=f"Боец {index}",
            race_id="human",
            class_id="warrior",
            level=15,
            gold=gold,
            bank_gold=999,
        )
        for index, gold in enumerate((*winner_gold, *loser_gold), 1)
    )
    winners = heroes[: len(winner_gold)]
    losers = heroes[len(winner_gold) :]
    attackers, defenders = (winners, losers) if winner_side == 0 else (losers, winners)
    session, roster = begin(
        content,
        battle_id="m08-payout",
        attackers=[(one, True) for one in attackers],
        defenders=[(one, True) for one in defenders],
        seed=b"m08-payout",
        kind=BattleKind.DUEL,
    )
    session = replace(
        session, state=replace(session.state, outcome=BattleOutcome.DECIDED, winner=winner_side)
    )
    winner_fighters = tuple(one for one in session.participants() if one.side == winner_side)
    loser_fighters = tuple(one for one in session.participants() if one.side != winner_side)
    updated = {one.id: one for one in heroes}
    payouts = {one.id: combat.Payout() for one in heroes}
    journal = []
    monkeypatch.setattr(economy_log.logger, "info", lambda event, **data: journal.append(data))

    await combat._settle_duel(session, roster, winner_fighters, loser_fighters, payouts, updated)

    for original, share in zip(winners, shares, strict=True):
        assert updated[original.id].gold == original.gold + share
        payout = payouts[original.id]
        assert payout.gold == share
        fighter = session.combatant_of(original.id)
        screen = fight_flow.render(
            content, updated[original.id], session, fighter.id, gold=payout.gold, extra=payout.extra
        )
        assert f"Золото: {share}." in screen.text()
        assert f"Ваша доля: {share}." in screen.text()
    for original, taken in zip(losers, losses, strict=True):
        assert updated[original.id].gold == original.gold - taken
        payout = payouts[original.id]
        assert payout.gold_lost == taken
        assert f"Снято золота: {taken}." in " ".join(payout.extra)
    assert all(one.bank_gold == 999 for one in updated.values())
    expected = {
        one.id: after.gold - one.gold
        for one in heroes
        if (after := updated[one.id]).gold != one.gold
    }
    assert {entry["character_id"]: entry["amount"] for entry in journal} == expected
    assert all(entry["flow"] == economy_log.DUEL for entry in journal)
    assert sum(expected.values()) == 0
    assert sum(one.gold for one in updated.values()) == sum(one.gold for one in heroes)
