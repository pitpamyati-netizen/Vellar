"""Отряд: кто идёт в бой вместе и как делится то, что после боя осталось.

Отряд - это не гильдия и не список друзей: он живёт, пока живёт вылазка, и
собирается из тех, кто стоит рядом. Позвать можно того, кого видно на своём
узле; согласиться он должен сам, потому что вход в чужой бой - это чужой ход и
чужие раны (``docs/accessibility.md``: бой ждёт нажатия, а не соглашается за
игрока).

Отряд не делает игру легче: противник тот же, а плата делится. Ходить вчетвером
стоит того ради боя, который в одиночку не берётся, - хозяин логова и дно
спуска, - а не ради того, чтобы вчетверо быстрее собирать волков.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from mmorpg.domain.entities.combat import MAX_SIDE

#: Сколько человек помещается в отряд. Столько же, сколько бойцов на стороне:
#: отряд и есть сторона (``domain/entities/combat.MAX_SIDE``).
MAX_MEMBERS = MAX_SIDE

#: Насколько далеко могут разойтись уровни в отряде. Шире окна поединка: вместе
#: ходят и затем, чтобы провести младшего, - но не настолько шире, чтобы бой
#: тридцатого уровня платил первому.
LEVEL_WINDOW = 10


@dataclass(frozen=True, slots=True)
class Party:
    """Кто сейчас вместе. Первый - тот, кто собрал.

    Хранится там же, где экран и бой, и с тем же сроком: отряд, о котором все
    забыли, распадается сам (``Claude.md``, правило 8).
    """

    leader_id: int
    members: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.leader_id not in self.members:
            object.__setattr__(self, "members", (self.leader_id, *self.members))

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def full(self) -> bool:
        return self.size >= MAX_MEMBERS

    def has(self, character_id: int) -> bool:
        return character_id in self.members

    def with_member(self, character_id: int) -> Party:
        if self.has(character_id) or self.full:
            return self
        return replace(self, members=(*self.members, character_id))

    def without(self, character_id: int) -> Party:
        """Отряд без этого человека. Ушёл собравший - отряда больше нет."""
        if character_id == self.leader_id:
            return Party(leader_id=0, members=())
        return replace(self, members=tuple(one for one in self.members if one != character_id))

    @property
    def disbanded(self) -> bool:
        return self.leader_id == 0 or self.size <= 1


def invite_refusal(
    *,
    inviter_level: int,
    invitee_name: str,
    invitee_level: int,
    party: Party | None,
    invitee_in_party: bool,
) -> str:
    """Пусто, когда звать можно; иначе - почему нельзя, целой фразой."""
    if party is not None and party.full:
        return f"В отряде уже {MAX_MEMBERS} человек: больше не помещается."
    if invitee_in_party:
        return f"{invitee_name} уже в отряде."
    if abs(inviter_level - invitee_level) > LEVEL_WINDOW:
        return (
            f"Разница уровней больше {LEVEL_WINDOW}: "
            f"ваш {inviter_level}, у {invitee_name} {invitee_level}."
        )
    return ""


def split(amount: int, members: int) -> tuple[int, ...]:
    """Разделить плату на всех. Остаток достаётся первым по списку.

    Ни одна монета не пропадает: делить пятёрку на четверых так, чтобы одна
    монета исчезла, - это тихая потеря, а тихих потерь в игре быть не должно.
    """
    if members <= 0:
        return ()
    base, extra = divmod(max(0, amount), members)
    return tuple(base + (1 if index < extra else 0) for index in range(members))


def distribute(
    loot: tuple[str, ...], members: tuple[int, ...], source: random.Random
) -> dict[int, tuple[str, ...]]:
    """Кому какая вещь досталась.

    По кругу, начиная со случайного места: иначе собравший отряд забирал бы всё
    ценное просто потому, что он первый в списке.
    """
    if not members:
        return {}
    shares: dict[int, list[str]] = {member: [] for member in members}
    start = source.randrange(len(members)) if len(members) > 1 else 0
    for index, item_id in enumerate(loot):
        shares[members[(start + index) % len(members)]].append(item_id)
    return {member: tuple(items) for member, items in shares.items()}
