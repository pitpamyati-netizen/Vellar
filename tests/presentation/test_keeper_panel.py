"""Панель смотрителя целиком: от кнопки в меню до жителя, с которым говорит игрок.

Проход идёт через настоящий автомат, а роль хендлера играет :class:`Panel`: она
делает ровно то, что делает он — записывает правку и пересобирает мир. Ради этого
тест и написан: панель, которая правит, но не показывает исправленное, выглядит
работающей ровно до первой проверки в игре.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.ports.repositories import Census
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.play import Clock, advance, begin, render
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.keeper import KeeperView

WORLD_SEED = "vellar-test"
#: Сколько страниц списка пролистывает поиск кнопки. Больше ни у одного экрана
#: панели нет.
_PAGES_SEARCHED = 8

CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)
KEEPER_ACCOUNT = 500_100


@pytest.fixture
def player() -> Character:
    return Character(
        id=1, user_id=KEEPER_ACCOUNT, name="Аргус", race_id="human", class_id="warrior"
    )


@pytest.fixture
def keeper(player: Character) -> Character:
    return replace(player, is_admin=True)


class Panel:
    """Смотритель за панелью: нажимает кнопки, а мир меняется по-настоящему."""

    def __init__(self, base: GameContent, who: Character) -> None:
        self.base = base
        self.content = base
        self.who = who
        self.records: tuple[OverlayRecord, ...] = ()
        self.players: tuple[Character, ...] = ()
        self.target: Character | None = None
        self.state = begin(who)
        self.services: list[str] = []
        self.removed: list[int] = []
        # Раздача самого права смотрителя: кому её показывают и что она застаёт.
        self.granting = False
        self.target_keeper = False
        self.target_locked = False
        self.granted: list[tuple[int, bool]] = []
        # Блокировки, наложенные и снятые за проход, и журнал панели.
        self.bans: list[tuple[int, str, str]] = []
        self.target_ban = Ban()
        self.notes: list[KeeperEntry] = []

    @property
    def view(self) -> KeeperView:
        return KeeperView(
            records=self.records,
            players=self.players,
            target=self.target,
            census=Census(characters=len(self.players)),
            granting=self.granting,
            target_keeper=self.target_keeper,
            target_locked=self.target_locked,
            target_ban=self.target_ban,
            now=CLOCK.now,
        )

    def press(self, *messages: str) -> Panel:
        for message in messages:
            self.state = advance(
                self.content,
                self.who,
                self.state,
                message,
                clock=CLOCK,
                world_seed=WORLD_SEED,
                keeper=self.view,
            )
            self._store(self.state.pending)
        return self

    def screen(self) -> Screen:
        return render(self.content, self.who, self.state, world_seed=WORLD_SEED, keeper=self.view)

    def buttons(self) -> list[str]:
        return [text for row in self.screen().button_texts() for text in row]

    def button_with(self, needle: str) -> str:
        """Кнопка с этим словом, хоть бы и на следующей странице.

        Смотритель ищет поле листая, а не считая: карточка задания занимает две
        страницы, и какое поле на какой - это верстка, а не поведение.
        """
        seen: list[str] = []
        # С начала списка: прошлый поиск мог оставить карточку на другой странице.
        for _ in range(_PAGES_SEARCHED):
            if labels.PREVIOUS_PAGE.text not in self.buttons():
                break
            self.press(labels.PREVIOUS_PAGE.text)
        for _ in range(_PAGES_SEARCHED):
            buttons = self.buttons()
            seen = buttons
            found = [text for text in buttons if needle in text]
            if found:
                return found[0]
            if labels.NEXT_PAGE.text not in buttons:
                break
            self.press(labels.NEXT_PAGE.text)
        raise AssertionError(f"кнопки со словом {needle!r} нет: {seen}")

    def _store(self, write: object) -> None:
        """То же, что делает хендлер, и в том же порядке."""
        pending = self.state.pending
        if pending.edit is not None:
            kept = tuple(
                record
                for record in self.records
                if (record.kind, record.entity_id) != (pending.edit.kind, pending.edit.entity_id)
            )
            self.records = (*kept, pending.edit)
        if pending.forget is not None:
            kind, entity_id = pending.forget
            self.records = tuple(
                record
                for record in self.records
                if (record.kind.value, record.entity_id) != (kind, entity_id)
            )
        if pending.other is not None:
            self.target = pending.other
            self.players = tuple(
                pending.other if person.id == pending.other.id else person
                for person in self.players
            )
        if pending.remove_character:
            self.removed.append(pending.remove_character)
        if pending.keeper_grant is not None:
            self.granted.append(pending.keeper_grant)
            self.target_keeper = pending.keeper_grant[1]
        if pending.ban is not None:
            self.bans.append(pending.ban)
            _, key, reason = pending.ban
            sentence = moderation_rules.sentence_of(key) if key else None
            self.target_ban = (
                moderation_rules.imposed(sentence, reason, now=CLOCK.now)
                if sentence is not None
                else moderation_rules.lifted()
            )
        if pending.note is not None:
            self.notes.append(pending.note)
        if pending.service:
            self.services.append(pending.service)
        self.content = overlay_rules.apply(self.base, self.records)


@pytest.fixture
def panel(content: GameContent, keeper: Character) -> Panel:
    return Panel(content, keeper).press(labels.KEEPER.text)


# --- дверь -------------------------------------------------------------


def test_the_panel_opens_on_four_doors(panel: Panel) -> None:
    assert panel.state.screen is ScreenId.KEEPER
    for door in (
        labels.KEEPER_WORLD,
        labels.KEEPER_PLAYERS,
        labels.KEEPER_STATS,
        labels.KEEPER_SERVICE,
    ):
        assert door.text in panel.buttons()


def test_a_player_who_lost_the_right_is_shown_the_door(
    content: GameContent, player: Character
) -> None:
    keeper = replace(player, is_admin=True)
    walked = Panel(content, keeper).press(labels.KEEPER.text, labels.KEEPER_WORLD.text)

    walked.who = player
    walked.press(labels.KEEPER_PLAYERS.text)

    assert walked.state.screen is ScreenId.MAIN_MENU
    assert walked.state.notice == keeper_flow.LOST_RIGHT


# --- житель ------------------------------------------------------------


def _add_npc(panel: Panel) -> Panel:
    """Завести жителя и дописать его до рабочего состояния."""
    panel.press(labels.KEEPER_WORLD.text, "Жители", labels.KEEPER_ADD.text)
    panel.press(panel.button_with("Имя"), "Довен")
    panel.press(panel.button_with("Занятие"), "писарь заставы")
    return panel


def test_a_person_added_by_the_panel_stands_in_the_city(panel: Panel) -> None:
    _add_npc(panel)

    assert panel.content.npcs_in("farhold")
    assert panel.content.npcs_in("farhold")[0].title == "Довен, писарь заставы"


def test_a_half_written_person_is_not_in_the_game_and_says_why(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, "Жители", labels.KEEPER_ADD.text)

    assert panel.state.screen is ScreenId.KEEPER_ENTITY
    assert panel.content.npcs == ()
    assert "Пока не работает в игре:" in panel.screen().text()


def test_the_city_of_a_new_person_is_the_one_the_keeper_stands_in(panel: Panel) -> None:
    _add_npc(panel)

    assert panel.content.npcs[0].city_id == "farhold"


def test_a_person_can_be_taken_out_and_put_back(panel: Panel) -> None:
    _add_npc(panel)

    panel.press(labels.KEEPER_REMOVE.text)
    assert panel.content.npcs == ()

    panel.press(labels.KEEPER_RETURN.text)
    assert panel.content.npcs


def test_dropping_the_edit_leaves_the_world_as_content_wrote_it(panel: Panel) -> None:
    _add_npc(panel)

    panel.press(labels.KEEPER_FORGET.text)

    assert panel.content.npcs == ()
    assert panel.records == ()
    assert panel.state.screen is ScreenId.KEEPER_LIST


# --- задание у жителя ---------------------------------------------------


def _add_quest(panel: Panel) -> Panel:
    panel.press("Назад", "Назад", "Задания", labels.KEEPER_ADD.text)
    panel.press(panel.button_with("Название"), "Столбы у брода")
    panel.press(panel.button_with("Условие словами"), "Обойдите три места и скажите, что там.")
    who = panel.button_with("Кто даёт")
    panel.press(who)
    panel.press(panel.button_with("Довен"))
    return panel


def test_a_contract_is_handed_to_a_person_the_panel_created(panel: Panel) -> None:
    _add_npc(panel)
    _add_quest(panel)

    npc = panel.content.npcs[0]
    offered = panel.content.quests_of(npc.id)

    assert [quest.name for quest in offered] == ["Столбы у брода"]
    assert offered[0].giver == "Довен, писарь заставы"


def test_a_player_meets_that_person_in_the_city_and_takes_the_work(
    panel: Panel, player: Character
) -> None:
    """То, ради чего всё это: житель и его задание доходят до игрока."""
    _add_npc(panel)
    _add_quest(panel)

    walk = begin(player)
    for pressed in ("Мир", "Дубно"):
        walk = advance(panel.content, player, walk, pressed, clock=CLOCK, world_seed=WORLD_SEED)
    city = render(panel.content, player, walk, world_seed=WORLD_SEED)
    assert labels.NPCS.text in [text for row in city.button_texts() for text in row]

    walk = advance(
        panel.content, player, walk, labels.NPCS.text, clock=CLOCK, world_seed=WORLD_SEED
    )
    walk = advance(
        panel.content, player, walk, "Довен, писарь заставы", clock=CLOCK, world_seed=WORLD_SEED
    )
    assert walk.screen is ScreenId.NPC

    talking = render(panel.content, player, walk, world_seed=WORLD_SEED)
    offer = next(text for row in talking.button_texts() for text in row)
    walk = advance(panel.content, player, walk, offer, clock=CLOCK, world_seed=WORLD_SEED)
    assert walk.screen is ScreenId.QUEST_OFFER

    walk = advance(
        panel.content, player, walk, labels.QUEST_ACCEPT.text, clock=CLOCK, world_seed=WORLD_SEED
    )
    taken = walk.pending.character
    assert taken is not None
    assert taken.quests.is_taken(panel.content.quests_of(panel.content.npcs[0].id)[0].id)


def test_a_city_without_residents_shows_no_button(content: GameContent, player: Character) -> None:
    walk = begin(player)
    for pressed in ("Мир", "Дубно"):
        walk = advance(content, player, walk, pressed, clock=CLOCK, world_seed=WORLD_SEED)

    city = render(content, player, walk, world_seed=WORLD_SEED)

    assert labels.NPCS.text not in [text for row in city.button_texts() for text in row]


# --- локации и противники ----------------------------------------------


def test_a_location_is_added_to_a_city_and_shows_up_in_its_list(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, "Локации", labels.KEEPER_ADD.text)
    panel.press(panel.button_with("Название"), "Брод у Ольхи")
    panel.press(panel.button_with("Местность"))
    # Местностей больше страницы, поэтому берётся первая, какая бы ни была: тест
    # проверяет, что выбор доходит до мира, а не какой в мире набор местностей.
    chosen = panel.button_with("1. ")
    panel.press(chosen)

    city = panel.content.city("farhold")
    added = next(location for location in city.locations if location.name == "Брод у Ольхи")
    assert added.biome == chosen.removeprefix("1. ")
    assert len(city.locations) == len(panel.base.city("farhold").locations) + 1


def test_a_flag_is_set_by_two_words(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, "Локации")
    panel.press(panel.button_with("Луга у Заставы"))
    panel.press(panel.button_with("Вольная земля"), "Да")

    assert panel.content.city("farhold").location(1).pvp is True


def test_an_enemy_is_settled_by_pressing_biomes_on_and_off(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, "Противники", labels.KEEPER_ADD.text)
    panel.press(panel.button_with("Название"), "Болотная пиявка")
    panel.press(panel.button_with("Где водится"))
    panel.press(panel.button_with("болото"))

    added = next(
        enemy for enemy in panel.content.enemy_archetypes if enemy.name == "Болотная пиявка"
    )
    assert added.fits("болото")

    panel.press(panel.button_with("болото"))
    assert not any(enemy.name == "Болотная пиявка" for enemy in panel.content.enemy_archetypes)


def test_a_city_cannot_be_founded_from_the_panel(panel: Panel) -> None:
    """Уровни и порядок городов держат дорогу целиком: их правит только content."""
    panel.press(labels.KEEPER_WORLD.text, "Города")

    assert labels.KEEPER_ADD.text not in panel.buttons()

    # Набрана руками — отказ словами, а не молчание и не заведённый город.
    panel.press(labels.KEEPER_ADD.text)

    assert panel.state.screen is ScreenId.KEEPER_LIST
    assert panel.state.notice
    assert panel.records == ()
    assert [city.id for city in panel.content.cities] == [city.id for city in panel.base.cities]


def test_an_enemy_from_content_can_be_taken_out(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, "Противники")
    panel.press(panel.button_with("Серый волк"))
    panel.press(labels.KEEPER_REMOVE.text)

    assert "grey_wolf" not in {enemy.id for enemy in panel.content.enemy_archetypes}


def test_the_panel_warns_when_a_biome_is_left_with_nobody(panel: Panel) -> None:
    everybody = tuple(
        OverlayRecord(kind=OverlayKind.ENEMY, entity_id=enemy.id, removed=True)
        for enemy in panel.base.enemy_archetypes
    )
    panel.records = everybody
    panel.content = overlay_rules.apply(panel.base, everybody)

    panel.press(labels.KEEPER_WORLD.text)

    assert "Некому водиться в местностях" in panel.screen().text()


# --- игроки ------------------------------------------------------------


@pytest.fixture
def with_players(panel: Panel) -> Panel:
    panel.players = (
        Character(id=7, user_id=900, name="Мерла", race_id="human", class_id="warrior", level=4),
    )
    return panel


def test_a_player_is_opened_from_the_list(with_players: Panel) -> None:
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.target = with_players.players[0]
    with_players.press(with_players.button_with("Мерла"))

    assert with_players.state.screen is ScreenId.KEEPER_PLAYER
    assert with_players.state.keeper_target == 7


def test_gold_given_to_somebody_else_is_stored_as_somebody_else(with_players: Panel) -> None:
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    before = with_players.target.gold

    with_players.press(labels.KEEPER_GOLD.text)

    assert with_players.target is not None
    assert with_players.target.gold > before
    # Персонаж смотрителя при этом не тронут.
    assert with_players.state.pending.character is None


def test_a_player_is_moved_to_another_city(with_players: Panel) -> None:
    elsewhere = with_players.base.cities[1]
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    with_players.press(labels.KEEPER_MOVE.text)
    with_players.press(with_players.button_with(elsewhere.name))

    assert with_players.target is not None
    assert with_players.target.city_id == elsewhere.id
    assert with_players.state.screen is ScreenId.KEEPER_PLAYER


def test_deleting_a_player_asks_twice(with_players: Panel) -> None:
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))

    with_players.press(labels.KEEPER_DELETE.text)
    assert with_players.removed == []
    assert "ещё раз" in with_players.state.notice

    with_players.press(labels.KEEPER_DELETE.text)
    assert with_players.removed == [7]


def test_any_other_button_disarms_the_deletion(with_players: Panel) -> None:
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    with_players.press(labels.KEEPER_DELETE.text, labels.KEEPER_HEAL.text)

    with_players.press(labels.KEEPER_DELETE.text)

    assert with_players.removed == []


def test_the_right_is_asked_for_by_account_and_says_so(with_players: Panel) -> None:
    with_players.granting = True
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    assert labels.KEEPER_PROMOTE.text in with_players.buttons()

    with_players.press(labels.KEEPER_PROMOTE.text)

    # Спрашивается аккаунт, а не персонаж: право одно на человека.
    assert with_players.granted == [(900, True)]
    assert "теперь смотритель" in with_players.state.notice
    assert labels.KEEPER_DEMOTE.text in with_players.buttons()


def test_the_right_is_neither_shown_nor_given_by_somebody_who_cannot_hand_it_out(
    with_players: Panel,
) -> None:
    """Кнопки нет, строки нет, и набранная руками надпись отвечает как обычно."""
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    assert labels.KEEPER_PROMOTE.text not in with_players.buttons()
    assert "Права смотрителя" not in with_players.screen().text()

    with_players.press(labels.KEEPER_PROMOTE.text)

    assert with_players.granted == []
    assert with_players.state.notice == keeper_flow.PRESS_A_BUTTON


def test_an_account_the_setting_names_keeps_its_right(with_players: Panel) -> None:
    with_players.granting = True
    with_players.target_keeper = True
    with_players.target_locked = True
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    assert "из настройки" in with_players.screen().text()
    assert labels.KEEPER_DEMOTE.text not in with_players.buttons()

    with_players.press(labels.KEEPER_DEMOTE.text)

    assert with_players.granted == []


def test_a_name_that_belongs_to_nobody_is_answered_plainly(panel: Panel) -> None:
    panel.press(labels.KEEPER_PLAYERS.text, labels.KEEPER_FIND.text, "Никто")

    assert panel.state.screen is ScreenId.KEEPER_PLAYERS
    assert "Никто" in panel.state.notice


def test_a_found_name_opens_the_card(with_players: Panel) -> None:
    with_players.press(labels.KEEPER_PLAYERS.text, labels.KEEPER_FIND.text)
    with_players.target = with_players.players[0]
    with_players.press("Мерла")

    assert with_players.state.screen is ScreenId.KEEPER_PLAYER


# --- статистика и обслуживание -----------------------------------------


def test_statistics_are_read_only(panel: Panel) -> None:
    panel.press(labels.KEEPER_STATS.text)

    assert panel.state.screen is ScreenId.KEEPER_STATS
    assert panel.buttons() == [labels.BACK.text, labels.MAIN_MENU.text]


def test_every_maintenance_button_asks_for_its_own_sweep(panel: Panel) -> None:
    panel.press(labels.KEEPER_SERVICE.text)

    for pressed, expected in (
        (labels.KEEPER_SWEEP_DRAFTS, keeper_flow.SWEEP_DRAFTS),
        (labels.KEEPER_CHECK_BLOCKED, keeper_flow.SWEEP_CHECK),
        (labels.KEEPER_DROP_BLOCKED, keeper_flow.SWEEP_BLOCKED),
    ):
        panel.press(pressed.text)
        assert panel.services[-1] == expected


def test_rereading_the_edits_is_asked_for_not_done_here(panel: Panel) -> None:
    panel.press(labels.KEEPER_WORLD.text, labels.KEEPER_RELOAD.text)

    assert panel.state.pending.reload is True


# --- набранное значение ------------------------------------------------


def test_a_command_is_never_taken_for_a_value(panel: Panel) -> None:
    _add_npc(panel)
    panel.press(panel.button_with("Имя"))

    panel.press("/меню")

    assert panel.state.screen is ScreenId.MAIN_MENU


def test_a_value_that_looks_like_a_command_is_refused(panel: Panel) -> None:
    _add_npc(panel)
    panel.press(panel.button_with("Имя"), "/выдумка")

    assert panel.state.screen is ScreenId.KEEPER_FIELD
    assert "команда" in panel.state.notice
    assert panel.content.npcs[0].name == "Довен"


def test_back_walks_out_of_a_card_to_its_list(panel: Panel) -> None:
    _add_npc(panel)

    assert panel.press("Назад").state.screen is ScreenId.KEEPER_LIST


# --- игра всегда отвечает ----------------------------------------------


def walk_to(panel: Panel, screen: ScreenId) -> Panel:
    """Довести панель до нужного экрана самым коротким путём."""
    match screen:
        case ScreenId.KEEPER:
            return panel
        case ScreenId.KEEPER_CONTENT:
            return panel.press(labels.KEEPER_WORLD.text)
        case ScreenId.KEEPER_LIST:
            return panel.press(labels.KEEPER_WORLD.text, "Жители")
        case ScreenId.KEEPER_ENTITY:
            return _add_npc(panel)
        case ScreenId.KEEPER_FIELD:
            walked = _add_npc(panel)
            return walked.press(walked.button_with("Город"))
        case ScreenId.KEEPER_PLAYERS:
            return panel.press(labels.KEEPER_PLAYERS.text)
        case ScreenId.KEEPER_PLAYER:
            panel.players = (
                Character(id=7, user_id=900, name="Мерла", race_id="human", class_id="warrior"),
            )
            panel.target = panel.players[0]
            walked = panel.press(labels.KEEPER_PLAYERS.text)
            return walked.press(walked.button_with("Мерла"))
        case ScreenId.KEEPER_STATS:
            return panel.press(labels.KEEPER_STATS.text)
        case ScreenId.KEEPER_LOG:
            return panel.press(labels.KEEPER_LOG.text)
        case ScreenId.KEEPER_BAN:
            walked = walk_to(panel, ScreenId.KEEPER_PLAYER)
            return walked.press(labels.KEEPER_BAN.text)
        case ScreenId.KEEPER_TRADES:
            walked = walk_to(panel, ScreenId.KEEPER_PLAYER)
            return walked.press(labels.KEEPER_TRADES.text)
        case ScreenId.KEEPER_TUNE:
            return panel.press(labels.KEEPER_TUNE.text)
        case ScreenId.KEEPER_AMOUNT:
            return panel.press(labels.KEEPER_TUNE.text, labels.KEEPER_SET_GOLD.text)
        case ScreenId.KEEPER_GIVE:
            walked = walk_to(panel, ScreenId.KEEPER_PLAYER)
            return walked.press(labels.KEEPER_GIVE_ITEM.text)
        case ScreenId.KEEPER_GIVE_GEAR:
            walked = walk_to(panel, ScreenId.KEEPER_GIVE)
            return walked.press(walked.button_with("топор"))
        case ScreenId.KEEPER_GIVE_ITEM:
            walked = walk_to(panel, ScreenId.KEEPER_GIVE)
            return walked.press(walked.button_with("сырьё"))
        case (
            ScreenId.KEEPER_SKILLS
            | ScreenId.KEEPER_SKILL
            | ScreenId.KEEPER_SKILL_LEARN
            | ScreenId.KEEPER_SKILL_EDGE
            | ScreenId.KEEPER_SKILL_SLOT
        ):
            skilled = Character(
                id=7,
                user_id=900,
                name="Мерла",
                race_id="human",
                class_id="warrior",
                level=20,
                loadout=SkillLoadout(
                    actives=("warrior_rassechenie", None, None, None, None, None),
                    racial="race_human_second_wind",
                    ranks=MappingProxyType({"warrior_rassechenie": 3, "race_human_second_wind": 1}),
                ),
            )
            panel.players = (skilled,)
            panel.target = skilled
            walked = panel.press(labels.KEEPER_PLAYERS.text)
            walked = walked.press(walked.button_with("Мерла"))
            walked = walked.press(labels.KEEPER_SKILLS.text)
            if screen is ScreenId.KEEPER_SKILLS:
                return walked
            if screen is ScreenId.KEEPER_SKILL_LEARN:
                return walked.press(labels.KEEPER_SKILL_LEARN.text)
            walked = walked.press(walked.button_with("Рассечение"))
            if screen is ScreenId.KEEPER_SKILL:
                return walked
            if screen is ScreenId.KEEPER_SKILL_EDGE:
                return walked.press(labels.KEEPER_SKILL_EDGE_BTN.text)
            return walked.press(labels.KEEPER_SKILL_SLOT_BTN.text)
        case ScreenId.KEEPER_STATS_EDIT:
            walked = walk_to(panel, ScreenId.KEEPER_PLAYER)
            return walked.press(labels.KEEPER_STATS_EDIT_BTN.text)
        case _:
            return panel.press(labels.KEEPER_SERVICE.text)


@pytest.mark.parametrize("screen", sorted(keeper_flow.PANEL, key=lambda item: item.value))
def test_no_panel_screen_ever_answers_with_silence(
    content: GameContent, keeper: Character, screen: ScreenId
) -> None:
    """Кнопка от чужой клавиатуры получает объяснение, а не молчание и не падение.

    Это правило 12 доступности, и на служебном экране оно держится так же, как
    на игровом: смотритель тоже нажимает вслепую.
    """
    panel = walk_to(Panel(content, keeper).press(labels.KEEPER.text), screen)
    assert panel.state.screen is screen

    answered = panel.press("Совершенно посторонняя кнопка").state

    assert answered.notice, f"{screen} промолчал"
    assert answered.pending.empty, f"{screen} что-то записал в ответ на чушь"


@pytest.mark.parametrize("screen", sorted(keeper_flow.PANEL, key=lambda item: item.value))
def test_the_service_row_works_on_every_panel_screen(
    content: GameContent, keeper: Character, screen: ScreenId
) -> None:
    panel = walk_to(Panel(content, keeper).press(labels.KEEPER.text), screen)

    assert panel.press("Главное меню").state.screen is ScreenId.MAIN_MENU


# --- задать точно -----------------------------------------------------


def _tune(with_players: Panel) -> Panel:
    """Карточку чужого персонажа довести до меню точных правок."""
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    return with_players.press(labels.KEEPER_TUNE.text)


def test_exact_gold_is_a_signed_amount(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(labels.KEEPER_SET_GOLD.text, "250")
    assert card.target is not None and card.target.gold == 250

    card.press(labels.KEEPER_SET_GOLD.text, "-1000")
    assert card.target.gold == 0, "ниже нуля не уходит"
    assert card.state.screen is ScreenId.KEEPER_TUNE


def test_exact_bank_is_set_outright(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(card.button_with("Ячейка"), "700")

    assert card.target is not None and card.target.bank_gold == 700


def test_exact_level_climbs_to_the_target_and_never_down(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(card.button_with("Уровень"), "9")
    assert card.target is not None and card.target.level == 9

    card.press(card.button_with("Уровень"), "2")
    assert card.target.level == 9
    assert card.state.notice


def test_points_are_only_handed_out_never_taken(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(card.button_with("очки характеристик"), "-3")
    assert card.target is not None and card.target.unspent_stat_points == 0
    assert card.state.notice

    card.press("Назад")
    card.press(card.button_with("очки умений"), "5")
    assert card.target.unspent_skill_points == 5


def test_renaming_goes_through_the_name_check(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(labels.KEEPER_RENAME.text, "!!!")
    assert card.target is not None and card.target.name == "Мерла"
    assert card.state.notice

    card.press("Назад")
    card.press(labels.KEEPER_RENAME.text, "Дорн")
    assert card.target.name == "Дорн"


def test_an_exact_edit_of_another_player_is_written_down(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(labels.KEEPER_SET_GOLD.text, "100")

    assert card.notes and card.notes[-1].action == KeeperAction.GOLD
    assert card.notes[-1].target == "Мерла"


def test_the_keeper_tunes_their_own_character_without_a_journal_line(panel: Panel) -> None:
    panel.press(labels.KEEPER_TUNE.text, labels.KEEPER_SET_GOLD.text, "777")

    assert panel.state.pending.character is not None
    assert panel.state.pending.character.gold == 777
    assert panel.notes == []


def test_a_tune_value_that_is_not_a_number_is_refused(with_players: Panel) -> None:
    card = _tune(with_players)
    card.press(labels.KEEPER_SET_GOLD.text, "щедро")

    assert card.state.screen is ScreenId.KEEPER_AMOUNT
    assert card.state.notice
    assert card.target is not None and card.target.gold == 0


# --- выдать вещь ------------------------------------------------------


def _give(with_players: Panel) -> Panel:
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    return with_players.press(labels.KEEPER_GIVE_ITEM.text)


def test_assembled_gear_lands_in_the_bag(with_players: Panel) -> None:
    card = _give(with_players)
    card.press(card.button_with("топор"))
    card.press(card.button_with("Обычный"))

    assert card.state.pending.grant_item is not None
    cid, item_id, delta = card.state.pending.grant_item
    assert cid == 7 and delta == 1
    assert item_id.startswith("axe@") and item_id.endswith("#common")
    assert card.state.screen is ScreenId.KEEPER_PLAYER


def test_gear_level_defaults_to_the_player_and_can_be_typed(with_players: Panel) -> None:
    with_players.players = (replace(with_players.players[0], level=40),)
    card = _give(with_players)
    card.press(card.button_with("топор"))
    assert "26" in card.screen().text(), "ступень по уровню игрока"

    card.press("6")
    card.press(card.button_with("Редкий"))

    assert card.state.pending.grant_item is not None
    assert card.state.pending.grant_item[1] == "axe@6#rare"


def test_a_written_item_is_given_by_count(with_players: Panel) -> None:
    card = _give(with_players)
    card.press(card.button_with("сырьё"))
    card.press("5")

    assert card.state.pending.grant_item is not None
    assert card.state.pending.grant_item[2] == 5


def test_a_negative_count_removes_from_the_bag(with_players: Panel) -> None:
    card = _give(with_players)
    card.press(card.button_with("расходник"))
    card.press("-2")

    assert card.state.pending.grant_item is not None
    assert card.state.pending.grant_item[2] == -2


def test_giving_an_item_is_written_down(with_players: Panel) -> None:
    card = _give(with_players)
    card.press(card.button_with("сырьё"), "3")

    assert card.notes and card.notes[-1].action == KeeperAction.GRANT_ITEM
    assert card.notes[-1].target == "Мерла"


# --- умения игрока ---------------------------------------------------


def _skilled(with_players: Panel) -> Panel:
    with_players.players = (
        replace(
            with_players.players[0],
            level=20,
            loadout=SkillLoadout(
                actives=("warrior_rassechenie", None, None, None, None, None),
                racial="race_human_second_wind",
                ranks=MappingProxyType({"warrior_rassechenie": 3, "race_human_second_wind": 1}),
            ),
        ),
    )
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    return with_players.press(labels.KEEPER_SKILLS.text)


def test_a_skill_rank_moves_both_ways_without_points(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(card.button_with("Рассечение"))
    card.press(labels.KEEPER_RANK_UP.text)
    assert card.target is not None and card.target.loadout.rank_of("warrior_rassechenie") == 4

    card.press(labels.KEEPER_RANK_DOWN.text, labels.KEEPER_RANK_DOWN.text)
    assert card.target.loadout.rank_of("warrior_rassechenie") == 2


def test_forgetting_a_skill_returns_to_the_list(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(card.button_with("Рассечение"), labels.KEEPER_SKILL_FORGET.text)

    assert card.target is not None and "warrior_rassechenie" not in card.target.loadout.ranks
    assert card.state.screen is ScreenId.KEEPER_SKILLS


def test_a_skill_is_taught_without_points(with_players: Panel) -> None:
    card = _skilled(with_players)
    before = set(card.target.loadout.ranks) if card.target else set()
    card.press(labels.KEEPER_SKILL_LEARN.text)
    option = next(text for text in card.buttons() if "боевое" in text or "пассивное" in text)
    card.press(option)

    assert card.target is not None and set(card.target.loadout.ranks) - before
    assert card.state.screen is ScreenId.KEEPER_SKILLS


def test_respec_returns_points_and_keeps_the_racial(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(labels.KEEPER_SKILL_RESPEC.text)

    assert card.target is not None
    assert card.target.unspent_skill_points > 0
    assert "race_human_second_wind" in card.target.loadout.ranks
    assert "warrior_rassechenie" not in card.target.loadout.ranks


def test_an_edge_is_chosen_from_the_card(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(card.button_with("Рассечение"), labels.KEEPER_SKILL_EDGE_BTN.text)
    edge = next(
        text for text in card.buttons() if text not in {labels.BACK.text, labels.MAIN_MENU.text}
    )
    card.press(edge)

    assert card.target is not None
    assert card.target.loadout.edge_of("warrior_rassechenie") is not None
    assert card.state.screen is ScreenId.KEEPER_SKILL


def test_a_skill_is_dropped_into_a_slot(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(labels.KEEPER_SKILL_LEARN.text)
    option = next(text for text in card.buttons() if "боевое" in text)
    card.press(option)
    name = option.split(". ", 1)[1].split(" — ")[0]
    card.press(card.button_with(name))
    card.press(labels.KEEPER_SKILL_SLOT_BTN.text, "Слот 3")

    assert card.target is not None and card.target.loadout.actives[2] is not None


def test_editing_a_skill_is_written_down(with_players: Panel) -> None:
    card = _skilled(with_players)
    card.press(card.button_with("Рассечение"), labels.KEEPER_RANK_UP.text)

    assert card.notes and card.notes[-1].action == KeeperAction.SKILL


# --- характеристики игрока -----------------------------------------


def test_a_stat_is_set_from_its_button_and_a_typed_value(with_players: Panel) -> None:
    with_players.players = (replace(with_players.players[0], unspent_stat_points=10),)
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    card = with_players.press(labels.KEEPER_STATS_EDIT_BTN.text)

    card.press(card.button_with("Сила"), "4")
    assert card.target is not None and card.target.allocated.STR == 4
    assert card.target.unspent_stat_points == 6

    # больше, чем есть очков — отказ, значение не меняется
    card.press(card.button_with("Выносливость"), "99")
    assert card.target.allocated.END == 0
    assert card.state.notice


def test_a_stat_edit_is_written_down(with_players: Panel) -> None:
    with_players.players = (replace(with_players.players[0], unspent_stat_points=5),)
    with_players.target = with_players.players[0]
    with_players.press(labels.KEEPER_PLAYERS.text)
    with_players.press(with_players.button_with("Мерла"))
    card = with_players.press(labels.KEEPER_STATS_EDIT_BTN.text)
    card.press(card.button_with("Удача"), "2")

    assert card.notes and card.notes[-1].action == KeeperAction.POINTS


# --- блокировка --------------------------------------------------------


def opened(with_players: Panel) -> Panel:
    """Карточка чужого персонажа, открытая из списка."""
    with_players.target = with_players.players[0]
    return with_players.press(labels.KEEPER_PLAYERS.text).press(with_players.button_with("Мерла"))


def test_a_ban_takes_a_reason_first_and_a_term_second(with_players: Panel) -> None:
    """Причину читает не смотритель, а тот, кого блокируют, поэтому она первая."""
    card = opened(with_players).press(labels.KEEPER_BAN.text)
    assert card.state.screen is ScreenId.KEEPER_BAN

    card.press(labels.KEEPER_REASON.text, "ругался в группе")
    assert card.state.keeper_reason == "ругался в группе"

    card.press("На сутки")
    assert card.bans == [(900, "day", "ругался в группе")]
    # Панель возвращает туда, откуда пришли: наказание — не отдельная комната.
    assert card.state.screen is ScreenId.KEEPER_PLAYER
    assert "Мерла" in card.state.notice


def test_a_ban_without_a_reason_still_lands(with_players: Panel) -> None:
    card = opened(with_players).press(labels.KEEPER_BAN.text, "Навсегда")

    assert card.bans == [(900, "forever", "")]
    assert card.target_ban.forever


def test_an_unknown_term_bans_nobody(with_players: Panel) -> None:
    card = opened(with_players).press(labels.KEEPER_BAN.text, "На века")

    assert card.bans == []
    assert card.state.screen is ScreenId.KEEPER_BAN
    assert card.state.notice


def test_a_ban_is_lifted_from_the_card_in_one_press(with_players: Panel) -> None:
    card = opened(with_players).press(labels.KEEPER_BAN.text, "На час")
    assert labels.KEEPER_UNBAN.text in card.press(labels.KEEPER_BAN.text).buttons()

    card.press(labels.KEEPER_UNBAN.text)

    assert card.bans[-1] == (900, "", "")
    assert not moderation_rules.is_banned(card.target_ban, now=CLOCK.now)


def test_the_card_says_whether_the_player_is_banned(with_players: Panel) -> None:
    card = opened(with_players)
    assert any("Блокировка: нет" in line for line in card.screen().lines)

    card.press(labels.KEEPER_BAN.text, "На неделю")
    lines = card.screen().lines
    assert any("Блокировка: есть" in line for line in lines)
    # Кнопка на карточке меняется вместе со строкой: блокировать снова нечего.
    assert labels.KEEPER_UNBAN.text in card.buttons()


def test_what_is_done_to_somebody_else_is_written_down(with_players: Panel) -> None:
    """Журнал — это то, ради чего у панели есть имя нажавшего."""
    card = opened(with_players)
    card.press(labels.KEEPER_GOLD.text)
    card.press(labels.KEEPER_DELETE.text, labels.KEEPER_DELETE.text)

    actions = [note.action for note in card.notes]
    assert actions == [KeeperAction.GOLD, KeeperAction.DELETE]
    assert all(note.target == "Мерла" for note in card.notes)


def test_the_journal_is_a_door_of_its_own_and_only_reads(panel: Panel) -> None:
    walked = panel.press(labels.KEEPER_LOG.text)

    assert walked.state.screen is ScreenId.KEEPER_LOG
    # Нажимать здесь нечего: кнопок нет, кроме служебного ряда.
    assert walked.buttons() == ["Назад", "Главное меню"]
