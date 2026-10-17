import asyncio
import logging
import sys
import os

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestUserIssues")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.gemma_agent import GemmaMobileAgent
from app.agent.conversation_memory import conversation_manager
from app.agent.universal_mobile_agent import universal_mobile_agent

async def run_tests():
    agent = GemmaMobileAgent()
    session_id = "test_user_session_123"

    print("=" * 70)
    print("TEST 1: 'Play a song named habibi'")
    print("=" * 70)
    res1 = await agent.chat(
        "Play a song named habibi",
        session_id=session_id
    )
    print(f"Reply 1: {res1.get('reply')}")
    print(f"Tool Calls 1: {res1.get('tool_calls')}")
    assert any("youtube" in str(tc).lower() for tc in res1.get("tool_calls", [])), "Test 1 Failed: YouTube tool was not called!"
    assert "habibi" in str(res1.get("reply", "")).lower() or "habibi" in str(res1.get("tool_calls", "")).lower(), "Test 1 Failed: 'habibi' song title not extracted!"
    print("[PASS] TEST 1 passed! Smartly selected YouTube and played 'habibi' without hallucination.\n")

    print("=" * 70)
    print("TEST 2: 'Perform Addition of two number 2 and 3'")
    print("=" * 70)
    res2 = await agent.chat(
        "Perform Addition of two number 2 and 3",
        session_id="test_user_session_456"
    )
    print(f"Reply 2: {res2.get('reply')}")
    print(f"Tool Calls 2: {res2.get('tool_calls')}")
    assert not any("email" in str(tc).lower() for tc in res2.get("tool_calls", [])), "Test 2 Failed: Hallucinated email!"
    assert "email" not in str(res2.get("reply", "")).lower(), "Test 2 Failed: Email mentioned in reply!"
    assert any("calculate" in str(tc).lower() for tc in res2.get("tool_calls", [])), "Test 2 Failed: Calculate tool not called!"
    assert "5" in str(res2.get("reply", "")), "Test 2 Failed: Result 5 not found in reply!"
    print("[PASS] TEST 2 passed! Automatically chose Calculator and calculated 2 + 3 = 5 without email hallucination.\n")

    print("=" * 70)
    print("TEST 3: Multi-turn Calculator Tracking")
    print("Turn 1: 'Open the calcualtor'")
    print("=" * 70)
    multi_session = "test_user_multi_turn_789"
    res3_turn1 = await agent.chat(
        "Open the calcualtor",
        session_id=multi_session
    )
    print(f"Reply 3 (Turn 1): {res3_turn1.get('reply')}")
    print(f"Tool Calls 3 (Turn 1): {res3_turn1.get('tool_calls')}")
    assert any("open_app" in str(tc).lower() and "calculator" in str(tc).lower() for tc in res3_turn1.get("tool_calls", [])), "Test 3 Turn 1 Failed: Calculator not opened!"

    print("-" * 50)
    print("Turn 2: 'add 2 number 2 and 3'")
    print("-" * 50)
    res3_turn2 = await agent.chat(
        "add 2 number 2 and 3",
        session_id=multi_session
    )
    print(f"Reply 3 (Turn 2): {res3_turn2.get('reply')}")
    print(f"Tool Calls 3 (Turn 2): {res3_turn2.get('tool_calls')}")
    assert not any("email" in str(tc).lower() for tc in res3_turn2.get("tool_calls", [])), "Test 3 Turn 2 Failed: Hallucinated email!"
    assert any("calculate" in str(tc).lower() for tc in res3_turn2.get("tool_calls", [])), "Test 3 Turn 2 Failed: Calculate tool not called!"
    assert "5" in str(res3_turn2.get("reply", "")), "Test 3 Turn 2 Failed: Result 5 not found in reply!"
    print("[PASS] TEST 3 passed! Multi-turn calculator state preserved, operation performed, no hallucination!\n")

    print("=" * 70)
    print("ALL 3 USER ISSUES RESOLVED AND VERIFIED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_tests())
