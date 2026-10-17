import sys
import asyncio
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.agent.gemma_agent import GemmaMobileAgent
from app.agent.task_checkpoint_manager import task_checkpoint_manager

async def test_order_history_and_progress():
    agent = GemmaMobileAgent()
    session_id = f"sess_progress_{int(time.time())}"

    print("=" * 65)
    print("STEP 1: User says: 'go to flipkart and order luxur pen'")
    print("=" * 65)
    res1 = await agent.run_hybrid_intent_analysis("go to flipkart and order luxur pen", session_id=session_id)
    print(res1.get("reply"))

    ckpt = task_checkpoint_manager.get_checkpoint(session_id)
    print(f"\n[Checkpoint Step History Count]: {len(ckpt.step_history)}")
    for s in ckpt.step_history:
        print(f"  Step {s['step']} [{s['stage']}]: {s['action']} (paused={s['is_paused']})")

    print("\n" + "=" * 65)
    print("STEP 2: User asks: 'where did we get stuck and should I proceed where I was stuck at last time?'")
    print("=" * 65)
    res2 = await agent.run_hybrid_intent_analysis("where did we get stuck and should I proceed where I was stuck at last time?", session_id=session_id)
    print(res2.get("reply"))

    print("\n" + "=" * 65)
    print("STEP 3: User asks in Hindi: 'kahan atak gaye the order mein?'")
    print("=" * 65)
    res3 = await agent.run_hybrid_intent_analysis("kahan atak gaye the order mein?", session_id=session_id)
    print(res3.get("reply"))

    print("\n" + "=" * 65)
    print("STEP 4: User says: 'haan aage badho'")
    print("=" * 65)
    res4 = await agent.run_hybrid_intent_analysis("haan aage badho", session_id=session_id)
    print(res4.get("reply"))

if __name__ == "__main__":
    asyncio.run(test_order_history_and_progress())
