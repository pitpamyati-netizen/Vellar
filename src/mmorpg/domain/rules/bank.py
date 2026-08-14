"""The city vault: what the Chamber does with a player's gold.

Gold in the vault is out of reach of everything that can take it away - a lost
fight, a duty, a trade gone wrong - and that safety is what the Chamber sells. It
charges for taking gold in and nothing for handing it back: the fee pays for the
ledger entry, not for the storage (``Narrative.md``, section 1 - everything has a
price and the price has a recipient).

The vault is not a second purse. What fits in it grows with the level, so a first
level character cannot park a fortune there and walk the road with nothing to
lose.

This module counts; the wording a player hears lives in ``presentation``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg.domain.entities.character import Character

DEPOSIT_FEE_PERCENT = 2
VAULT_PER_LEVEL = 500


class TransferKind(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"


class VaultRefusal(StrEnum):
    """Why a transfer cannot happen."""

    NOTHING = "nothing"
    NO_GOLD = "no_gold"
    NO_STORED = "no_stored"
    OVER_LIMIT = "over_limit"


@dataclass(frozen=True, slots=True)
class Transfer:
    """A settled decision: how much moves, and what the Chamber takes for it."""

    kind: TransferKind
    amount: int
    fee: int = 0

    @property
    def purse_change(self) -> int:
        """What the change does to the gold in hand, duty included."""
        if self.kind is TransferKind.DEPOSIT:
            return -(self.amount + self.fee)
        return self.amount

    @property
    def vault_change(self) -> int:
        return self.amount if self.kind is TransferKind.DEPOSIT else -self.amount


def vault_limit(level: int) -> int:
    """How much the vault holds for a character of this level."""
    return VAULT_PER_LEVEL * max(1, level)


def deposit_fee(amount: int, *, percent: int = DEPOSIT_FEE_PERCENT) -> int:
    """The duty on taking gold in. A deposit always costs at least one gold."""
    if amount <= 0:
        return 0
    return max(1, round(amount * percent / 100))


def vault_room(character: Character) -> int:
    """How much more the vault will accept before it is full."""
    return max(0, vault_limit(character.level) - character.bank_gold)


def largest_deposit(character: Character) -> int:
    """The biggest amount the player can put in right now, duty included.

    Computed rather than guessed: the duty is rounded, so the answer is trimmed
    until the amount and its duty both fit in the purse.
    """
    amount = min(vault_room(character), character.gold)
    while amount > 0 and amount + deposit_fee(amount) > character.gold:
        amount -= 1
    return amount


def plan_deposit(character: Character, amount: int) -> Transfer | VaultRefusal:
    """Check a deposit against the purse and the limit, without performing it."""
    if amount <= 0:
        return VaultRefusal.NOTHING
    if amount > vault_room(character):
        return VaultRefusal.OVER_LIMIT
    fee = deposit_fee(amount)
    if amount + fee > character.gold:
        return VaultRefusal.NO_GOLD
    return Transfer(kind=TransferKind.DEPOSIT, amount=amount, fee=fee)


def plan_withdrawal(character: Character, amount: int) -> Transfer | VaultRefusal:
    """Check a withdrawal. Handing gold back is free."""
    if amount <= 0:
        return VaultRefusal.NOTHING
    if amount > character.bank_gold:
        return VaultRefusal.NO_STORED
    return Transfer(kind=TransferKind.WITHDRAW, amount=amount)


def apply_transfer(character: Character, transfer: Transfer) -> Character:
    """Move the gold. The caller has already planned the transfer."""
    return replace(
        character,
        gold=character.gold + transfer.purse_change,
        bank_gold=character.bank_gold + transfer.vault_change,
    )
