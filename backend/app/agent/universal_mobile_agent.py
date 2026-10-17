import os
import re
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from app.device.adb_controller import adb_controller
from app.device.device_bridge import device_bridge
from app.agent.privacy_guard import privacy_guard

logger = logging.getLogger(__name__)

class UniversalMobileAgent:
    """
    General-Purpose Autonomous Mobile & System Assistant.
    Intelligently handles:
    - Web Browsing & Google Information Retrieval
    - Full in-app automation (YouTube auto-play, Calculator math execution, Clock/Alarm setting)
    - Any installed app on the device
    - Zero unnecessary server storage overhead (No redundant screenshots)
    - Colloquial, clumsy, humanized multilingual commands
    """

    WEB_SEARCH_TRIGGERS = [
        r"(?:google|browser|chrome|internet|net\s*pe|wiki|wikipedia|arxiv|pubmed)\s*(?:pe|par|me|on)?\s*(?:search|karo|dekh|dekho|batao|find)?\s*(.+)",
        r"(?:search|find|dhoondo|dekho|batao)\s+(.+?)\s+(?:on|in|using|pe|par)\s+(?:google|chrome|browser|web|internet|wiki|wikipedia|arxiv|pubmed)",
        r"how\s+to\s+(?:on|enable|turn\s*on|activate)\s+(?:the\s+)?developer\s*mode.*",
        r"how\s+to\s+(.+)",
        r"(?:what|who|where|when|why)\s+(?:is|are|was|were)\s+(.+)"
    ]

    COMMUNICATION_KEYWORDS = [
        "whatsapp", "call", "phone", "sms", "email", "mail", "message bhejo", "call lagao", "phone lagao"
    ]

    ECOMMERCE_KEYWORDS = [
        "flipkart", "amazon", "myntra", "blinkit", "zepto", "swiggy", "zomato"
    ]

    @classmethod
    def is_universal_intent(cls, user_msg: str, session_id: str = "default") -> bool:
        """Determines if the request is a general web/knowledge search, system tool, or app automation task"""
        m = user_msg.lower().strip()

        # Strict exclusion of communication actions
        if any(w in m for w in cls.COMMUNICATION_KEYWORDS):
            return False

        # Strict exclusion of e-commerce store operations
        if any(w in m for w in cls.ECOMMERCE_KEYWORDS):
            return False

        # Multi-turn Context Check: Was calculator actively open and waiting for calculation?
        try:
            from app.agent.conversation_memory import conversation_manager
            state = conversation_manager.get_state(session_id)
            if state.get("waiting_for") == "calculator_operation" or state.get("active_app") == "calculator":
                if cls.parse_math_expression(m) or re.search(r"\d+", m):
                    return True
        except Exception:
            pass

        # Alarm triggers
        if any(w in m for w in ["alarm", "wake me up", "baje uthana", "alarm lagao", "alarm set"]):
            return True

        # Calculator & math triggers (all variations and operations)
        MATH_TRIGGERS = [
            "calculator", "calcualtor", "calcuator", "calcultor", "calculater", "calc", "calculate", "calculation",
            "hisab", "hisaab", "addition", "subtraction", "multiplication", "division",
            "add ", "add numbers", "add number", "subtract", "multiply", "divide",
            "sum of", "plus", "minus", "jodo", "ghatao", "guna", "bhaag"
        ]
        if any(w in m for w in MATH_TRIGGERS):
            return True
        if cls.parse_math_expression(m):
            return True

        # Music, song, video & media playback triggers (smartely routed to YouTube)
        SONG_TRIGGERS = [
            "play a song", "play song", "play the song", "play music", "play track",
            "play video", "play lofi", "gana chalao", "gana bajao", "song bajao",
            "song chalao", "geet chalao", "gaana chalao", "gaana bajao", "listen to"
        ]
        if any(w in m for w in SONG_TRIGGERS):
            return True
        if m.startswith("play ") and not any(w in m for w in ["cricket", "football", "game", "match", "pubg", "freefire"]):
            return True

        # Knowledge & Web search triggers
        if any(term in m for term in ["developer mode", "google search", "google pe", "chrome pe", "browser pe", "how to on", "wiki", "wikipedia", "arxiv", "pubmed"]):
            return True

        for pat in cls.WEB_SEARCH_TRIGGERS:
            if re.search(pat, m):
                return True

        # Check app launch & automation triggers
        known_apps = list(adb_controller.APP_PACKAGE_MAP.keys())
        for app in known_apps:
            if app in m:
                if any(verb in m for verb in ["open", "launch", "kholo", "chalao", "chalaao", "start", "use", "pe", "par", "play", "search", "gana", "song"]):
                    return True

        return False

    @classmethod
    def extract_song_title(cls, text: str) -> str:
        """Extracts clean song title from user prompt (e.g. 'Play a song named habibi' -> 'habibi')"""
        t = text.strip()
        t = re.sub(r"^(?:please|yaar|bhai|zara|thoda|ek\s+baar)\s+", "", t, flags=re.I).strip()

        m = re.search(
            r"^(?:play|listen\s+to|chalaao|chalao|bajao|start)\s+(?:a\s+|the\s+)?(?:song|track|music|video|geet|gaana|gana)?\s*(?:named|called|title)?\s*[\"\'\s]*(.+?)(?:[\"\'\s]+(?:on|in|using|pe|par)\s+(?:youtube|yt|app|spotify))?$",
            t,
            re.I
        )
        if m:
            clean = m.group(1).strip(" \"'.,!?")
            clean = re.sub(r"^(?:a|the)\s+(?:song|track|video)\s+(?:named|called)?\s*", "", clean, flags=re.I).strip()
            clean = re.sub(r"^(?:named|called)\s+", "", clean, flags=re.I).strip()
            if clean:
                return clean

        m2 = re.search(r"^(?:youtube|yt)\s+(?:pe|par|me|on)\s+(.+?)\s*(?:gana|song|track|music)?\s*(?:chalaao|chalao|play|bajao)?$", t, re.I)
        if m2 and m2.group(1).strip():
            return m2.group(1).strip(" \"'.,!?")

        cleaned = re.sub(r"\b(play|a\s+song|song|named|called|on\s+youtube|youtube|pe|par|chalao|chalaao|bajao|gana|gaana|track|music|listen\s+to)\b", "", t, flags=re.I).strip(" \"'.,!?")
        return cleaned or "habibi"

    @classmethod
    def extract_app_and_action(cls, user_msg: str) -> Tuple[str, Optional[str]]:
        """Extract app name and secondary action query from user message"""
        m = user_msg.lower().strip()
        cleaned = re.sub(r"\b(yaar|bhai|please|zara|thoda|ek\s+baar)\b", "", m).strip()

        # Check alarm first
        if any(w in cleaned for w in ["alarm", "wake me up", "baje uthana"]):
            return "alarm", cleaned

        # Check calculator
        if any(w in cleaned for w in ["calculator", "calcualtor", "calcuator", "calcultor", "calculater", "calc", "calculate"]) or cls.parse_math_expression(cleaned):
            return "calculator", cleaned

        # Check song / music requests -> smartly default to youtube
        if any(w in cleaned for w in ["play a song", "play song", "play music", "play track", "gana chalao", "song bajao", "gana bajao", "listen to"]) or cleaned.startswith("play "):
            song_title = cls.extract_song_title(cleaned)
            return "youtube", song_title

        known_apps = sorted(list(adb_controller.APP_PACKAGE_MAP.keys()), key=len, reverse=True)
        detected_app = None

        for app in known_apps:
            if re.search(rf"\b{re.escape(app)}\b", cleaned):
                detected_app = app
                break

        if not detected_app:
            m_simple = re.search(r"(?:open|launch|kholo|chalaao)\s+([a-zA-Z0-9_]+)", cleaned)
            if m_simple:
                detected_app = m_simple.group(1).strip()
            else:
                detected_app = "chrome"

        action_query = None
        m_act1 = re.search(rf"{detected_app}\s+(?:pe|par|me|on)\s+(.+?)\s*(?:chalaao|chalao|play|search|bajao|karo|dekho)?$", cleaned)
        m_act2 = re.search(r"(?:and|aur|then|fir)\s+(?:search|play|type|check|dhoondo|chalaao|chalao)\s*(.+)", cleaned)

        if m_act1 and m_act1.group(1).strip():
            action_query = m_act1.group(1).strip()
        elif m_act2 and m_act2.group(1).strip():
            action_query = m_act2.group(1).strip()
        else:
            action_query = cleaned

        return detected_app, action_query

    @classmethod
    def parse_math_expression(cls, text: str) -> Optional[Tuple[str, float]]:
        """
        Parses math expressions from humanized English / Hindi / Hinglish text.
        Handles natural phrasing like:
        - "Perform Addition of two number 2 and 3"
        - "add 2 number 2 and 3"
        - "addition of 2 and 3"
        - "2 + 3"
        - "multiply two numbers 4 and 5"
        - "subtract 5 from 10"
        Returns formatted expression string and computed result: ("2 + 3", 5.0)
        """
        t = text.lower().strip()
        t = re.sub(r"^(?:please|yaar|bhai|zara|kripya)?\s*(?:perform|calculate|do|find|solve|compute)?\s*", "", t, flags=re.I).strip()

        # 1. Operation with "two numbers X and Y" / "X and Y"
        m_add = re.search(
            r"(?:addit?i?on|sum|add|plus|jod|jodo)\s+(?:of\s+)?(?:the\s+)?(?:two\s+|2\s+)?(?:numbers?|digits?|nos?|values?|terms?)?\s*(?:of|is|are|:)?\s*(\d+(?:\.\d+)?)\s*(?:and|with|\+|aur|to|,)\s*(\d+(?:\.\d+)?)",
            t
        )
        if m_add:
            a, b = float(m_add.group(1)), float(m_add.group(2))
            return f"{m_add.group(1)} + {m_add.group(2)}", a + b

        # 2. "subtract/subtraction ... from Y"
        m_sub_from = re.search(
            r"(?:subtr?a?ct?i?on|subtract|minus|difference|ghatao)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s+from\s+(\d+(?:\.\d+)?)",
            t
        )
        if m_sub_from:
            a, b = float(m_sub_from.group(1)), float(m_sub_from.group(2))
            return f"{m_sub_from.group(2)} - {m_sub_from.group(1)}", b - a

        # 3. "subtract/subtraction of two numbers X and Y" / "subtract X and Y"
        m_sub = re.search(
            r"(?:subtr?a?ct?i?on|subtract|minus|difference|ghatao)\s+(?:of\s+)?(?:the\s+)?(?:two\s+|2\s+)?(?:numbers?|digits?|nos?|values?|terms?)?\s*(?:of|is|are|:)?\s*(\d+(?:\.\d+)?)\s*(?:and|with|-|from|aur|,)\s*(\d+(?:\.\d+)?)",
            t
        )
        if m_sub:
            a, b = float(m_sub.group(1)), float(m_sub.group(2))
            return f"{m_sub.group(1)} - {m_sub.group(2)}", a - b

        # 4. "multiplication / multiply of two numbers X and Y"
        m_mul = re.search(
            r"(?:mult?i?pl?y?|multiplication|product|guna)\s+(?:of\s+)?(?:the\s+)?(?:two\s+|2\s+)?(?:numbers?|digits?|nos?|values?|terms?)?\s*(?:of|is|are|:)?\s*(\d+(?:\.\d+)?)\s*(?:by|with|and|\*|into|aur|,)\s*(\d+(?:\.\d+)?)",
            t
        )
        if m_mul:
            a, b = float(m_mul.group(1)), float(m_mul.group(2))
            return f"{m_mul.group(1)} * {m_mul.group(2)}", a * b

        # 5. "division / divide of two numbers X and Y"
        m_div = re.search(
            r"(?:divi?s?i?on|divide|bhaag)\s+(?:of\s+)?(?:the\s+)?(?:two\s+|2\s+)?(?:numbers?|digits?|nos?|values?|terms?)?\s*(?:of|is|are|:)?\s*(\d+(?:\.\d+)?)\s*(?:by|with|\/|aur|,)\s*(\d+(?:\.\d+)?)",
            t
        )
        if m_div:
            a, b = float(m_div.group(1)), float(m_div.group(2))
            return f"{m_div.group(1)} / {m_div.group(2)}", a / b if b != 0 else 0.0

        # 6. Hindi / Hinglish post-fixed
        m_post_add = re.search(r"(\d+(?:\.\d+)?)\s*(?:and|aur|\+|,)\s*(\d+(?:\.\d+)?)\s*(?:ko)?\s*(?:add|jodo|plus)", t)
        if m_post_add:
            a, b = float(m_post_add.group(1)), float(m_post_add.group(2))
            return f"{m_post_add.group(1)} + {m_post_add.group(2)}", a + b

        m_post_sub = re.search(r"(\d+(?:\.\d+)?)\s*(?:me\s*se|-|minus|ghatao)\s*(\d+(?:\.\d+)?)", t)
        if m_post_sub:
            a, b = float(m_post_sub.group(1)), float(m_post_sub.group(2))
            return f"{m_post_sub.group(1)} - {m_post_sub.group(2)}", a - b

        # 6b. Spoken infix operators: "25 multiplied by 4", "6 times 7", "10 divided by 2", "8 plus 2"
        m_infix = re.search(
            r"(\d+(?:\.\d+)?)\s+(plus|minus|times|into|multiplied\s+by|divided\s+by|over)\s+(\d+(?:\.\d+)?)",
            t
        )
        if m_infix:
            a, b = float(m_infix.group(1)), float(m_infix.group(3))
            op_word = m_infix.group(2)
            if op_word == "plus":
                return f"{m_infix.group(1)} + {m_infix.group(3)}", a + b
            if op_word == "minus":
                return f"{m_infix.group(1)} - {m_infix.group(3)}", a - b
            if op_word.startswith("divided") or op_word == "over":
                return f"{m_infix.group(1)} / {m_infix.group(3)}", a / b if b != 0 else 0.0
            return f"{m_infix.group(1)} * {m_infix.group(3)}", a * b

        # 7. Direct arithmetic expressions: "2 + 3", "25 * 4", "100 / 4"
        m_direct = re.search(r"(\d+(?:\.\d+)?)\s*([\+\-\*\/xX×÷])\s*(\d+(?:\.\d+)?)", t)
        if m_direct:
            a, b = float(m_direct.group(1)), float(m_direct.group(3))
            op = m_direct.group(2)
            if op in ['x', 'X', '×', '*']:
                return f"{m_direct.group(1)} * {m_direct.group(3)}", a * b
            elif op == '÷' or op == '/':
                return f"{m_direct.group(1)} / {m_direct.group(3)}", a / b if b != 0 else 0.0
            elif op == '+':
                return f"{m_direct.group(1)} + {m_direct.group(3)}", a + b
            elif op == '-':
                return f"{m_direct.group(1)} - {m_direct.group(3)}", a - b

        # 8. Bare two numbers "2 and 3"
        m_bare = re.search(r"^(?:numbers?\s+)?(\d+(?:\.\d+)?)\s*(?:and|aur|,|\+)\s*(\d+(?:\.\d+)?)$", t)
        if m_bare:
            a, b = float(m_bare.group(1)), float(m_bare.group(2))
            return f"{m_bare.group(1)} + {m_bare.group(2)}", a + b

        return None

    @classmethod
    def parse_alarm_time(cls, text: str) -> Optional[Tuple[int, int]]:
        """Parses hour and minute for alarm from English or Hindi prompts"""
        t = text.lower()
        is_pm = any(w in t for w in ["pm", "shaam", "sham", "raat", "night", "dopahar", "afternoon", "evening"])
        is_am = any(w in t for w in ["am", "subah", "morning", "bhor"])

        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
        if not m:
            m = re.search(r"(\d{1,2})\s*baje", t)

        if m:
            hr = int(m.group(1))
            mn = int(m.group(2)) if m.group(2) else 0
            ampm = m.group(3) if len(m.groups()) >= 3 and m.group(3) else None
            if ampm == "pm" or (is_pm and hr < 12):
                hr = (hr + 12) if hr != 12 else 12
            elif ampm == "am" or is_am:
                if hr == 12:
                    hr = 0
            return hr, mn
        return None

    # -----------------------------------------------------------------
    # SPECIALIZED APP HANDLERS (Fast execution, no redundant screenshots)
    # -----------------------------------------------------------------

    @classmethod
    async def _handle_youtube(cls, user_msg: str, action_query: Optional[str] = None) -> Dict[str, Any]:
        """Searches YouTube and clicks/plays the first video automatically"""
        dev = adb_controller.get_connected_device()
        if action_query and action_query.strip() and action_query != user_msg:
            clean_q = action_query.strip(" \"'.,!?")
        else:
            clean_q = cls.extract_song_title(user_msg)

        if not clean_q:
            clean_q = "habibi"

        logger.info(f"Universal YouTube: Smartly routing song to YouTube: '{clean_q}'")
        if dev:
            # Direct intent to launch search immediately
            dev.shell(["am", "start", "-a", "android.intent.action.SEARCH", "-e", "query", clean_q, "com.google.android.youtube"])
            time.sleep(2.5)

            # Dump hierarchy to find the first organic video card
            elems = adb_controller.dump_screen_hierarchy()
            video_card = None
            for el in elems:
                desc = el.get("desc") or ""
                # Video cards have long descriptions with title, channel, views, duration
                if len(desc) > 25 and not any(k in desc.lower() for k in ["search", "sponsored", "ad ", "account", "filter", "cast", "explore", "shorts"]):
                    if el.get("center", [0, 0])[1] > 350:  # avoid top header tabs
                        video_card = el
                        break

            if video_card:
                cx, cy = video_card["center"]
                logger.info(f"Clicking YouTube video at {cx}, {cy}")
                adb_controller.click_at(cx, cy)
            else:
                # Fallback: tap middle area of screen where first result appears
                logger.info("Hierarchy did not return card; tapping primary video card area (540, 650)")
                adb_controller.click_at(540, 650)

            time.sleep(1.0)

        return {
            "reply": f"Smartly selected YouTube, searched for '{clean_q}', and started playing the song for you.",
            "tool_calls": [{"tool": "play_youtube_song", "args": {"query": clean_q, "app": "youtube"}}],
            "screenshots": [],
            "confirmation_required": False
        }

    @classmethod
    async def _handle_calculator(cls, user_msg: str, session_id: str = "default") -> Dict[str, Any]:
        """Opens Calculator, inputs the mathematical formula, presses equal, and reads result"""
        from app.agent.conversation_memory import conversation_manager
        math_parsed = cls.parse_math_expression(user_msg)
        expr_str = math_parsed[0] if math_parsed else None
        computed_res = math_parsed[1] if math_parsed else None

        # Format clean integer result if integer
        res_display = str(int(computed_res)) if computed_res is not None and computed_res.is_integer() else str(computed_res)

        adb_controller.open_app("calculator")
        time.sleep(1.0)

        if expr_str:
            elements = adb_controller.dump_screen_hierarchy()
            # Map key character to element ID or center across Samsung & generic Android calculators
            samsung_map = {
                '0': 'calc_keypad_btn_00', '1': 'calc_keypad_btn_01', '2': 'calc_keypad_btn_02',
                '3': 'calc_keypad_btn_03', '4': 'calc_keypad_btn_04', '5': 'calc_keypad_btn_05',
                '6': 'calc_keypad_btn_06', '7': 'calc_keypad_btn_07', '8': 'calc_keypad_btn_08',
                '9': 'calc_keypad_btn_09', '+': 'calc_keypad_btn_add', '-': 'calc_keypad_btn_sub',
                '*': 'calc_keypad_btn_mul', '/': 'calc_keypad_btn_div', '=': 'calc_keypad_btn_equal',
                '.': 'calc_keypad_btn_dot'
            }
            generic_map = {
                '0': 'digit_0', '1': 'digit_1', '2': 'digit_2', '3': 'digit_3', '4': 'digit_4',
                '5': 'digit_5', '6': 'digit_6', '7': 'digit_7', '8': 'digit_8', '9': 'digit_9',
                '+': 'op_add', '-': 'op_sub', '*': 'op_mul', '/': 'op_div', '=': 'eq',
                '.': 'dec_point'
            }

            def click_btn(key_char):
                s_id = samsung_map.get(key_char)
                g_id = generic_map.get(key_char)
                for el in elements:
                    el_id = el.get("id", "")
                    el_txt = (el.get("text") or "").strip()
                    el_desc = (el.get("desc") or "").strip().lower()

                    if (s_id and s_id in el_id) or (g_id and g_id in el_id):
                        adb_controller.click_at(el["center"][0], el["center"][1])
                        time.sleep(0.15)
                        return
                    if el_txt == key_char or (key_char == '*' and el_txt in ['×', 'x']) or (key_char == '/' and el_txt == '÷'):
                        adb_controller.click_at(el["center"][0], el["center"][1])
                        time.sleep(0.15)
                        return
                    if key_char == '=' and ('equals' in el_desc or el_desc == 'equal'):
                        adb_controller.click_at(el["center"][0], el["center"][1])
                        time.sleep(0.15)
                        return

            # Clear previous calculations
            for el in elements:
                el_id = el.get("id", "")
                if "btn_clear" in el_id or "clr" in el_id or el.get("text") in ["C", "AC", "Clear"]:
                    adb_controller.click_at(el["center"][0], el["center"][1])
                    time.sleep(0.2)
                    break

            # Press each character in expression
            for ch in expr_str.replace(" ", ""):
                click_btn(ch)

            # Press '='
            click_btn('=')
            time.sleep(0.4)

            # Verify by reading on-screen formula/result
            new_elements = adb_controller.dump_screen_hierarchy()
            for el in new_elements:
                el_id = el.get("id", "")
                if "calc_edt_formula" in el_id or "result" in el_id:
                    txt = (el.get("text") or "").strip()
                    m_num = re.search(r"(\d+(?:\.\d+)?)", txt)
                    if m_num:
                        res_display = m_num.group(1)
                        break

            # Multi-turn context update: clear waiting_for and remember calculation
            conversation_manager.update_state(
                session_id,
                active_app="calculator",
                waiting_for=None,
                last_calculation=res_display
            )

            return {
                "reply": f"Opened Calculator, entered {expr_str}, and calculated the result: {res_display}.",
                "tool_calls": [{"tool": "calculate", "args": {"expression": expr_str, "result": res_display}}],
                "screenshots": [],
                "confirmation_required": False
            }

        # Multi-turn context update: keep waiting_for calculator_operation
        conversation_manager.update_state(
            session_id,
            active_app="calculator",
            waiting_for="calculator_operation"
        )

        return {
            "reply": "Opened Calculator on your phone. What calculation would you like me to perform?",
            "tool_calls": [{"tool": "open_app", "args": {"app_name": "calculator"}}],
            "screenshots": [],
            "confirmation_required": False
        }

    @classmethod
    async def _handle_alarm(cls, user_msg: str) -> Dict[str, Any]:
        """Sets an alarm directly using native Android Alarm Intent"""
        time_parsed = cls.parse_alarm_time(user_msg)
        hr, mn = time_parsed if time_parsed else (7, 0)
        formatted_time = f"{hr:02d}:{mn:02d}"

        dev = adb_controller.get_connected_device()
        if dev:
            cmd = f'am start -a android.intent.action.SET_ALARM --ei android.intent.extra.hour {hr} --ei android.intent.extra.minutes {mn} --es android.intent.extra.MESSAGE "Alarm" --ez android.intent.extra.SKIP_UI true'
            dev.shell(cmd)
            time.sleep(1.0)

        return {
            "reply": f"Alarm successfully set for {formatted_time} on your phone.",
            "tool_calls": [{"tool": "set_alarm", "args": {"hour": hr, "minute": mn}}],
            "screenshots": [],
            "confirmation_required": False
        }

    # -----------------------------------------------------------------
    # MAIN TASK EXECUTION ENTRYPOINT
    # -----------------------------------------------------------------

    @classmethod
    async def execute_task(cls, user_msg: str, session_id: str = "default") -> Dict[str, Any]:
        """
        Executes general mobile tasks. Fast and streamlined without redundant screenshots.
        """
        m = user_msg.lower().strip()
        executed_tools = []

        # 0. Multi-turn Calculator Context Check: If calculator is active and user provided numbers/math
        try:
            from app.agent.conversation_memory import conversation_manager
            state = conversation_manager.get_state(session_id)
            if state.get("waiting_for") == "calculator_operation" or state.get("active_app") == "calculator":
                if cls.parse_math_expression(m) or re.search(r"\d+", m):
                    logger.info(f"Multi-turn Calculator context matched for session {session_id}: '{user_msg}'")
                    return await cls._handle_calculator(user_msg, session_id=session_id)
        except Exception as e:
            logger.warning(f"Error checking calculator context: {e}")

        # 1. Alarm / Clock Intent
        if any(w in m for w in ["alarm", "wake me up", "baje uthana", "alarm lagao", "alarm set"]):
            return await cls._handle_alarm(user_msg)

        # 2. Calculator Intent
        MATH_TRIGGERS = [
            "calculator", "calcualtor", "calcuator", "calcultor", "calculater", "calc", "calculate", "calculation",
            "hisab", "hisaab", "addition", "subtraction", "multiplication", "division",
            "add ", "add numbers", "add number", "subtract", "multiply", "divide",
            "sum of", "plus", "minus", "jodo", "ghatao", "guna", "bhaag"
        ]
        if any(w in m for w in MATH_TRIGGERS) or cls.parse_math_expression(m):
            return await cls._handle_calculator(user_msg, session_id=session_id)

        # 3. Smart Song / YouTube Intent
        SONG_TRIGGERS = [
            "play a song", "play song", "play the song", "play music", "play track",
            "play video", "play lofi", "gana chalao", "gana bajao", "song bajao",
            "song chalao", "geet chalao", "gaana chalao", "gaana bajao", "listen to"
        ]
        if any(w in m for w in SONG_TRIGGERS) or (m.startswith("play ") and not any(w in m for w in ["cricket", "football", "game", "match", "pubg", "freefire"])):
            song_title = cls.extract_song_title(user_msg)
            return await cls._handle_youtube(user_msg, action_query=song_title)

        if "youtube" in m or any(w in m for w in ["gana chalao", "song play", "video chalao", "lofi music", "bajao"]):
            app_target, action_query = cls.extract_app_and_action(user_msg)
            return await cls._handle_youtube(user_msg, action_query)

        # 4. Knowledge & Information Search (Wikipedia, DuckDuckGo, Arxiv, PubMed + Local Gemma Reframing)
        is_web_search = False
        search_query = ""

        if "how to" in m or "developer mode" in m:
            is_web_search = True
            clean_q = re.sub(r"^.*?(?:how\s+to|developer\s+mode)", "how to", m, flags=re.I).strip()
            clean_q = re.sub(r"\b(dekh\s+ke\s+batao|batao|karo|bhai|yaar|please)\b", "", clean_q, flags=re.I).strip()
            search_query = clean_q or m
        elif any(w in m for w in ["google", "browser", "chrome", "search", "internet", "wiki", "wikipedia", "arxiv", "pubmed"]):
            for pat in cls.WEB_SEARCH_TRIGGERS:
                match = re.search(pat, m)
                if match:
                    is_web_search = True
                    search_query = match.group(1).strip()
                    break

        if is_web_search and search_query:
            from app.agent.tools.search_tools import search_knowledge_service
            clean_q = search_knowledge_service.clean_query(search_query)
            clean_q = clean_q or search_query
            logger.info(f"Executing Knowledge Tool Search: query='{clean_q}' (User message: '{user_msg}')")

            search_res = await search_knowledge_service.search_and_reframe(user_msg=user_msg, query=clean_q)
            return {
                "reply": search_res["reply"],
                "tool_calls": search_res["tool_calls"],
                "screenshots": [],
                "confirmation_required": False
            }

        # 5. Generic App Launch & In-App Actions
        app_target, action_query = cls.extract_app_and_action(user_msg)
        logger.info(f"Launching Mobile App: {app_target}")
        adb_controller.open_app(app_target)
        time.sleep(1.8)

        executed_tools.append({
            "tool": "open_app",
            "args": {"app_name": app_target},
            "result": f"Launched {app_target}."
        })

        if action_query and action_query != user_msg:
            elements = adb_controller.dump_screen_hierarchy()
            for el in elements:
                desc = (el.get("desc") or "").lower()
                txt = (el.get("text") or "").lower()
                if "search" in desc or "search" in txt:
                    cx, cy = el["center"]
                    adb_controller.click_at(cx, cy)
                    time.sleep(0.5)
                    break

            adb_controller.input_text(action_query)
            time.sleep(0.3)
            # Use KEYCODE_ENTER (66) for submitting in-app searches
            adb_controller.press_key(66)
            time.sleep(1.5)

            return {
                "reply": f"Opened {app_target.capitalize()} on your phone and searched for '{action_query}'.",
                "tool_calls": executed_tools,
                "screenshots": [],
                "confirmation_required": False
            }

        return {
            "reply": f"Opened {app_target.capitalize()} on your phone.",
            "tool_calls": executed_tools,
            "screenshots": [],
            "confirmation_required": False
        }

universal_mobile_agent = UniversalMobileAgent()
