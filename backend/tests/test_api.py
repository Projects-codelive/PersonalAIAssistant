"""HTTP API behaviour: chat routing, per-client sessions, honest call/message actions, manual dialer."""
import threading

import pytest

from app.agent.gemma_agent import agent


@pytest.fixture
def fake_chat(monkeypatch):
    """Replace the agent with a stub; records the session id and the thread it ran on"""
    seen = {}

    async def chat(message, session_id="default"):
        seen.update(message=message, session_id=session_id, thread=threading.current_thread().name)
        return seen.get("result", {"reply": "ok", "tool_calls": []})

    monkeypatch.setattr(agent, "chat", chat)
    return seen


def chat(client, **body):
    return client.post("/api/chat", json={"voice_response": False, **body}).json()


def test_chat_runs_on_agent_worker_thread(make_client, fake_chat):
    assert chat(make_client(), message="hello", session_id="s1")["reply"] == "ok"
    assert fake_chat["thread"] == "agent-worker"


def test_chat_without_session_gets_per_client_session(make_client, fake_chat):
    chat(make_client(client_ip="127.0.0.1"), message="hi")
    assert fake_chat["session_id"] == "client_127.0.0.1"
    chat(make_client(client_ip="127.0.0.1"), message="hi", session_id="default")
    assert fake_chat["session_id"] == "client_127.0.0.1"


def test_empty_message_rejected(make_client):
    assert make_client().post("/api/chat", json={"message": "   "}).status_code == 400


@pytest.mark.parametrize("result, expected_status", [
    ("SUCCESS: Phone call placed to Alice at +15550192834.", "SUCCESS"),
    ("NOT_CONFIRMED: Opened the dialer for Alice at +15550192834, but could not confirm", "NOT_CONFIRMED"),
    ("DISPATCHED: Sent the call to Alice at +15550192834 to your connected device to dial.", "DISPATCHED"),
])
def test_call_action_reflects_real_outcome(make_client, fake_chat, result, expected_status):
    fake_chat["result"] = {"reply": "x", "tool_calls": [
        {"tool": "make_phone_call", "args": {"phone_number": "+15550192834", "contact_name": "Alice"}, "result": result}]}
    body = chat(make_client(), message="yes", session_id="s")
    assert body["call_action"]["status"] == expected_status
    assert body["call_action"]["phone_number"] == "+15550192834"


def test_failed_call_has_no_call_action(make_client, fake_chat):
    fake_chat["result"] = {"reply": "x", "tool_calls": [
        {"tool": "make_phone_call", "args": {"phone_number": "+1555"}, "result": "FAILED: No phone is connected"}]}
    assert chat(make_client(), message="yes", session_id="s")["call_action"] is None


@pytest.mark.parametrize("tool, expected_type", [
    ("send_whatsapp_message", "WHATSAPP"), ("send_sms", "SMS"), ("send_email", "EMAIL")])
def test_message_action_carries_status(make_client, fake_chat, tool, expected_type):
    fake_chat["result"] = {"reply": "x", "tool_calls": [
        {"tool": tool, "args": {"phone_number": "+1555", "to_email": "a@b.com", "message": "hi"},
         "result": "NOT_CONFIRMED: Opened the composer"}]}
    action = chat(make_client(), message="yes", session_id="s")["message_action"]
    assert action["type"] == expected_type and action["status"] == "NOT_CONFIRMED"


def test_manual_dialer_without_phone_reports_error(make_client, fake_bridge):
    fake_bridge.recipients = 0
    body = make_client().post("/api/device/call", json={"phone_number": "+15550192834"}).json()
    assert body["status"] == "error"


def test_manual_dialer_uses_one_path(make_client, fake_phone, fake_bridge):
    body = make_client().post("/api/device/call", json={"phone_number": "+15550192834"}).json()
    assert body["status"] == "success"
    dial = [c for c in fake_phone.commands if isinstance(c, str) and "action.DIAL" in c]
    assert len(dial) == 1
    assert fake_bridge.events == []  # ADB opened the dialer, so the app is not told to open it again
