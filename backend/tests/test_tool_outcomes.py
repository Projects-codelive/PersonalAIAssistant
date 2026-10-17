"""Calls and messages report what really happened, and each action is dispatched exactly once."""
import pytest

from app.agent.gemma_agent import agent
from app.agent.tools.messaging_tools import send_sms, send_whatsapp_message, send_email
from app.agent.tools.phone_tools import make_phone_call
from app.device.adb_controller import adb_controller

SMS_ARGS = {"phone_number": "+15550192834", "message": "hi there", "contact_name": "Alice"}
EMAIL_ARGS = {"to_email": "alice@example.com", "message": "hi there", "subject": "Hello"}
CALL_ARGS = {"phone_number": "+15550192834", "contact_name": "Alice"}

SEND_BUTTON = ("", "Send", "com.whatsapp:id/send", True)
CALL_BUTTON = ("", "Call", "com.google.android.dialer:id/dialpad_floating_action_button", True)

MESSAGE_TOOLS = [
    pytest.param(send_sms, SMS_ARGS, id="sms"),
    pytest.param(send_whatsapp_message, SMS_ARGS, id="whatsapp"),
    pytest.param(send_email, EMAIL_ARGS, id="email"),
]


def taps(dev):
    return [c for c in dev.commands if isinstance(c, str) and c.startswith("input tap")]


@pytest.fixture(autouse=True)
def no_call_watcher(monkeypatch):
    async def noop(number):
        return None
    monkeypatch.setattr(adb_controller, "watch_call_termination", noop)


@pytest.mark.parametrize("tool, args", MESSAGE_TOOLS)
async def test_message_sent_when_send_button_tapped(tool, args, fake_phone, fake_bridge):
    fake_phone.show(SEND_BUTTON)
    result = await tool.ainvoke(args)
    assert result.startswith("SUCCESS")
    assert len(taps(fake_phone)) == 1
    assert fake_bridge.events == []  # ADB handled it: no second dispatch over websocket


@pytest.mark.parametrize("tool, args", MESSAGE_TOOLS)
async def test_message_not_confirmed_without_send_button(tool, args, fake_phone, fake_bridge):
    fake_phone.show()  # composer opened but no Send button visible
    result = await tool.ainvoke(args)
    assert result.startswith("NOT_CONFIRMED")
    assert "tap Send" in result
    assert fake_bridge.events == []


@pytest.mark.parametrize("tool, args", MESSAGE_TOOLS)
async def test_message_dispatched_to_device_app_without_adb(tool, args, fake_bridge):
    result = await tool.ainvoke(args)
    assert result.startswith("DISPATCHED")
    assert len(fake_bridge.events) == 1


@pytest.mark.parametrize("tool, args", MESSAGE_TOOLS)
async def test_message_failed_when_nothing_connected(tool, args, fake_bridge):
    fake_bridge.recipients = 0
    result = await tool.ainvoke(args)
    assert result.startswith("FAILED")


async def test_call_placed_once_when_call_button_tapped(fake_phone, fake_bridge):
    fake_phone.show(CALL_BUTTON)
    result = await make_phone_call.ainvoke(CALL_ARGS)
    assert result.startswith("SUCCESS")
    dial_intents = [c for c in fake_phone.commands if isinstance(c, str) and "android.intent.action.DIAL" in c]
    assert len(dial_intents) == 1
    assert len(taps(fake_phone)) == 1
    assert fake_bridge.events == []


async def test_call_not_confirmed_without_call_button(fake_phone, fake_bridge):
    fake_phone.show()
    result = await make_phone_call.ainvoke(CALL_ARGS)
    assert result.startswith("NOT_CONFIRMED")
    assert "tap Call" in result


async def test_call_failed_when_dialer_cannot_open(fake_phone, fake_bridge, monkeypatch):
    monkeypatch.setattr(adb_controller, "open_mobile_dialer", lambda n: False)
    result = await make_phone_call.ainvoke(CALL_ARGS)
    assert result.startswith("FAILED")
    assert taps(fake_phone) == []


async def test_call_dispatched_once_to_device_app_without_adb(fake_bridge):
    result = await make_phone_call.ainvoke(CALL_ARGS)
    assert result.startswith("DISPATCHED")
    assert [e[0] for e in fake_bridge.events] == ["TRIGGER_PHONE_CALL"]


async def test_call_failed_when_nothing_connected(fake_bridge):
    fake_bridge.recipients = 0
    assert (await make_phone_call.ainvoke(CALL_ARGS)).startswith("FAILED")


async def test_agent_reply_only_claims_sent_on_success(fake_phone, fake_bridge, session_id):
    pending = {"action": "send_sms", "channel": "SMS", "display_target": "Alice", "message": "hi there", "args": SMS_ARGS}

    fake_phone.show()  # Send button missing
    agent.pending_confirmations[session_id] = dict(pending)
    result = await agent.run_hybrid_intent_analysis("yes", session_id=session_id)
    assert "Sent" not in result["reply"]
    assert "could not confirm" in result["reply"]

    fake_phone.show(SEND_BUTTON)
    agent.pending_confirmations[session_id] = dict(pending)
    result = await agent.run_hybrid_intent_analysis("yes", session_id=session_id)
    assert result["reply"].startswith("Sent SMS")
