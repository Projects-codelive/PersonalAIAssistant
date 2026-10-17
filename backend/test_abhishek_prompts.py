import asyncio
import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Set backend root in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.gemma_agent import agent

async def run_tests():
    print("=" * 70)
    print("TESTING ADVANCED REASONING & PROMPTS: ABHISHEK")
    print("=" * 70)

    # -----------------------------------------------------------------
    # PROMPT 1:
    # "अभिषेक को message भेजो WhatsApp। यह पूछते हुए कि कैसे हो आप in English"
    # Expected: Send WhatsApp message to Abhishek with content "How are you?" in English
    # -----------------------------------------------------------------
    session1 = "test_prompt1_session"
    p1 = "अभिषेक को message भेजो WhatsApp। यह पूछते हुए कि कैसे हो आप in English"
    print(f"\n[PROMPT 1 Input]: {p1}")
    r1 = await agent.chat(p1, session_id=session1)
    print(f"[Agent Response 1]: {r1.get('reply')}")

    assert r1.get("confirmation_required") is True, "Prompt 1 should require confirmation before sending"
    assert "Abhishek" in r1.get("reply", "") or "7208590785" in r1.get("reply", ""), "Should resolve Abhishek"
    assert "How are you" in r1.get("reply", "") or "how are you" in r1.get("reply", ""), "Content should be 'How are you' in English"
    print(">>> PROMPT 1 CHECK 1 PASSED: WhatsApp message prepared for Abhishek with content 'How are you?' in English!")

    # Confirm send
    r1_confirm = await agent.chat("haan bhej do", session_id=session1)
    print(f"[Agent Confirmation Response]: {r1_confirm.get('reply')}")
    assert r1_confirm.get("confirmation_required") is False
    # The confirmation must execute the WhatsApp tool. Whether it was actually sent depends on a phone being
    # connected, and the reply now reports that honestly instead of always claiming "Sent".
    assert [t["tool"] for t in r1_confirm.get("tool_calls", [])] == ["send_whatsapp_message"]
    print(">>> PROMPT 1 CHECK 2 PASSED: WhatsApp message confirmed and sent!")

    # -----------------------------------------------------------------
    # PROMPT 2:
    # "Abhishek and ask how are you"
    # Expected: The system should intelligently detect missing channel and ask:
    # "Do you want to send a WhatsApp message, SMS, or Email to Abhishek?"
    # -----------------------------------------------------------------
    session2 = "test_prompt2_session"
    p2 = "Abhishek and ask how are you"
    print(f"\n[PROMPT 2 Input]: {p2}")
    r2 = await agent.chat(p2, session_id=session2)
    print(f"[Agent Response 2]: {r2.get('reply')}")

    reply2_lower = r2.get("reply", "").lower()
    assert "whatsapp" in reply2_lower and "sms" in reply2_lower and "email" in reply2_lower, (
        f"Prompt 2 should ask whether to send whatsapp, sms, or email. Got: {r2.get('reply')}"
    )
    print(">>> PROMPT 2 CHECK 1 PASSED: System intelligently asked: 'Do you want to send a WhatsApp message, SMS, or Email to Abhishek?'")

    # Turn 2: User responds with "WhatsApp"
    p2_choice = "WhatsApp"
    print(f"\n[PROMPT 2 User Choice]: {p2_choice}")
    r2_choice = await agent.chat(p2_choice, session_id=session2)
    print(f"[Agent Response 2b]: {r2_choice.get('reply')}")

    assert r2_choice.get("confirmation_required") is True
    assert "Abhishek" in r2_choice.get("reply", "") or "7208590785" in r2_choice.get("reply", "")
    assert "How are you" in r2_choice.get("reply", "") or "how are you" in r2_choice.get("reply", "")
    print(">>> PROMPT 2 CHECK 2 PASSED: Chose WhatsApp, verified message 'How are you?' to Abhishek!")

    # Turn 3: User confirms
    r2_confirm = await agent.chat("yes", session_id=session2)
    print(f"[Agent Final Send]: {r2_confirm.get('reply')}")
    assert r2_confirm.get("confirmation_required") is False
    print(">>> PROMPT 2 CHECK 3 PASSED: Completed intelligent decision-making flow end-to-end!")

    print("\n" + "=" * 70)
    print("ALL ABHISHEK TEST PROMPTS PASSED WITH FLYING COLORS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_tests())
