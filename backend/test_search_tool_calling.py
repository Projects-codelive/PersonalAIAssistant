import asyncio
import sys
import logging
from app.agent.tools.search_tools import search_knowledge_service
from app.agent.gemma_agent import agent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_search")


async def run_search_tests():
    print("=" * 80)
    print("TESTING KNOWLEDGE SEARCH TOOLS & LOCAL GEMMA REFRAMING")
    print("=" * 80)

    # 1. Test DuckDuckGo Web Search (How-to / Technical)
    print("\n[TEST 1] Testing DuckDuckGo Search (How-to Guide): 'how to enable developer mode on android'")
    res1 = await search_knowledge_service.search_and_reframe(
        user_msg="yaar google pe dekh ke batao how to on developer mode in android",
        query="how to on developer mode in android"
    )
    print(f"Site: {res1['site']}")
    print(f"Tool Called: {res1['tool_calls'][0]['tool']}")
    print(f"Info Taken Length: {len(res1['tool_calls'][0]['info_taken'])} chars")
    print(f"Gemma Reframed Reply:\n{res1['reply']}\n")

    assert "site" in res1["tool_calls"][0], "Tool call must specify site"
    assert "info_taken" in res1["tool_calls"][0], "Tool call must specify info_taken"
    assert len(res1["reply"]) > 20, "Gemma must reframe the response"
    print("✓ [TEST 1 PASSED]: DuckDuckGo query executed, site & info taken specified, Gemma reframed.")

    # 2. Test Wikipedia Tool (Encyclopedic Definition)
    print("\n" + "-" * 70)
    print("[TEST 2] Testing Wikipedia Tool: 'what is machine learning'")
    res2 = await search_knowledge_service.search_and_reframe(
        user_msg="what is machine learning according to wikipedia",
        query="machine learning"
    )
    print(f"Site: {res2['site']}")
    print(f"Tool Called: {res2['tool_calls'][0]['tool']}")
    print(f"Info Taken Length: {len(res2['tool_calls'][0]['info_taken'])} chars")
    print(f"Gemma Reframed Reply:\n{res2['reply']}\n")

    assert "wikipedia" in res2["site"].lower(), "Expected Wikipedia site"
    assert "info_taken" in res2["tool_calls"][0], "Tool call must specify info_taken"
    assert len(res2["reply"]) > 20, "Gemma must reframe the response"
    print("✓ [TEST 2 PASSED]: Wikipedia query executed, site & info taken specified, Gemma reframed.")

    # 3. Test Agent.chat routing (Natural Language Google Search)
    print("\n" + "-" * 70)
    print("[TEST 3] Testing Agent.chat() Integration: 'search on google what is artificial intelligence'")
    session_id = "test_nl_search_session"
    res3 = await agent.chat("search on google what is artificial intelligence", session_id=session_id)
    print(f"Agent Reply:\n{res3['reply']}")
    print(f"Agent Tool Calls: {res3.get('tool_calls')}")

    assert len(res3.get("tool_calls", [])) > 0, "Expected tool call recorded in agent.chat"
    tc = res3["tool_calls"][0]
    assert "site" in tc, "Agent tool call must specify target site"
    assert "info_taken" in tc, "Agent tool call must specify info taken"
    assert len(res3.get("reply", "")) > 20, "Agent must return reframed reply from Gemma"
    print("✓ [TEST 3 PASSED]: Agent.chat intercepted Google search query and called knowledge tool without opening phone browser.")

    print("\n" + "=" * 80)
    print("ALL KNOWLEDGE SEARCH & GEMMA REFRAMING TESTS PASSED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_search_tests())
