import sys
import asyncio
import time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.agent.gemma_agent import GemmaMobileAgent

async def test_full_shopping_conversation():
    agent = GemmaMobileAgent()
    session_id = f"session_{int(time.time())}"

    print("=" * 65)
    print("TURN 1: User says: 'go to flipkart and order luxur pen'")
    print("=" * 65)
    res1 = await agent.run_hybrid_intent_analysis("go to flipkart and order luxur pen", session_id=session_id)
    print("\n[Agent Reply Turn 1]:")
    print(res1.get("reply"))
    print(f"Screenshots captured ({len(res1.get('screenshots', []))}):")
    for s in res1.get('screenshots', []):
        print(f"  - {Path(s).name}")

    print("\n" + "=" * 65)
    print("TURN 2: User says: 'maine number daal diya, continue'")
    print("=" * 65)
    res2 = await agent.run_hybrid_intent_analysis("maine number daal diya, continue", session_id=session_id)
    print("\n[Agent Reply Turn 2]:")
    print(res2.get("reply"))
    print(f"Screenshots captured ({len(res2.get('screenshots', []))}):")
    for s in res2.get('screenshots', []):
        print(f"  - {Path(s).name}")

if __name__ == "__main__":
    asyncio.run(test_full_shopping_conversation())
