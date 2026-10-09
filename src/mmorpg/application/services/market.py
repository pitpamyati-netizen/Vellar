"""Резерв, расчёт, возврат и ремесло выполняются общей операцией M01."""

from dataclasses import replace

from pydantic import TypeAdapter

from mmorpg import economy_log
from mmorpg.application.operations import MissingResourceError, atomic_action, current_operation
from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.item_instance import instance_ids
from mmorpg.domain.entities.market import CraftOrder, Lot, MarketResult, MarketState
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository, StateCache
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import crafts, market, quests
from mmorpg.domain.rules.economy import trade_tax

CODEC = TypeAdapter(MarketState)
KEY = "market:board"


def receipt() -> str:
    operation = current_operation()
    assert operation is not None
    return operation.id


class Market:
    def __init__(
        self,
        content: GameContent,
        cache: StateCache,
        characters: CharacterRepository,
        inventory: InventoryRepository,
    ) -> None:
        self.content = content
        self._cache = cache
        self.characters = characters
        self.inventory = inventory

    async def load(self) -> MarketState:
        raw = await self._cache.get(KEY)
        return CODEC.validate_json(raw) if raw else MarketState()

    async def save(self, state: MarketState) -> None:
        await self._cache.set(KEY, CODEC.dump_json(state).decode(), 10**12)

    async def _available(self, actor_id: int) -> bool:
        return bool(await self.characters.get(actor_id)) and not await BattleStore(
            self._cache
        ).busy(actor_id)

    @atomic_action
    async def sweep(self, now: int) -> int:
        state = await self.load()
        changed = 0
        for lot in state.lots:
            if lot.status not in {"open", "reserved"}:
                continue
            if lot.expires <= now:
                if await self.characters.get(lot.seller_id) is None:
                    raise MissingResourceError("market escrow owner disappeared")
                if lot.status == "reserved":
                    if await self.characters.get(lot.buyer_id) is None:
                        raise MissingResourceError("market deposit owner disappeared")
                    await self.characters.grant_gold(lot.buyer_id, lot.price)
                await self.inventory.add(lot.seller_id, lot.item_id, lot.quantity)
                state = market.change_lot(
                    state, replace(lot, status="expired", receipts=(*lot.receipts, receipt()))
                )
                changed += 1
            elif lot.status == "reserved" and lot.reserved_until <= now:
                if await self.characters.get(lot.buyer_id) is None:
                    raise MissingResourceError("market deposit owner disappeared")
                await self.characters.grant_gold(lot.buyer_id, lot.price)
                state = market.change_lot(
                    state,
                    replace(
                        lot,
                        status="open",
                        buyer_id=0,
                        reserved_until=0,
                        receipts=(*lot.receipts, receipt()),
                    ),
                )
                changed += 1
        for order in state.orders:
            if order.status == "open" and order.expires <= now:
                if await self.characters.get(order.buyer_id) is None:
                    raise MissingResourceError("market order owner disappeared")
                await self.characters.grant_gold(order.buyer_id, order.price)
                state = market.change_order(
                    state, replace(order, status="expired", receipts=(*order.receipts, receipt()))
                )
                changed += 1
        if changed:
            await self.save(state)
        return changed

    @atomic_action
    async def publish(
        self, actor_id: int, item_id: str, quantity: int, price: int, now: int
    ) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        if not await self._available(actor_id):
            return MarketResult("Сначала завершите бой и вернитесь к рынку.")
        if reason := market.refusal(self.content.market_rules, state, actor_id, price, quantity):
            return MarketResult(reason)
        if not self.content.has_item(item_id):
            return MarketResult("Такой вещи нет. Выберите вещь из сумки.")
        item = self.content.item(item_id)
        if item.is_equipment and (len(instance_ids(item_id)) != 1 or quantity != 1):
            return MarketResult("Снаряжение продаётся по одному, с номером вещи из сумки.")
        if not await self.inventory.remove(actor_id, item_id, quantity):
            return MarketResult("Вещь уже недоступна в сумке. Обновите список.")
        lot = Lot(
            state.next_id,
            actor_id,
            item_id,
            quantity,
            price,
            now + self.content.market_rules.lifetime,
            receipts=(receipt(),),
        )
        await self.save(replace(state, next_id=state.next_id + 1, lots=(*state.lots, lot)))
        return MarketResult(
            f"Лот {lot.id} опубликован. Товар передан в резерв. Цена партии: {price} золота.",
            lot.id,
            True,
        )

    @atomic_action
    async def purchase(
        self, actor_id: int, lot_id: int, expected_price: int, now: int, *, reserve: bool = False
    ) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        lot = next((one for one in state.lots if one.id == lot_id), None)
        if not await self._available(actor_id):
            return MarketResult("Сначала завершите бой.")
        if lot is None or lot.status not in {"open", "reserved"}:
            return MarketResult("Лот уже закрыт. Обновите рынок.")
        if lot.seller_id == actor_id:
            return MarketResult("Свои лоты покупать нельзя. Можно отменить продажу.")
        if lot.price != expected_price:
            return MarketResult("Цена не совпадает с показанной. Откройте лот заново.")
        if lot.status == "reserved":
            if lot.buyer_id != actor_id or reserve:
                return MarketResult("Лот уже зарезервирован. Проверьте другие предложения.")
        elif not await self.characters.spend_gold(actor_id, lot.price):
            return MarketResult("Для покупки не хватает золота в кошельке.")
        if await self.characters.get(lot.seller_id) is None:
            raise MissingResourceError("market seller disappeared")
        if reserve:
            updated = replace(
                lot,
                status="reserved",
                buyer_id=actor_id,
                reserved_until=min(
                    lot.expires, now + self.content.market_rules.reservation_lifetime
                ),
                receipts=(*lot.receipts, receipt()),
            )
            await self.save(market.change_lot(state, updated))
            return MarketResult(
                "Товар и оплата зарезервированы. Купите лот или снимите резерв; "
                "после срока оплата вернётся.",
                lot.id,
                True,
            )
        tax = trade_tax(lot.price)
        await self.inventory.add(actor_id, lot.item_id, lot.quantity)
        await self.characters.grant_gold(lot.seller_id, lot.price - tax)
        await self.save(
            market.change_lot(
                state,
                replace(
                    lot,
                    status="sold",
                    buyer_id=actor_id,
                    tax=tax,
                    receipts=(*lot.receipts, receipt()),
                ),
            )
        )
        economy_log.record(
            economy_log.TRADE_PRICE,
            lot.price - tax,
            character_id=lot.seller_id,
            detail=f"market lot {lot.id}",
        )
        economy_log.record(
            economy_log.TRADE_DUTY, -tax, character_id=actor_id, detail=f"market lot {lot.id}"
        )
        return MarketResult(
            f"Лот {lot.id} куплен. Получено: {lot.quantity}. Оплата: {lot.price}; "
            f"продавцу: {lot.price - tax}; пошлина: {tax} золота.",
            lot.id,
            True,
        )

    @atomic_action
    async def cancel(
        self, actor_id: int, lot_id: int, now: int, *, reservation: bool = False
    ) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        lot = next((one for one in state.lots if one.id == lot_id), None)
        if lot is None:
            return MarketResult("Лот не найден.")
        if reservation and lot.status == "reserved" and lot.buyer_id == actor_id:
            await self.characters.grant_gold(actor_id, lot.price)
            updated = replace(
                lot,
                status="open",
                buyer_id=0,
                reserved_until=0,
                receipts=(*lot.receipts, receipt()),
            )
            notice = f"Резерв снят. Возвращено {lot.price} золота. Лот снова открыт."
        elif not reservation and lot.status == "open" and lot.seller_id == actor_id:
            await self.inventory.add(actor_id, lot.item_id, lot.quantity)
            updated = replace(lot, status="cancelled", receipts=(*lot.receipts, receipt()))
            notice = "Продажа отменена. Вся партия возвращена в сумку."
        else:
            return MarketResult("Это действие недоступно: проверьте владельца и состояние лота.")
        await self.save(market.change_lot(state, updated))
        return MarketResult(notice, lot.id, True)

    @atomic_action
    async def order(self, actor_id: int, recipe_id: str, price: int, now: int) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        if not await self._available(actor_id):
            return MarketResult("Сначала завершите бой.")
        if recipe_id not in self.content.market_rules.order_recipes:
            return MarketResult("Такого заказа нет в перечне рынка.")
        if reason := market.refusal(self.content.market_rules, state, actor_id, price, 1):
            return MarketResult(reason)
        if not await self.characters.spend_gold(actor_id, price):
            return MarketResult("Для заказа не хватает золота.")
        order = CraftOrder(
            state.next_id,
            actor_id,
            recipe_id,
            price,
            now + self.content.market_rules.lifetime,
            receipts=(receipt(),),
        )
        await self.save(replace(state, next_id=state.next_id + 1, orders=(*state.orders, order)))
        return MarketResult(
            f"Заказ {order.id} открыт. Оплата {price} золота "
            "зарезервирована до изготовления или отмены.",
            order.id,
            True,
        )

    @atomic_action
    async def cancel_order(self, actor_id: int, order_id: int, now: int) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        order = next((one for one in state.orders if one.id == order_id), None)
        if order is None or order.status != "open" or order.buyer_id != actor_id:
            return MarketResult("Заказ уже закрыт или принадлежит другому игроку.")
        await self.characters.grant_gold(actor_id, order.price)
        await self.save(
            market.change_order(
                state, replace(order, status="cancelled", receipts=(*order.receipts, receipt()))
            )
        )
        return MarketResult(f"Заказ отменён. Возвращено {order.price} золота.", order.id, True)

    @atomic_action
    async def fulfill(
        self, actor_id: int, order_id: int, expected_price: int, now: int
    ) -> MarketResult:
        await self.sweep(now)
        state = await self.load()
        order = next((one for one in state.orders if one.id == order_id), None)
        actor = await self.characters.get(actor_id)
        if actor is None or not await self._available(actor_id):
            return MarketResult("Сначала завершите бой.")
        if order is None or order.status != "open":
            return MarketResult("Заказ уже закрыт. Обновите рынок.")
        if order.buyer_id == actor_id:
            return MarketResult("Свой заказ выполнять нельзя.")
        if order.price != expected_price:
            return MarketResult("Оплата не совпадает с показанной. Откройте заказ заново.")
        if await self.characters.get(order.buyer_id) is None:
            raise MissingResourceError("market buyer disappeared")
        recipe = self.content.recipe(order.recipe_id)
        owned = {one.item_id: one.quantity for one in await self.inventory.list_items(actor_id)}
        worked, made = crafts.make(
            self.content, actor, recipe, owned, seed=derive("market-order", order.id, actor_id)
        )
        if not made.ok:
            return MarketResult(made.refused)
        for item, count in made.spent:
            if not await self.inventory.remove(actor_id, item, -count):
                raise MissingResourceError("market crafting material disappeared")
        log, _steps = quests.record_craft(
            self.content, worked, made.item_id, made.count, city_id=actor.city_id
        )
        tax = trade_tax(order.price)
        await self.characters.save(replace(worked.with_gold(order.price - tax), quests=log))
        await self.inventory.add(order.buyer_id, made.item_id, made.count)
        await self.save(
            market.change_order(
                state,
                replace(
                    order,
                    status="filled",
                    maker_id=actor_id,
                    item_id=made.item_id,
                    quantity=made.count,
                    tax=tax,
                    receipts=(*order.receipts, receipt()),
                ),
            )
        )
        economy_log.record(
            economy_log.TRADE_PRICE,
            order.price - tax,
            character_id=actor_id,
            detail=f"market order {order.id}",
        )
        economy_log.record(
            economy_log.TRADE_DUTY,
            -tax,
            character_id=order.buyer_id,
            detail=f"market order {order.id}",
        )
        return MarketResult(
            f"Заказ {order.id} изготовлен и передан заказчику: {made.count}. "
            f"Получено {order.price - tax}; пошлина {tax} золота.",
            order.id,
            True,
        )
