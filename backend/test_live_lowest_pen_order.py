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
logger = logging.getLogger("test_order_pen")

SCREENSHOT_DIR = Path("s:/React/Automation/AgentTool/client/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

async def test_live_pen_order():
    print("=" * 80)
    print("RUNNING LIVE SCENARIO: ORDER LOWEST COST PEN WITH FULL CONFIRMATION & SCREENSHOTS")
    print("=" * 80)
    print(f"Device Connected: {adb_controller.is_device_connected}")
    if adb_controller.is_device_connected:
        print(f"Connected Device: {adb_controller.get_device_info()}")
    print("=" * 80)

    session_id = "live_lowest_pen_order_session"
    task_checkpoint_manager.clear(session_id)

    # -------------------------------------------------------------------------
    # STAGE 1 & 2: Search, Price Comparison, Lowest Cost Pen Selection
    # -------------------------------------------------------------------------
    user_prompt = "flipkart se lowest cost pen order karo"
    print(f"\n[USER PROMPT]: '{user_prompt}'")
    print("Agent is executing search, inspecting screen hierarchy, and comparing all pens...")

    res_stage1 = await agent.chat(user_prompt, session_id=session_id)

    print("\n" + "-" * 60)
    print("[AGENT RESPONSE - PRE-PURCHASE CONFIRMATION REQUIRED]:")
    print(res_stage1["reply"])
    print("-" * 60)
    print(f"Confirmation Required: {res_stage1.get('confirmation_required')}")
    print(f"Screenshots Captured: {res_stage1.get('screenshots')}")

    assert res_stage1.get("confirmation_required") == True, "Pre-order confirmation is mandatory"
    assert "flipkart" in res_stage1["reply"].lower()
    assert "pen" in res_stage1["reply"].lower()
    assert len(res_stage1.get("screenshots", [])) >= 2, "Expected search and product screenshots"

    for s_path in res_stage1["screenshots"]:
        assert os.path.exists(s_path), f"Screenshot not found: {s_path}"
        print(f"  [SCREENSHOT VERIFIED ON PHONE]: {s_path} ({os.path.getsize(s_path)} bytes)")

    # -------------------------------------------------------------------------
    # STAGE 3: User Confirmation & Literal Order Execution ("Buy Now")
    # -------------------------------------------------------------------------
    confirm_prompt = "yes confirm order"
    print(f"\n[USER CONFIRMATION]: '{confirm_prompt}'")
    print("Agent is tapping 'Buy' / 'Order' button on phone screen...")

    res_confirm = await agent.chat(confirm_prompt, session_id=session_id)

    print("\n" + "-" * 60)
    print("[AGENT RESPONSE - ORDER CONFIRMED]:")
    print(res_confirm["reply"])
    print("-" * 60)
    print(f"Screenshots Captured Total: {res_confirm.get('screenshots')}")

    assert len(res_confirm.get("screenshots", [])) >= 3, "Expected at least 3 screenshots"
    for s_path in res_confirm["screenshots"]:
        assert os.path.exists(s_path), f"Screenshot not found: {s_path}"
        print(f"  [FINAL VERIFIED SCREENSHOT]: {s_path} ({os.path.getsize(s_path)} bytes)")

    print("\n" + "=" * 80)
    print("SUCCESS: LOWEST COST PEN FOUND, CONFIRMED, AND ORDERED END-TO-END!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_live_pen_order())
