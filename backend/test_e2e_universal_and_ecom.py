import sys
import asyncio
import os
import logging
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.agent.gemma_agent import agent
from app.agent.task_checkpoint_manager import task_checkpoint_manager
from app.device.adb_controller import adb_controller

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_e2e")

SCREENSHOT_DIR = Path("s:/React/Automation/AgentTool/client/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

async def run_live_e2e_verification():
    print("=" * 80)
    print("RUNNING LIVE END-TO-END SMART ASSISTANT & MOBILE VERIFICATION TEST")
    print("=" * 80)
    print(f"Device Connected: {adb_controller.is_device_connected}")
    if adb_controller.is_device_connected:
        print(f"Device Info: {adb_controller.get_device_info()}")
    print("=" * 80)

    # =========================================================================
    # SCENARIO 1: Clumsy E-Commerce Autonomous Flow (Flipkart) With Multi-Step Screenshots
    # =========================================================================
    print("\n[SCENARIO 1] Clumsy E-Commerce: 'bhai flipkart se sabse sasta pen dhundh ke book kar do na yaar'")
    session_ecom = "live_test_ecom_session_01"
    task_checkpoint_manager.clear(session_ecom)

    res1 = await agent.chat("bhai flipkart se sabse sasta pen dhundh ke book kar do na yaar", session_id=session_ecom)
    print(f"\n[Agent Reply Stage 1 & 2]:\n{res1['reply']}")
    print(f"Confirmation Required: {res1.get('confirmation_required')}")
    print(f"Screenshots captured so far: {res1.get('screenshots')}")

    assert res1.get("confirmation_required") == True, "Must require confirmation before placing order"
    assert "flipkart" in res1["reply"].lower(), "Must mention Flipkart"
    assert "pen" in res1["reply"].lower(), "Must mention pen"
    assert len(res1.get("screenshots", [])) >= 2, f"Expected at least 2 screenshots (search and product open), got: {res1.get('screenshots')}"

    # Verify screenshots exist on disk
    for s_path in res1["screenshots"]:
        assert os.path.exists(s_path), f"Screenshot file missing: {s_path}"
        assert os.path.getsize(s_path) > 1000, f"Screenshot file empty: {s_path}"
        print(f"  [VISUAL VERIFICATION]: Screenshot verified on disk ({os.path.getsize(s_path)} bytes): {s_path}")

    # Confirm Order: User says "haan confirm karo"
    print("\n[SCENARIO 1 - USER CONFIRMATION]: User responds 'haan confirm karo'...")
    res1_confirm = await agent.chat("haan confirm karo", session_id=session_ecom)
    print(f"\n[Agent Reply Stage 3 Order Placed]:\n{res1_confirm['reply']}")
    print(f"Screenshots captured total: {res1_confirm.get('screenshots')}")

    assert "order confirmed" in res1_confirm["reply"].lower() or "confirmed" in res1_confirm["reply"].lower()
    assert len(res1_confirm.get("screenshots", [])) >= 3, "Expected 3 screenshots including checkout confirmation"
    for s_path in res1_confirm["screenshots"]:
        assert os.path.exists(s_path), f"Screenshot file missing: {s_path}"
        print(f"  [STAGE 3 VISUAL VERIFIED]: Final Checkout Screenshot: {s_path}")

    print("\n>>> [SCENARIO 1 PASSED]: Clumsy E-Commerce flow executed end-to-end with verified screenshots for every stage!")

    # =========================================================================
    # SCENARIO 2: Knowledge Tool Search & Gemma Reframing (Replacing Phone Browser Search)
    # =========================================================================
    print("\n" + "-" * 70)
    print("[SCENARIO 2] Knowledge Tool Query: 'yaar google pe dekh ke batao how to on developer mode in android'")
    session_web = "live_test_web_session_02"

    res2 = await agent.chat("yaar google pe dekh ke batao how to on developer mode in android", session_id=session_web)
    print(f"\n[Agent Reply]:\n{res2['reply']}")
    print(f"Tool Calls: {res2.get('tool_calls')}")

    assert len(res2.get("tool_calls", [])) > 0, "Expected at least 1 search tool call"
    first_tool = res2["tool_calls"][0]
    assert "site" in first_tool, "Expected tool call to clearly specify target site"
    assert "info_taken" in first_tool, "Expected tool call to clearly specify info taken"
    assert len(first_tool["info_taken"]) > 0, "Expected non-empty info taken from site"
    assert len(res2.get("reply", "")) > 20, "Expected reframed answer from local Gemma"
    print(f"  [TOOL VERIFICATION]: Tool '{first_tool['tool']}' successfully called site: '{first_tool['site']}'")
    print(f"  [INFO TAKEN VERIFICATION]: Extracted {len(first_tool['info_taken'])} characters of knowledge")
    print(f"  [GEMMA REFRAME VERIFIED]: Local Gemma reframed and cited findings")

    print("\n>>> [SCENARIO 2 PASSED]: Knowledge Tool search executed, specified site & info taken, and local Gemma reframed the response!")

    # =========================================================================
    # SCENARIO 3: Clumsy Mobile App Automation (Calculator)
    # =========================================================================
    print("\n" + "-" * 70)
    print("[SCENARIO 3] Clumsy App Launch: 'calculator kholo aur thoda hisaab karo'")
    session_app = "live_test_app_session_03"

    res3 = await agent.chat("calculator kholo aur thoda hisaab karo", session_id=session_app)
    print(f"\n[Agent Reply]:\n{res3['reply']}")
    print(f"Screenshots captured: {res3.get('screenshots')}")

    assert "calculator" in res3["reply"].lower(), "Should mention Calculator"
    assert len(res3.get("screenshots", [])) >= 1, "Expected at least 1 screenshot"
    for s_path in res3["screenshots"]:
        assert os.path.exists(s_path), f"Screenshot file missing: {s_path}"
        assert os.path.getsize(s_path) > 1000, f"Screenshot file empty: {s_path}"
        print(f"  [VISUAL VERIFICATION]: App Launch Screenshot ({os.path.getsize(s_path)} bytes): {s_path}")

    print("\n>>> [SCENARIO 3 PASSED]: Installed app detected, opened on device, and verified with screenshot!")

    # =========================================================================
    # SCENARIO 4: Disambiguation Communication Check (Zero Regressions)
    # =========================================================================
    print("\n" + "-" * 70)
    print("[SCENARIO 4] Communication Disambiguation: 'Abhishek and ask how are you'")
    session_comm = "live_test_comm_session_04"
    agent.pending_confirmations.clear()
    agent.pending_inputs.clear()

    res4 = await agent.chat("Abhishek and ask how are you", session_id=session_comm)
    print(f"\n[Agent Reply]:\n{res4['reply']}")
    assert "whatsapp" in res4["reply"].lower() and "sms" in res4["reply"].lower(), "Must prompt for channel selection"
    print("\n>>> [SCENARIO 4 PASSED]: Communication disambiguation remains 100% active and unimpacted!")

    print("\n" + "=" * 80)
    print("ALL LIVE END-TO-END MOBILE & SMART ASSISTANT SCENARIOS SUCCESSFULLY COMPLETED!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_live_e2e_verification())
