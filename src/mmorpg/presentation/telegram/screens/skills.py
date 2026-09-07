"""Экраны умений: что изучено, что покупает очко и что лежит в панели.

Два экрана, и не больше, потому что форма самой панели не меняется никогда:

- **Умения** — все умения класса, ранг каждого и то, что сделало бы одно очко;
- **Слоты умений** — шесть боевых мест и расовое.

Слот всегда держит свой номер и своё место, пустой он или нет, поэтому панель
можно один раз выучить по положению и не переучивать (правило доступности 7).

Пассивные умения слотов не занимают: изученное работает, и укладывать его
некуда. Три слота из шести означали только то, что половина потраченных очков
не считалась ни в одном бою.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.rules import combo as combo_rules
from mmorpg.domain.rules import passives as passive_rules
from mmorpg.domain.rules import skill_mastery as mastery_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

EMPTY_SLOT = "пусто"
CLEAR_SLOT = label("Освободить слот")

#: Разделы списка умений. Их ровно два, потому что умение либо жмут в бою, либо
#: оно работает само; третьего вида в игре нет.
ACTIVE_SECTION = "Боевые"
PASSIVE_SECTION = "Пассивные"
SKILL_SECTIONS: tuple[str, ...] = (ACTIVE_SECTION, PASSIVE_SECTION)


def matching_skills(
    content: GameContent, character: Character, pool: tuple[Skill, ...], state: PageState
) -> tuple[Skill, ...]:
    """Умения, прошедшие раздел и поиск.

    Ищут по названию и по описанию: игрок помнит «оглушает», а не «Удар щитом».
    """
    section = state.filters.category
    needle = state.filters.query.casefold().strip()
    return tuple(
        skill
        for skill in pool
        if (not section or section == (ACTIVE_SECTION if skill.is_active else PASSIVE_SECTION))
        and (not needle or needle in skill.name.casefold() or needle in skill.text.casefold())
    )


def turns_word(count: int) -> str:
    """«один ход», «два хода», «пять ходов» - слово согласовано с числом."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} ходов"
    last = count % 10
    if last == 1:
        return f"{count} ход"
    if 2 <= last <= 4:
        return f"{count} хода"
    return f"{count} ходов"


def points_word(count: int) -> str:
    """«одно очко», «два очка», «пять очков» - слово согласовано с числом."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} очков"
    last = count % 10
    if last == 1:
        return "одно очко" if count == 1 else f"{count} очко"
    if 2 <= last <= 4:
        return f"{count} очка"
    return f"{count} очков"


def rank_gain_words(
    rank: int, skill: Skill | None = None, content: GameContent | None = None
) -> str:
    """Что ранг уже прибавил этому умению. Пусто - первый ранг ничего не прибавил.

    Называется ровно то, что считает движок (``skill_rules.rank_gain``): очко,
    вложенное в ранг, обязано быть слышно, а не подразумеваться (ADR 0067).

    У пассивного умения нет ни отката, ни срока, ни цены, и говорить о них
    значило бы обещать то, чего движок ему не считает (``Claude.md``, правило 7).
    Ранг пассивке даёт ровно одно - размер самой прибавки
    (``modifiers.passive_modifiers``), - и называется здесь именно он, числом.
    """
    if skill is not None and content is not None and not skill.is_active:
        return passive_power_words(content, skill, rank)
    gain = skill_rules.rank_gain(rank)
    parts = []
    if gain.cooldown_cut:
        parts.append(f"откат короче на {turns_word(gain.cooldown_cut)}")
    if gain.duration_bonus:
        parts.append(f"сроки длиннее на {turns_word(gain.duration_bonus)}")
    if gain.cost_factor < 1.0:
        parts.append(f"цена ниже на {round((1.0 - gain.cost_factor) * 100)} процентов")
    return ", ".join(parts)


def passive_power_words(content: GameContent, skill: Skill, rank: int) -> str:
    """Что пассивка даёт сейчас - и что даст следующий ранг.

    Обе величины числами: текст умения в ``skills.toml`` называет силу первого
    ранга и с рангом не меняется, поэтому без этой строки пассивка на пятом
    ранге выглядела бы ровно так же, как на первом.

    Уклад называется своей фразой (``rules/passives``): «крит возвращает восемь
    процентов запаса» - это не прибавка, и строкой прибавки её не сказать.
    """
    from mmorpg.presentation.telegram.screens.items import modifier_line

    amount = skill.power_at_rank(max(1, rank))
    if passive_rules.has_power(skill.effect):
        return passive_rules.power(skill.effect).words(amount)
    return modifier_line(content, skill.effect, amount)


def mastery_words(content: GameContent, character: Character, skill: Skill) -> str:
    """Чему умение научено. Пусто - ничему пока (``rules/skill_mastery``)."""
    taken = skill_rules.taken_masteries(character, skill)
    if not taken:
        return ""
    return "Выучка: " + "; ".join(f"{one.name} — {mastery_rules.words(one)}" for one in taken)


def mastery_call(content: GameContent, character: Character, skill: Skill) -> str:
    """Зов выбрать выучку. Пусто - выбирать нечего или не пора."""
    tier = skill_rules.mastery_tier_due(content, character, skill)
    if tier is None:
        return ""
    return "Пора выбрать выучку: нажмите умение."


def skill_standing(content: GameContent, character: Character, skill: Skill) -> str:
    """Положение умения одной короткой фразой - ровно то, что стоит на кнопке.

    Короткой нарочно: надпись длиннее ``BUTTON_LIMIT`` Telegram отдаёт игре
    обрезанной, а маршрут идёт по точному тексту, и такая кнопка не работает
    вовсе. Всё, что ранг дал и что даст следующий, говорит ``rank_offer`` - в
    теле сообщения, где место есть.
    """
    if not skill_rules.is_known(character, skill.code):
        taken = skill_rules.fork_taken(content, character, skill)
        if taken is not None:
            return f"закрыто развилкой: взято {taken.name}"
        return f"не изучено, {points_word(skill_rules.cost_to_learn(content, character, skill))}"
    said = f"ранг {character.loadout.rank_of(skill.code)} из {content.rules.max_rank}"
    if skill_rules.mastery_tier_due(content, character, skill) is not None:
        return f"{said}, ждёт выучки"
    return said


def rank_offer(content: GameContent, character: Character, skill: Skill) -> str:
    """Что ранг умению уже дал и что даст следующее очко. Пусто - сказать нечего.

    Говорится телом сообщения, а не кнопкой: ранг стоит одно очко (ADR 0067), и
    цена вместе с прибавкой обязаны быть слышны до нажатия, - но в надписи им
    места нет, она отдаётся Telegram обрезанной.
    """
    rules = content.rules
    if not skill_rules.is_known(character, skill.code):
        if skill_rules.fork_taken(content, character, skill) is not None:
            return ""
        # Пассивка объявляет свою прибавку числом: без него «одно очко» покупает
        # строку, а не силу.
        if not skill.is_active:
            return f"Одно очко даст {passive_power_words(content, skill, 1)}."
        return ""
    rank = character.loadout.rank_of(skill.code)
    # Ранг, купленный и не потраченный на выбор, - это очко, лежащее без дела, и
    # зовёт потратить его ``mastery_call``: два зова подряд сказали бы одно и то же.
    if skill_rules.mastery_tier_due(content, character, skill) is not None:
        return ""
    said = []
    if gained := rank_gain_words(rank, skill, content):
        said.append(f"Ранг {rank} даёт: {gained}.")
    if rank >= rules.max_rank:
        said.append("Выше некуда.")
    else:
        cost = points_word(skill_rules.cost_to_learn(content, character, skill))
        # Пассивка объявляет, во что превратится её прибавка: очко, купившее «плюс
        # шесть» вместо «плюс пяти», обязано быть названо до нажатия, а не после.
        if not skill.is_active:
            grown = passive_power_words(content, skill, rank + 1)
            said.append(f"Следующий за {cost} даст {grown}.")
        else:
            said.append(f"Следующий за {cost}.")
    return " ".join(said)


def refusal(content: GameContent, character: Character, skill: Skill) -> str:
    """Почему очко не легло. Отказ обязан называть причину, а не просто случиться."""
    if skill_rules.is_known(character, skill.code):
        if character.loadout.rank_of(skill.code) >= content.rules.max_rank:
            return f"{skill.name} уже на высшем ранге."
    else:
        taken = skill_rules.fork_taken(content, character, skill)
        if taken is not None:
            return (
                f"{skill.name} и {taken.name} стоят на одной развилке, и {taken.name} "
                "уже взято. Разобрать его может наставник."
            )
    cost = skill_rules.cost_to_learn(content, character, skill)
    return (
        f"На {skill.name} нужно {points_word(cost)}, а есть {character.unspent_skill_points}. "
        "Очко умений приходит через уровень, и вложенное возвращает наставник."
    )


def weapon_demand(content: GameContent, skill: Skill) -> str:
    """Каким оружием это умение вообще работает. Пусто — любым и без оружия.

    Список читается со стороны умения, а не со стороны рук: экран умений
    открывают вне боя, и там важно, подо что умение брать, а не что надето сейчас.
    """
    if not skill.weapon_types:
        return ""
    wanted = ", ".join(
        content.weapon_type(type_id).name.lower()
        for type_id in skill.weapon_types
        if content.has_weapon_type(type_id)
    )
    return f"Работает только с таким оружием: {wanted}." if wanted else ""


def fork_note(content: GameContent, skill: Skill) -> str:
    """Чему это умение соперник. Пусто - оно ни с чем не спорит."""
    rivals = skill_rules.fork_rivals(content, skill)
    if not rivals:
        return ""
    names = ", ".join(rival.name for rival in rivals)
    return f"Развилка: или это, или {names}."


def skill_detail(content: GameContent, skill: Skill, character: Character | None = None) -> str:
    """Строка под названием умения: что оно делает и чем его для этого держат.

    Связка называется здесь же (``rules/combo``): по чему это умение бьёт
    сильнее, игрок обязан узнать до того, как положит его в слот, - иначе панель
    собирается на глаз.
    """
    parts = [
        skill.text,
        combo_rules.demand_line(skill),
        weapon_demand(content, skill),
        fork_note(content, skill),
    ]
    if character is not None:
        parts.extend(
            [mastery_words(content, character, skill), mastery_call(content, character, skill)]
        )
    return " ".join(part for part in parts if part)


def skill_entry_text(content: GameContent, character: Character, skill: Skill) -> str:
    """Надпись кнопки: имя, вид и положение - и ничего сверх.

    Всё остальное стоит строкой в теле (``rank_offer``, ``skill_detail``):
    надпись длиннее ``BUTTON_LIMIT`` доезжает до игры обрезанной, и нажатие на
    неё не находит своего умения.
    """
    kind = "боевое" if skill.is_active else "пассивное"
    return f"{skill.name} — {kind}, {skill_standing(content, character, skill)}"


def spent_line(content: GameContent, character: Character) -> str:
    """Сколько очков уже лежит в дереве. Одно число: ветвей больше нет (ADR 0067)."""
    spent = sum(
        skill_rules.spent_on(content, character, code)
        for code in skill_rules.known_codes(character)
        if content.has_skill(code)
    )
    return f"Вложено в умения: {points_word(spent)}."


def skills_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Список, из которого тратят очко умений."""
    pool = matching_skills(content, character, skill_rules.teachable(content, character), state)
    entries = [
        ListEntry(
            key=skill.code,
            text=skill_entry_text(content, character, skill),
            # Что купит очко, стоит в теле, а не на кнопке: на кнопке для этого
            # нет места, и обрезанная надпись перестаёт быть нажимаемой.
            detail=" ".join(
                part
                for part in (
                    rank_offer(content, character, skill),
                    skill_detail(content, skill, character),
                )
                if part
            ),
        )
        for skill in pool
    ]
    # Вступление короткое нарочно: оно повторяется на каждой странице списка, а
    # места в сообщении столько же, сколько у самих умений. Устройство панели
    # рассказывает экран слотов - тот, на котором это и делают.
    lead = [
        notice or f"Умения. Очков умений: {character.unspent_skill_points}.",
        f"Ваш уровень: {character.level}. Пассивные умения слотов не занимают.",
        spent_line(content, character),
    ]
    if not character.unspent_skill_points:
        lead.append(
            f"Очко умений приходит через уровень: одно на {content.rules.levels_per_skill_point}."
        )
    return paginated_screen(
        screen_id=ScreenId.SKILLS,
        title="Умения",
        entries=entries,
        state=state,
        lead_lines=lead,
        empty_text="Пока учить нечего, следующее умение откроется с уровнем.",
        extra_rows=((labels.SKILL_SLOTS,),),
        categories=SKILL_SECTIONS,
    )


def slot_label(content: GameContent, character: Character, slot: int) -> Label:
    """Кнопка слота несёт свой номер и то, что в нём лежит."""
    code = character.loadout.actives[slot]
    name = content.skill(code).name if code and content.has_skill(code) else EMPTY_SLOT
    return label(f"Боевой слот {slot + 1}: {name}")


def slots_screen(content: GameContent, character: Character, notice: str = "") -> Screen:
    """Панель ровно в том виде, в каком она будет выглядеть в бою."""
    rules = content.rules
    racial_code = character.loadout.racial
    racial = (
        content.skill(racial_code).name
        if racial_code and content.has_skill(racial_code)
        else EMPTY_SLOT
    )
    working = skill_rules.known_passives(content, character)
    lines = [
        *head("Слоты умений.", notice),
        "Нажмите слот, чтобы положить в него умение.",
        f"Боевых слотов {rules.active_slots}, расовый один, и он не меняется.",
        f"Расовое умение: {racial}.",
        "Пассивные умения слотов не занимают: изученное работает всегда.",
    ]
    if working:
        # Пассивка называет не только ранг, но и то, что она даёт этим рангом:
        # иначе изученное молчит, и игрок не знает, работает ли оно вообще.
        lines.append("Работают сейчас:")
        lines.extend(
            f"{skill.name}, ранг {character.loadout.rank_of(skill.code)}: "
            f"{passive_power_words(content, skill, character.loadout.rank_of(skill.code))}."
            for skill in working
        )
    rows: list[tuple[Label, ...]] = [
        (slot_label(content, character, slot),) for slot in range(rules.active_slots)
    ]
    return Screen(id=ScreenId.SKILL_SLOTS, lines=tuple(lines), rows=tuple(rows))


def mastery_screen(
    content: GameContent,
    character: Character,
    skill: Skill,
    notice: str = "",
) -> Screen:
    """Чему умение научилось, взяв ранг (``rules/skill_mastery``).

    Список короткий и без страниц: выучек, подходящих одному умению, всегда
    несколько, а не десятки. Каждая названа тем, что она делает, - и делает
    ровно это.
    """
    choices = skill_rules.mastery_choices(content, character, skill)
    tier = skill_rules.mastery_tier_due(content, character, skill)
    lines = [
        *head(f"Выучка: {skill.name}.", notice),
        skill.text,
        "Выбранное не меняют: вернуть выучку можно только вместе с умением, у наставника.",
    ]
    if tier is not None:
        lines.append(
            f"Это выбор {tier} ступени, он приходит с рангом {mastery_rules.rank_of_tier(tier)}."
        )
    if taken := mastery_words(content, character, skill):
        lines.append(taken + ".")
    if not choices:
        lines.append("Выбирать пока нечего.")
    lines.extend(f"{one.name}: {mastery_rules.words(one)}" for one in choices)
    rows = tuple((label(one.name),) for one in choices)
    return Screen(id=ScreenId.SKILL_MASTERY, lines=tuple(lines), rows=rows)


def pick_screen(
    content: GameContent,
    character: Character,
    slot: int,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Что можно положить в слот: изученные боевые умения, и больше ничего."""
    available = skill_rules.equippable(content, character)
    entries = [
        ListEntry(
            key=skill.code,
            text=f"{skill.name} — ранг {character.loadout.rank_of(skill.code)}",
            detail=skill_detail(content, skill, character),
        )
        for skill in available
    ]
    return paginated_screen(
        screen_id=ScreenId.SKILL_PICK,
        title=f"Слот {slot + 1}, боевой",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Выберите умение для слота {slot + 1}.",
            "Умение занимает один слот: из другого оно уйдёт само.",
        ),
        empty_text="Изученных боевых умений нет. Сначала изучите их в разделе «Умения».",
        show_filters=False,
        extra_rows=((CLEAR_SLOT,),),
    )
