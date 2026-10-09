"""Выбор и подтверждение используют сохранённый номер товара и цену."""

from dataclasses import replace
from math import ceil

from mmorpg.application.services.market import Market
from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules.economy import trade_tax
from mmorpg.domain.rules.market import supply_and_demand
from mmorpg.presentation.telegram.flows.play import begin
from mmorpg.presentation.telegram.flows.state import PlayState, go_back
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.routing import Intent, parse_command
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

OPEN = "Рынок"
WORDS = {
    "Обновить рынок": "",
    "Продать вещь на рынке": "сумка",
    "Заказать изготовление": "заказ",
    "Отчёт рынка": "отчёт",
    "Подтвердить сделку": "подтвердить",
    "Отменить выбор": "",
    "Следующая страница рынка": "дальше",
    "Предыдущая страница рынка": "раньше",
}


def requested(text: str, flow: PlayState) -> bool:
    return (
        text.casefold().split(maxsplit=1)[0:1] == ["/рынок"]
        or text == OPEN
        or flow.screen is ScreenId.MARKET
    )


async def step(
    text: str, flow: PlayState, actor: Character, service: Market, *, now: int
) -> tuple[PlayState, Screen | None]:
    command = parse_command(text)
    if (command and command.intent is Intent.BACK) or labels.BACK.matches(text):
        return go_back(flow), None
    if (command and command.intent is Intent.MAIN_MENU) or labels.MAIN_MENU.matches(text):
        return begin(actor), None
    if command and command.intent not in {Intent.UNKNOWN, Intent.LOOK}:
        return go_back(flow), None
    if text.startswith(("/событие", "/набор")):
        return go_back(flow), None
    await service.sweep(now)
    state = await service.load()
    notice = ""
    chosen = dict(flow.market_choices).get(text)
    action = WORDS.get(
        text,
        text.partition(" ")[2].strip()
        if text.casefold().startswith("/рынок")
        else ""
        if text == OPEN
        else text,
    )
    if chosen:
        action = chosen
    parts = action.casefold().split()
    verb = parts[0] if parts else ""
    args = parts[1:]
    flow = flow.at(ScreenId.MARKET)
    try:
        if verb in {"дальше", "раньше"}:
            flow = replace(
                flow, market_page=max(1, flow.market_page + (1 if verb == "дальше" else -1))
            )
        elif verb == "сумка":
            flow = replace(flow, market_mode="bag", market_page=1, market_action="")
        elif verb in {"вещь", "продать"}:
            options = [value for _button, value in flow.market_choices if value.startswith("вещь ")]
            index = int(args[0]) - 1
            # Номер ссылается на показанный список, сам ID предмета не принимается от клиента.
            references = [value.split(" ", 2)[2] for value in options]
            if not 0 <= index < len(references):
                raise ValueError("choice")
            flow = replace(
                flow,
                market_target=references[index],
                market_mode="quantity",
                market_quantity=1,
                market_price=0,
                market_action="sell",
            )
            if verb == "продать" and len(args) == 3:
                flow = replace(
                    flow,
                    market_quantity=int(args[1]),
                    market_price=int(args[2]),
                    market_mode="confirm",
                )
        elif verb == "количество" or (
            flow.market_mode == "quantity" and verb.lstrip("+-").isdecimal()
        ):
            if flow.market_mode != "quantity":
                raise ValueError("quantity context")
            value = int(args[0] if verb == "количество" else verb)
            if not 1 <= value <= service.content.market_rules.max_quantity:
                raise ValueError("quantity")
            flow = replace(flow, market_quantity=value, market_mode="price")
        elif verb == "цена" or (flow.market_mode == "price" and verb.lstrip("+-").isdecimal()):
            if flow.market_mode != "price":
                raise ValueError("price context")
            value = int(args[0] if verb == "цена" else verb)
            if not 1 <= value <= service.content.market_rules.max_price:
                raise ValueError("price")
            flow = replace(flow, market_price=value, market_mode="confirm")
        elif verb == "заказ":
            recipe_id = service.content.market_rules.order_recipes[0]
            flow = replace(
                flow,
                market_target=recipe_id,
                market_quantity=1,
                market_price=0,
                market_mode="price",
                market_action="order",
            )
            if args:
                price = int(args[0])
                if not 1 <= price <= service.content.market_rules.max_price:
                    raise ValueError("price")
                flow = replace(flow, market_price=price, market_mode="confirm")
        elif verb in {"лот", "купить", "резерв", "отменить", "вернуть"}:
            lot = next((one for one in state.lots if one.id == int(args[0])), None)
            if lot is None:
                raise ValueError("lot")
            flow = replace(
                flow,
                market_target=str(lot.id),
                market_price=lot.price,
                market_quantity=lot.quantity,
                market_mode="lot" if verb == "лот" else "confirm",
                market_action=verb,
            )
        elif verb in {"заказ-номер", "изготовить", "отменить-заказ"}:
            order = next((one for one in state.orders if one.id == int(args[0])), None)
            if order is None:
                raise ValueError("order")
            flow = replace(
                flow,
                market_target=str(order.id),
                market_price=order.price,
                market_mode="order" if verb == "заказ-номер" else "confirm",
                market_action=verb,
            )
        elif verb == "подтвердить":
            if flow.market_mode != "confirm":
                raise ValueError("confirmation")
            purpose = flow.market_action
            target = flow.market_target
            if purpose == "sell":
                result = await service.publish(
                    actor.id, target, flow.market_quantity, flow.market_price, now
                )
            elif purpose == "order":
                result = await service.order(actor.id, target, flow.market_price, now)
            elif purpose in {"купить", "резерв"}:
                result = await service.purchase(
                    actor.id, int(target), flow.market_price, now, reserve=purpose == "резерв"
                )
            elif purpose in {"отменить", "вернуть"}:
                result = await service.cancel(
                    actor.id, int(target), now, reservation=purpose == "вернуть"
                )
            elif purpose == "изготовить":
                result = await service.fulfill(actor.id, int(target), flow.market_price, now)
            elif purpose == "отменить-заказ":
                result = await service.cancel_order(actor.id, int(target), now)
            else:
                raise ValueError("action")
            notice = result.notice
            flow = replace(flow, market_mode="board", market_action="", market_target="")
        elif verb == "отчёт":
            flow = replace(flow, market_mode="report")
        elif verb == "":
            flow = replace(
                flow, market_mode="board", market_action="", market_target="", market_page=1
            )
        else:
            notice = "Выберите действие рынка кнопкой. Команды и цены приведены на экране."
    except ValueError, IndexError:
        notice = "Выбор не распознан. Используйте показанный номер и положительное целое число."
    return await show(flow, actor, service, notice)


async def show(
    flow: PlayState, actor: Character, service: Market, notice: str = ""
) -> tuple[PlayState, Screen]:
    content = service.content
    state = await service.load()
    lines = [*head("Рынок и ремесленные заказы.", notice)]
    rows: list[tuple[Label, ...]] = []
    choices: list[tuple[str, str]] = []

    def button(title: str, action: str) -> None:
        rows.append((label(title),))
        choices.append((title, action))

    mode = flow.market_mode
    if mode == "bag":
        items = list(await service.inventory.list_items(actor.id))
        pages = max(1, ceil(len(items) / 8))
        page = min(flow.market_page, pages)
        flow = replace(flow, market_page=page)
        lines.append(f"Сумка. Страница {page} из {pages}. Выберите вещь для продажи.")
        for index, owned in enumerate(items[(page - 1) * 8 : page * 8], 1):
            name = content.item(owned.item_id).name
            mark = owned.item_id.rpartition("!")[2] if "!" in owned.item_id else "партия"
            lines.append(
                f"Вещь {index}: {name}, {owned.quantity}. Номер вещи: {mark}. "
                f"Команда: /рынок вещь {index}."
            )
            button(f"Продать вещь {index}: {name}", f"вещь {index} {owned.item_id}")
        if not items:
            lines.append("В сумке нет вещей для продажи.")
    elif mode in {"quantity", "price"}:
        if flow.market_action == "sell":
            lines.append(f"Продажа: {content.item(flow.market_target).name}.")
        else:
            recipe = content.recipe(flow.market_target)
            lines.append(
                f"Заказ на изготовление: {content.item(recipe.output_id).name}, "
                f"не менее {recipe.output_count}."
            )
        if mode == "quantity":
            lines.append(
                f"Введите количество от 1 до {content.market_rules.max_quantity}: "
                "/рынок количество ЧИСЛО. Снаряжение продаётся по одному."
            )
        else:
            lines.append(
                f"Введите цену всей партии от 1 до {content.market_rules.max_price} золота: "
                "/рынок цена ЧИСЛО. Пошлина удерживается при расчёте."
            )
        rows.append((label("Отменить выбор"),))
    elif mode == "lot":
        lot = next(one for one in state.lots if str(one.id) == flow.market_target)
        lines.extend(
            (
                f"Лот {lot.id}: {content.item(lot.item_id).name}, {lot.quantity}.",
                f"Цена партии {lot.price} золота. Пошлина {trade_tax(lot.price)}. "
                f"Состояние: {_status(lot.status)}.",
            )
        )
        if lot.status in {"open", "reserved"}:
            if lot.seller_id == actor.id and lot.status == "open":
                button("Отменить продажу", f"отменить {lot.id}")
            elif lot.seller_id != actor.id and (lot.status == "open" or lot.buyer_id == actor.id):
                button("Купить этот лот", f"купить {lot.id}")
                if lot.status == "open":
                    button("Зарезервировать лот", f"резерв {lot.id}")
                else:
                    button("Снять резерв и вернуть оплату", f"вернуть {lot.id}")
        lines.append(
            f"Команды: /рынок купить {lot.id}, /рынок резерв {lot.id}, "
            f"/рынок отменить {lot.id}, /рынок вернуть {lot.id}."
        )
    elif mode == "order":
        order = next(one for one in state.orders if str(one.id) == flow.market_target)
        recipe = content.recipe(order.recipe_id)
        lines.extend(
            (
                f"Заказ {order.id}: {content.item(recipe.output_id).name}, "
                f"не менее {recipe.output_count}.",
                f"Оплата: {order.price}. Мастеру: {order.price - trade_tax(order.price)}. "
                f"Пошлина: {trade_tax(order.price)} золота. Состояние: {_status(order.status)}.",
                "Материалы мастера: "
                + ", ".join(
                    f"{content.item(one.item_id).name}, {one.count}" for one in recipe.inputs
                )
                + ".",
            )
        )
        if order.status == "open":
            if order.buyer_id == actor.id:
                button("Отменить этот заказ", f"отменить-заказ {order.id}")
            else:
                button("Изготовить по заказу", f"изготовить {order.id}")
        lines.append(f"Команды: /рынок изготовить {order.id}, /рынок отменить-заказ {order.id}.")
    elif mode == "confirm":
        purpose = flow.market_action
        lines.extend(
            (
                f"Подтверждение: {_action(purpose)}.",
                f"Цена всей партии: {flow.market_price} золота. "
                f"Пошлина при покупке: {trade_tax(flow.market_price)}. "
                f"Получателю оплаты: {flow.market_price - trade_tax(flow.market_price)} золота.",
            )
        )
        if purpose == "sell":
            lines.append(
                f"Товар: {content.item(flow.market_target).name}, {flow.market_quantity}. "
                "Он уйдёт из сумки в резерв рынка."
            )
        elif purpose == "order":
            recipe = content.recipe(flow.market_target)
            lines.append(
                f"Изделие: {content.item(recipe.output_id).name}, не менее {recipe.output_count}. "
                "Оплата уйдёт в резерв до выполнения или возврата."
            )
        else:
            lines.append(
                f"Номер объявления: {flow.market_target}. Количество: {flow.market_quantity}."
            )
            if purpose in {"купить", "резерв"}:
                lot = next(one for one in state.lots if str(one.id) == flow.market_target)
                lines.append(f"Товар: {content.item(lot.item_id).name}.")
            if purpose == "изготовить":
                order = next(one for one in state.orders if str(one.id) == flow.market_target)
                recipe = content.recipe(order.recipe_id)
                lines.append(
                    "Материалы будут потрачены: "
                    + ", ".join(
                        f"{content.item(one.item_id).name}, {one.count}" for one in recipe.inputs
                    )
                    + ". Изделия сразу получит заказчик."
                )
        lines.append("Для выполнения: /рынок подтвердить. Для отмены выбора: /рынок.")
        rows.append((label("Подтвердить сделку"), label("Отменить выбор")))
    elif mode == "report":
        lines.append(
            "Наблюдение рынка за всё время: предложение в штуках, спрос в заказах, "
            "получено покупателями в штуках."
        )
        observations = supply_and_demand(state, {one.id: one.output_id for one in content.recipes})
        pages = max(1, ceil(len(observations) / 8))
        page = min(flow.market_page, pages)
        flow = replace(flow, market_page=page)
        lines.append(f"Страница {page} из {pages}.")
        for item, offered, asked, sold in observations[(page - 1) * 8 : page * 8]:
            lines.append(
                f"{content.item(item).name}: предложение {offered}; спрос {asked}; куплено {sold}."
            )
        if not observations:
            lines.append("Объявлений и покупок пока нет.")
    else:
        visible = [
            (one.id, "лот", one.item_id, one.price, one.status)
            for one in state.lots
            if one.status in {"open", "reserved"} or actor.id in {one.seller_id, one.buyer_id}
        ]
        visible.extend(
            (one.id, "заказ-номер", content.recipe(one.recipe_id).output_id, one.price, one.status)
            for one in state.orders
            if one.status == "open" or actor.id in {one.buyer_id, one.maker_id}
        )
        visible.sort(reverse=True)
        pages = max(1, ceil(len(visible) / 8))
        page = min(flow.market_page, pages)
        flow = replace(flow, market_page=page)
        lines.append(f"Объявления. Страница {page} из {pages}.")
        for number, kind, item, price, status in visible[(page - 1) * 8 : page * 8]:
            title = "Лот" if kind == "лот" else "Заказ"
            lines.append(
                f"{title} {number}: {content.item(item).name}. Цена партии {price} золота. "
                f"{_status(status)}. Команда: /рынок {kind} {number}."
            )
            button(f"{title} {number}: {content.item(item).name}", f"{kind} {number}")
        if not visible:
            lines.append("Объявлений пока нет. Можно продать вещь или заказать изготовление.")
        lines.extend(
            (
                f"Продажа и заказ действуют {content.market_rules.lifetime // 86400} суток. "
                "Резерв покупателя — до "
                f"{content.market_rules.reservation_lifetime // 3600} часов "
                "и не дольше срока лота. По истечении ценности возвращаются "
                "при следующем обращении к рынку или запуске игры.",
                "Для общего дела можно заказать изделия мастеру, затем передать их городу: "
                "/событие снабдить. Предложения между игроками облагаются общей пошлиной.",
                "Команды: /рынок сумка, /рынок заказ, /рынок отчёт, /рынок дальше, /рынок раньше.",
            )
        )
        rows.extend(
            (
                (label("Продать вещь на рынке"), label("Заказать изготовление")),
                (label("Отчёт рынка"),),
            )
        )
    if mode in {"board", "bag", "report"}:
        rows.append((label("Предыдущая страница рынка"), label("Следующая страница рынка")))
    rows.append((label("Обновить рынок"),))
    flow = replace(flow, market_choices=tuple(choices)).with_notice(notice)
    return flow, Screen(id=ScreenId.MARKET, lines=tuple(lines), rows=tuple(rows))


def _status(value: str) -> str:
    return {
        "open": "открыто",
        "reserved": "в резерве",
        "sold": "куплено",
        "filled": "изготовлено",
        "cancelled": "отменено",
        "expired": "срок завершён",
    }.get(value, value)


def _action(value: str) -> str:
    return {
        "sell": "выставить товар",
        "order": "заказать изготовление",
        "купить": "купить товар",
        "резерв": "зарезервировать товар и оплату",
        "отменить": "вернуть товар продавцу",
        "вернуть": "снять резерв и вернуть оплату",
        "изготовить": "изготовить и передать заказ",
        "отменить-заказ": "отменить заказ и вернуть оплату",
    }.get(value, value)
