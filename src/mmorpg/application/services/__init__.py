"""mmorpg.application.services layer package.

A service is where a use case is orchestrated: it reads through the repository
ports, calls the pure rules in ``domain``, writes back, and returns a result the
presentation layer can word. Services know no aiogram types and no SQL.
"""

from mmorpg.application.services.group_trade import GroupOutcome, GroupResult, GroupTrade
from mmorpg.application.services.offers import OfferStore

__all__ = ["GroupOutcome", "GroupResult", "GroupTrade", "OfferStore"]
