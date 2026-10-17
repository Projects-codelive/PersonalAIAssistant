"""Confirmation gating: what counts as "yes", corrections, expiry, and the fuzzy-contact question."""
import pytest

import app.agent.gemma_agent as gemma_agent
from app.agent.gemma_agent import agent, ExpiringDict, PENDING_ACTION_TTL_SECONDS

PENDING_SMS = {
    "action": "send_sms", "channel": "SMS", "display_target": "Alice", "message": "hi",
    "args": {"phone_number": "+15550192834", "message": "hi", "contact_name": "Alice"},
}


@pytest.fixture
def executed(monkeypatch):
    """Record tool executions instead of running them"""
    calls = []

    async def fake_execute(name, args):
        calls.append(name)
        return f"SUCCESS: {name} done"

    monkeypatch.setattr(agent, "execute_tool", fake_execute)
    return calls


async def answer_pending(reply, session_id):
    agent.pending_confirmations[session_id] = dict(PENDING_SMS)
    return await agent.run_hybrid_intent_analysis(reply, session_id=session_id)


@pytest.mark.parametrize("reply", ["yes", "haan bhejo", "ok send it", "yes call him", "haan", "theek hai"])
async def test_clear_confirmation_executes_once(reply, session_id, executed):
    await answer_pending(reply, session_id)
    assert executed == ["send_sms"]


@pytest.mark.parametrize("reply", [
    "no",
    "mat karo",                              # Hindi "don't do it" (contains yes-word "karo")
    "please",                                # politeness alone is not consent
    "ok, actually call Mom instead",         # correction: must not send the old message
    "ok wait send it to Bob instead",
    "yes but message Bob that I am late",    # ambiguous yes + new command: re-ask instead of guessing
])
async def test_non_confirmations_never_execute(reply, session_id, executed):
    await answer_pending(reply, session_id)
    assert executed == []


@pytest.mark.parametrize("reply", ["yes but message Bob that I am late", "yes and then open whatsapp for me later"])
async def test_ambiguous_reply_never_fires_old_action(reply, session_id, executed):
    # Depending on phrasing the agent either re-asks (pending kept) or treats it as a new request
    # (pending discarded); it must never execute the old action on a mixed reply.
    result = await answer_pending(reply, session_id)
    assert executed == []
    if session_id in agent.pending_confirmations:
        assert result.get("confirmation_required") is True


async def test_expired_confirmation_cannot_be_triggered(session_id, executed, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gemma_agent.time, "monotonic", lambda: clock[0])
    agent.pending_confirmations[session_id] = dict(PENDING_SMS)
    clock[0] += PENDING_ACTION_TTL_SECONDS + 1
    await agent.run_hybrid_intent_analysis("yes", session_id=session_id)
    assert executed == []


async def test_fuzzy_contact_question_refusal(session_id, executed):
    agent.pending_inputs[session_id] = {
        "waiting_for": "fuzzy_contact_confirm", "action": "make_phone_call", "channel": "Call",
        "resolved": {"name": "Alice", "phone": "+15550192834"},
    }
    result = await agent.run_hybrid_intent_analysis("mat karo", session_id=session_id)
    assert not result.get("confirmation_required")
    assert session_id not in agent.pending_confirmations
    assert executed == []


def test_expiring_dict_expires_entries(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(gemma_agent.time, "monotonic", lambda: clock[0])
    d = ExpiringDict(ttl=10)
    d["s"] = {"x": 1}
    clock[0] = 9
    assert "s" in d and d["s"] == {"x": 1} and d.get("s") == {"x": 1}
    clock[0] = 11
    assert "s" not in d
    assert d.get("s") is None
    assert d.pop("s", None) is None
    with pytest.raises(KeyError):
        d["s"]


def test_expiring_dict_reset_on_set(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(gemma_agent.time, "monotonic", lambda: clock[0])
    d = ExpiringDict(ttl=10)
    d["s"] = 1
    clock[0] = 8
    d["s"] = 2          # re-asking refreshes the timer
    clock[0] = 15
    assert d.get("s") == 2
