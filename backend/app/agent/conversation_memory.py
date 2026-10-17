import re
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class ConversationMemoryManager:
    """
    Manages multi-turn conversation trajectories, conversational context,
    coreference resolution (e.g. 'him', 'that', 'same message'), and entity memory.
    """
    def __init__(self):
        # In-memory session history: session_id -> list of turn dicts
        self.histories: Dict[str, List[Dict[str, Any]]] = {}
        # In-memory session state: session_id -> state dict
        self.states: Dict[str, Dict[str, Any]] = {}

    def get_history(self, session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self.histories.get(session_id, [])[-limit:]

    def add_turn(self, session_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        if session_id not in self.histories:
            self.histories[session_id] = []
        
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {}
        }
        self.histories[session_id].append(turn)
        if len(self.histories[session_id]) > 30:
            self.histories[session_id] = self.histories[session_id][-30:]

    def get_state(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.states:
            self.states[session_id] = {
                "last_contact": None,       # {"name": ..., "phone": ..., "email": ...}
                "last_channel": None,       # "Call", "WhatsApp", "SMS", "Email"
                "last_message": None,       # str
                "last_subject": None,       # str
                "last_action": None,        # "make_phone_call", "send_whatsapp_message", etc.
            }
        return self.states[session_id]

    def update_state(
        self,
        session_id: str,
        contact: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        message: Optional[str] = None,
        subject: Optional[str] = None,
        action: Optional[str] = None,
        **kwargs
    ):
        state = self.get_state(session_id)
        if contact:
            state["last_contact"] = contact
        if channel:
            state["last_channel"] = channel
        if message:
            state["last_message"] = message
            if channel and channel != "Call":
                state["last_message_channel"] = channel
        if subject:
            state["last_subject"] = subject
        if action:
            state["last_action"] = action
        for k, v in kwargs.items():
            state[k] = v

    def get_conversation_context_prompt(self, session_id: str, limit: int = 14) -> str:
        """
        Formats recent conversation turns and active entity memory into rich conversational context for the LLM.
        """
        turns = self.get_history(session_id, limit=limit)
        state = self.get_state(session_id)
        lines = []

        # 1. Active Entity & Trajectory Memory
        entity_summary = []
        if state.get("last_contact"):
            c = state["last_contact"]
            entity_summary.append(f"- Last active contact: {c.get('name')} (Phone: {c.get('phone')}, Email: {c.get('email')})")
        if state.get("last_channel"):
            entity_summary.append(f"- Last communication channel: {state.get('last_channel')}")
        if state.get("last_message"):
            entity_summary.append(f"- Last drafted message: \"{state.get('last_message')}\"")
        if state.get("last_subject"):
            entity_summary.append(f"- Last subject line: \"{state.get('last_subject')}\"")
        if state.get("last_action"):
            entity_summary.append(f"- Last action initiated: {state.get('last_action')}")

        if entity_summary:
            lines.append("ACTIVE CONVERSATION MEMORY (Ground Truth):")
            lines.extend(entity_summary)
            lines.append("")

        # 2. Dialogue History
        if turns:
            lines.append("Recent Dialogue History:")
            for t in turns:
                role = "User" if t["role"] == "user" else "Assistant"
                lines.append(f"{role}: {t['content']}")

        return "\n".join(lines)

    def resolve_coreference(self, session_id: str, text: str) -> Dict[str, Any]:
        """
        Inspects human language for coreferences to previous context across languages (English, Hindi, Hinglish, Spanish):
        - Pronouns: 'him', 'her', 'them', 'usko', 'use', 'unko', 'unhe', 'usi ko', 'to him'
        - Demonstratives: 'that', 'same message', 'wohi message', 'pichla message', 'that message', 'it'
        - Message modifications: 'change message to...', 'say instead...', 'message badal do...', 'yeh likho...'
        - Channel shifts: 'call him instead', 'usko call lagao', 'send that on whatsapp', 'email him that'
        """
        state = self.get_state(session_id)
        resolved_contact = None
        resolved_message = None
        resolved_channel = None
        resolved_subject = state.get("last_subject")
        is_modification = False
        new_message = None

        t_lower = text.lower().strip()

        # 1. Multilingual Pronoun reference to contact (e.g. "send that to him", "call her", "usko phone lagao", "use message bhejo", "unko call karo")
        pronoun_match = bool(re.search(r"\b(him|her|them|that person|the same person|same person|to him|to her|to them|usko|use|usey|unko|unhe|unse|usi ko|usi se|usse|a él|a ella)\b", t_lower))
        if pronoun_match and state.get("last_contact"):
            resolved_contact = state["last_contact"]

        # 2. Multilingual message modification: "change message to...", "actually say...", "message badal do: ...", "yeh likho: ..."
        mod_match = re.search(r"(?:change(?:\s+the)?\s+message\s*(?:to\s*:?|:)|actually\s+say\s*:?|instead\s+say\s*:?|change\s+to\s*:?|message\s+badal(?:\s+kar|\s+ke|\s+do)\s*:?|badal\s+do\s*:?|yeh\s+likho\s*:?|usme\s+likho\s*:?|cambiar\s+mensaje\s+a\s*:?)\s*[\"\'\s]*(.+?)[\"\'\s]*$", text, re.IGNORECASE)
        if mod_match:
            is_modification = True
            new_message = mod_match.group(1).strip()
            resolved_message = new_message
            if state.get("last_contact"):
                resolved_contact = state["last_contact"]
            if state.get("last_channel") and state.get("last_channel") != "Call":
                resolved_channel = state["last_channel"]
            elif state.get("last_message_channel"):
                resolved_channel = state["last_message_channel"]
            else:
                resolved_channel = "WhatsApp"

        # 3. Multilingual demonstrative reference to message body ("send that", "wohi message bhejo", "same message", "pichla message")
        demonstrative_match = bool(re.search(r"\b(that|same message|the same message|that message|same thing|it|previous message|last message|woh message|wohi message|vahi message|wahi message|yeh message|yehi message|pichla message|pichhla message|ese mensaje|el mismo mensaje)\b", t_lower))
        if demonstrative_match and not is_modification and state.get("last_message"):
            resolved_message = state["last_message"]

        # 4. Multilingual channel shift detection
        if bool(re.search(r"\b(whatsapp|ping)\b", t_lower)):
            resolved_channel = "WhatsApp"
        elif bool(re.search(r"\b(call|phone|ring|dial|lagao|milaao|baat karao|llamar|appeler)\b", t_lower)) and not bool(re.search(r"\b(whatsapp|sms|email|message)\b", t_lower)):
            resolved_channel = "Call"
        elif bool(re.search(r"\b(email|mail|correo)\b", t_lower)):
            resolved_channel = "Email"
        elif bool(re.search(r"\b(sms|text|mensaje)\b", t_lower)):
            resolved_channel = "SMS"
        elif state.get("last_channel") and (pronoun_match or demonstrative_match):
            resolved_channel = state["last_channel"]

        return {
            "has_pronoun_reference": pronoun_match,
            "has_message_reference": demonstrative_match,
            "is_message_modification": is_modification,
            "new_message": new_message,
            "resolved_contact": resolved_contact,
            "resolved_message": resolved_message,
            "resolved_subject": resolved_subject,
            "resolved_channel": resolved_channel
        }

conversation_manager = ConversationMemoryManager()

