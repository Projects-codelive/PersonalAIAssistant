import asyncio
import sys
import os

# Set backend root in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.gemma_agent import agent
from app.agent.conversation_memory import conversation_manager

async def run_tests():
    session_id = "test_multilingual_history_session"
    print("=" * 70)
    print("STARTING COMPREHENSIVE MULTILINGUAL & HISTORY TRACKING TESTS")
    print("=" * 70)

    # -------------------------------------------------------------
    # TEST 1: Hindi WhatsApp Messaging (One-shot)
    # -------------------------------------------------------------
    print("\n--- TEST 1: Hindi WhatsApp Command ---")
    t1_input = "Shyam ko whatsapp bhejo ki main 10 minute me aa raha hu"
    print(f"User: {t1_input}")
    res1 = await agent.chat(t1_input, session_id=session_id)
    print(f"Agent: {res1.get('reply')}")
    assert res1.get("confirmation_required") is True, "Expected confirmation_required to be True"
    assert "Shyam" in res1.get("reply", ""), "Expected Shyam in confirmation reply"
    assert "main 10 minute me aa raha hu" in res1.get("reply", ""), "Expected message body in confirmation reply"
    print(">>> TEST 1 PASSED: Hindi WhatsApp command parsed and confirmation created!")

    # -------------------------------------------------------------
    # TEST 2: Hindi Affirmation to execute pending confirmation
    # -------------------------------------------------------------
    print("\n--- TEST 2: Hindi Affirmation ('haan bhej do') ---")
    t2_input = "haan bhej do"
    print(f"User: {t2_input}")
    res2 = await agent.chat(t2_input, session_id=session_id)
    print(f"Agent: {res2.get('reply')}")
    assert res2.get("confirmation_required") is False, "Expected confirmation_required to be False"
    assert "Sent WhatsApp message" in res2.get("reply", "") or "Executed" in res2.get("reply", "") or "Calling" in res2.get("reply", "")
    print(">>> TEST 2 PASSED: Hindi affirmation triggered WhatsApp execution!")

    # -------------------------------------------------------------
    # TEST 3: History Tracking - Pronoun Coreference ('Usko phone lagao')
    # -------------------------------------------------------------
    print("\n--- TEST 3: History Tracking - Hindi Pronoun Coreference ('Usko phone lagao') ---")
    t3_input = "Usko phone lagao"
    print(f"User: {t3_input}")
    res3 = await agent.chat(t3_input, session_id=session_id)
    print(f"Agent: {res3.get('reply')}")
    assert res3.get("confirmation_required") is True, "Expected confirmation_required for call"
    assert "Shyam" in res3.get("reply", ""), "Expected 'Usko' to resolve to Shyam from history!"
    print(">>> TEST 3 PASSED: 'Usko' resolved to Shyam from history!")

    # Cancel the call to test Hindi negative
    print("\n--- TEST 3b: Hindi Cancellation ('nahi mat karo') ---")
    t3b_input = "nahi mat karo"
    print(f"User: {t3b_input}")
    res3b = await agent.chat(t3b_input, session_id=session_id)
    print(f"Agent: {res3b.get('reply')}")
    assert "cancelled" in res3b.get("reply", "").lower(), "Expected call to be cancelled"
    print(">>> TEST 3b PASSED: Hindi rejection cleanly cancelled pending call!")

    # -------------------------------------------------------------
    # TEST 4: Cross-channel Demonstrative History ('Wohi message 9167078537 ko SMS se bhejo')
    # -------------------------------------------------------------
    print("\n--- TEST 4: History Tracking - Demonstrative Reference ('Wohi message...') ---")
    t4_input = "Wohi message 9167078537 ko SMS se bhejo"
    print(f"User: {t4_input}")
    res4 = await agent.chat(t4_input, session_id=session_id)
    print(f"Agent: {res4.get('reply')}")
    assert res4.get("confirmation_required") is True, "Expected SMS confirmation"
    assert "9167078537" in res4.get("reply", "")
    assert "main 10 minute me aa raha hu" in res4.get("reply", ""), "Expected 'wohi message' to resolve to previous text"
    print(">>> TEST 4 PASSED: 'Wohi message' successfully recalled previous text!")

    # -------------------------------------------------------------
    # TEST 5: History Question in Hindi ('Maine abhi kisko message bheja tha?')
    # -------------------------------------------------------------
    print("\n--- TEST 5: History Question in Hindi ('Maine abhi kisko message bheja tha?') ---")
    t5_input = "Maine abhi kisko message bheja tha?"
    print(f"User: {t5_input}")
    res5 = await agent.chat(t5_input, session_id=session_id)
    print(f"Agent: {res5.get('reply')}")
    assert "Shyam" in res5.get("reply", "") or "9167078537" in res5.get("reply", "")
    print(">>> TEST 5 PASSED: History query answered with context from dialogue memory!")

    # -------------------------------------------------------------
    # TEST 6: Spanish Voice Call ('Llama a Shyam por favor')
    # -------------------------------------------------------------
    print("\n--- TEST 6: Spanish Voice Call ('Llama a Shyam por favor') ---")
    t6_input = "Llama a Shyam por favor"
    print(f"User: {t6_input}")
    res6 = await agent.chat(t6_input, session_id=session_id)
    print(f"Agent: {res6.get('reply')}")
    assert res6.get("confirmation_required") is True
    assert "Shyam" in res6.get("reply", "")
    print(">>> TEST 6 PASSED: Spanish call command correctly extracted!")

    # Cancel Spanish call with Spanish rejection ('no cancela')
    print("\n--- TEST 6b: Spanish Cancellation ('no cancela') ---")
    t6b_input = "no cancela"
    print(f"User: {t6b_input}")
    res6b = await agent.chat(t6b_input, session_id=session_id)
    print(f"Agent: {res6b.get('reply')}")
    assert "cancelled" in res6b.get("reply", "").lower()
    print(">>> TEST 6b PASSED: Spanish cancellation executed!")

    # -------------------------------------------------------------
    # TEST 7: Hindi Wake Word ('Namaste Agent')
    # -------------------------------------------------------------
    print("\n--- TEST 7: Hindi Wake Word ('Namaste Agent') ---")
    t7_input = "Namaste Agent"
    print(f"User: {t7_input}")
    res7 = await agent.chat(t7_input, session_id=session_id)
    print(f"Agent: {res7.get('reply')}")
    assert "listening" in res7.get("reply", "").lower() or "help" in res7.get("reply", "").lower()
    print(">>> TEST 7 PASSED: Hindi wake word detected!")

    print("\n" + "=" * 70)
    print("ALL MULTILINGUAL & HISTORY TRACKING TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_tests())
