import os
import re
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.config import settings
from app.agent.tools import (
    ALL_TOOLS, make_phone_call, send_sms, send_whatsapp_message,
    search_contacts, save_contact, read_screen_state, tap_element, open_app, go_home
)
from app.db.mongo import save_conversation_turn

logger = logging.getLogger(__name__)

# Pending calls/messages expire so a stray "yes" long after the question can never fire them
PENDING_ACTION_TTL_SECONDS = 120


class ExpiringDict(dict):
    """dict whose entries silently disappear `ttl` seconds after they were last set"""

    def __init__(self, ttl: float):
        super().__init__()
        self.ttl = ttl
        self._set_at: Dict[Any, float] = {}

    def __setitem__(self, key, value):
        self._set_at[key] = time.monotonic()
        super().__setitem__(key, value)

    def _expire(self, key):
        if dict.__contains__(self, key) and time.monotonic() - self._set_at.get(key, 0) > self.ttl:
            logger.info(f"Pending action for session '{key}' expired after {self.ttl}s")
            dict.pop(self, key, None)
            self._set_at.pop(key, None)

    def __contains__(self, key):
        self._expire(key)
        return super().__contains__(key)

    def __getitem__(self, key):
        self._expire(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._expire(key)
        return super().get(key, default)

    def pop(self, key, *default):
        self._expire(key)
        self._set_at.pop(key, None)
        return super().pop(key, *default)

SYSTEM_PROMPT = """You are AgentTool, an autonomous mobile AI assistant powered by local Gemma.
You are running locally on the device with zero cloud API key requirements.

CAPABILITIES & MOBILE ACTIONS:
1. Making phone calls:
   - CONFIRMATION MANDATORY: Before calling anyone, always ask for user confirmation: 'Do you want to call [Name] ([Number])? Say yes to call or no to cancel.'
   - If user just says 'call' without a name, ask: 'Who would you like me to call?'
   - Only execute `make_phone_call` after the user confirms with 'yes' or 'proceed'.
   - If user says 'no' or 'cancel', cancel the call immediately.
2. WhatsApp & SMS Messaging:
   - CONFIRMATION MANDATORY: Before sending any WhatsApp or SMS message, always ask for confirmation with the full text and recipient:
     'Do you want to send this [WhatsApp/SMS] message to [Name] ([Number]): "[Message]"? Say yes to send or no to cancel.'
   - If the user did not specify who to message or what to say, ask clarifying questions (e.g. 'What message would you like to send?', 'Who would you like to message?').
   - Only execute `send_whatsapp_message` or `send_sms` after the user confirms with 'yes'.
3. Contacts: Use `search_contacts` or `save_contact` to query or add contacts in MongoDB.
4. Screen navigation:
   - Use `read_screen_state` to inspect on-screen UI elements.
   - Use `tap_element` to tap buttons.
   - Use `open_app` to launch applications.
   - Use `go_home` to return to the home screen.
5. Shopping on the phone (Flipkart by default; also Amazon, Myntra, Blinkit, Zepto, Swiggy, Zomato):
   - You CAN shop: the agent opens the store app, searches the product, compares prices and asks before ordering.
   - Never say you cannot buy things. If a shopping request reaches you, tell the user to say it like
     "buy a pen from Flipkart" or "flipkart se sabse sasta pen dhundho".
   - Login, OTP and payment screens are always left for the user to complete.

Be concise, helpful, and action-oriented. Always ask clarifying questions if details are missing, and ask for confirmation before executing tasks.
"""

OLLAMA_KEEP_ALIVE = "30m"


async def warm_up_model() -> None:
    """Load Gemma into memory at startup so the first real message doesn't wait for the model to load"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # An empty prompt makes Ollama load the model without generating anything
            r = await client.post(f"{settings.OLLAMA_BASE_URL}/api/generate",
                                  json={"model": settings.GEMMA_MODEL_NAME, "keep_alive": OLLAMA_KEEP_ALIVE})
        logger.info(f"Gemma model '{settings.GEMMA_MODEL_NAME}' warmed up ({r.status_code})")
    except Exception as e:
        logger.warning(f"Could not warm up Gemma model: {e}")


def create_local_gemma():
    """Initialize local Gemma via Ollama (No API Key Required)"""
    try:
        from langchain_ollama import ChatOllama
        logger.info(f"Connecting to local Gemma via Ollama ({settings.GEMMA_MODEL_NAME}) at {settings.OLLAMA_BASE_URL}...")
        llm = ChatOllama(
            model=settings.GEMMA_MODEL_NAME,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0.2,
            # Intent extraction and short replies don't need chain-of-thought: with thinking on, a
            # Gemma 4 call took ~11s vs ~0.4s off
            reasoning=False,
            # Keep the model in memory between messages (avoids a ~25s reload after Ollama's 5 min default)
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
        return llm
    except Exception as e:
        logger.warning(f"Could not connect to local Ollama Gemma: {e}. Fallback router active.")
        return None

class GemmaMobileAgent:
    def __init__(self):
        self.llm = create_local_gemma()
        self.tools_map = {t.name: t for t in ALL_TOOLS}
        self.llm_with_tools = None
        # In-memory pending confirmations keyed by session_id
        self.pending_confirmations: Dict[str, Dict[str, Any]] = ExpiringDict(PENDING_ACTION_TTL_SECONDS)
        # In-memory conversational slot filling (missing parameters) keyed by session_id
        self.pending_inputs: Dict[str, Dict[str, Any]] = ExpiringDict(PENDING_ACTION_TTL_SECONDS)
        # Optional policy hook (tool_name, args) -> refusal message or None; set by channels such as
        # WhatsApp for the duration of one command (agent tasks are serialized on the agent worker)
        self.action_guard = None
        
        if self.llm:
            try:
                # Try binding tools if model supports tool-calling schemas
                self.llm_with_tools = self.llm.bind_tools(ALL_TOOLS)
                logger.info("Bound mobile tools to local Gemma LLM.")
            except Exception as e:
                logger.info(f"Tool binding not supported natively on this model, using structured agent router: {e}")
                self.llm_with_tools = None

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute one of the registered LangChain tools"""
        tool_func = self.tools_map.get(tool_name)
        if not tool_func:
            return f"Error: Tool '{tool_name}' not found."
        refusal = self.action_guard(tool_name, args) if self.action_guard else None
        if refusal:
            logger.warning(f"Blocked tool {tool_name} by channel policy")
            return f"BLOCKED: {refusal}"
        try:
            logger.info(f"Executing tool {tool_name} with args: {args}")
            if hasattr(tool_func, "ainvoke"):
                res = await tool_func.ainvoke(args)
            else:
                res = tool_func.invoke(args)
            return str(res)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return f"Error executing {tool_name}: {str(e)}"

    @classmethod
    def extract_email_subject_and_body(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts subject and content/body from email commands such as:
        - 'in subject put X and in content put Y'
        - 'in content put Y and in subject put X'
        - 'with subject X and content/body Y'
        - 'subject: X, content/body: Y'
        """
        subj = None
        body = None
        clean_text = text.strip()

        # Pattern 1: (in) subject ... and (in) content/body/message ...
        m1 = re.search(
            r'(?:and\s+)?(?:in\s+)?subject(?:\s+is|\s*:|\s+as|\s+put|\s+write)?\s+[\"\'\s]*(.+?)\s+and\s+(?:in\s+)?(?:content|body|message)(?:\s+is|\s*:|\s+as|\s+put|\s+write)?\s+[\"\'\s]*(.+?)[\"\'\s\.]*$',
            clean_text,
            re.IGNORECASE
        )
        if m1:
            subj = m1.group(1).strip()
            body = m1.group(2).strip()
        else:
            # Pattern 2: (in) content/body/message ... and (in) subject ...
            m2 = re.search(
                r'(?:and\s+)?(?:in\s+)?(?:content|body|message)(?:\s+is|\s*:|\s+as|\s+put|\s+write)?\s+[\"\'\s]*(.+?)\s+and\s+(?:in\s+)?subject(?:\s+is|\s*:|\s+as|\s+put|\s+write)?\s+[\"\'\s]*(.+?)[\"\'\s\.]*$',
                clean_text,
                re.IGNORECASE
            )
            if m2:
                body = m2.group(1).strip()
                subj = m2.group(2).strip()
            else:
                # Pattern 3: with subject <S> and (content|body|message) <B>
                m3 = re.search(
                    r'(?:with\s+)?subject(?:\s+is|\s*:|\s+as)?\s+[\"\'\s]*([^,;]+?)(?:[\"\'\s]*(?:and\s+(?:in\s+)?(?:content|body|message)|(?:saying|that|with body|body|message))[\s\:\'\"]*(.+?))[\"\'\s\.]*$',
                    clean_text,
                    re.IGNORECASE
                )
                if m3:
                    subj = m3.group(1).strip()
                    body = m3.group(2).strip()

        def clean_val(v):
            if not v:
                return None
            res = re.sub(r'^(?:put|write|set|enter|use|add|as)\s+', '', v, flags=re.IGNORECASE).strip()
            res = re.sub(r'[\"\'\.]+$', '', res).strip()
            return res

        return clean_val(subj), clean_val(body)

    async def extract_intent_with_gemma(self, user_message: str, session_id: str = "default") -> Optional[Dict[str, Any]]:
        """
        Uses local Gemma to extract communication action, recipient, message body, and subject
        from arbitrary natural language user phrasing across multiple languages (English, Hindi, Hinglish, Spanish).
        Leverages active conversation history context to resolve coreferences ('him', 'use', 'usko', 'that message').
        """
        if not self.llm:
            return None
        import json
        from app.agent.conversation_memory import conversation_manager
        context_prompt = conversation_manager.get_conversation_context_prompt(session_id, limit=8)

        EXTRACTION_PROMPT = """You are a multilingual natural language understanding engine for an AI mobile personal assistant.
Analyze the user request and extract communication action and parameters into a clean JSON object.
The user request may be in English, Hindi, Hinglish, Spanish, or any other language.

Allowed actions: "whatsapp", "sms", "email", "call", "general"

JSON Schema:
{
  "action": "whatsapp" | "sms" | "email" | "call" | "general",
  "recipient": string or null,
  "message": string or null,
  "subject": string or null
}

Multilingual Instructions:
- "action":
  - "whatsapp" for WhatsApp (e.g. "whatsapp", "ping", "whatsapp bhejo", "whatsapp karo")
  - "sms" for SMS (e.g. "sms", "text", "sms bhejo", "text karo")
  - "email" for Email (e.g. "email", "mail", "email bhejo", "mail karo")
  - "call" for voice calls (e.g. "call", "dial", "phone lagao", "call karo", "ring karo")
  - "general" for other tasks or questions
- "recipient": the target person name, phone number, or email address.
  - Coreference: If the user says "him", "her", "them", "use", "usko", "unhe", "unko", resolve the recipient from Active Conversation Memory if available.
- "subject": email subject line if specified; otherwise null.
  - If the user specifies "in subject [put/write] X", extract X exactly as the subject.
  - Remove filler words like "put", "write", "set" from the extracted subject value.
- "message": the body text or content to communicate.
  - If the user specifies "in content/body [put/write] Y", extract Y exactly as the message.
  - If the user says "wohi message", "same message", "that message", resolve from Active Conversation Memory if available.
  - Remove filler words like "put", "write", "set" from the extracted message value.
- Output ONLY valid JSON.
"""
        prompt_content = f"{context_prompt}\n\nUser request: {user_message}" if context_prompt else f"User request: {user_message}"

        try:
            res = await self.llm.ainvoke([
                SystemMessage(content=EXTRACTION_PROMPT),
                HumanMessage(content=prompt_content)
            ])
            raw = res.content.strip()
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            first_b = cleaned.find("{")
            last_b = cleaned.rfind("}")
            if first_b != -1 and last_b != -1 and last_b > first_b:
                cleaned = cleaned[first_b:last_b+1]
            data = json.loads(cleaned.strip())

            u_lower = user_message.lower()
            rec = data.get("recipient")

            # Check if recipient was resolved from conversation memory trajectory
            state = conversation_manager.get_state(session_id)
            last_c = state.get("last_contact")
            is_from_memory = False
            if last_c and rec:
                last_name = (last_c.get("name") or "").lower()
                last_phone = str(last_c.get("phone") or "")
                if rec.lower() in last_name or rec in last_phone:
                    is_from_memory = True

            from app.agent.contact_matcher import ContactMatcher
            u_latin = ContactMatcher.devanagari_to_latin(u_lower)

            # If not from memory, check that recipient text appears in user's prompt or transliterated Hindi phrasing
            if rec and not is_from_memory:
                rec_clean = ContactMatcher.devanagari_to_latin(rec.lower().strip())
                rec_clean = re.sub(r"\(you\)", "", rec_clean).strip()
                tokens = [w for w in re.findall(r"\b[a-zA-Z0-9_\-\+@\.]+\b", rec_clean) if len(w) >= 3 and w not in ["the", "contact", "person", "usko", "unko", "unhe"]]
                if tokens and not any((t in u_lower or t in u_latin) for t in tokens):
                    data["recipient"] = None

            msg_content = data.get("message")
            last_msg = state.get("last_message")
            is_msg_from_memory = False
            if last_msg and msg_content and msg_content.strip() == last_msg.strip():
                is_msg_from_memory = True

            # Check if user asked for English translation or cross-lingual translation
            is_translation_requested = bool(re.search(r"\b(in\s+english|angrezi|translate|in\s+hindi|in\s+spanish)\b", u_lower))

            if msg_content and not is_msg_from_memory and not is_translation_requested:
                msg_clean = ContactMatcher.devanagari_to_latin(msg_content.lower().strip())
                tokens = [w for w in re.findall(r"\b[a-zA-Z0-9]+\b", msg_clean) if len(w) >= 3 and w not in ["that", "with", "saying", "please", "wohi", "same"]]
                if tokens and not any((t in u_lower or t in u_latin) for t in tokens):
                    data["message"] = None

            # Anti-hallucination guardrails for communication actions:
            act = (data.get("action") or "").lower()
            if act == "email" and not any(w in u_lower for w in ["email", "mail", "gmail", "inbox", "@"]):
                data["action"] = "general"
                data["recipient"] = None
                data["message"] = None
                data["subject"] = None
            elif act == "call" and not any(w in u_lower for w in ["call", "phone", "dial", "ring", "lagao", "milaao", "llama", "llamar"]):
                data["action"] = "general"
                data["recipient"] = None
            elif act == "sms" and not any(w in u_lower for w in ["sms", "text", "sandesh", "mensaje"]):
                data["action"] = "general"
                data["recipient"] = None
            elif act == "whatsapp" and not any(w in u_lower for w in ["whatsapp", "wp", "wa", "ping", "व्हाट्सएप", "व्हाट्सऐप"]):
                data["action"] = "general"
                data["recipient"] = None

            # Refine email parameters: exact subject/body separation and filler word removal
            if act == "email" or "@" in u_lower or any(w in u_lower for w in ["email", "mail"]):
                p_subj, p_body = self.extract_email_subject_and_body(user_message)
                if p_subj:
                    data["subject"] = p_subj
                if p_body:
                    data["message"] = p_body

                # Check if explicit email address is in prompt
                m_direct_email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", user_message)
                if m_direct_email:
                    data["recipient"] = m_direct_email.group(1).strip()
                    data["action"] = "email"

            if data.get("subject"):
                data["subject"] = re.sub(r'^(?:put|write|set|enter|use|add|as)\s+', '', data["subject"], flags=re.IGNORECASE).strip()
                data["subject"] = re.sub(r'["\']+$', '', data["subject"]).strip()
            if data.get("message"):
                data["message"] = re.sub(r'^(?:put|write|set|enter|use|add|as)\s+', '', data["message"], flags=re.IGNORECASE).strip()
                data["message"] = re.sub(r'["\']+$', '', data["message"]).strip()

            logger.info(f"Gemma extracted intent: {data}")
            return data
        except Exception as e:
            logger.warning(f"Gemma intent extraction failed: {e}")
            return None

    async def translate_to_english_if_needed(self, text: str, user_request: str) -> str:
        """
        Translates Hindi/multilingual message intent to natural English if the user explicitly requested 'in English'.
        Handles common conversational inquiries ('how are you' / 'kaise ho aap') and uses local Gemma for complex text.
        """
        if not text:
            return text
        is_eng_req = bool(re.search(r"\b(in\s+english|angrezi\s+me|english\s+me|in\s+inglés)\b", user_request, re.IGNORECASE))
        if not is_eng_req:
            return text

        clean_t = text.strip()
        clean_t_clean = re.sub(r"[.?!,।]+$", "", clean_t).strip()

        # Deterministic common conversational translations
        translations = {
            "कैसे हो आप": "How are you?",
            "कैसे हो": "How are you?",
            "आप कैसे हैं": "How are you?",
            "kaise ho aap": "How are you?",
            "kaise ho": "How are you?",
            "kya haal hai": "How are you?",
            "क्या कर रहे हो": "What are you doing?",
            "kya kar rahe ho": "What are you doing?",
            "कहाँ हो": "Where are you?",
            "kaha ho": "Where are you?",
            "कब आ रहे हो": "When are you coming?",
            "kab aa rahe ho": "When are you coming?",
            "मैं आ रहा हूँ": "I am on my way",
            "main aa raha hu": "I am on my way",
            "मैं 10 मिनट में आ रहा हूँ": "I will be there in 10 minutes",
            "main 10 minute me aa raha hu": "I will be there in 10 minutes"
        }
        for k, v in translations.items():
            if k == clean_t_clean or k in clean_t_clean or clean_t_clean in k:
                return v

        # If not in table, query local Gemma for natural translation
        try:
            if self.llm:
                res = await self.llm.ainvoke([
                    SystemMessage(content="Translate this Hindi text into natural English for a chat message. Output ONLY the concise English translation, with no explanation."),
                    HumanMessage(content=clean_t)
                ])
                translated = res.content.strip().split("\n")[0].strip('"\'')
                if translated and len(translated) > 1:
                    return translated
        except Exception:
            pass

        return clean_t

    async def resolve_target_contact(self, target: str, preferred_channel: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Resolves target into contact name, email, and phone number using intelligent ContactMatcher
        which aggregates ADB contacts, WhatsApp sync, Gmail contacts, MongoDB, and direct digits/emails
        with phonetic and fuzzy similarity matching for voice typo tolerance.
        """
        from app.agent.contact_matcher import ContactMatcher
        clean_target = target.strip()
        clean_target = re.sub(r"[.?!,।]+$", "", clean_target).strip()
        clean_target = re.sub(r"(?:\b(?:please|now|right now|ko|se|par|pe|karein|karo|bhejo|lagao)\b|[,\s]*(?:को|से|पर|पे|भेजो|करो|लगाओ))", "", clean_target, flags=re.IGNORECASE).strip()
        clean_target = re.sub(r"\(you\)", "", clean_target, flags=re.IGNORECASE).strip()

        # 0. Check if target is an explicit email address
        if "@" in clean_target:
            m_email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", clean_target)
            em = m_email.group(1).strip() if m_email else clean_target
            try:
                from app.device.adb_controller import adb_controller
                if adb_controller.is_device_connected:
                    c = adb_controller.find_email_contact(em)
                    if c:
                        return {"name": c["name"], "email": em, "phone": em, "status": "exact"}
            except Exception as e:
                logger.debug(f"Email contact lookup error: {e}")
            return {"name": em, "email": em, "phone": em, "status": "exact"}

        # 1. Direct phone numbers or emergency services
        digits = re.sub(r"[^\d]", "", clean_target)
        if len(digits) >= 5 or digits in ["911", "112", "100", "108"]:
            from app.db.mongo import get_contacts
            try:
                contacts = await get_contacts(search=digits)
                if contacts:
                    for c in contacts:
                        if re.sub(r"[^\d]", "", c.get("phone", "")) == digits:
                            return {"name": c.get("name", clean_target), "phone": c.get("phone", clean_target), "email": c.get("email"), "status": "exact"}
            except Exception:
                pass
            return {"name": clean_target, "phone": clean_target, "status": "exact"}

        # 2. Intelligent fuzzy match across all unified sources
        match_res = ContactMatcher.match_contact(clean_target, preferred_channel=preferred_channel)
        if match_res["status"] == "exact":
            c = dict(match_res["contact"])
            c["status"] = "exact"
            return c
        elif match_res["status"] == "high_confidence":
            c = dict(match_res["contact"])
            c["status"] = "high_confidence"
            c["match_note"] = f"I matched '{clean_target}' to {c['name']} ({int(match_res['score'])}% similarity)."
            return c
        elif match_res["status"] == "medium_confidence":
            c = dict(match_res["contact"])
            c["status"] = "medium_confidence"
            c["needs_fuzzy_confirmation"] = True
            c["match_score"] = match_res["score"]
            c["original_query"] = clean_target
            return c
        else:
            return None

    async def prepare_messaging_confirmation(
        self,
        session_id: str,
        channel: str,
        action: str,
        resolved: Dict[str, str],
        message_body: str,
        subject: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validates WhatsApp / SMS / Email availability, builds descriptive prompt specifying
        WHO and WHAT is being sent, and registers pre-confirmation.
        """
        from app.device.adb_controller import adb_controller

        if channel == "Email":
            target_email = resolved.get("email")
            if not target_email or "@" not in target_email:
                c = adb_controller.find_email_contact(resolved.get("name", ""))
                if c:
                    target_email = c["email"]
                    resolved["email"] = c["email"]
                else:
                    target_email = resolved.get("name") or resolved.get("phone")

            check = adb_controller.verify_email_availability(target_email, resolved.get("name"))
            if not check.get("available", False):
                return {
                    "reply": f"Cannot send email: {check.get('error', 'Invalid email address')}. Please check and provide a valid email.",
                    "tool_calls": [],
                    "confirmation_required": False
                }

            valid_email = check["email"]
            c_name = check.get("name") or resolved.get("name")
            display = f"{c_name} ({valid_email})" if c_name and c_name != valid_email else valid_email
            subj = subject or "Message from AgentTool Assistant"

            if check.get("status") == "existing_contact":
                status_note = f"I found {display} in your email contacts."
            else:
                status_note = f"I verified that {valid_email} is a valid email address."

            confirm_prompt = f"{status_note} I am ready to send the email with subject: \"{subj}\" and message: \"{message_body}\". Would you like me to send this email? Please confirm with yes or no."

            if resolved.get("match_note"):
                confirm_prompt = f"{resolved['match_note']} {confirm_prompt}"

            pending_data = {
                "action": "send_email",
                "channel": "Email",
                "args": {
                    "to_email": valid_email,
                    "message": message_body,
                    "subject": subj,
                    "contact_name": c_name
                },
                "display_target": display,
                "message": message_body,
                "subject": subj,
                "to_email": valid_email,
                "contact_name": c_name
            }
            self.pending_confirmations[session_id] = pending_data
            from app.agent.conversation_memory import conversation_manager
            conversation_manager.update_state(
                session_id=session_id,
                contact=resolved,
                channel="Email",
                message=message_body,
                subject=subj,
                action="send_email"
            )
            return {
                "reply": confirm_prompt,
                "tool_calls": [],
                "confirmation_required": True,
                "pending_action": pending_data
            }

        display = f"{resolved['name']} ({resolved['phone']})" if resolved['name'] != resolved['phone'] else resolved['phone']

        if channel == "WhatsApp":
            check = adb_controller.verify_whatsapp_availability(resolved["phone"], resolved["name"])
            if not check.get("available", False):
                # WhatsApp not available for this number! Offer SMS alternative
                self.pending_inputs[session_id] = {
                    "action": "send_sms",
                    "channel": "SMS",
                    "target": resolved["name"],
                    "resolved": resolved,
                    "message": message_body,
                    "waiting_for": "switch_to_sms"
                }
                return {
                    "reply": f"WhatsApp is not available for {display}. Would you like me to send an SMS instead?",
                    "tool_calls": [],
                    "confirmation_required": False
                }

            if check.get("status") == "existing_contact":
                status_note = f"I found {display} in your WhatsApp contacts."
            else:
                status_note = f"I searched and verified that WhatsApp is available for {display}."

            confirm_prompt = f"{status_note} I am ready to send: \"{message_body}\". Would you like me to send this message? Please confirm with yes or no."

        else: # SMS
            check = adb_controller.verify_sms_availability(resolved["phone"], resolved["name"])
            if not check.get("available", False):
                return {
                    "reply": f"The phone number for {display} is invalid for sending SMS.",
                    "tool_calls": []
                }
            confirm_prompt = f"I found {display}. I am ready to send SMS: \"{message_body}\". Would you like me to send this SMS? Please confirm with yes or no."

        if resolved.get("match_note"):
            confirm_prompt = f"{resolved['match_note']} {confirm_prompt}"

        pending_data = {
            "action": action,
            "channel": channel,
            "args": {"phone_number": resolved["phone"], "message": message_body, "contact_name": resolved["name"]},
            "display_target": display,
            "message": message_body,
            "contact_name": resolved["name"],
            "phone_number": resolved["phone"]
        }
        self.pending_confirmations[session_id] = pending_data
        from app.agent.conversation_memory import conversation_manager
        conversation_manager.update_state(
            session_id=session_id,
            contact=resolved,
            channel=channel,
            message=message_body,
            action=action
        )
        return {
            "reply": confirm_prompt,
            "tool_calls": [],
            "confirmation_required": True,
            "pending_action": pending_data
        }

    async def run_hybrid_intent_analysis(self, user_message: str, session_id: str = "default") -> Dict[str, Any]:
        """
        Extracts action intents and combines with local Gemma for natural responses.
        Enforces user confirmation before calling or messaging, and asks clarifying questions
        when parameters are missing (slot filling).
        """
        # ---------------------------------------------------------
        # -0.5 Wake-Word & Greeting Handler ("Hi Agent", "Hello Agent", "Namaste Agent", "Suno Agent")
        # ---------------------------------------------------------
        clean_user_msg = user_message.strip()
        wake_match = re.match(r"^(?:(?:hi|hello|hey|ok|okay|namaste|suno|oye|hola)\s+)?agent[\s.!?]*$", clean_user_msg, re.IGNORECASE)
        if wake_match or clean_user_msg.lower() in [
            "agent", "hi agent", "hello agent", "hey agent", "ok agent", "okay agent",
            "namaste agent", "suno agent", "oye agent", "hola agent"
        ]:
            return {
                "reply": "Hello! I'm listening. How can I help you?",
                "tool_calls": [],
                "confirmation_required": False
            }

        # Strip wake-word prefix if user preceded command with it (e.g. "Hi Agent, send whatsapp...", "Namaste Agent...")
        clean_user_msg = re.sub(r"^(?:(?:hi|hello|hey|ok|okay|namaste|suno|oye|hola)\s+)?agent[,:\s]+", "", clean_user_msg, flags=re.IGNORECASE).strip()
        msg = clean_user_msg.lower().strip()
        executed_tools = []

        # ---------------------------------------------------------
        # 0. Check if this session is waiting for missing information (Slot-Filling)
        # ---------------------------------------------------------
        if session_id in self.pending_inputs:
            p_input = self.pending_inputs[session_id]
            waiting = p_input.get("waiting_for")
            action = p_input.get("action", "send_whatsapp_message")
            channel = p_input.get("channel", "WhatsApp")

            # Check if user wants to cancel the pending operation
            if re.search(r"\b(cancel|stop|never\s*mind|nevermind|abort|don't|no|nahi|nahin|mat bhejo|mat karo|cancel karo|roko|rehne do|rehnedo|nahi chahiye)\b", msg) and waiting not in ["switch_to_sms", "fuzzy_contact_confirm"]:
                self.pending_inputs.pop(session_id, None)
                return {"reply": f"Cancelled {channel} operation.", "tool_calls": [], "confirmation_required": False}

            if waiting == "fuzzy_contact_confirm":
                self.pending_inputs.pop(session_id, None)
                # Check refusals first: "mat karo" / "don't" contain yes-words like "karo"
                is_refusal = re.search(r"\b(no|nope|nah|cancel|stop|abort|don't|do not|wrong|galat|nahi|nahin|mat|mat karo|mat bhejo|roko|rehne do)\b", msg)
                if not is_refusal and re.search(r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm|do it|proceed|haan|ha|haanji|hanji|theek hai|thik hai|sahi hai|bilkul|zaroor|chalo|pakka|karo|bhejo|lagao|si|sí|claro|correct|right)\b", msg):
                    resolved = p_input["resolved"]
                    resolved.pop("needs_fuzzy_confirmation", None)
                    resolved.pop("original_query", None)
                    action = p_input.get("action", "send_whatsapp_message")
                    channel = p_input.get("channel", "WhatsApp")
                    if action == "make_phone_call":
                        display = f"{resolved['name']} ({resolved['phone']})" if resolved['name'] != resolved['phone'] else resolved['phone']
                        pending_data = {
                            "action": "make_phone_call",
                            "channel": "Call",
                            "args": {"phone_number": resolved["phone"], "contact_name": resolved["name"]},
                            "display_target": display,
                            "contact_name": resolved["name"],
                            "phone_number": resolved["phone"]
                        }
                        self.pending_confirmations[session_id] = pending_data
                        from app.agent.conversation_memory import conversation_manager
                        conversation_manager.update_state(session_id=session_id, contact=resolved, channel="Call", action="make_phone_call")
                        return {
                            "reply": f"Do you want to call {display}? Say 'yes' to call or 'no' to cancel.",
                            "tool_calls": [],
                            "confirmation_required": True,
                            "pending_action": pending_data
                        }
                    else:
                        return await self.prepare_messaging_confirmation(
                            session_id=session_id,
                            channel=channel,
                            action=action,
                            resolved=resolved,
                            message_body=p_input.get("message", ""),
                            subject=p_input.get("subject")
                        )
                else:
                    return {"reply": "Okay, operation cancelled. Who would you like to contact?", "tool_calls": [], "confirmation_required": False}

            elif waiting == "switch_to_sms":
                # User asked whether to switch to SMS after WhatsApp wasn't available
                self.pending_inputs.pop(session_id, None)
                if re.search(r"\b(yes|yeah|sure|ok|okay|please|confirm|do it|proceed|sms|text)\b", msg):
                    return await self.prepare_messaging_confirmation(
                        session_id=session_id,
                        channel="SMS",
                        action="send_sms",
                        resolved=p_input["resolved"],
                        message_body=p_input.get("message", "")
                    )
                else:
                    return {"reply": "SMS cancelled.", "tool_calls": [], "confirmation_required": False}

            elif waiting == "channel_choice":
                # User is choosing communication channel (WhatsApp, SMS, or Email)
                self.pending_inputs.pop(session_id, None)
                if re.search(r"\b(cancel|stop|nevermind|no|don't|nahi|nahin|mat karo)\b", msg):
                    return {"reply": "Operation cancelled.", "tool_calls": [], "confirmation_required": False}

                chosen_channel = None
                chosen_action = None
                if bool(re.search(r"\b(whatsapp|wa|व्हाट्सएप|व्हाट्सऐप)\b", msg)):
                    chosen_channel = "WhatsApp"
                    chosen_action = "send_whatsapp_message"
                elif bool(re.search(r"\b(sms|text|संदेश|एसएमएस)\b", msg)):
                    chosen_channel = "SMS"
                    chosen_action = "send_sms"
                elif bool(re.search(r"\b(email|mail|ईमेल|डाक)\b", msg)):
                    chosen_channel = "Email"
                    chosen_action = "send_email"
                else:
                    chosen_channel = "WhatsApp"
                    chosen_action = "send_whatsapp_message"

                resolved = p_input.get("resolved")
                message_body = p_input.get("message", "")
                subject = p_input.get("subject")

                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=chosen_channel,
                    action=chosen_action,
                    resolved=resolved,
                    message_body=message_body,
                    subject=subject
                )

            elif waiting == "message":
                # User provided the missing message content (or clarified both recipient and message)
                target = p_input.get("target")
                raw_text = user_message.strip()
                cleaned_text = re.sub(r"^(?:tell\s+(?:him|her|them)|saying|that|say)\s+", "", raw_text, flags=re.IGNORECASE).strip()

                # Check if user also changed or clarified recipient in this turn (e.g. "to Sunny: please join")
                m_sub = re.search(r'^(?:to\s+)?([a-zA-Z0-9_\s\(\)]+?)(?:\s*(?:saying|that|with message|message:|:)\s*["\'\s]*(.+?)["\'\s]*$)', raw_text, re.IGNORECASE)
                if m_sub:
                    target = m_sub.group(1).strip()
                    cleaned_text = m_sub.group(2).strip()

                self.pending_inputs.pop(session_id, None)

                resolved = await self.resolve_target_contact(target, preferred_channel=channel)
                if not resolved:
                    if "@" in target:
                        resolved = {"name": target, "email": target, "phone": target}
                    else:
                        self.pending_inputs[session_id] = {
                            "action": action,
                            "channel": channel,
                            "message": cleaned_text,
                            "waiting_for": "target",
                            "subject": p_input.get("subject")
                        }
                        return {
                            "reply": f"Could not find contact '{target}' in your contact list. Please check the name or provide their phone number.",
                            "tool_calls": []
                        }

                if resolved.get("needs_fuzzy_confirmation"):
                    self.pending_inputs[session_id] = {
                        "action": action,
                        "channel": channel,
                        "resolved": resolved,
                        "target": resolved["name"],
                        "message": cleaned_text,
                        "subject": p_input.get("subject"),
                        "waiting_for": "fuzzy_contact_confirm"
                    }
                    return {
                        "reply": f"I couldn't find an exact match for '{target}'. Did you mean {resolved['name']}? Say 'yes' to proceed with sending or tell me who you want to message.",
                        "tool_calls": [],
                        "confirmation_required": False
                    }

                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=channel,
                    action=action,
                    resolved=resolved,
                    message_body=cleaned_text,
                    subject=p_input.get("subject")
                )

            elif waiting == "target":
                # User provided the missing recipient (and optionally also the message)!
                raw_text = user_message.strip()
                extracted_target = None
                extracted_msg = None

                # Check if user provided both recipient and message in this turn
                m_sub = re.search(r'^(?:to\s+)?([a-zA-Z0-9_\s\(\)]+?)(?:\s*(?:saying|that|with message|message:|:)\s*["\'\s]*(.+?)["\'\s]*$)', raw_text, re.IGNORECASE)
                if m_sub:
                    extracted_target = m_sub.group(1).strip()
                    extracted_msg = m_sub.group(2).strip()
                else:
                    extracted_target = raw_text

                target = extracted_target or raw_text
                target = re.sub(r"[.?!,]+$", "", target).strip()
                target = re.sub(r"\b(?:please|now|right now)\b", "", target, flags=re.IGNORECASE).strip()
                target = re.sub(r"^to\s+", "", target, flags=re.IGNORECASE).strip()

                message_body = extracted_msg or p_input.get("message")

                # If message is STILL missing, ask the follow-up question!
                if not message_body:
                    self.pending_inputs[session_id] = {
                        "action": action,
                        "channel": channel,
                        "target": target,
                        "waiting_for": "message",
                        "subject": p_input.get("subject")
                    }
                    return {
                        "reply": f"What message would you like to send to {target} on {channel}?",
                        "tool_calls": [],
                        "confirmation_required": False
                    }

                self.pending_inputs.pop(session_id, None)

                resolved = await self.resolve_target_contact(target, preferred_channel=channel)
                if not resolved:
                    if "@" in target:
                        resolved = {"name": target, "email": target, "phone": target}
                    else:
                        self.pending_inputs[session_id] = {
                            "action": action,
                            "channel": channel,
                            "message": message_body,
                            "waiting_for": "target",
                            "subject": p_input.get("subject")
                        }
                        return {
                            "reply": f"Could not find contact '{target}' in your contact list. Please check the name or provide their phone number.",
                            "tool_calls": []
                        }

                if resolved.get("needs_fuzzy_confirmation"):
                    self.pending_inputs[session_id] = {
                        "action": action,
                        "channel": channel,
                        "resolved": resolved,
                        "target": resolved["name"],
                        "message": message_body,
                        "subject": p_input.get("subject"),
                        "waiting_for": "fuzzy_contact_confirm"
                    }
                    return {
                        "reply": f"I couldn't find an exact match for '{target}'. Did you mean {resolved['name']}? Say 'yes' to proceed with sending or tell me who you want to message.",
                        "tool_calls": [],
                        "confirmation_required": False
                    }

                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=channel,
                    action=action,
                    resolved=resolved,
                    message_body=message_body,
                    subject=p_input.get("subject")
                )

            elif waiting == "call_target":
                # User provided who to call
                target = user_message.strip()
                self.pending_inputs.pop(session_id, None)

                resolved = await self.resolve_target_contact(target, preferred_channel="Call")
                if not resolved:
                    return {
                        "reply": f"Could not find contact '{target}' in your contact list. Please check the name or save the contact first.",
                        "tool_calls": []
                    }

                display = f"{resolved['name']} ({resolved['phone']})" if resolved['name'] != resolved['phone'] else resolved['phone']
                pending_data = {
                    "action": "make_phone_call",
                    "channel": "Call",
                    "args": {"phone_number": resolved["phone"], "contact_name": resolved["name"]},
                    "display_target": display,
                    "contact_name": resolved["name"],
                    "phone_number": resolved["phone"]
                }
                self.pending_confirmations[session_id] = pending_data
                from app.agent.conversation_memory import conversation_manager
                conversation_manager.update_state(session_id=session_id, contact=resolved, channel="Call", action="make_phone_call")
                return {
                    "reply": f"Do you want to call {display}? Say 'yes' to call or 'no' to cancel.",
                    "tool_calls": [],
                    "confirmation_required": True,
                    "pending_action": pending_data
                }

        # ---------------------------------------------------------
        # 0.5 Multi-Turn Conversation Coreference & Context Resolution
        # ---------------------------------------------------------
        from app.agent.conversation_memory import conversation_manager
        # A short "yes call him" / "ok send it" answers the pending question: don't re-route it via its pronoun
        is_short_confirmation = (
            session_id in self.pending_confirmations
            and len(msg.split()) <= 3
            and re.search(r"\b(yes|yeah|yep|ok|okay|sure|haan|ha|haanji|confirm|go ahead|do it|si|sí)\b", msg)
            and not re.search(r"\b(no|not|don't|nahi|mat|actually|instead|change)\b", msg)
        )
        coref = {} if is_short_confirmation else conversation_manager.resolve_coreference(session_id, user_message)

        # Handle message modification: "change message to...", "actually say..."
        if coref.get("is_message_modification"):
            new_msg = coref["new_message"]
            c_contact = coref.get("resolved_contact") or conversation_manager.get_state(session_id).get("last_contact")
            c_channel = coref.get("resolved_channel") or conversation_manager.get_state(session_id).get("last_message_channel") or "WhatsApp"
            if c_contact and c_channel:
                c_action = "send_email" if c_channel == "Email" else ("send_whatsapp_message" if c_channel == "WhatsApp" else "send_sms")
                self.pending_confirmations.pop(session_id, None)
                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=c_channel,
                    action=c_action,
                    resolved=c_contact,
                    message_body=new_msg,
                    subject=coref.get("resolved_subject")
                )

        # Handle multi-turn coreference & cross-channel shifts:
        # e.g. "Now send that to him on WhatsApp", "Send that same message to him on WhatsApp", "Call him instead", "Send SMS to him"
        if (coref.get("has_pronoun_reference") or coref.get("has_message_reference")):
            self.pending_confirmations.pop(session_id, None)
            if coref.get("resolved_channel") == "Call" and coref.get("resolved_contact"):
                c = coref["resolved_contact"]
                display = f"{c['name']} ({c['phone']})" if c.get('phone') and c['name'] != c['phone'] else c.get('phone', c['name'])
                pending_data = {
                    "action": "make_phone_call",
                    "channel": "Call",
                    "args": {"phone_number": c.get("phone", ""), "contact_name": c["name"]},
                    "display_target": display,
                    "contact_name": c["name"],
                    "phone_number": c.get("phone", "")
                }
                self.pending_confirmations[session_id] = pending_data
                conversation_manager.update_state(session_id=session_id, contact=c, channel="Call", action="make_phone_call")
                return {
                    "reply": f"Do you want to call {display}? Say 'yes' to proceed or 'no' to cancel.",
                    "tool_calls": [],
                    "confirmation_required": True,
                    "pending_action": pending_data
                }

            target_contact = coref.get("resolved_contact")
            target_msg = coref.get("resolved_message")
            target_channel = coref.get("resolved_channel")

            if target_contact and target_msg and target_channel:
                target_action = "send_email" if target_channel == "Email" else ("send_whatsapp_message" if target_channel == "WhatsApp" else "send_sms")
                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=target_channel,
                    action=target_action,
                    resolved=target_contact,
                    message_body=target_msg,
                    subject=coref.get("resolved_subject")
                )

        # ---------------------------------------------------------
        # 0.7 Order Progress, History & Checkpoint Query Handler
        # (e.g. "Where did we get stuck?", "Kahan atak gaye the?", "What happened to my order?",
        #  "What was I ordering?", "Should I proceed from where we stopped?", "Order ka status kya hai?")
        # ---------------------------------------------------------
        from app.agent.task_checkpoint_manager import task_checkpoint_manager
        is_order_status_query = bool(re.search(
            r"\b(where\s+(?:did\s+we|was\s+i|are\s+we|he)\s+(?:get\s+stuck|stuck|stopped|paused)|"
            r"kahan\s+(?:atak|atke|ruke|phanse)\s+(?:the|gaye)|"
            r"kahan\s+tak\s+pahuncha|kahan\s+par\s+hai|"
            r"order\s+ka\s+kya\s+hua|order\s+status|status\s+of\s+(?:my\s+)?order|"
            r"what\s+happened\s+to\s+(?:my\s+)?order|"
            r"what\s+was\s+i\s+ordering|what\s+was\s+the\s+order|"
            r"main\s+kya\s+order\s+kar\s+raha\s+tha|pichhla\s+order|last\s+order|"
            r"should\s+(?:i|we|he)\s+proceed|can\s+we\s+proceed|can\s+we\s+continue|"
            r"kya\s+aage\s+badhein|aage\s+proceed\s+karna\s+chahiye|"
            r"where\s+was\s+stuck\s+last\s+time|progress\s+and\s+history|"
            r"order\s+history|what\s+is\s+the\s+progress)\b",
            msg, re.IGNORECASE
        ))
        if is_order_status_query:
            is_hindi = bool(re.search(r"\b(kahan|kya|hua|karo|atak|atke|ruke|phanse|aage|badhein|tha|thi|the|mera|meri|hum|maine)\b", msg, re.IGNORECASE))
            report = task_checkpoint_manager.format_progress_summary(session_id=session_id, is_hindi=is_hindi)
            return {
                "reply": report,
                "tool_calls": [],
                "confirmation_required": False
            }

        # ---------------------------------------------------------
        # 0.8 Autonomous E-Commerce Task Resumption & Confirmation
        # ---------------------------------------------------------
        from app.agent.ecommerce_agent import ecommerce_agent
        if ecommerce_agent.is_order_confirmation_response(clean_user_msg, session_id):
            is_neg = bool(re.search(r"\b(no|nope|nah|cancel|stop|never\s*mind|don't|nahi|nahin|mat karo)\b", msg))
            is_aff = bool(re.search(r"\b(yes|yeah|yep|yup|sure|ok|okay|proceed|confirm|go ahead|do it|haan|ha|theek hai|bilkul|buy it|order it)\b", msg))
            if is_neg:
                return await ecommerce_agent.handle_order_confirmation(session_id, is_confirmed=False)
            elif is_aff:
                return await ecommerce_agent.handle_order_confirmation(session_id, is_confirmed=True)

        if ecommerce_agent.is_resumption_intent(clean_user_msg, session_id):
            return await ecommerce_agent.resume_autonomous_task(session_id)

        # ---------------------------------------------------------
        # 1. Check if this session has a pending confirmation
        # ---------------------------------------------------------
        if session_id in self.pending_confirmations:
            pending = self.pending_confirmations[session_id]
            channel = pending.get("channel", "Action")

            # If user asks a question about history while a confirmation is pending:
            is_history_in_pending = bool(re.search(
                r"\b(kisko\s+(?:message|phone|call|bheja)|kis\s+ko|kya\s+message|who\s+did\s+i|what\s+did\s+i\s+send|whom\s+did\s+i|what\s+was\s+the\s+message|last\s+(?:contact|person|message)|pichla\s+message|pichhla\s+message|a\s+quién|qué\s+mensaje)\b",
                msg, re.IGNORECASE
            ))
            if is_history_in_pending:
                from app.agent.conversation_memory import conversation_manager
                state = conversation_manager.get_state(session_id)
                c = state.get("last_contact")
                c_name = c.get("name") if c else "someone"
                c_phone = c.get("phone") if c else ""
                c_disp = f"{c_name} ({c_phone})" if c_phone and c_phone != c_name else c_name
                last_ch = state.get("last_channel") or state.get("last_message_channel") or "message"
                last_m = state.get("last_message")

                pending_target = pending.get("display_target") or pending["args"].get("contact_name") or pending["args"].get("phone_number") or pending["args"].get("to_email")
                action_name = "call" if pending["action"] == "make_phone_call" else f"send {channel} to"

                if re.search(r"\b(kisko|kya|bheja|kiya|kise|pichla|maine)\b", msg):
                    reply = f"Aapne haal hi me {c_disp} ke saath {last_ch} par baat ki thi. (Pending confirmation: Kya aap {pending_target} ko {channel} bhejna chahte hain? Say 'yes' or 'no'.)"
                else:
                    reply = f"You recently contacted {c_disp} via {last_ch}. (Pending confirmation: Do you still want to {action_name} {pending_target}? Say 'yes' or 'no'.)"

                return {
                    "reply": reply,
                    "tool_calls": [],
                    "confirmation_required": True,
                    "pending_action": pending
                }

            # 1. Detect negative response (English, Hindi, Hinglish, Spanish)
            is_negative = bool(re.search(
                r"\b(no|nope|nah|cancel|stop|never\s*mind|nevermind|abort|don't|do not|don't send|don't call|nahi|nahin|mat bhejo|mat karo|cancel karo|roko|rehne do|rehnedo|nahi chahiye|para|no enviar)\b",
                msg
            ))
            # 2. Detect affirmative response (English, Hindi, Hinglish, Spanish)
            is_affirmative = bool(re.search(
                r"\b(yes|yeah|yep|yup|sure|ok|okay|proceed|confirm|go ahead|do it|haan|ha|haanji|hanji|bhej do|kar do|kardo|theek hai|thik hai|sahi hai|bilkul|zaroor|chalo|pakka|call karo|call lagao|send it|si|sí|claro|dale|adelante)\b",
                msg
            )) or (msg.strip() in ["bhejo", "karo", "call", "dial", "send", "haan", "ha"] and not is_negative)
            # "ok, actually call Mom instead" is a correction, not a confirmation of the old action
            is_correction = bool(re.search(r"\b(actually|instead|rather|wait|change|not\s+that|balki|nahi\s+balki|en\s+vez)\b", msg))
            is_new_command = bool(re.search(r"\b(?:call|dial|phone|whatsapp|sms|text|message|email|mail|search|save|open|llama|llamar|manda|mandar|envia|enviar|lagao|bhejo)\b", msg, re.IGNORECASE))
            # A long reply that also contains a command verb is ambiguous: re-ask instead of guessing
            is_ambiguous_confirm = is_affirmative and is_new_command and len(msg.split()) > 3

            if is_correction:
                self.pending_confirmations.pop(session_id, None)
                logger.info(f"Discarded pending {channel} for session '{session_id}': user issued a correction")
            elif is_affirmative and not is_negative and not is_ambiguous_confirm:
                # User confirmed! Execute the pending action
                self.pending_confirmations.pop(session_id, None)
                action = pending["action"]
                tool_args = pending["args"]
                tool_res = await self.execute_tool(action, tool_args)
                executed_tools.append({"tool": action, "args": tool_args, "result": tool_res})
                display = pending.get("display_target") or tool_args.get("contact_name") or tool_args.get("phone_number") or tool_args.get("to_email")

                # Only claim success when the tool confirmed it; otherwise relay what actually happened
                if not tool_res.startswith("SUCCESS"):
                    reply = tool_res.split(":", 1)[1].strip() if ":" in tool_res else tool_res
                elif action == "make_phone_call":
                    reply = f"Calling {display} now on your mobile device."
                elif action == "send_whatsapp_message":
                    reply = f"Sent WhatsApp message to {display}: \"{pending.get('message', '')}\"."
                elif action == "send_sms":
                    reply = f"Sent SMS message to {display}: \"{pending.get('message', '')}\"."
                elif action == "send_email":
                    reply = f"Sent email to {display} with subject \"{pending.get('subject', 'Message')}\": \"{pending.get('message', '')}\"."
                else:
                    reply = f"Executed {action} for {display}."

                return {
                    "reply": reply,
                    "tool_calls": executed_tools,
                    "confirmation_required": False
                }

            elif is_negative:
                # User cancelled!
                self.pending_confirmations.pop(session_id, None)
                display = pending.get("display_target") or pending["args"].get("contact_name") or pending["args"].get("phone_number") or pending["args"].get("to_email")
                return {
                    "reply": f"{channel} to {display} cancelled.",
                    "tool_calls": [],
                    "confirmation_required": False
                }

            else:
                # User gave neither a clear yes nor no: a new command discards the stale confirmation
                if is_new_command and not is_ambiguous_confirm:
                    # Discard stale pending confirmation and proceed to parse the new command
                    self.pending_confirmations.pop(session_id, None)
                else:
                    # Ambiguous message while pending: re-ask clearly
                    display = pending.get("display_target") or pending["args"].get("contact_name") or pending["args"].get("phone_number") or pending["args"].get("to_email")
                    action_name = "call" if pending["action"] == "make_phone_call" else f"send {channel} to"
                    return {
                        "reply": f"Please confirm: Do you want to {action_name} {display}? Say 'yes' to proceed or 'no' to cancel.",
                        "tool_calls": [],
                        "confirmation_required": True,
                        "pending_action": pending
                    }

        # ---------------------------------------------------------
        # 1.2 Autonomous Mobile E-Commerce & Shopping Flow
        # (e.g. "go to flipkart and order a pen", "flipkart se pen order karo lowest price wala")
        # ---------------------------------------------------------
        # "click the buy button" / "tap pay": the agent never buys or pays, so say so instead of starting a search
        if (re.search(r"\b(click|tap|press|hit|dabao|daba\s+do|click\s+karo)\b", msg)
                and re.search(r"\b(buy|order|pay|payment|checkout|place\s+order|kharido)\b", msg)):
            return {
                "reply": ("For your safety I never tap Buy, Place order or Pay. The product is open on your phone: "
                          "tap Buy there yourself to finish. I can search or compare something else for you."),
                "tool_calls": [],
                "confirmation_required": False
            }

        if ecommerce_agent.is_ecommerce_intent(clean_user_msg):
            parsed = ecommerce_agent.parse_ecommerce_intent(clean_user_msg)
            return await ecommerce_agent.run_autonomous_shopping_flow(
                session_id=session_id,
                app_name=parsed["app"],
                query=parsed["query"],
                criteria=parsed["criteria"]
            )

        # ---------------------------------------------------------
        # 1.3 Universal Smart Mobile Assistant: Web Search & Installed App Automation
        # (e.g. "how to on developer mode in android", "open calculator", "youtube pe lofi music chalaao")
        # ---------------------------------------------------------
        from app.agent.universal_mobile_agent import universal_mobile_agent
        if universal_mobile_agent.is_universal_intent(clean_user_msg, session_id=session_id):
            return await universal_mobile_agent.execute_task(clean_user_msg, session_id=session_id)

        # ---------------------------------------------------------
        # 1.5 Gemma Natural Language Parameter Extraction & Multilingual Direct Matching
        # ---------------------------------------------------------
        gemma_data = await self.extract_intent_with_gemma(clean_user_msg, session_id=session_id)
        g_action = (gemma_data.get("action") or "").lower() if gemma_data else None
        g_recipient = gemma_data.get("recipient") if gemma_data else None
        g_message = gemma_data.get("message") if gemma_data else None
        g_subject = gemma_data.get("subject") if gemma_data else None

        # Multilingual Direct Intent Pattern Matcher (Hindi, Hinglish, Spanish)
        # 1. Hindi Call: "[Recipient] ko phone/call lagao/karo" or "phone/call lagao [Recipient] ko"
        m_h_call = re.search(r"([a-zA-Z0-9_\-\+@\.\s\(\)]+?)\s+ko\s+(?:phone|call|call\s+bhi)?\s*(?:lagao|milaao|karo|dial\s+karo)\b", clean_user_msg, re.IGNORECASE)
        m_h_call_rev = re.search(r"(?:phone|call)\s*(?:lagao|milaao|karo|dial\s+karo)\s+([a-zA-Z0-9_\-\+@\.\s\(\)]+?)(?:\s+ko)?$", clean_user_msg, re.IGNORECASE)
        m_es_call = re.search(r"(?:llama|llamar|haz\s+una\s+llamada)\s+a\s+([a-zA-Z0-9_\-\+@\.\s\(\)]+)", clean_user_msg, re.IGNORECASE)

        if m_h_call or m_h_call_rev or m_es_call:
            cand = m_h_call.group(1).strip() if m_h_call else (m_h_call_rev.group(1).strip() if m_h_call_rev else m_es_call.group(1).strip())
            if cand.lower() not in ["phone", "call", "agent", "usko", "unko", "use", "él", "ella"]:
                g_action = "call"
                g_recipient = cand
            elif cand.lower() in ["usko", "unko", "use", "unhe", "él", "ella"]:
                g_action = "call"

        # ---------------------------------------------------------
        # 1.6 Channel-Ambiguous Contact + Message Directive
        # (e.g. "Abhishek and ask how are you", "ask Abhishek how are you", "tell Abhishek I will be late")
        # When recipient and message are provided but communication channel is omitted,
        # proactively ask the user which channel they prefer: WhatsApp, SMS, or Email!
        # ---------------------------------------------------------
        m_ambig1 = re.search(r'^([a-zA-Z0-9_\u0900-\u097F\s\(\)]+?)\s+(?:and\s+ask|and\s+tell|and\s+say|ko\s+pucho|ko\s+bolo)\s*(?:saying|that|ki|if)?\s*(.+)$', clean_user_msg, re.IGNORECASE)
        m_ambig2 = re.search(r'^(?:ask|tell|ping)\s+([a-zA-Z0-9_\u0900-\u097F\s\(\)]+?)\s+(?:to\s+|that\s+|about\s+|if\s+|saying\s+|:\s*)?([a-zA-Z0-9_\u0900-\u097F\s\?\.\!\'\"]+)$', clean_user_msg, re.IGNORECASE)
        m_ambig = m_ambig1 or m_ambig2

        has_explicit_channel = bool(re.search(r"\b(whatsapp|sms|email|call|phone|dial|व्हाट्सएप|व्हाट्सऐप|एसएमएस|ईमेल)\b", clean_user_msg, re.IGNORECASE))

        if m_ambig and not has_explicit_channel:
            ambig_target = m_ambig.group(1).strip()
            ambig_msg = m_ambig.group(2).strip()

            # Ensure ambig_target is not a system keyword, or the user themself ("tell me a fun fact" is a
            # question for the assistant, not a message to send)
            if ambig_target.lower() not in ["whatsapp", "sms", "email", "message", "agent", "phone", "call",
                                            "me", "us", "myself", "i", "mujhe", "humein", "hume", "hamein", "mere"]:
                resolved = await self.resolve_target_contact(ambig_target)
                if not resolved:
                    resolved = {"name": ambig_target, "phone": ambig_target, "status": "exact"}

                formatted_msg = ambig_msg.strip()
                if formatted_msg:
                    formatted_msg = formatted_msg[0].upper() + formatted_msg[1:]
                    if formatted_msg.lower().startswith(("how", "what", "where", "when", "why", "who", "are", "is", "can", "do")) and not formatted_msg.endswith("?"):
                        formatted_msg += "?"

                # Translate if in English requested
                formatted_msg = await self.translate_to_english_if_needed(formatted_msg, clean_user_msg)

                # Store in pending_inputs waiting for channel selection
                self.pending_inputs[session_id] = {
                    "waiting_for": "channel_choice",
                    "resolved": resolved,
                    "target": resolved["name"],
                    "message": formatted_msg
                }

                target_disp = resolved["name"]
                return {
                    "reply": f"Do you want to send a WhatsApp message, SMS, or Email to {target_disp}?",
                    "tool_calls": [],
                    "confirmation_required": False
                }

        # 2. Hindi Messaging: "[Recipient] ko [Channel] bhejo/karo ki [Message]"
        m_h_msg = re.search(r"([a-zA-Z0-9_\-\+@\.\s\(\)]+?)\s+ko\s+(whatsapp|phone|call|sms|message|email|mail)(?:[a-zA-Z\s]*?)(?:\s+(?:ki|aur\s+likho|likho|bolo|saying|that|with\s+message|message\s*:|:)\s+(.+)$|$)", clean_user_msg, re.IGNORECASE)
        # 3. Hindi Messaging (Channel First): "[Channel] pe [Recipient] ko bhejo ki [Message]"
        m_h_msg2 = re.search(r"(whatsapp|sms|message|email|mail)\s+(?:pe|par|se)?\s*([a-zA-Z0-9_\-\+@\.\s\(\)]+?)\s+ko\s+(?:bhejo|karo|likho)?(?:\s+(?:ki|saying|that|:)\s+(.+)$|$)", clean_user_msg, re.IGNORECASE)
        # 4. Spanish Messaging: "envia un [whatsapp/sms/mensaje] a [Recipient] diciendo [Message]"
        m_es_msg = re.search(r"(?:envia|envía|manda|mandar)\s+(?:un\s+)?(whatsapp|mensaje|sms|correo|email)\s+a\s+([a-zA-Z0-9_\-\+@\.\s\(\)]+?)(?:\s+(?:diciendo|con\s+mensaje|que|:)\s+(.+)$|$)", clean_user_msg, re.IGNORECASE)
        # 5. Devanagari Hindi Messaging: "अभिषेक को message भेजो WhatsApp। यह पूछते हुए कि कैसे हो आप in English"
        m_dev_msg = re.search(
            r'([^\s,।]+)\s+(?:को|ko)\s+(?:message|संदेश|msg)?\s*(?:भेजो|bhejo|karo|करो)?\s*(whatsapp|व्हाट्सएप|व्हाट्सऐप|sms|एसएमएस|email|ईमेल)?'
            r'(?:[।\s,]+)?(?:(?:यह\s+)?(?:पूछते|कहते)\s+हुए\s+कि|(?:yeh\s+)?(?:puchte|kehte)\s+hue\s+ki|ki|कि|saying|that)?\s*(.+?)(?:\s+(?:in\s+english|angrezi\s+me|english\s+me))?$',
            clean_user_msg, re.IGNORECASE
        )

        matched_multi = m_dev_msg or m_h_msg or m_h_msg2 or m_es_msg
        if matched_multi:
            if m_dev_msg:
                t_cand = m_dev_msg.group(1).strip()
                ch_cand = (m_dev_msg.group(2) or "whatsapp").lower()
                raw_m = m_dev_msg.group(3).strip() if m_dev_msg.group(3) else None
                clean_m = re.sub(r"^(?:pe|par|se)?\s*(?:bhejo|karo|likho|send)\s*(?:ki|कि|saying|that|:)?\s*", "", raw_m or "", flags=re.IGNORECASE).strip()
                clean_m = re.sub(r"\s*(?:pe|par|se)?\s*(?:bhejo|karo|likho)$", "", clean_m, flags=re.IGNORECASE).strip()
                if clean_m.lower() in ["bhejo", "karo", "se bhejo", "pe bhejo", "ki", ""]:
                    m_cand = None
                else:
                    m_cand = clean_m
                if m_cand:
                    m_cand = await self.translate_to_english_if_needed(m_cand, clean_user_msg)
            elif m_h_msg:
                t_cand = m_h_msg.group(1).strip()
                ch_cand = m_h_msg.group(2).lower()
                m_cand = m_h_msg.group(3).strip() if m_h_msg.group(3) else None
                if m_cand:
                    m_cand = await self.translate_to_english_if_needed(m_cand, clean_user_msg)
            elif m_h_msg2:
                ch_cand = m_h_msg2.group(1).lower()
                t_cand = m_h_msg2.group(2).strip()
                m_cand = m_h_msg2.group(3).strip() if m_h_msg2.group(3) else None
                if m_cand:
                    m_cand = await self.translate_to_english_if_needed(m_cand, clean_user_msg)
            else:
                ch_cand = m_es_msg.group(1).lower()
                t_cand = m_es_msg.group(2).strip()
                m_cand = m_es_msg.group(3).strip() if m_es_msg.group(3) else None

            if "whatsapp" in ch_cand or "व्हाट्सएप" in ch_cand or "व्हाट्सऐप" in ch_cand:
                g_action = "whatsapp"
            elif "sms" in ch_cand or "mensaje" in ch_cand or "message" in ch_cand or "एसएमएस" in ch_cand:
                g_action = "sms"
            elif "email" in ch_cand or "mail" in ch_cand or "correo" in ch_cand or "ईमेल" in ch_cand:
                g_action = "email"

            if t_cand and t_cand.lower() not in ["whatsapp", "sms", "email", "message", "usko", "unko", "use", "él", "ella"]:
                t_cand = re.sub(r"^(?:wohi|wahi|vahi|woh|same|that|yeh|yehi)\s+(?:message|msg|text)\s*", "", t_cand, flags=re.IGNORECASE).strip()
                t_cand = re.sub(r"\s+(?:se|pe|par)$", "", t_cand, flags=re.IGNORECASE).strip()
                g_recipient = t_cand
            if m_cand:
                g_message = m_cand

        # ---------------------------------------------------------
        # 1.8 Check if message is a Question/Query about History or Status
        # (e.g. "Maine abhi kisko message bheja tha?", "Who did I text?", "What was the message?")
        # These are NOT new messaging requests!
        # ---------------------------------------------------------
        is_history_query = bool(re.search(
            r"\b(kisko\s+(?:message|phone|call|bheja)|kis\s+ko|kya\s+message|who\s+did\s+i|what\s+did\s+i\s+send|whom\s+did\s+i|what\s+was\s+the\s+message|last\s+(?:contact|person|message)|pichla\s+message|pichhla\s+message|a\s+quién|qué\s+mensaje)\b",
            msg, re.IGNORECASE
        ))
        if is_history_query:
            from app.agent.conversation_memory import conversation_manager
            state = conversation_manager.get_state(session_id)
            if state.get("last_contact") or state.get("last_message"):
                c = state.get("last_contact")
                c_name = c.get("name") if c else "someone"
                c_phone = c.get("phone") if c else ""
                c_disp = f"{c_name} ({c_phone})" if c_phone and c_phone != c_name else c_name
                last_ch = state.get("last_channel") or state.get("last_message_channel") or "message"
                last_m = state.get("last_message")

                if re.search(r"\b(kisko|kya|bheja|kiya|kise|pichla|maine)\b", msg):
                    reply_parts = [f"Aapne haal hi me {c_disp} ke saath {last_ch} par baat ki thi."]
                    if last_m:
                        reply_parts.append(f"Message tha: \"{last_m}\".")
                    return {"reply": " ".join(reply_parts), "tool_calls": []}
                elif re.search(r"\b(quién|qué|último|mensaje)\b", msg):
                    reply_parts = [f"Recientemente te comunicaste con {c_disp} por {last_ch}."]
                    if last_m:
                        reply_parts.append(f"El mensaje fue: \"{last_m}\".")
                    return {"reply": " ".join(reply_parts), "tool_calls": []}
                else:
                    reply_parts = [f"You recently contacted {c_disp} via {last_ch}."]
                    if last_m:
                        reply_parts.append(f"The message was: \"{last_m}\".")
                    return {"reply": " ".join(reply_parts), "tool_calls": []}

        # ---------------------------------------------------------
        # 2. Email Messaging Intent (with Email Validation, Clarifying Questions & Confirmation)
        # ---------------------------------------------------------
        is_email = (bool(re.search(r"\b(?:email|mail|send\s+(?:an?\s+)?email|gmail)\b", msg)) or (g_recipient and "@" in g_recipient) or (g_action == "email" and any(w in msg for w in ["email", "mail", "gmail", "@"])))
        if is_email:
            channel = "Email"
            action = "send_email"

            # Check if there is an explicit email address anywhere in the text or g_recipient
            m_email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", user_message)
            target_val = m_email.group(1).strip() if m_email else None
            if not target_val and g_recipient:
                target_val = g_recipient.strip()

            # Extract subject and message content
            p_subj, p_body = self.extract_email_subject_and_body(user_message)

            subject_val = p_subj
            if not subject_val:
                if g_subject:
                    subject_val = g_subject.strip()
                else:
                    m_subj = re.search(r'(?:with\s+)?subject(?:\s+is|\s*:|\s+as)?\s+["\'\s]*([^,;]+?)(?:["\'\s]*(?:and\s+(?:in\s+)?(?:content|body|message)|(?:saying|that|with body|body|message|$)))', user_message, re.IGNORECASE)
                    if m_subj:
                        subject_val = m_subj.group(1).strip()

            if subject_val:
                subject_val = re.sub(r'^(?:put|write|set|enter|use|add|as)\s+', '', subject_val, flags=re.IGNORECASE).strip()
                subject_val = re.sub(r'["\']+$', '', subject_val).strip()

            msg_val = p_body
            if not msg_val:
                if g_message:
                    msg_val = g_message.strip()
                else:
                    m_body = re.search(r'(?:saying|with message|message:|body:|content:|says)\s+["\'\s]*(.+?)["\'\s]*$', user_message, re.IGNORECASE)
                    if m_body:
                        msg_val = m_body.group(1).strip()
                    elif coref.get("has_message_reference"):
                        msg_val = coref.get("resolved_message")
                    elif ":" in user_message and not target_val:
                        m_colon = re.search(r'(?:email|mail)\s+([^:]+):\s*(.+)$', user_message, re.IGNORECASE)
                        if m_colon:
                            target_val = m_colon.group(1).strip()
                            msg_val = m_colon.group(2).strip()

            if msg_val:
                msg_val = re.sub(r'^(?:put|write|set|enter|use|add|as)\s+', '', msg_val, flags=re.IGNORECASE).strip()
                msg_val = re.sub(r'["\']+$', '', msg_val).strip()

            # If recipient wasn't an email address, extract contact name from "email to [Name]"
            if not target_val:
                m_to = re.search(r'(?:email|mail|send\s+(?:an?\s+)?email)\s+(?:to\s+)?([a-zA-Z0-9_\-\.\+@\s]+?)(?:\s*(?:saying|that|with|subject|:)|$)', user_message, re.IGNORECASE)
                if m_to:
                    candidate = m_to.group(1).strip()
                    if candidate.lower() not in ["email", "an email", "the email", "mail", "message"]:
                        target_val = candidate

            # Clean target
            if target_val:
                target_val = re.sub(r"[.?!,]+$", "", target_val).strip()
                target_val = re.sub(r"\b(?:please|now|right now)\b", "", target_val, flags=re.IGNORECASE).strip()
                target_val = re.sub(r"^to\s+", "", target_val, flags=re.IGNORECASE).strip()
                if target_val.lower() in ["email", "an email", "the email", "mail", "message"]:
                    target_val = None

            if not subject_val:
                subject_val = "Message from AgentTool Assistant"

            # Case A: Both recipient and message are given
            if target_val and msg_val:
                resolved = await self.resolve_target_contact(target_val, preferred_channel="Email")
                if not resolved:
                    if "@" in target_val:
                        resolved = {"name": target_val, "email": target_val, "phone": target_val}
                    else:
                        # Attempt direct email availability check on the target string
                        from app.device.adb_controller import adb_controller
                        check = adb_controller.verify_email_availability(target_val)
                        if not check.get("available", False):
                            return {
                                "reply": f"Cannot send email: {check.get('error', f'Contact {target_val} has no email address')}. Please provide a valid email.",
                                "tool_calls": []
                            }
                        resolved = {"name": check.get("name", target_val), "email": check["email"], "phone": check["email"]}

                if resolved.get("needs_fuzzy_confirmation"):
                    self.pending_inputs[session_id] = {
                        "action": "send_email",
                        "channel": "Email",
                        "resolved": resolved,
                        "target": resolved["name"],
                        "message": msg_val,
                        "subject": subject_val,
                        "waiting_for": "fuzzy_contact_confirm"
                    }
                    return {
                        "reply": f"I couldn't find an exact match for '{target_val}'. Did you mean {resolved['name']}? Say 'yes' to proceed with sending or tell me who you want to email.",
                        "tool_calls": [],
                        "confirmation_required": False
                    }

                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel="Email",
                    action="send_email",
                    resolved=resolved,
                    message_body=msg_val,
                    subject=subject_val
                )

            # Case B: Recipient given, but message missing -> Ask clarifying question!
            elif target_val and not msg_val:
                self.pending_inputs[session_id] = {
                    "action": "send_email",
                    "channel": "Email",
                    "target": target_val,
                    "subject": subject_val,
                    "waiting_for": "message"
                }
                return {
                    "reply": f"What message or subject would you like to send in the email to {target_val}?",
                    "tool_calls": []
                }

            # Case C: Message given, but recipient missing -> Ask clarifying question!
            elif not target_val and msg_val:
                self.pending_inputs[session_id] = {
                    "action": "send_email",
                    "channel": "Email",
                    "message": msg_val,
                    "subject": subject_val,
                    "waiting_for": "target"
                }
                return {
                    "reply": "Who would you like to send this email to?",
                    "tool_calls": []
                }

            # Case D: Generic email request without recipient or message
            else:
                self.pending_inputs[session_id] = {
                    "action": "send_email",
                    "channel": "Email",
                    "subject": subject_val,
                    "waiting_for": "target"
                }
                return {
                    "reply": "Who would you like to send an email to?",
                    "tool_calls": []
                }

        # ---------------------------------------------------------
        # 3. WhatsApp & SMS Messaging Intent (with Clarifying Questions & Confirmation)
        # ---------------------------------------------------------
        is_modification_command = bool(re.search(r"\b(?:change(?:\s+the)?\s+message|actually\s+say|instead\s+say)\b", msg, re.IGNORECASE))
        is_whatsapp = ("whatsapp" in msg or g_action == "whatsapp") and not is_modification_command and not is_history_query
        is_sms = (bool(re.search(r"\b(sms|text message|text)\b", msg)) or g_action == "sms" or (not is_whatsapp and not is_email and "message" in msg and not "read" in msg)) and not is_modification_command and not is_history_query

        if is_whatsapp or is_sms:
            channel = "WhatsApp" if is_whatsapp else "SMS"
            action = "send_whatsapp_message" if is_whatsapp else "send_sms"

            # Check if recipient is at end: e.g. "send whatsapp message saying hello to Shyam (You)"
            m_rev = re.search(r'(?:send\s+(?:a\s+)?(?:whatsapp|sms|text|message)(?:\s+message)?)\s+(?:saying|that)\s+["\'\s]*(.+?)["\'\s]*\s+to\s+([a-zA-Z0-9\s\(\)]+)$', msg, re.IGNORECASE)
            # Standard pattern: e.g. "send whatsapp to Shyam (You) saying hello" or "whatsapp Shyam (You): hello" or "whatsapp Shyam"
            m_std = re.search(r'(?:send\s+(?:a\s+)?(?:whatsapp|sms|text|message)(?:\s+message)?|whatsapp|text|sms|message)\s+(?:to\s+)?([a-zA-Z0-9\s\(\)]+?)(?:\s*(?:saying|that|with message|:)\s*["\'\s]*(.+?)["\'\s]*$|$)', msg, re.IGNORECASE)

            target_val = None
            msg_val = None

            # 1. Prioritize Gemma semantic NLP extraction
            if g_recipient:
                target_val = g_recipient.strip()
            if g_message:
                msg_val = g_message.strip()

            # 2. Check "ping [Name] on whatsapp that [Message]" or "tell [Name] on whatsapp: [Message]"
            if not target_val or not msg_val:
                m_on = re.search(r'(?:(?:ping|message|tell|send)\s+)?([a-zA-Z0-9_\s\(\)]+?)\s+on\s+(?:whatsapp|sms)(?:\s*(?:saying|that|with message|message:|:)\s*["\'\s]*(.+?)["\'\s]*$|$)', msg, re.IGNORECASE)
                if m_on:
                    candidate = m_on.group(1).strip()
                    candidate = re.sub(r"^(?:could\s+you\s+|can\s+you\s+|please\s+)", "", candidate, flags=re.IGNORECASE).strip()
                    if not target_val and candidate.lower() not in ["message", "whatsapp", "sms"]:
                        target_val = candidate
                    if not msg_val and m_on.group(2):
                        msg_val = m_on.group(2).strip()

            # 3. Check standard regex patterns if still missing
            if not target_val or not msg_val:
                if m_rev:
                    if not target_val:
                        target_val = m_rev.group(2).strip()
                    if not msg_val:
                        msg_val = m_rev.group(1).strip()
                elif m_std:
                    candidate = m_std.group(1).strip()
                    if candidate.lower() not in ["message", "whatsapp", "sms", "text", "a message", "the message"]:
                        if not target_val:
                            target_val = candidate
                        if not msg_val and m_std.group(2):
                            msg_val = m_std.group(2).strip()

            # Clean target
            if target_val:
                target_val = re.sub(r"[.?!,]+$", "", target_val).strip()
                target_val = re.sub(r"\b(?:please|now|right now)\b", "", target_val, flags=re.IGNORECASE).strip()
                target_val = re.sub(r"^(?:to|ping)\s+", "", target_val, flags=re.IGNORECASE).strip()
                if target_val.lower() in [
                    "message", "text", "a message", "a text", "the message",
                    "whatsapp", "whatsapp message", "a whatsapp message", "sms", "an sms"
                ]:
                    target_val = None

            if msg_val and msg_val.lower().strip() in [
                "send whatsapp message", "whatsapp message", "a whatsapp message",
                "send message", "message", "a message", "the message", "send a message",
                "send sms", "sms", "an sms", "text", "a text", "send text"
            ]:
                msg_val = None

            if not msg_val and coref.get("has_message_reference"):
                msg_val = coref.get("resolved_message")

            if not target_val and coref.get("has_pronoun_reference") and coref.get("resolved_contact"):
                c = coref["resolved_contact"]
                target_val = c.get("name") or c.get("phone") or c.get("email")
            if target_val and msg_val:
                resolved = await self.resolve_target_contact(target_val, preferred_channel=channel)
                if not resolved:
                    return {
                        "reply": f"Could not find contact '{target_val}' in your contact list. Please check the name or save the contact first.",
                        "tool_calls": []
                    }

                if resolved.get("needs_fuzzy_confirmation"):
                    self.pending_inputs[session_id] = {
                        "action": action,
                        "channel": channel,
                        "resolved": resolved,
                        "target": resolved["name"],
                        "message": msg_val,
                        "waiting_for": "fuzzy_contact_confirm"
                    }
                    return {
                        "reply": f"I couldn't find an exact match for '{target_val}'. Did you mean {resolved['name']}? Say 'yes' to proceed with sending or tell me who you want to message.",
                        "tool_calls": [],
                        "confirmation_required": False
                    }

                return await self.prepare_messaging_confirmation(
                    session_id=session_id,
                    channel=channel,
                    action=action,
                    resolved=resolved,
                    message_body=msg_val
                )

            # Case B: Recipient given, but message text is missing -> Ask clarifying question!
            elif target_val and not msg_val:
                self.pending_inputs[session_id] = {
                    "action": action,
                    "channel": channel,
                    "target": target_val,
                    "waiting_for": "message"
                }
                return {
                    "reply": f"What message would you like to send to {target_val} on {channel}?",
                    "tool_calls": []
                }

            # Case C: Message given, but recipient is missing -> Ask clarifying question!
            elif not target_val and msg_val:
                self.pending_inputs[session_id] = {
                    "action": action,
                    "channel": channel,
                    "message": msg_val,
                    "waiting_for": "target"
                }
                return {
                    "reply": f"Who would you like to send this {channel} message to?",
                    "tool_calls": []
                }

            # Case D: Generic message request without recipient or message -> Ask clarifying question!
            else:
                self.pending_inputs[session_id] = {
                    "action": action,
                    "channel": channel,
                    "waiting_for": "target"
                }
                return {
                    "reply": f"Who would you like to send a {channel} message to?",
                    "tool_calls": []
                }

        # ---------------------------------------------------------
        # 4. Phone Call Intent: Ask for confirmation before calling!
        # ---------------------------------------------------------
        call_match = re.search(r"(?:call|dial|phone|make a call to|ring)\s*([+\d\w\s'()-]*)", msg, re.IGNORECASE)
        digits_match = re.search(r"^(\+?[\d\s-]{5,})$", msg)

        target = None
        if call_match:
            raw_target = call_match.group(1).strip()
            if raw_target:
                target = raw_target
                target = re.sub(r"[.?!,]+$", "", target).strip()
                target = re.sub(r"\b(?:please|now|right now)\b", "", target, flags=re.IGNORECASE).strip()
            elif msg in ["call", "dial", "phone", "make a call"]:
                # User said just "call" without recipient -> Ask clarifying question!
                self.pending_inputs[session_id] = {
                    "action": "make_phone_call",
                    "channel": "Call",
                    "waiting_for": "call_target"
                }
                return {
                    "reply": "Who would you like me to call?",
                    "tool_calls": []
                }
        elif digits_match:
            target = digits_match.group(1).strip()
        elif g_action == "call" and g_recipient:
            target = g_recipient.strip()

        # Handle "save/add [name] [number] and call"
        save_and_call = re.search(r"(?:save|add)\s+([a-zA-Z\s]+)\s+(?:with number|number)?\s*([+\d\s-]+)\s*(?:and call|and dial)?", msg, re.IGNORECASE)
        if save_and_call and ("call" in msg or "dial" in msg):
            c_name = save_and_call.group(1).strip()
            c_phone = save_and_call.group(2).strip()
            save_res = await self.execute_tool("save_contact", {"name": c_name, "phone_number": c_phone})
            executed_tools.append({"tool": "save_contact", "args": {"name": c_name, "phone_number": c_phone}, "result": save_res})
            
            # Ask confirmation before calling!
            display = f"{c_name} ({c_phone})"
            pending_data = {
                "action": "make_phone_call",
                "channel": "Call",
                "args": {"phone_number": c_phone, "contact_name": c_name},
                "display_target": display,
                "contact_name": c_name,
                "phone_number": c_phone
            }
            self.pending_confirmations[session_id] = pending_data
            return {
                "reply": f"Saved {c_name} to your contacts. Do you want to call {display}? Say 'yes' to proceed or 'no' to cancel.",
                "tool_calls": executed_tools,
                "confirmation_required": True,
                "pending_action": pending_data
            }

        if target:
            # Resolve target contact to ensure valid person or number
            resolved = await self.resolve_target_contact(target, preferred_channel="Call")
            if not resolved:
                return {
                    "reply": f"Could not find contact '{target}' in your contact list. Please check the name or save the contact first.",
                    "tool_calls": []
                }

            if resolved.get("needs_fuzzy_confirmation"):
                self.pending_inputs[session_id] = {
                    "action": "make_phone_call",
                    "channel": "Call",
                    "resolved": resolved,
                    "target": resolved["name"],
                    "waiting_for": "fuzzy_contact_confirm"
                }
                return {
                    "reply": f"I couldn't find an exact match for '{target}'. Did you mean {resolved['name']}? Say 'yes' to call {resolved['name']} or specify another name.",
                    "tool_calls": [],
                    "confirmation_required": False
                }

            resolved_name = resolved["name"]
            resolved_phone = resolved["phone"]
            display_name = f"{resolved_name} ({resolved_phone})" if resolved_name != resolved_phone else resolved_phone

            # Register pending confirmation before making the call
            pending_data = {
                "action": "make_phone_call",
                "channel": "Call",
                "args": {"phone_number": resolved_phone, "contact_name": resolved_name},
                "display_target": display_name,
                "contact_name": resolved_name,
                "phone_number": resolved_phone
            }
            self.pending_confirmations[session_id] = pending_data
            from app.agent.conversation_memory import conversation_manager
            conversation_manager.update_state(session_id=session_id, contact=resolved, channel="Call", action="make_phone_call")

            reply = f"Do you want to call {display_name}? Say 'yes' to call or 'no' to cancel."
            if resolved.get("match_note"):
                reply = f"{resolved['match_note']} {reply}"
            return {
                "reply": reply,
                "tool_calls": [],
                "confirmation_required": True,
                "pending_action": pending_data
            }

        # 2. Search Contacts Intent
        search_match = re.search(r"(?:search contacts|find contact|look up|search for)\s+([+\d\w\s'-]+)", msg, re.IGNORECASE)
        if search_match:
            query = search_match.group(1).strip()
            tool_args = {"query": query}
            tool_res = await self.execute_tool("search_contacts", tool_args)
            executed_tools.append({"tool": "search_contacts", "args": tool_args, "result": tool_res})
            return {"reply": f"Here is what I found for '{query}':\n{tool_res}", "tool_calls": executed_tools}

        # 3. Save Contact Intent
        add_match = re.search(r"(?:save|add)\s+contact\s+([a-zA-Z\s]+)\s+(?:with number|number)?\s*([+\d\s-]+)", msg, re.IGNORECASE)
        if add_match:
            c_name = add_match.group(1).strip()
            c_phone = add_match.group(2).strip()
            tool_args = {"name": c_name, "phone_number": c_phone}
            tool_res = await self.execute_tool("save_contact", tool_args)
            executed_tools.append({"tool": "save_contact", "args": tool_args, "result": tool_res})
            return {"reply": f"Added {c_name} ({c_phone}) to your MongoDB contacts.", "tool_calls": executed_tools}

        # 4. Read Screen Intent
        if any(phrase in msg for phrase in ["read screen", "check screen", "what's on screen", "screen elements", "screen state"]):
            tool_res = await self.execute_tool("read_screen_state", {})
            executed_tools.append({"tool": "read_screen_state", "args": {}, "result": tool_res})
            return {"reply": f"Current mobile screen state:\n{tool_res}", "tool_calls": executed_tools}

        # 5. Open App Intent
        open_match = re.search(r"(?:open|launch)\s+([a-zA-Z\s]+)", msg, re.IGNORECASE)
        if open_match:
            app_name = open_match.group(1).strip()
            tool_args = {"app_name": app_name}
            tool_res = await self.execute_tool("open_app", tool_args)
            executed_tools.append({"tool": "open_app", "args": tool_args, "result": tool_res})
            return {"reply": f"Opening {app_name} on your device.", "tool_calls": executed_tools}

        # 6. Home Intent
        if "home" in msg:
            tool_res = await self.execute_tool("go_home", {})
            executed_tools.append({"tool": "go_home", "args": {}, "result": tool_res})
            return {"reply": "Navigated to home screen.", "tool_calls": executed_tools}

        # 7. General Conversational Query -> Answer using Local Gemma with multi-turn history!
        from app.agent.conversation_memory import conversation_manager
        state = conversation_manager.get_state(session_id)

        # Quick deterministic check for direct history questions in Hindi / Hinglish / English / Spanish
        is_history_query = bool(re.search(r"\b(kisko\s+(?:message|phone|call)|kis\s+ko|kya\s+message|who\s+did\s+i|what\s+did\s+i\s+send|last\s+(?:contact|person|message)|pichla\s+message|a\s+quién|qué\s+mensaje|whom\s+did\s+i)\b", msg))
        if is_history_query and (state.get("last_contact") or state.get("last_message")):
            c = state.get("last_contact")
            c_name = c.get("name") if c else "someone"
            c_phone = c.get("phone") if c else ""
            c_disp = f"{c_name} ({c_phone})" if c_phone and c_phone != c_name else c_name
            last_ch = state.get("last_channel") or state.get("last_message_channel") or "message"
            last_m = state.get("last_message")

            # Answer in Hindi/Hinglish if query was in Hindi/Hinglish
            if re.search(r"\b(kisko|kya|bheja|kiya|kise|pichla|maine)\b", msg):
                reply_parts = [f"Aapne haal hi me {c_disp} ke saath {last_ch} par baat ki thi."]
                if last_m:
                    reply_parts.append(f"Message tha: \"{last_m}\".")
                return {"reply": " ".join(reply_parts), "tool_calls": []}
            elif re.search(r"\b(quién|qué|último|mensaje)\b", msg):
                reply_parts = [f"Recientemente te comunicaste con {c_disp} por {last_ch}."]
                if last_m:
                    reply_parts.append(f"El mensaje fue: \"{last_m}\".")
                return {"reply": " ".join(reply_parts), "tool_calls": []}
            else:
                reply_parts = [f"You recently contacted {c_disp} via {last_ch}."]
                if last_m:
                    reply_parts.append(f"The message was: \"{last_m}\".")
                return {"reply": " ".join(reply_parts), "tool_calls": []}

        if self.llm:
            try:
                history_prompt = conversation_manager.get_conversation_context_prompt(session_id, limit=14)
                sys_msg = SYSTEM_PROMPT
                if history_prompt:
                    sys_msg += (
                        f"\n\nACTIVE CONVERSATION MEMORY:\n{history_prompt}\n\n"
                        "Instructions for History & Multilingual Dialogue:\n"
                        "- Use the ACTIVE CONVERSATION MEMORY to accurately recall who the user talked to, messages drafted, and actions executed.\n"
                        "- Always respond directly, naturally, and helpfully in the user's conversational language (e.g. Hindi, Hinglish, Spanish, English)."
                    )
                gemma_res = await self.llm.ainvoke([
                    SystemMessage(content=sys_msg),
                    HumanMessage(content=user_message)
                ])
                return {"reply": gemma_res.content.strip(), "tool_calls": []}
            except Exception as e:
                logger.error(f"Error querying local Gemma: {e}")

        # Fallback guidance
        reply = (
            "I'm running locally on Gemma! Ask me to:\n"
            "• 'Call Alice' or 'Make a call to 911'\n"
            "• 'Search contacts for Mom'\n"
            "• 'Save contact John with number 555-0199'\n"
            "• 'Read screen' to track active UI elements"
        )
        return {"reply": reply, "tool_calls": []}

    async def chat(self, user_message: str, session_id: str = "default") -> Dict[str, Any]:
        """
        Process user message via local Gemma and execute device automation tools.
        """
        # Save user message to MongoDB & in-memory ConversationMemoryManager
        await save_conversation_turn(session_id=session_id, role="user", content=user_message)
        from app.agent.conversation_memory import conversation_manager
        conversation_manager.add_turn(session_id=session_id, role="user", content=user_message)

        # Run local Gemma processing with session awareness for confirmations
        result = await self.run_hybrid_intent_analysis(user_message, session_id=session_id)

        # Save assistant reply to MongoDB & in-memory ConversationMemoryManager
        await save_conversation_turn(
            session_id=session_id,
            role="assistant",
            content=result["reply"],
            tool_calls=result["tool_calls"]
        )
        conversation_manager.add_turn(
            session_id=session_id,
            role="assistant",
            content=result["reply"],
            metadata={"tool_calls": result.get("tool_calls")}
        )

        return result

agent = GemmaMobileAgent()
