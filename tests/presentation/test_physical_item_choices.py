"""Кнопки одинаковых вещей выбирают точный экземпляр, старый выбор не меняет цель."""

from mmorpg.presentation.telegram.screens.paginated import PageState
from mmorpg.presentation.telegram.screens.shop import OwnedItem, inventory_screen, owned_from_button


def test_two_identical_items_have_distinct_readable_buttons(content):
    owned = (OwnedItem("sword@1#common!1", 1), OwnedItem("sword@1#common!2", 1))
    screen = inventory_screen(content, owned, PageState(), gold=10)
    buttons = [button for row in screen.rows for button in row if "Экземпляр" in button.text]
    assert len(buttons) == 2 and buttons[0].text != buttons[1].text
    for button, entry in zip(buttons, owned, strict=True):
        assert owned_from_button(content, button.text, owned).id == entry.item_id
    assert owned_from_button(content, buttons[0].text, owned[1:]) is None
