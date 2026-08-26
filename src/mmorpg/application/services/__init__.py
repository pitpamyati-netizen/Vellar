"""Слой mmorpg.application.services.

Сервис - место, где связывается сценарий: он читает через порты хранилищ, зовёт
чистые правила из ``domain``, пишет обратно и возвращает результат, который слой
представления сможет высказать словами. Сервисы не знают ни типов aiogram, ни
SQL.
"""

from mmorpg.application.services.group_trade import GroupOutcome, GroupResult, GroupTrade

__all__ = ["GroupOutcome", "GroupResult", "GroupTrade"]
