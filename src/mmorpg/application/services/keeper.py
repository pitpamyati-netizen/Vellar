"""Флаг смотрителя, которому можно верить.

Право приходит из двух мест, и колонка на персонаже отражает оба. Первое -
``ADMIN_IDS``: стоящий там id смотритель всегда, права его изнутри игры не
лишить, и только он выдаёт право кому-то ещё. Второе - как раз эта выдача: она
хранится на аккаунте (``users.keeper``), потому что право, которое обходится
заведением второго персонажа, правом не является.

Оба сверяются каждый раз, когда персонаж загружается для своего владельца,
поэтому выданное или отобранное право ложится на следующем нажатии. Ничто в игре
не пишет колонку персонажа напрямую - её только переписывают из этих двух
источников (``Claude.md``, правило 5: хендлер спрашивает, сервис решает).
"""

from __future__ import annotations

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import CharacterRepository, UserRepository


async def sync_keeper(
    character: Character,
    telegram_id: int,
    settings: Settings,
    characters: CharacterRepository,
    *,
    granted: bool = False,
) -> Character:
    """Вернуть персонажа с тем флагом, какой ему полагается по обоим источникам.

    ``granted`` - то, что аккаунту выдали изнутри игры; настройка проверяется здесь.
    Пишется, только когда ответ изменился, поэтому обычный случай - игрок, который
    не смотритель и никогда им не был, - стоит одного сравнения и ни одного похода в
    базу.
    """
    wanted = granted or settings.is_admin(telegram_id)
    if character.is_admin == wanted:
        return character
    updated = character.as_admin(wanted)
    await characters.save(updated)
    return updated


async def is_keeper(users: UserRepository, telegram_id: int, settings: Settings) -> bool:
    """Держит ли этот аккаунт право вообще, хоть из одного источника."""
    if settings.is_admin(telegram_id):
        return True
    user = await users.get(telegram_id)
    return user is not None and user.keeper


async def set_keeper(
    users: UserRepository,
    characters: CharacterRepository,
    telegram_id: int,
    *,
    keeper: bool,
    settings: Settings,
) -> bool:
    """Выдать право аккаунту или забрать его. False, когда изменить нельзя.

    Аккаунту, названному в ``ADMIN_IDS``, отказывают: его право живёт вне игры, и
    притворная попытка отобрать его здесь лишь развела бы отражение с тем, что
    прочитает следующая загрузка.

    Отражение пишется каждому персонажу аккаунта, а не только тому, кем играют
    сейчас: право принадлежит человеку, а играть он может любым из них.
    """
    if settings.is_admin(telegram_id):
        return False
    await users.set_keeper(telegram_id, keeper)
    for character in await characters.list_for_user(telegram_id):
        if character.is_admin != keeper:
            await characters.save(character.as_admin(keeper))
    return True
