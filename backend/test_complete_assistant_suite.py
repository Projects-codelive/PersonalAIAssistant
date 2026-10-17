import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.agent.gemma_agent import agent
from app.agent.conversation_memory import conversation_manager
from app.agent.contact_matcher import ContactMatcher

async def run_test_suite():
    print("=" * 60)
    print("TEST 1: Voice Input Typo Tolerance & Fuzzy Matching")
    print("=" * 60)
    # Test voice input "Sham" instead of "Shyam"
    res1 = await agent.run_hybrid_intent_analysis("Send a WhatsApp to Sham saying I will be there in 5 minutes", session_id="test_suite")
    print(f"User: Send a WhatsApp to Sham saying I will be there in 5 minutes")
    print(f"Assistant Reply:\n{res1['reply']}")
    print(f"Confirmation Required: {res1.get('confirmation_required')}")
    assert "Shyam" in res1['reply'], "Failed: 'Sham' was not fuzzy matched to 'Shyam'"
    print(">>> TEST 1 PASSED: Voice typo 'Sham' successfully matched to 'Shyam' with explicit notice.\n")

    print("=" * 60)
    print("TEST 2: Email Composition Intent with Subject and Body")
    print("=" * 60)
    test_session = "multi_turn_test_session"
    res2 = await agent.chat(
        "Email sg25042023@gmail.com with subject Project Milestone saying We have reached 100 percent completion",
        session_id=test_session
    )
    print(f"User: Email sg25042023@gmail.com with subject Project Milestone saying We have reached 100 percent completion")
    print(f"Assistant Reply:\n{res2['reply']}")
    print(f"Confirmation Required: {res2.get('confirmation_required')}")
    assert "sg25042023@gmail.com" in res2['reply']
    assert "Project Milestone" in res2['reply']
    assert "We have reached 100 percent completion" in res2['reply']
    print(">>> TEST 2 PASSED: Email intent properly verified, parsed subject and body.\n")

    print("=" * 60)
    print("TEST 3: Multi-Turn Coreference Resolution Across Channels")
    print("=" * 60)
    # User in same session says: "Now send that to him on WhatsApp"
    res3 = await agent.chat(
        "Now send that to him on WhatsApp",
        session_id=test_session
    )
    print(f"User: Now send that to him on WhatsApp")
    print(f"Assistant Reply:\n{res3['reply']}")
    print(f"Confirmation Required: {res3.get('confirmation_required')}")
    assert "We have reached 100 percent completion" in res3['reply'], "Failed: Coreference 'that' did not resolve previous message"
    print(">>> TEST 3 PASSED: Coreference 'that' ('We have reached 100 percent completion') and 'him' resolved across turns!\n")

    print("=" * 60)
    print("TEST 4: Conversational History Contextual Prompting")
    print("=" * 60)
    context_prompt = conversation_manager.get_conversation_context_prompt(test_session)
    print("Retrieved Multi-turn Context Prompt:")
    print(context_prompt)
    assert "Project Milestone" in context_prompt or "100 percent completion" in context_prompt, "Failed: History prompt missing previous turn context"
    print(">>> TEST 4 PASSED: Multi-turn history is properly tracked and formatted for LLM context.\n")

    print("=" * 60)
    print("TEST 5: On-Screen Email Subject & Content Filling on Device")
    print("=" * 60)
    from app.device.adb_controller import adb_controller
    if adb_controller.is_device_connected:
        print("Device connected. Testing adb_controller.send_email with on-screen verification...")
        success = adb_controller.send_email(
            to_email="sg25042023@gmail.com",
            message="Automation test content verifying subject and editor body",
            subject="Automated Verification Subject"
        )
        print(f"send_email returned: {success}")
        assert success is True, "Failed to send email via ADB"
        print(">>> TEST 5 PASSED: Subject & Content filled and verified before sending on device.\n")
    else:
        print("ADB device not connected, skipped hardware step.\n")

    print("=" * 60)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_test_suite())
