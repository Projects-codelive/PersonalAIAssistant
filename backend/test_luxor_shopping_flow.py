import sys
import asyncio
import time
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.agent.ecommerce_agent import AutonomousEcommerceAgent
from app.device.adb_controller import adb_controller

async def main():
    print("=" * 60)
    print("TESTING AUTONOMOUS SHOPPING FLOW: 'go to flipkart and order luxur pen'")
    print("=" * 60)

    session_id = f"test_luxor_{int(time.time())}"
    query = "luxur pen"
    app_name = "flipkart"

    parsed = AutonomousEcommerceAgent.parse_ecommerce_intent(f"go to {app_name} and order {query}")
    print(f"Parsed Intent: {parsed}")

    print("\n--- Executing Autonomous Shopping Flow ---")
    result = await AutonomousEcommerceAgent.run_autonomous_shopping_flow(
        session_id=session_id,
        app_name=parsed["app"],
        query=parsed["query"],
        criteria=parsed["criteria"]
    )

    print("\n--- Agent Result ---")
    print(f"Reply: {result.get('reply')}")
    print(f"Confirmation Required: {result.get('confirmation_required')}")
    print(f"Screenshots captured ({len(result.get('screenshots', []))}):")
    for s in result.get("screenshots", []):
        p = Path(s)
        print(f"  - {p.name} (exists={p.exists()})")

    print("\n--- Testing Resumption When User Informs Agent ---")
    resume_msg = "maine number daal diya, continue"
    is_res = AutonomousEcommerceAgent.is_resumption_intent(resume_msg, session_id)
    print(f"Is Resumption Intent ('{resume_msg}'): {is_res}")

if __name__ == "__main__":
    asyncio.run(main())
