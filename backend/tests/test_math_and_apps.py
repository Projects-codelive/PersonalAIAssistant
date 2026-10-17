"""Spoken math parsing and app-name → installed package resolution across phone brands."""
import pytest

from app.agent.tools.screen_tools import open_app
from app.agent.universal_mobile_agent import UniversalMobileAgent
from app.device.adb_controller import adb_controller


@pytest.mark.parametrize("text, expected", [
    ("25 multiplied by 4", ("25 * 4", 100.0)),
    ("6 times 7", ("6 * 7", 42.0)),
    ("10 divided by 2", ("10 / 2", 5.0)),
    ("8 plus 2", ("8 + 2", 10.0)),
    ("9 minus 3", ("9 - 3", 6.0)),
    ("addition of 2 and 3", ("2 + 3", 5.0)),
    ("subtract 5 from 10", ("10 - 5", 5.0)),
    ("25 * 4", ("25 * 4", 100.0)),
    ("2 aur 3 ko jodo", ("2 + 3", 5.0)),
    ("7 divided by 0", ("7 / 0", 0.0)),
])
def test_parse_math_expression(text, expected):
    assert UniversalMobileAgent.parse_math_expression(text) == expected


@pytest.mark.parametrize("text", ["call mom", "send 2 messages to alice", "open whatsapp", "hi agent"])
def test_non_math_is_not_parsed(text):
    assert UniversalMobileAgent.parse_math_expression(text) is None


PIXEL = "package:com.google.android.calculator\npackage:com.google.android.deskclock\npackage:com.whatsapp\n"
SAMSUNG = "package:com.sec.android.app.popupcalculator\npackage:com.sec.android.app.clockpackage\n"


@pytest.mark.parametrize("installed, name, expected", [
    (SAMSUNG, "calculator", "com.sec.android.app.popupcalculator"),  # mapped package installed
    (PIXEL, "calculator", "com.google.android.calculator"),          # Samsung map → Pixel equivalent
    (PIXEL, "clock", "com.google.android.deskclock"),
    (PIXEL, "WhatsApp", "com.whatsapp"),
    ("package:org.example.superweather\n", "superweather", "org.example.superweather"),  # fuzzy fallback
])
def test_package_resolution(fake_phone, installed, name, expected):
    fake_phone.shell_output["pm list packages"] = installed
    assert adb_controller.get_package_for_app_name(name) == expected


def test_open_app_reports_missing_app(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    assert adb_controller.open_app("instagram") is False
    assert not any(c == ["app_start", "com.instagram.android"] for c in fake_phone.commands)


async def test_open_app_tool_does_not_claim_success_for_missing_app(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    result = await open_app.ainvoke({"app_name": "instagram"})
    assert result.startswith("Error")


def test_open_app_launches_resolved_package(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    assert adb_controller.open_app("calculator") is True
    assert ["app_start", "com.google.android.calculator"] in fake_phone.commands


def test_press_key_accepts_raw_keycodes(fake_phone):
    adb_controller.press_key(66)          # the web-search flow passes an int
    adb_controller.press_key("enter")
    assert fake_phone.commands[-2:] == [["input", "keyevent", "66"], ["input", "keyevent", "66"]]


@pytest.mark.parametrize("text", ["tell me a fun fact about cricket", "ask me anything", "tell us a joke"])
async def test_questions_to_the_assistant_are_not_treated_as_messages(text, session_id):
    from app.agent.gemma_agent import agent
    result = await agent.run_hybrid_intent_analysis(text, session_id=session_id)
    assert "Do you want to send a WhatsApp message, SMS, or Email" not in result.get("reply", "")
    assert agent.pending_inputs.get(session_id) is None


@pytest.mark.parametrize("text, app, query", [
    ("buy a pen", "flipkart", "pen"),                                   # no store named -> default store
    ("buy a pen from flip kart", "flipkart", "pen"),                    # voice-typed store name
    ("Buy a pen from Flipcart", "flipkart", "pen"),
    ("order 2 notebooks", "flipkart", "notebooks"),
    ("mujhe ek pen kharidna hai", "flipkart", "pen"),
    ("amazone se charger mangwao", "amazon", "charger"),
    ("flipkart se sabse sasta pen dhundho", "flipkart", "pen"),         # the help-menu example
    ("फ्लिपकार्ट से पेन खरीदो", "flipkart", "पेन"),
    ("मुझे एक पेन चाहिए", "flipkart", "पेन"),
    ("buy a pen fron flipkart", "flipkart", "pen"),                     # typo of "from" (seen on WhatsApp)
    ("buy a pen frm flip kart", "flipkart", "pen"),
])
def test_shopping_requests_are_recognised(text, app, query):
    from app.agent.ecommerce_agent import ecommerce_agent
    assert ecommerce_agent.is_ecommerce_intent(text)
    parsed = ecommerce_agent.parse_ecommerce_intent(text)
    assert (parsed["app"], parsed["query"]) == (app, query)


@pytest.mark.parametrize("text", [
    "order status", "where is my order", "book a cab", "call mom", "buy",
    "send whatsapp to alice saying buy milk", "what should I buy?", "which pen should I buy",
    "play a song", "search wikipedia for taj mahal",
])
def test_non_shopping_messages_are_not_hijacked(text):
    from app.agent.ecommerce_agent import ecommerce_agent
    assert not ecommerce_agent.is_ecommerce_intent(text)


async def test_buy_a_pen_routes_to_the_shopping_flow(monkeypatch, session_id):
    # End-to-end routing through the agent (the phone flow itself is replaced by a recorder)
    from app.agent.ecommerce_agent import ecommerce_agent
    from app.agent.gemma_agent import agent
    calls = []

    async def fake_flow(session_id, app_name, query, criteria):
        calls.append((app_name, query))
        return {"reply": "searching", "tool_calls": []}

    monkeypatch.setattr(ecommerce_agent, "run_autonomous_shopping_flow", fake_flow)
    await agent.run_hybrid_intent_analysis("buy a pen from flip kart", session_id=session_id)
    assert calls == [("flipkart", "pen")]
