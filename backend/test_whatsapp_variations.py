import asyncio
from app.agent.gemma_agent import agent
from app.agent.conversation_memory import conversation_manager

async def test_whatsapp_variations():
    print("\n" + "="*70)
    print("TESTING WHATSAPP VARIOUS NATURAL HUMAN INPUTS & CONVERSATION HISTORY")
    print("="*70)

    # -------------------------------------------------------------
    # SCENARIO 1: Multi-turn Follow-up Questions (Slot-Filling)
    # -------------------------------------------------------------
    print("\n--- SCENARIO 1: Step-by-step conversational follow-ups ---")
    s1 = "session_slot_filling_test"

    # Turn 1: Generic request without recipient or message
    t1 = "send whatsapp message"
    r1 = await agent.chat(t1, session_id=s1)
    print(f"User: '{t1}'")
    print(f"Assistant: {r1['reply']}")
    assert "who" in r1["reply"].lower(), "Should ask who to send to"
    print("-> PASS: Follow-up question 1 asked successfully!")

    # Turn 2: User provides recipient only
    t2 = "Shyam (You)"
    r2 = await agent.chat(t2, session_id=s1)
    print(f"User: '{t2}'")
    print(f"Assistant: {r2['reply']}")
    assert "what message" in r2["reply"].lower() and "shyam" in r2["reply"].lower(), "Should ask what message to send"
    print("-> PASS: Follow-up question 2 asked successfully without KeyError!")

    # Turn 3: User provides the message content
    t3 = "Please join the project discussion at 5 PM"
    r3 = await agent.chat(t3, session_id=s1)
    print(f"User: '{t3}'")
    print(f"Assistant: {r3['reply']}")
    print(f"Confirmation Required: {r3.get('confirmation_required')}")
    pa3 = r3.get("pending_action", {})
    print(f"Pending: {pa3.get('action')}, Target: {pa3.get('display_target')}, Msg: {pa3.get('message')}")
    assert pa3.get("action") == "send_whatsapp_message"
    assert "shyam" in pa3.get("display_target", "").lower() or "8169945458" in pa3.get("display_target", "")
    assert "5 pm" in pa3.get("message", "").lower()
    print("-> PASS: Full slot-filling completed and confirmation requested!")

    # Turn 4: User confirms
    t4 = "yes"
    r4 = await agent.chat(t4, session_id=s1)
    print(f"User: '{t4}'")
    print(f"Assistant: {r4['reply']}")
    print(f"Tool calls: {r4.get('tool_calls')}")
    assert "sent whatsapp" in r4["reply"].lower() or len(r4.get("tool_calls", [])) > 0
    print("-> PASS: Execution confirmed!")

    # -------------------------------------------------------------
    # SCENARIO 2: Natural human phrasing in one shot
    # -------------------------------------------------------------
    print("\n--- SCENARIO 2: Natural human phrasing in one shot ---")
    s2 = "session_oneshot_test"
    t_one = "could you ping Shyam (You) on whatsapp that I will be late by 15 mins"
    r_one = await agent.chat(t_one, session_id=s2)
    print(f"User: '{t_one}'")
    print(f"Assistant: {r_one['reply']}")
    pa_one = r_one.get("pending_action", {})
    print(f"Pending: {pa_one.get('action')}, Target: {pa_one.get('display_target')}, Msg: {pa_one.get('message')}")
    assert pa_one.get("action") == "send_whatsapp_message"
    assert "shyam" in pa_one.get("display_target", "").lower() or "8169945458" in pa_one.get("display_target", "")
    assert "late" in pa_one.get("message", "").lower()
    print("-> PASS: Natural one-shot human phrasing parsed and ready for confirmation!")

    # -------------------------------------------------------------
    # SCENARIO 3: Conversation History & Pronoun Coreference ("him")
    # -------------------------------------------------------------
    print("\n--- SCENARIO 3: Tracking conversation history across turns ---")
    s3 = "session_history_test"

    # Turn 1: Mention Shyam in a call
    t_call = "Call Shyam (You)"
    r_call = await agent.chat(t_call, session_id=s3)
    print(f"User: '{t_call}'")
    print(f"Assistant: {r_call['reply']}")

    # Turn 2: User cancels the call
    t_canc = "cancel"
    r_canc = await agent.chat(t_canc, session_id=s3)
    print(f"User: '{t_canc}'")
    print(f"Assistant: {r_canc['reply']}")

    # Turn 3: User says "Actually send him a whatsapp message saying hello there"
    t_pro = "Actually send him a whatsapp message saying hello there"
    r_pro = await agent.chat(t_pro, session_id=s3)
    print(f"User: '{t_pro}'")
    print(f"Assistant: {r_pro['reply']}")
    pa_pro = r_pro.get("pending_action", {})
    print(f"Pending: {pa_pro.get('action')}, Target: {pa_pro.get('display_target')}, Msg: {pa_pro.get('message')}")
    assert pa_pro.get("action") == "send_whatsapp_message"
    assert "shyam" in pa_pro.get("display_target", "").lower() or "8169945458" in pa_pro.get("display_target", "")
    print("-> PASS: Pronoun 'him' successfully resolved to Shyam from history!")

    # Turn 4: User modifies message: "change message to: are you free for a quick sync?"
    t_mod = "change message to: are you free for a quick sync?"
    r_mod = await agent.chat(t_mod, session_id=s3)
    print(f"User: '{t_mod}'")
    print(f"Assistant: {r_mod['reply']}")
    pa_mod = r_mod.get("pending_action", {})
    print(f"Pending: {pa_mod.get('action')}, Target: {pa_mod.get('display_target')}, Msg: {pa_mod.get('message')}")
    assert pa_mod.get("action") == "send_whatsapp_message"
    assert "quick sync" in pa_mod.get("message", "").lower()
    print("-> PASS: Message modification tracked from conversation history!")

    print("\n" + "="*70)
    print("ALL WHATSAPP VARIATION TESTS PASSED SUCCESSFULLY!")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(test_whatsapp_variations())
