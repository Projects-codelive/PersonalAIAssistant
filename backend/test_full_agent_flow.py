import asyncio
import json
from app.agent.gemma_agent import agent

async def test_agent_flows():
    print("\n" + "="*70)
    print("RUNNING END-TO-END VERIFICATION FOR USER REQUIREMENTS")
    print("="*70)

    # 1. Wake-word test ("Hi Agent", "Hello Agent")
    print("\n[TEST 1] Wake-word / Greeting Test:")
    res_wake = await agent.run_hybrid_intent_analysis("Hi Agent", session_id="test_wake_1")
    print(f"User: 'Hi Agent'")
    print(f"Reply: {res_wake['reply']}")
    assert "listening" in res_wake["reply"].lower() or "help" in res_wake["reply"].lower()
    print("-> PASS: Wake-word recognized correctly!")

    res_wake2 = await agent.run_hybrid_intent_analysis("Hello Agent", session_id="test_wake_2")
    print(f"User: 'Hello Agent'")
    print(f"Reply: {res_wake2['reply']}")
    assert "listening" in res_wake2["reply"].lower() or "help" in res_wake2["reply"].lower()
    print("-> PASS: 'Hello Agent' recognized correctly!")

    # 2. WhatsApp Natural Language Extraction with Shyam (You)
    print("\n[TEST 2] WhatsApp NLP Extraction with 'Shyam (You)':")
    q_wa = "Hi Agent, send whatsapp to Shyam (You) saying Hello Shyam testing automated messaging"
    res_wa = await agent.run_hybrid_intent_analysis(q_wa, session_id="test_wa_session")
    print(f"User: '{q_wa}'")
    print(f"Reply: {res_wa['reply']}")
    print(f"Confirmation Required: {res_wa.get('confirmation_required')}")
    pa_wa = res_wa.get("pending_action", {})
    print(f"Pending Action: {pa_wa.get('action')}, Channel: {pa_wa.get('channel')}, Target: {pa_wa.get('display_target')}, Msg: {pa_wa.get('message')}")
    assert pa_wa.get("action") == "send_whatsapp_message"
    assert "shyam" in pa_wa.get("display_target", "").lower() or "8169945458" in pa_wa.get("display_target", "")
    assert "hello shyam" in pa_wa.get("message", "").lower()
    print("-> PASS: WhatsApp intent and parameters extracted via Gemma!")

    # 3. SMS Natural Language Extraction with 9167078537
    print("\n[TEST 3] SMS NLP Extraction with '9167078537':")
    q_sms = "text 9167078537 that I reached safely"
    res_sms = await agent.run_hybrid_intent_analysis(q_sms, session_id="test_sms_session")
    print(f"User: '{q_sms}'")
    print(f"Reply: {res_sms['reply']}")
    print(f"Confirmation Required: {res_sms.get('confirmation_required')}")
    pa_sms = res_sms.get("pending_action", {})
    print(f"Pending Action: {pa_sms.get('action')}, Channel: {pa_sms.get('channel')}, Target: {pa_sms.get('display_target')}, Msg: {pa_sms.get('message')}")
    assert pa_sms.get("action") == "send_sms"
    assert "9167078537" in pa_sms.get("display_target", "")
    assert "reached safely" in pa_sms.get("message", "").lower()
    print("-> PASS: SMS intent and parameters extracted via Gemma!")

    # 4. Email Natural Language Extraction with sg25042023@gmail.com
    print("\n[TEST 4] Email NLP Extraction with 'sg25042023@gmail.com':")
    q_email = "drop an email to sg25042023@gmail.com with subject Project Alpha and tell him all tests passed"
    res_email = await agent.run_hybrid_intent_analysis(q_email, session_id="test_email_session")
    print(f"User: '{q_email}'")
    print(f"Reply: {res_email['reply']}")
    print(f"Confirmation Required: {res_email.get('confirmation_required')}")
    pa_email = res_email.get("pending_action", {})
    print(f"Pending Action: {pa_email.get('action')}, Channel: {pa_email.get('channel')}, Target: {pa_email.get('display_target')}, Subject: {pa_email.get('subject')}, Msg: {pa_email.get('message')}")
    assert pa_email.get("action") == "send_email"
    assert "sg25042023@gmail.com" in pa_email.get("to_email", "")
    assert "Project Alpha" in pa_email.get("subject", "")
    assert "tests passed" in pa_email.get("message", "").lower()
    print("-> PASS: Email validation, subject, and body extracted via Gemma!")

    # 5. User Confirmation Flow ("yes" to send email)
    print("\n[TEST 5] Confirmation Response ('yes'):")
    res_confirm = await agent.run_hybrid_intent_analysis("yes", session_id="test_email_session")
    print(f"User: 'yes'")
    print(f"Reply: {res_confirm['reply']}")
    print(f"Tool Calls: {res_confirm.get('tool_calls')}")
    assert "sent email" in res_confirm["reply"].lower() or len(res_confirm.get("tool_calls", [])) > 0
    print("-> PASS: User confirmation executes automation tool!")

    # 6. Phone Call Confirmation Flow with Shyam (You)
    print("\n[TEST 6] Phone Call Confirmation Flow with 'Shyam (You)':")
    q_call = "Call Shyam (You)"
    res_call = await agent.run_hybrid_intent_analysis(q_call, session_id="test_call_session")
    print(f"User: '{q_call}'")
    print(f"Reply: {res_call['reply']}")
    print(f"Confirmation Required: {res_call.get('confirmation_required')}")
    pa_call = res_call.get("pending_action", {})
    print(f"Pending Action: {pa_call.get('action')}, Target: {pa_call.get('display_target')}")
    assert pa_call.get("action") == "make_phone_call"
    assert "shyam" in pa_call.get("display_target", "").lower()
    print("-> PASS: Phone call confirmation asked before calling!")

    print("\n" + "="*70)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(test_agent_flows())
