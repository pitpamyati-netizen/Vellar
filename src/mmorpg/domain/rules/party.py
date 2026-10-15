"""Отряд: кто идёт в бой вместе и как делится то, что после боя осталось.

Отряд - это объединение игроков, и ничего сверх того (ADR 0026). Мест в отряде
нет: никто не назначается щитом и никто не платит за прибавку, потому что
прибавок за место не выдаётся. Каждый дерётся тем, что принёс с собой.

Заводит отряд сам игрок - кнопкой «Создать отряд» в главном меню, - и сам же
его расформировывает. Отряд из одного человека это уже отряд: он заведён, и
звать в него можно.

Позвать можно того, кто стоит рядом на узле, и того, кому ответили в игровой
группе. Согласиться позванный должен сам: вход в чужой бой - это чужой ход и
чужие раны.

Отряд не делает игру легче: противник тот же, а плата делится. Ходить впятером
стоит ради боя, который в одиночку не берётся.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import MAX_SIDE
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.procgen import items as gear_procgen

#: Сколько человек помещается в отряд. Столько же, сколько бойцов на стороне:
#: отряд и есть сторона (``domain/entities/combat.MAX_SIDE``).
MAX_MEMBERS = MAX_SIDE

#: Насколько далеко могут разойтись уровни в отряде. Шире окна поединка: вместе
#: ходят и затем, чтобы провести младшего, - но не настолько шире, чтобы бой
#: тридцатого уровня платил первому.
LEVEL_WINDOW = 5


@dataclass(frozen=True, slots=True)
class Party:
    """Кто сейчас вместе. Первый - тот, кто собрал.

    Состав лежит в базе и держится, пока отряд не расформируют или пока из него
    не уйдёт собравший (``PartyRepository``, ADR 0029): постоянный состав нельзя
    терять между заходами. Приглашения - другое дело, они висят в кэше со сроком.
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

    @property
    def alone(self) -> bool:
        """Отряд заведён, но пока в нём один человек - тот, кто его завёл."""
        return self.size <= 1

    def has(self, character_id: int) -> bool:
        return character_id in self.members

    def with_member(self, character_id: int) -> Party:
        if self.has(character_id) or self.full:
            return self
        return replace(self, members=(*self.members, character_id))

    def without(self, character_id: int) -> Party:
        """Отряд без этого человека. Ушёл собравший - отряда больше нет.

        Оставшийся в одиночестве отряд не распускается: его завели нарочно, и
        расформировать его - тоже нарочное движение.
        """
        if character_id == self.leader_id:
            return Party(leader_id=0, members=())
        return replace(self, members=tuple(one for one in self.members if one != character_id))

    @property
    def disbanded(self) -> bool:
        return self.leader_id == 0


def invite_refusal(
    *,
    inviter_level: int,
    invitee_name: str,
    invitee_level: int,
    party: Party | None,
    invitee_in_party: bool,
) -> str:
    """Пусто, когда звать можно; иначе - почему нельзя, целой фразой."""
    if party is None:
        return "У вас нет отряда. Создайте его в главном меню, а потом зовите."
    if party.full:
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
    loot: tuple[str, ...],
    members: tuple[int, ...],
    source: random.Random,
    *,
    content: GameContent | None = None,
    roster: Mapping[int, Character] | None = None,
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
        recipient = members[(start + index) % len(members)]
        if content is not None and roster is not None and content.has_item(item_id):
            item = content.item(item_id)
            parsed = gear_procgen.parse_gear_id(item_id)
            if parsed is not None and item.is_equipment and not item.is_tool:
                archetype = content.gear_archetype(parsed[0])
                eligible = [
                    member
                    for member in members
                    if member in roster
                    and gear_procgen.suits(content, archetype, (roster[member].class_id,))
                ]
                profile = (
                    gear_procgen.stamp_class(content, parsed[3])
                    if parsed[3] >= gear_procgen.STAMP_V2
                    else ""
                )
                profiled = [member for member in eligible if roster[member].class_id == profile]
                eligible = profiled or eligible
                if eligible:
                    for offset in range(len(members)):
                        candidate = members[(start + index + offset) % len(members)]
                        if candidate in eligible:
                            recipient = candidate
                            break
        shares[recipient].append(item_id)
    return {member: tuple(items) for member, items in shares.items()}
