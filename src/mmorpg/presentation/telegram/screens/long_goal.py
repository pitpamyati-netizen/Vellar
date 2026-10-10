"""Линейные условия долгих целей и последствия до подтверждения."""

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.long_goal import GoalState, ProjectState, StoryState
from mmorpg.domain.rules import long_goal as rules
from mmorpg.domain.rules.guild import Guild
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.crafts import output_name
from mmorpg.presentation.telegram.screens.format import head

OPEN = label("Долгие цели")
PROJECT = label("Общий проект гильдии")
STORY = label("Решение у маяка")
START = label("Начать путевой двор")
STUDY = label("Прочитать записи смотрителей")
TRIAL = label("Совместное испытание маяка")
REFRESH = label("Обновить долгие цели")
CHRONICLE = label("Прочитать собранную хронику")
CLAIMS = {
    "trial": label("Награда за испытание"),
    "craft": label("Награда за мастерство"),
    "chronicle": label("Награда за хронику"),
}


def project(
    content: GameContent,
    actor: Character,
    guild: Guild | None,
    state: ProjectState | None,
    notice: str,
    member_names: dict[int, str] | None = None,
) -> Screen:
    config = state.rules if state else content.long_goals
    lines = [*head(f"Общий проект: {config.project_name}.", notice)]
    rows: list[tuple[Label, ...]] = []
    if guild is None:
        lines.append("Проект ведёт гильдия. Вступление: /гильдия.")
    else:
        lines.append(
            f"Гильдия «{guild.name}». Срока сдачи нет; недельный подряд продолжается отдельно."
        )
        for kind, name in zip(
            rules.KINDS,
            ("Выигранные общие бои", "Полные спуски", "Новый вклад в казну"),
            strict=True,
        ):
            lines.append(
                f"{name}: {rules.progress(state, kind) if state else 0} из "
                f"{rules.targets(config)[kind]}."
            )
        lines.append(
            f"Завершение: {config.project_deeds} деяний гильдии один раз; "
            f"постоянная запись путевого двора."
        )
        lines.append(
            "Золото остаётся в казне. Выемка подчиняется прежним званиям; "
            "возвращённое золото не даёт нового вклада."
        )
        if state:
            lines.append(
                "Путевой двор завершён."
                if state.complete
                else "Путевой двор строится. Общий бой считается один раз, участие видно у каждого."
            )
            for member in sorted({key for key, _, _ in state.participation}):
                mine = member == actor.id
                tally = [
                    sum(
                        value
                        for key, activity, value in state.participation
                        if key == member and activity == kind
                    )
                    for kind in rules.KINDS
                ]
                name = (member_names or {}).get(member, "Бывший участник")
                lines.append(
                    f"{'Ваш вклад' if mine else name}: боёв "
                    f"{tally[0]}, спусков {tally[1]}, золота {tally[2]}."
                )
        elif guild.founder_id == actor.id:
            rows.append((START,))
    lines.append("Начало: /проект начать. Вклад: обычные бои, полные спуски, /гильдия казна.")
    rows.extend(((OPEN, STORY), (REFRESH,)))
    return Screen(ScreenId.LONG_PROJECT, tuple(lines), tuple(rows))


def story(content: GameContent, state: StoryState | None, offer: str, notice: str) -> Screen:
    config = content.long_goals
    lines = [*head(f"Решение у маяка. Город {content.city(config.city_id).name}.", notice)]
    rows: list[tuple[Label, ...]] = []
    if state:
        lines.extend(
            (
                state.outcome.name + ".",
                state.outcome.text,
                f"Выезд из города дешевле на {state.outcome.travel_discount} процентов для всех.",
            )
        )
        rows.append((STUDY,))
    else:
        lines.append(
            "Веденей передаёт дому сохранённые письма. Дом Курганов оплатит "
            "одно место: архив смотрителей или сигнальный двор."
        )
        lines.append(
            "Выбор доступен после трёх заданий смотрителя маяка, в Колве. "
            "Первый подтверждённый выбор станет общим и постоянным."
        )
        for index, outcome in enumerate(config.outcomes, 1):
            lines.append(
                f"Путь {index}: {outcome.name}. {outcome.text} Скидка выезда: "
                f"{outcome.travel_discount} процентов."
            )
            rows.append((label(f"Выбрать: {outcome.name}"),))
            if offer == outcome.id:
                rows.append((label(f"Подтвердить: {outcome.name}"),))
        lines.append(
            "Команды: /историямира выбрать 1 или 2; /историямира подтвердить 1 "
            "или 2. Перерыв не отменяет прочитанный выбор."
        )
    lines.append(
        "Запись хроники: /историямира изучить. Прежние задания и награды "
        "продолжаются по прежним правилам."
    )
    rows.append((OPEN, PROJECT))
    return Screen(ScreenId.LONG_STORY, tuple(lines), tuple(rows))


def goals(
    content: GameContent,
    actor: Character,
    state: GoalState,
    notice: str,
    *,
    chronicle: bool = False,
) -> Screen:
    config = content.long_goals
    dungeon = content.city(config.city_id).dungeon(config.dungeon_id)
    lines = [
        *head("Долгие цели на уровне 150.", notice),
        f"Ваш уровень: {actor.level} из {config.level}.",
    ]
    completed_quests = sum(actor.quests.is_done(key) for key in config.story_quests)
    lines.extend(
        (
            (
                f"Совместное испытание: полный спуск «{dungeon.name}» в Колве с "
                f"участием во всех встречах, не меньше двух разных игроков. "
                f"Пройдено: {int(state.trial)} из 1."
            ),
            (
                f"Мастерство: изготовить {config.craft_target} партии последнего "
                f"ранга через этот экран. Изделия остаются в сумке. Сделано: "
                f"{state.crafted} из {config.craft_target}."
            ),
            (
                f"Хроника: завершить три задания смотрителя маяка и прочитать общую "
                f"запись дома. Заданий: {completed_quests} из {len(config.story_quests)}. "
                f"Запись прочитана: {int(state.studied)} из 1."
            ),
            (
                f"Каждое направление даёт {config.reward_gold} золота один раз. "
                f"Можно выбирать любое; PvP добровольно и не требуется для этих "
                f"целей."
            ),
            "Герой, вещи и эти достижения не сбрасываются по календарю.",
        )
    )
    rows: list[tuple[Label, ...]] = [(TRIAL, STORY), (PROJECT,)]
    rows.append((CHRONICLE,))
    if chronicle:
        for fragment in config.fragments:
            if actor.quests.is_done(fragment.quest_id):
                lines.append(f"Запись «{fragment.title}». {fragment.text}")
            else:
                lines.append(
                    f"Запись «{fragment.title}» ещё не собрана. "
                    f"Нужна работа «{content.quest(fragment.quest_id).name}»."
                )
        rows.extend(((CLAIMS["chronicle"],), (OPEN,)))
        return Screen(ScreenId.LONG_GOALS, tuple(lines), tuple(rows))
    crafts: set[str] = set()
    index = 0
    for recipe in content.recipes:
        if recipe.rank != config.craft_rank or recipe.craft_id in crafts:
            continue
        crafts.add(recipe.craft_id)
        index += 1
        name = output_name(content, recipe)
        materials = ", ".join(
            f"{content.item(one.item_id).name}: {one.count}" for one in recipe.inputs
        )
        lines.append(
            f"Работа {index}: {name}. Ранг {recipe.rank}; материалы: "
            f"{materials}. Команда /цели изготовить {index}."
        )
        rows.append((label(f"Изготовить для цели {index}: {name}"),))
    for track in rules.TRACKS:
        lines.append(
            f"{CLAIMS[track].text}: {'получена' if track in state.claimed else 'ещё не получена'}."
        )
        rows.append((CLAIMS[track],))
    lines.append(
        "Команды: /цели испытание; /цели награда испытание, мастерство или хроника; /цели обновить."
    )
    lines.append("Собранные записи можно прочитать: /цели хроника.")
    rows.append((REFRESH,))
    return Screen(ScreenId.LONG_GOALS, tuple(lines), tuple(rows))
