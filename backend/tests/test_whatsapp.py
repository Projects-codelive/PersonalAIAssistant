"""WhatsApp Cloud API bridge: webhook verification, signatures, allowlist, dedupe, voice notes, replies."""
import asyncio
import hashlib
import hmac
import json
import uuid

import pytest

import app.whatsapp.webhook as webhook
from app.agent.gemma_agent import agent
from app.config import settings
from app.whatsapp.cloud_api import RecipientNotAllowed, whatsapp_client

ALLOWED = "15550001111"   # from tests/conftest.py
STRANGER = "15559998888"


def signed_post(client, payload, secret=None, signature=None):
    raw = json.dumps(payload).encode()
    if signature is None:
        digest = hmac.new((secret or settings.WHATSAPP_APP_SECRET).encode(), raw, hashlib.sha256).hexdigest()
        signature = f"sha256={digest}"
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = signature
    return client.post(webhook.WEBHOOK_PATH, content=raw, headers=headers)


def inbound(message, phone_number_id=None):
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID},
        "messages": [message]}}]}]}


def text_message(body, sender=ALLOWED, message_id=None):
    return {"from": sender, "id": message_id or f"wamid.{uuid.uuid4().hex}", "type": "text", "text": {"body": body}}


@pytest.fixture
def wa(monkeypatch):
    """Fake Cloud API: records replies/read receipts; collects scheduled handlers so tests can await them"""
    state = {"sent": [], "read": [], "scheduled": [], "agent_calls": [], "agent_reply": "Done ✅", "agent_error": None}

    async def fake_post(payload):
        if payload.get("type") == "text":
            state["sent"].append((payload["to"], payload["text"]["body"]))
        elif payload.get("type") == "interactive":
            buttons = [b["reply"]["id"] for b in payload["interactive"]["action"]["buttons"]]
            state["sent"].append((payload["to"], payload["interactive"]["body"]["text"], buttons))
        elif payload.get("status") == "read":
            state["read"].append(payload["message_id"])
        return {"messages": [{"id": "wamid.out"}]}

    async def fake_chat(message, session_id="default"):
        state["agent_calls"].append((message, session_id))
        if state["agent_error"]:
            raise state["agent_error"]
        if state.get("agent_delay"):
            await asyncio.sleep(state["agent_delay"])
        return state.get("agent_result") or {"reply": state["agent_reply"], "tool_calls": []}

    monkeypatch.setattr(whatsapp_client, "_post_message", fake_post)
    monkeypatch.setattr(agent, "chat", fake_chat)
    monkeypatch.setattr(webhook, "schedule", lambda coro: state["scheduled"].append(coro))
    monkeypatch.setattr(webhook, "_seen_message_ids", type(webhook._seen_message_ids)())

    async def run_scheduled():
        await asyncio.gather(*state["scheduled"])
        state["scheduled"].clear()

    state["run"] = run_scheduled
    return state


# --- Subscription handshake -------------------------------------------------------------------

def test_verify_handshake_returns_challenge(make_client):
    r = make_client().get(webhook.WEBHOOK_PATH, params={
        "hub.mode": "subscribe", "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN, "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


def test_verify_handshake_rejects_wrong_token(make_client):
    r = make_client().get(webhook.WEBHOOK_PATH, params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"})
    assert r.status_code == 403


# --- Signature ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [{"signature": ""}, {"signature": "sha256=deadbeef"}, {"secret": "attacker-secret"}])
async def test_unsigned_or_forged_webhooks_rejected(make_client, wa, kwargs):
    r = signed_post(make_client(client_ip="203.0.113.9"), inbound(text_message("call mom")), **kwargs)
    assert r.status_code == 401
    assert wa["scheduled"] == [] and wa["agent_calls"] == []


async def test_webhook_needs_no_api_token_but_api_still_does_through_tunnel(make_client, wa):
    # Through a tunnel requests arrive from localhost with the public Host header
    tunnel = make_client(client_ip="127.0.0.1", base_url="https://abc.trycloudflare.com")
    assert signed_post(tunnel, inbound(text_message("hi"))).status_code == 200
    await wa["run"]()
    assert tunnel.get("/api/status").status_code == 401


# --- Message handling --------------------------------------------------------------------------

async def test_text_command_runs_agent_and_replies_to_sender(make_client, wa):
    msg = text_message("open calculator")
    assert signed_post(make_client(), inbound(msg)).status_code == 200
    await wa["run"]()
    assert wa["agent_calls"] == [("open calculator", f"whatsapp_{ALLOWED}")]
    assert wa["read"] == [msg["id"]]
    assert wa["sent"] == [(ALLOWED, "Done ✅")]


async def test_non_allowlisted_sender_is_ignored_silently(make_client, wa):
    signed_post(make_client(), inbound(text_message("call mom", sender=STRANGER)))
    await wa["run"]()
    assert wa["agent_calls"] == [] and wa["sent"] == [] and wa["read"] == []


async def test_redelivered_message_runs_once(make_client, wa):
    msg = text_message("open calculator", message_id="wamid.same")
    client = make_client()
    signed_post(client, inbound(msg))
    signed_post(client, inbound(msg))
    await wa["run"]()
    assert len(wa["agent_calls"]) == 1 and len(wa["sent"]) == 1


async def test_message_for_another_business_number_ignored(make_client, wa):
    signed_post(make_client(), inbound(text_message("hi"), phone_number_id="999"))
    await wa["run"]()
    assert wa["agent_calls"] == []


async def test_status_updates_are_ignored(make_client, wa):
    payload = inbound(text_message("x"))
    value = payload["entry"][0]["changes"][0]["value"]
    value.pop("messages")
    value["statuses"] = [{"id": "wamid.out", "status": "delivered", "recipient_id": ALLOWED}]
    assert signed_post(make_client(), payload).status_code == 200
    assert wa["scheduled"] == []


async def test_voice_note_is_transcribed_then_run(make_client, wa, monkeypatch):
    from app.voice.elevenlabs_service import voice_service
    downloads = []

    async def fake_download(media_id):
        downloads.append(media_id)
        return b"OggS-fake-audio", "audio/ogg; codecs=opus"

    async def fake_stt(audio, filename="audio.wav", content_type=None):
        assert audio == b"OggS-fake-audio" and filename.endswith(".ogg")
        return "Mummy ko call karo"

    monkeypatch.setattr(whatsapp_client, "download_media", fake_download)
    monkeypatch.setattr(voice_service, "speech_to_text", fake_stt)
    msg = {"from": ALLOWED, "id": "wamid.voice1", "type": "audio", "audio": {"id": "media-123", "mime_type": "audio/ogg"}}
    signed_post(make_client(), inbound(msg))
    await wa["run"]()
    assert downloads == ["media-123"]
    assert wa["agent_calls"] == [("Mummy ko call karo", f"whatsapp_{ALLOWED}")]
    assert wa["sent"][0][1].startswith('🎙️ "Mummy ko call karo"')


async def test_unintelligible_voice_note_asks_again(make_client, wa, monkeypatch):
    async def no_audio(media_id):
        return None, None
    monkeypatch.setattr(whatsapp_client, "download_media", no_audio)
    signed_post(make_client(), inbound({"from": ALLOWED, "id": "wamid.v2", "type": "audio", "audio": {"id": "m"}}))
    await wa["run"]()
    assert wa["agent_calls"] == []
    assert "couldn't make out that voice note" in wa["sent"][0][1]


async def test_unsupported_message_type_gets_help(make_client, wa):
    signed_post(make_client(), inbound({"from": ALLOWED, "id": "wamid.img", "type": "image", "image": {"id": "x"}}))
    await wa["run"]()
    assert wa["agent_calls"] == []
    assert wa["sent"] == [(ALLOWED, webhook.UNSUPPORTED_REPLY)]


async def test_agent_failure_sends_apology(make_client, wa):
    wa["agent_error"] = RuntimeError("boom")
    signed_post(make_client(), inbound(text_message("open calculator")))
    await wa["run"]()
    assert wa["sent"] == [(ALLOWED, webhook.ERROR_REPLY)]


async def test_confirmation_flow_over_whatsapp_keeps_one_session(make_client, wa):
    client = make_client()
    signed_post(client, inbound(text_message("call Alice")))
    signed_post(client, inbound(text_message("yes")))
    await wa["run"]()
    assert {s for _, s in wa["agent_calls"]} == {f"whatsapp_{ALLOWED}"}


# --- Outbound guard ----------------------------------------------------------------------------

async def test_client_refuses_to_message_anyone_else():
    # No fake transport here: the guard must trip before any network call (the httpx tripwire would raise)
    with pytest.raises(RecipientNotAllowed):
        await whatsapp_client.send_text(STRANGER, "hello")
    with pytest.raises(RecipientNotAllowed):
        await whatsapp_client.send_text("+1 (555) 999-8888", "hello")


async def test_long_replies_are_truncated(wa):
    await whatsapp_client.send_text(f"+{ALLOWED}", "x" * 5000)
    to, body = wa["sent"][0]
    assert to == ALLOWED and len(body) == 4096


async def test_real_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="never call external services"):
        await whatsapp_client.send_text(ALLOWED, "hello")


# --- Smoother flow: help, buttons, progress, target policy -------------------------------------

async def test_help_menu_needs_no_agent(make_client, wa):
    signed_post(make_client(), inbound(text_message("Help")))
    await wa["run"]()
    assert wa["agent_calls"] == []
    assert wa["sent"][0][1] == webhook.HELP_REPLY


def allowed_call_pending():
    return {"reply": "Do you want to call +15550001111? Say 'yes' to call or 'no' to cancel.",
            "tool_calls": [], "confirmation_required": True,
            "pending_action": {"action": "make_phone_call", "args": {"phone_number": "+1 555-000-1111"}}}


async def test_confirmation_is_offered_with_yes_no_buttons(make_client, wa):
    wa["agent_result"] = allowed_call_pending()
    signed_post(make_client(), inbound(text_message("call 5550001111")))
    await wa["run"]()
    to, body, buttons = wa["sent"][0]
    assert to == ALLOWED and "Do you want to call" in body
    assert buttons == ["confirm_yes", "confirm_no"]


@pytest.mark.parametrize("button_id, expected", [("confirm_yes", "yes"), ("confirm_no", "no")])
async def test_tapping_a_button_sends_yes_or_no(make_client, wa, button_id, expected):
    msg = {"from": ALLOWED, "id": f"wamid.{button_id}", "type": "interactive",
           "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": "✅ Yes"}}}
    signed_post(make_client(), inbound(msg))
    await wa["run"]()
    assert wa["agent_calls"] == [(expected, f"whatsapp_{ALLOWED}")]


async def test_confirmation_for_other_number_is_refused_and_cleared(make_client, wa, session_id):
    wa["agent_result"] = {"reply": "Do you want to call Shyam (+919167078537)?", "tool_calls": [],
                          "confirmation_required": True,
                          "pending_action": {"action": "make_phone_call", "args": {"phone_number": "+919167078537"}}}
    agent.pending_confirmations[f"whatsapp_{ALLOWED}"] = {"action": "make_phone_call", "args": {}}
    signed_post(make_client(), inbound(text_message("call shyam")))
    await wa["run"]()
    assert wa["sent"][0][1].startswith("🔒 For safety")
    assert len(wa["sent"][0]) == 2  # plain text, no Yes/No buttons
    assert f"whatsapp_{ALLOWED}" not in agent.pending_confirmations


async def test_slow_task_sends_progress_then_result(make_client, wa, monkeypatch):
    monkeypatch.setattr(webhook, "PROGRESS_AFTER_SECONDS", 0.05)
    wa["agent_delay"] = 0.3
    signed_post(make_client(), inbound(text_message("flipkart se pen dhundho")))
    await wa["run"]()
    assert [body for _, body, *rest in wa["sent"]] == [webhook.PROGRESS_REPLY, "Done ✅"]


async def test_fast_task_sends_no_progress_message(make_client, wa):
    signed_post(make_client(), inbound(text_message("open calculator")))
    await wa["run"]()
    assert [body for _, body, *rest in wa["sent"]] == ["Done ✅"]


@pytest.mark.parametrize("tool, args, allowed", [
    ("make_phone_call", {"phone_number": "+1 (555) 000-1111"}, True),
    ("send_sms", {"phone_number": "5550001111"}, True),          # no country code, same 10 digits
    ("send_whatsapp_message", {"phone_number": "+919167078537"}, False),
    ("make_phone_call", {"phone_number": "911"}, False),
    ("send_email", {"to_email": "someone@example.com"}, False),  # email blocked unless allowlisted
    ("open_app", {"app_name": "calculator"}, True),               # non-communication tools unaffected
])
def test_target_policy(tool, args, allowed):
    assert (webhook.target_refusal(tool, args) is None) is allowed


async def test_tool_guard_blocks_execution_of_disallowed_pending_action(monkeypatch, session_id):
    # Real agent (no fake chat): a pending call to another number confirmed with "yes" must not dial
    ran = []

    async def fake_tool(args):
        ran.append(args)
        return "SUCCESS: called"

    from app.agent.tools.phone_tools import make_phone_call
    monkeypatch.setattr(make_phone_call, "coroutine", fake_tool, raising=False)
    agent.pending_confirmations[session_id] = {
        "action": "make_phone_call", "channel": "Call", "display_target": "Shyam",
        "args": {"phone_number": "+919167078537", "contact_name": "Shyam"}}
    result = await webhook.run_guarded_command("yes", session_id)
    assert ran == []
    assert result["reply"].startswith("🔒 For safety")
    assert agent.action_guard is None  # policy only active during the WhatsApp command


@pytest.mark.parametrize("tool, args", [
    ("make_phone_call", {"phone_number": "+919167078537"}),
    ("send_whatsapp_message", {"phone_number": "+44 20 7946 0958"}),
    ("send_sms", {"phone_number": "911"}),
])
def test_wildcard_targets_allow_anyone(monkeypatch, tool, args):
    monkeypatch.setattr(settings, "WHATSAPP_ALLOWED_TARGETS", "*")
    assert webhook.target_refusal(tool, args) is None


async def test_wildcard_offers_buttons_for_other_numbers(make_client, wa, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ALLOWED_TARGETS", "*")
    wa["agent_result"] = {"reply": "Do you want to call Shyam (+919167078537)?", "tool_calls": [],
                          "confirmation_required": True,
                          "pending_action": {"action": "make_phone_call", "args": {"phone_number": "+919167078537"}}}
    signed_post(make_client(), inbound(text_message("call shyam")))
    await wa["run"]()
    to, body, buttons = wa["sent"][0]
    assert "Do you want to call Shyam" in body and buttons == ["confirm_yes", "confirm_no"]


def test_wildcard_email(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ALLOWED_EMAILS", "*")
    assert webhook.target_refusal("send_email", {"to_email": "anyone@example.com"}) is None
