"""Сто игроков сразу — и что при этом происходит с задержкой.

Обещание игры — сто миллисекунд на нажатие (``docs/architecture.md``). Проверено
оно было на одном игроке, и это не проверка: узкое место у такой игры не в
правилах, а в том, сколько одновременных запросов держит пул PostgreSQL. Здесь
это и меряется — тем же кодом, которым играют.

    uv run python scripts/loadtest.py                     сто игроков, по двадцать действий
    uv run python scripts/loadtest.py --players 20        поменьше
    uv run python scripts/loadtest.py --actions 50        подольше
    uv run python scripts/loadtest.py --pause 3           так, как жмут живые
    uv run python scripts/loadtest.py --keep              не убирать за собой

Что меряется: хранилища и правила — чтение персонажа, счёт характеристик,
кошелёк условным ``UPDATE``, сумка, бой в домене. Что **не** меряется, и это надо
знать до того, как поверить числу: ни Telegram, ни сеть до него, ни очередь
отправки (``middlewares/sending.py``). Столько стоит игра сама по себе; путь до
игрока добавляет к этому свою дорогу.

Пишет в ту базу, которая названа в ``POSTGRES_DSN``. Персонажей заводит своих —
имя начинается с ``нагрузка-`` — и убирает их за собой; ``--keep`` оставляет их,
если надо посмотреть глазами. Всё равно: на живом мире это запускают до того, как
в нём появились живые, или на копии.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from collections.abc import Sequence
from contextlib import AsyncExitStack

from mmorpg.config import AppEnv, Settings, load_settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, BattleAction
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, UserRepository
from mmorpg.domain.ports.repositories import User as Account
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.infrastructure.content import load_content
from mmorpg.metrics import Metrics, Stopwatch
from mmorpg.presentation.telegram.flows.combat import spawn_for_node

#: Персонажей этой нагрузки видно по имени, и убрать их можно по нему же.
PREFIX = "нагрузка-"
#: Аккаунты берутся из заведомо пустого угла: живой Telegram id столько не весит.
ACCOUNT_BASE = 9_000_000_000
#: Дальше этого хода бой не считают: нагрузке нужен бой обычной длины, а не
#: редкий длинный, растянувший замер.
TURN_CEILING = 12


async def one_player(
    index: int,
    *,
    content: GameContent,
    characters: CharacterRepository,
    users: UserRepository,
    actions: int,
    pause: float,
    metrics: Metrics,
    seed: str,
) -> int:
    """Один игрок: завестись и нажимать. Возвращает число сорвавшихся действий."""
    account = ACCOUNT_BASE + index
    await users.upsert(Account(telegram_id=account, username=f"{PREFIX}{index}"))
    await characters.create(
        Character(
            id=0,
            user_id=account,
            name=f"{PREFIX}{index}",
            race_id=content.races[index % len(content.races)].id,
            class_id=content.classes[index % len(content.classes)].id,
            level=1 + index % 30,
            gold=500,
        )
    )
    dice = random.Random(f"{seed}-{index}")
    failures = 0

    for step in range(actions):
        if pause:
            await asyncio.sleep(pause * (0.5 + dice.random()))
        watch = Stopwatch()
        try:
            read = await characters.get_active(account)
            if read is None:
                raise RuntimeError("персонаж исчез посреди нагрузки")
            stats = derived_stats(content, read)

            # Кошелёк — тем же условным шагом, каким его двигает игра.
            if step % 3 == 0:
                await characters.grant_gold(read.id, 10)
            elif step % 3 == 1:
                await characters.spend_gold(read.id, 5)
            else:
                await characters.save(read.with_health(stats.max_health, stats.max_health))

            # И один бой: правила должны попадать в тот же замер, что и база.
            spot = derive(seed, index, step, dice.randrange(1_000))
            enemies = spawn_for_node(
                content,
                seed=spot,
                biome=content.cities[index % len(content.cities)].locations[0].biome,
                level=read.level,
            )
            roster = {1: read}
            fighters = [
                hero_combatant(content, read, combatant_id=1, side=0, live=True),
                *(
                    monster_combatant(enemy, combatant_id=number + 2, side=1)
                    for number, enemy in enumerate(enemies)
                ),
            ]
            fight = open_battle(content, roster, fighters, spot)
            while not fight.is_over and fight.round <= TURN_CEILING:
                fight = act(
                    content,
                    roster,
                    fight,
                    BattleAction(kind=ActionKind.ATTACK),
                    derive(spot, "turn", fight.round),
                )
        except Exception:
            failures += 1
            metrics.observe(watch.seconds, failed=True)
        else:
            metrics.observe(watch.seconds)

    return failures


async def sweep(characters: CharacterRepository, players: int) -> int:
    """Убрать за собой. Персонаж нагрузки — это мусор в мире, а не история."""
    removed = 0
    for index in range(players):
        for character in await characters.list_for_user(ACCOUNT_BASE + index):
            await characters.delete(character.id)
            removed += 1
    return removed


async def run(options: argparse.Namespace, settings: Settings) -> int:
    content = load_content(settings.content_dir)
    stack = AsyncExitStack()
    metrics = Metrics()

    async with stack:
        characters, users = await _repositories(settings, stack)
        pacing = (
            f"пауза между нажатиями {options.pause} с" if options.pause else "все сразу, без пауз"
        )
        print(
            f"Игроков: {options.players}, действий каждому: {options.actions}, {pacing}. "
            f"База: {'память' if settings.app_env is AppEnv.LOCAL else settings.postgres_dsn}"
        )
        started = time.perf_counter()
        try:
            failures = await asyncio.gather(
                *(
                    one_player(
                        index,
                        content=content,
                        characters=characters,
                        users=users,
                        actions=options.actions,
                        pause=options.pause,
                        metrics=metrics,
                        seed=options.seed,
                    )
                    for index in range(options.players)
                )
            )
        finally:
            if not options.keep:
                print(f"Убрано персонажей нагрузки: {await sweep(characters, options.players)}.")

        elapsed = time.perf_counter() - started
        snapshot = metrics.snapshot()
        done = int(snapshot["updates"])
        print(
            f"Действий: {done} за {elapsed:.1f} с — "
            f"{done / elapsed:.0f} в секунду, сорвалось: {sum(failures)}."
        )
        print(
            f"Задержка: половина укладывается в {snapshot['p50']} с, "
            f"девяносто пять из ста — в {snapshot['p95']} с, "
            f"самое долгое — {snapshot['slowest']} с."
        )
        # Обещание игры названо здесь же: иначе число надо помнить наизусть.
        budget = settings.slow_callback_seconds
        if float(snapshot["p95"]) > budget:
            print(f"** Хуже обещанного: девяносто пятая доля должна укладываться в {budget} с.")
            if not options.pause:
                print(
                    "   Замер без пауз - это сто человек, нажавших в одну секунду. "
                    "Как жмут живые, покажет --pause 3."
                )
            print(
                f"   Пул PostgreSQL: {settings.postgres_pool_max} соединений (POSTGRES_POOL_MAX)."
            )
            return 1
    return 0


async def _repositories(
    settings: Settings, stack: AsyncExitStack
) -> tuple[CharacterRepository, UserRepository]:
    """Те же хранилища, что у игры. Память — только когда база и не нужна."""
    if not settings.uses_postgres:
        from mmorpg.infrastructure.persistence import (
            InMemoryCharacterRepository,
            InMemoryUserRepository,
        )

        return InMemoryCharacterRepository(), InMemoryUserRepository()

    from mmorpg.infrastructure.persistence.pool import create_postgres_pool
    from mmorpg.infrastructure.persistence.postgres import (
        PostgresCharacterRepository,
        PostgresUserRepository,
    )

    pool = await create_postgres_pool(settings)
    stack.push_async_callback(pool.close)
    return PostgresCharacterRepository(pool), PostgresUserRepository(pool)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сколько игроков игра держит сразу.")
    parser.add_argument("--players", type=int, default=100, help="сколько игроков сразу")
    parser.add_argument("--actions", type=int, default=20, help="сколько действий каждому")
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="секунд между нажатиями одного игрока; ноль - худший случай, все сразу",
    )
    parser.add_argument("--seed", default="loadtest", help="сид: тот же сид - те же бои")
    parser.add_argument("--keep", action="store_true", help="не убирать персонажей нагрузки")
    options = parser.parse_args(argv)

    settings = load_settings()
    return asyncio.run(run(options, settings))


if __name__ == "__main__":
    raise SystemExit(main())
