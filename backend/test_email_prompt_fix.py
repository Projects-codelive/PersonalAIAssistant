import asyncio
import logging
import sys
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.gemma_agent import GemmaMobileAgent

async def run():
    agent = GemmaMobileAgent()
    user_prompt = "Send an email to sg25042023@gmail.com and in subject put going for event and in content put Oktoberfest event."
    session_id = "test_email_session_sg25"

    res = await agent.chat(user_prompt, session_id=session_id)
    reply = res.get("reply", "")
    print("=" * 70)
    print("USER PROMPT:")
    print(user_prompt)
    print("AGENT REPLY:")
    print(reply)
    print("=" * 70)

    # Validations:
    # 1. Subject must be 'going for event' (not 'put going for event and in content...')
    assert 'subject: "going for event"' in reply or "subject: 'going for event'" in reply, f"Failed subject check in reply: {reply}"
    # 2. Message / content must be 'Oktoberfest event'
    assert 'message: "Oktoberfest event"' in reply or "message: 'Oktoberfest event'" in reply, f"Failed message check in reply: {reply}"
    # 3. Recipient email address must be verified
    assert "sg25042023@gmail.com" in reply, f"Failed recipient check in reply: {reply}"

    print("[SUCCESS] All email subject, content, and recipient separations passed perfectly!")

if __name__ == "__main__":
    asyncio.run(run())
