"""Shopping never claims a product, an order or a checkout that the phone screen doesn't show."""
import pytest

from app.agent.ecommerce_agent import ecommerce_agent
from app.agent.task_checkpoint_manager import task_checkpoint_manager
from app.device.adb_controller import adb_controller

ITEM = {"title": "Some Pen (Pack of 5)", "price": "₹50"}


@pytest.fixture
def no_screenshots(monkeypatch):
    monkeypatch.setattr(adb_controller, "save_screenshot", lambda path: True)
    monkeypatch.setattr(adb_controller, "find_yellow_shopping_button", lambda: (540, 2200))
    monkeypatch.setattr(adb_controller, "is_keyboard_shown", lambda: False, raising=False)


def pending_order(session_id):
    ckpt = task_checkpoint_manager.create_or_update(session_id=session_id, goal="Order pen", target_app="flipkart",
                                                    stage="AWAITING_ORDER_CONFIRMATION", query="pen", criteria="lowest_price")
    ckpt.selected_item = dict(ITEM)


async def test_search_with_phone_but_unreadable_results_offers_no_fake_item(fake_phone, no_screenshots, session_id):
    fake_phone.shell_output["pm list packages"] = "package:com.flipkart.android\n"
    fake_phone.show()  # store opened but nothing readable on screen
    result = await ecommerce_agent.run_autonomous_shopping_flow(session_id=session_id, app_name="flipkart",
                                                               query="pen", criteria="lowest_price")
    assert result["confirmation_required"] is False
    assert "couldn't read the product list" in result["reply"]
    assert "Cello" not in result["reply"]          # the old canned demo item must not appear


async def test_search_without_phone_is_labelled_demo(session_id):
    result = await ecommerce_agent.run_autonomous_shopping_flow(session_id=session_id, app_name="flipkart",
                                                               query="pen", criteria="lowest_price")
    assert "Demo mode" in result["reply"]


async def test_yes_never_taps_buy_or_pay(fake_phone, no_screenshots, session_id):
    pending_order(session_id)
    fake_phone.show(("Buy now", "", "", True), ("Place order", "", "", True))
    result = await ecommerce_agent.handle_order_confirmation(session_id, is_confirmed=True)
    assert "never place orders or make payments" in result["reply"]
    assert not [c for c in fake_phone.commands if "tap" in str(c) or "click" in str(c)]


def test_search_uses_the_store_search_link(fake_phone, no_screenshots, session_id):
    import asyncio
    fake_phone.shell_output["pm list packages"] = "package:com.flipkart.android\n"
    asyncio.run(ecommerce_agent.run_autonomous_shopping_flow(session_id=session_id, app_name="flipkart",
                                                           query="pen", criteria="lowest_price"))
    assert ["am", "start", "-a", "android.intent.action.VIEW", "-d", "https://www.flipkart.com/search?q=pen",
            "com.flipkart.android"] in fake_phone.commands


async def test_real_results_are_compared_and_handed_over_without_buying(fake_phone, no_screenshots, session_id):
    fake_phone.shell_output["pm list packages"] = "package:com.flipkart.android\n"
    fake_phone.show(
        ("Reynolds DFine Ball Pen Pack of 70, Ink Color - Blue, 76%, ₹700, ₹169", "", "", True),
        ("Samba Erasable Pen with Magic Wipe Eraser Gel Pen Pack of 6, Ink Color - Blue, 83%, ₹999, ₹167", "", "", True),
        ("PIERRE CARDIN Monza Black Exclusive Ball Pen, 20%, ₹225, ₹180", "", "", True),
        ("Buy at ₹171", "", "", True),
    )
    result = await ecommerce_agent.run_autonomous_shopping_flow(session_id=session_id, app_name="flipkart",
                                                               query="pen", criteria="lowest_price")
    assert result["confirmation_required"] is False
    assert "Cheapest" in result["reply"] and "₹" in result["reply"]
    assert "never place orders or make payments" in result["reply"]
    buy_node_x = 10 + 3 * 100 + 40  # center x of the "Buy at" node on the fake screen
    assert not [c for c in fake_phone.commands if isinstance(c, str) and c.startswith("input tap") and f" {buy_node_x} " in f" {c} "]


@pytest.mark.parametrize("label", ["Buy now", "BUY AT ₹171", "Place Order", "Pay ₹169", "Proceed to Checkout", "Pay with UPI"])
def test_tap_guard_refuses_purchase_buttons(fake_phone, label):
    fake_phone.show((label, "", "", True))
    assert adb_controller.click_element_by_text(label) is False
    assert not [c for c in fake_phone.commands if "tap" in str(c) or "click" in str(c)]


@pytest.mark.parametrize("label", ["Add to cart", "Search", "Calculator", "Sort & Filter"])
def test_tap_guard_allows_normal_buttons(label):
    from app.device.adb_controller import adb_controller as a
    assert not a.is_purchase_button(label)


async def test_tap_tool_explains_refusal(fake_phone):
    from app.agent.tools.screen_tools import tap_element
    result = await tap_element.ainvoke({"label_or_id": "Place order"})
    assert result.startswith("Refused to tap") and "never buy or pay" in result


async def test_no_cancels(session_id):
    pending_order(session_id)
    result = await ecommerce_agent.handle_order_confirmation(session_id, is_confirmed=False)
    assert "cancelled" in result["reply"]


@pytest.mark.parametrize("text", ["just click the buy button", "tap buy now on the mobile", "press place order", "click pay", "buy button daba do"])
async def test_requests_to_press_buy_are_refused_not_searched(monkeypatch, session_id, text):
    from app.agent.gemma_agent import agent
    started = []

    async def fake_flow(**kwargs):
        started.append(kwargs)
        return {"reply": "searching", "tool_calls": []}

    monkeypatch.setattr(ecommerce_agent, "run_autonomous_shopping_flow", fake_flow)
    result = await agent.run_hybrid_intent_analysis(text, session_id=session_id)
    assert started == []
    assert "never tap Buy" in result["reply"]
