import re
import difflib
import logging
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class ContactMatcher:
    """
    Intelligent Contact Resolver & Fuzzy Matcher for Voice Assistants.
    Handles speech-to-text (STT) phonetic variations, typos, partial names,
    and nicknames across Phone, WhatsApp, and Email contacts.
    """

    @staticmethod
    def devanagari_to_latin(text: str) -> str:
        if not text or not any('\u0900' <= ch <= '\u097F' for ch in text):
            return text
        consonants = {
            'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
            'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
            'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
            'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
            'प': 'p', 'फ': 'f', 'ब': 'b', 'भ': 'bh', 'म': 'm',
            'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'श': 'sh',
            'ष': 'sh', 'स': 's', 'ह': 'h', 'क्ष': 'ksh', 'त्र': 'tr', 'ज्ञ': 'gy'
        }
        vowels = {
            'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo',
            'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'अं': 'an'
        }
        matras = {
            'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u',
            'ृ': 'ri', 'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au',
            'ं': 'n', 'ँ': 'n', 'ः': 'h'
        }
        virama = '्'
        nukta = '़'

        res = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch in vowels:
                res.append(vowels[ch])
                i += 1
            elif ch in consonants:
                c = consonants[ch]
                if i + 1 < n and text[i+1] == nukta:
                    if ch == 'ज': c = 'z'
                    elif ch == 'फ': c = 'f'
                    i += 1
                if i + 1 < n:
                    nxt = text[i+1]
                    if nxt == virama:
                        res.append(c)
                        i += 2
                        continue
                    elif nxt in matras:
                        res.append(c + matras[nxt])
                        i += 2
                        continue
                if i + 1 < n and text[i+1] in consonants:
                    res.append(c + 'a')
                else:
                    res.append(c)
                i += 1
            elif ch in matras:
                res.append(matras[ch])
                i += 1
            else:
                res.append(ch)
                i += 1
        return ''.join(res)

    @staticmethod
    def normalize_str(s: str) -> str:
        if not s:
            return ""
        s = ContactMatcher.devanagari_to_latin(s)
        s = s.lower().strip()
        # Remove common phone labels and special symbols
        s = re.sub(r"\(you\)", "", s, flags=re.I)
        s = re.sub(r"\b(?:ko|se|par|pe|karein|karo|bhejo|lagao)\b", "", s, flags=re.I)
        s = re.sub(r"[\(\)\[\]\{\}\<\>\,\.\:\;\"\'\-\_\+]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def calculate_similarity(query: str, target: str) -> float:
        """
        Calculates holistic similarity score between 0.0 and 1.0 using:
        - SequenceMatcher ratio
        - Token subset matching
        - Prefix matching
        """
        q = ContactMatcher.normalize_str(query)
        t = ContactMatcher.normalize_str(target)

        if not q or not t:
            return 0.0

        # Exact match
        if q == t:
            return 1.0

        # Prefix / Starts with
        if t.startswith(q) or q.startswith(t):
            len_ratio = min(len(q), len(t)) / max(len(q), len(t))
            return 0.85 + (0.10 * len_ratio)

        # Word-in-phrase matching (e.g. 'Shyam' in 'Shyam Gupta')
        q_words = set(q.split())
        t_words = set(t.split())
        if q_words and t_words:
            if q_words.issubset(t_words) or t_words.issubset(q_words):
                return 0.90
            common = q_words.intersection(t_words)
            if common:
                return 0.75 + (0.15 * (len(common) / max(len(q_words), len(t_words))))

        # Levenshtein sequence ratio
        seq_ratio = difflib.SequenceMatcher(None, q, t).ratio()
        return seq_ratio

    @classmethod
    def get_all_contacts(cls, preferred_channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Aggregates contacts across ADB phone contacts, WhatsApp contacts,
        Email contacts, and MongoDB database into a unified list.
        """
        unified: Dict[str, Dict[str, Any]] = {}

        # 1. From ADB Device
        try:
            from app.device.adb_controller import adb_controller
            dev = adb_controller.get_connected_device()
            if dev:
                # A. Phone contacts
                raw_phones = dev.shell("content query --uri content://com.android.contacts/data/phones --projection display_name:data1")
                if raw_phones:
                    for line in raw_phones.splitlines():
                        m = re.search(r"display_name=([^,]+),\s*data1=(.+)$", line)
                        if m:
                            name = m.group(1).strip()
                            phone = re.sub(r"[^\d+]", "", m.group(2).strip())
                            key = name.lower()
                            if key not in unified:
                                unified[key] = {"name": name, "phone": phone, "has_phone": True}
                            else:
                                if not unified[key].get("phone"):
                                    unified[key]["phone"] = phone

                # B. WhatsApp contacts
                raw_wa = dev.shell("content query --uri content://com.android.contacts/raw_contacts --projection account_type:sync1:display_name")
                if raw_wa:
                    for line in raw_wa.splitlines():
                        if "account_type=com.whatsapp" in line:
                            m_name = re.search(r"display_name=([^,]+)", line)
                            m_sync = re.search(r"sync1=([^,]+)", line)
                            if m_name and m_sync:
                                name = m_name.group(1).strip()
                                sync1 = m_sync.group(1).strip()
                                phone = sync1.split("@")[0] if "@" in sync1 else sync1
                                phone = "+" + phone if not phone.startswith("+") else phone
                                key = name.lower()
                                if key not in unified:
                                    unified[key] = {"name": name, "phone": phone, "has_whatsapp": True}
                                else:
                                    unified[key]["has_whatsapp"] = True
                                    if not unified[key].get("phone"):
                                        unified[key]["phone"] = phone

                # C. Email contacts
                raw_emails = dev.shell("content query --uri content://com.android.contacts/data/emails --projection display_name:data1")
                if raw_emails:
                    for line in raw_emails.splitlines():
                        m = re.search(r"display_name=([^,]+),\s*data1=(.+)$", line)
                        if m:
                            name = m.group(1).strip()
                            email = m.group(2).strip().lower()
                            key = name.lower()
                            if key not in unified:
                                unified[key] = {"name": name, "email": email, "has_email": True}
                            else:
                                unified[key]["email"] = email
                                unified[key]["has_email"] = True

        except Exception as e:
            logger.debug(f"Error reading ADB contacts in ContactMatcher: {e}")

        # 3. Builtin contacts fallback (e.g. offline testing or when ADB not connected)
        builtin_contacts = [
            {"name": "Abhishek", "phone": "+917208590785", "has_phone": True, "has_whatsapp": True},
            {"name": "Shyam (You)", "phone": "+919167078537", "email": "sg25042023@gmail.com", "has_phone": True, "has_whatsapp": True, "has_email": True}
        ]
        for c in builtin_contacts:
            key = c["name"].lower()
            if key not in unified:
                unified[key] = dict(c)

        return list(unified.values())

    @classmethod
    def match_contact(
        cls,
        query: str,
        preferred_channel: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fuzzy matches a user-provided name or phone number against all contacts.
        Returns:
            {
                "status": "exact" | "high_confidence" | "medium_confidence" | "not_found",
                "contact": Dict[str, Any] or None,
                "score": float (0-100),
                "clarification_needed": bool,
                "message": str,
                "alternatives": List[Dict]
            }
        """
        q = query.strip()
        if not q:
            return {"status": "not_found", "contact": None, "score": 0, "clarification_needed": False}

        # If query is direct email
        if "@" in q:
            return {
                "status": "exact",
                "contact": {"name": q, "email": q, "phone": q},
                "score": 100.0,
                "clarification_needed": False,
                "message": f"Direct email address: {q}"
            }

        # If query is direct phone number
        clean_digits = re.sub(r"[^\d]", "", q)
        if len(clean_digits) >= 7:
            return {
                "status": "exact",
                "contact": {"name": q, "phone": q, "email": None},
                "score": 100.0,
                "clarification_needed": False,
                "message": f"Direct phone number: {q}"
            }

        all_contacts = cls.get_all_contacts(preferred_channel)
        if not all_contacts:
            return {"status": "not_found", "contact": None, "score": 0, "clarification_needed": False}

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for c in all_contacts:
            name = c.get("name", "")
            base_score = cls.calculate_similarity(q, name)

            # Boost score if contact satisfies preferred channel
            if preferred_channel == "Email" and c.get("email"):
                base_score = min(1.0, base_score + 0.05)
            elif preferred_channel == "WhatsApp" and c.get("has_whatsapp"):
                base_score = min(1.0, base_score + 0.05)

            scored.append((base_score * 100.0, c))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top_contact = scored[0]

        alternatives = [c for score, c in scored[1:4] if score >= 60.0]

        if top_score >= 92.0:
            return {
                "status": "exact",
                "contact": top_contact,
                "score": top_score,
                "clarification_needed": False,
                "message": f"Found {top_contact['name']}",
                "alternatives": alternatives
            }
        elif top_score >= 70.0:
            return {
                "status": "high_confidence",
                "contact": top_contact,
                "score": top_score,
                "clarification_needed": False,
                "message": f"Matched '{query}' to '{top_contact['name']}' ({int(top_score)}% similarity)",
                "alternatives": alternatives
            }
        elif top_score >= 50.0:
            return {
                "status": "medium_confidence",
                "contact": top_contact,
                "score": top_score,
                "clarification_needed": True,
                "message": f"Did you mean {top_contact['name']}?",
                "alternatives": alternatives
            }
        else:
            return {
                "status": "not_found",
                "contact": None,
                "score": top_score,
                "clarification_needed": False,
                "message": f"No contact found matching '{query}'.",
                "alternatives": []
            }
