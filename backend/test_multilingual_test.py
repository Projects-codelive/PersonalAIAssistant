import asyncio
import json
import re
from app.agent.gemma_agent import agent
from langchain_core.messages import SystemMessage, HumanMessage

EXTRACTION_PROMPT = """You are a multilingual natural language understanding engine for an AI mobile personal assistant.
Analyze the user request and extract communication action and parameters into a clean JSON object.
The user request may be in English, Hindi, Hinglish, Spanish, or any other language.

Allowed actions: "whatsapp", "sms", "email", "call", "general"

JSON Schema:
{
  "action": "whatsapp" | "sms" | "email" | "call" | "general",
  "recipient": string or null,
  "message": string or null,
  "subject": string or null,
  "language": "en" | "hi" | "hinglish" | "es" | "other"
}

Instructions:
- "action":
  - "whatsapp" for WhatsApp (e.g. "whatsapp karo", "whatsapp bhejo", "ping on whatsapp")
  - "sms" for SMS/Text (e.g. "sms bhejo", "message karo", "text karo")
  - "email" for Email (e.g. "email bhejo", "mail karo")
  - "call" for calls (e.g. "call lagao", "phone karo", "call karo", "dial karo")
  - "general" for other tasks
- Coreference: If the user says "use", "usko", "unhe", "him", "her", resolve recipient from Context if available.
- If user says "woh message", "wohi message", "that message", resolve message from Context if available.
- Output ONLY valid JSON.
"""

async def test():
    context = """ACTIVE CONVERSATION MEMORY:
- Last Contact Mentioned: Shyam (+918169945458)
- Last Communication Channel: WhatsApp
- Last Drafted Message: "Main 10 minute me pahunch raha hu"
- Last Action: send_whatsapp_message"""

    queries = [
        "Shyam ko whatsapp bhejo ki main 10 minute me aa raha hu",
        "Usko phone lagao",
        "Wohi message SMS se bhejo",
        "sg25042023@gmail.com ko email karo subject Meeting aur likho please join zoom link"
    ]

    for q in queries:
        prompt = f"{context}\n\nUser request: {q}"
        res = await agent.llm.ainvoke([SystemMessage(content=EXTRACTION_PROMPT), HumanMessage(content=prompt)])
        cleaned = re.sub(r"^```(?:json)?\s*", "", res.content.strip(), flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        first_b = cleaned.find("{")
        last_b = cleaned.rfind("}")
        data = json.loads(cleaned[first_b:last_b+1])
        print(f"User: {q}")
        print(f"Extracted: {json.dumps(data, indent=2)}")
        print("-" * 50)

if __name__ == "__main__":
    asyncio.run(test())
