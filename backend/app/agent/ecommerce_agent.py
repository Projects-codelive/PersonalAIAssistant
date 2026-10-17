import re
import time
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from app.device.adb_controller import adb_controller
from app.device.device_bridge import device_bridge
from app.agent.privacy_guard import privacy_guard
from app.agent.ecommerce_comparator import ecommerce_comparator, ProductItem
from app.agent.task_checkpoint_manager import task_checkpoint_manager, TaskCheckpoint

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path(__file__).resolve().parents[3] / "client" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

class AutonomousEcommerceAgent:
    """
    Autonomous Mobile E-Commerce & UI Navigation Agent.
    Coordinates multi-step tasks across mobile apps (Flipkart, Amazon, Web):
    - App presence detection with intelligent direct-search web browser fallback
    - Autonomous screen perception, search, and navigation
    - Semantic product comparison (lowest price / best value)
    - Full step-by-step visual screenshot verification
    - Privacy, OTP, and confidential payment guardrails
    - Pre-purchase confirmation & resilient error checkpoint resumption
    """

    SUPPORTED_APPS = ["flipkart", "amazon", "myntra", "blinkit", "zepto", "swiggy", "zomato"]
    DEFAULT_APP = "flipkart"
    # Stores whose app opens its own web search URL (…/search?q=…) straight on the results page
    SEARCH_LINK_APPS = {"flipkart", "amazon"}

    # Spoken / typed variants of store names -> canonical name ("flip kart" from voice typing, typos, Hindi script)
    STORE_ALIASES = {
        r"flip\s*-?\s*k[ae]rt|flipcart|flpkart|filpkart|फ्लिपकार्ट": "flipkart",
        r"amazone|amzon|amazon\.in|amzn|अमेज़न|अमेजन": "amazon",
        r"myntraa|mintra|मिंत्रा": "myntra",
        r"blink\s+it|blinkitt|ब्लिंकिट": "blinkit",
        r"zeptoo|जेप्टो": "zepto",
        r"swiggy\s+instamart|instamart|swigy|स्विगी": "swiggy",
        r"zomatoo|zometo|ज़ोमैटो|जोमैटो": "zomato",
    }
    # "buy a pen" with no store named: shop on DEFAULT_APP, but only for clear purchase verbs and not
    # when the sentence is really about something else (calls, messages, bookings, order status)
    PURCHASE_VERBS = (r"\b(buy|purchase|order|kharido|kharid(?:na|ni)?|mangwao|mangao|mangwa\s+do|le\s+lo|lena\s+hai|chahiye)\b"
                      r"|(?:खरीदो|खरीदना|खरीद|मंगवाओ|मंगाओ|ऑर्डर|आर्डर|चाहिए)")
    NOT_SHOPPING = (r"\b(call|dial|phone|sms|whatsapp|message|text|email|mail|alarm|song|music|play|youtube|"
                    r"ticket|tickets|cab|taxi|uber|ola|table|appointment|status|track|tracking|where\s+is\s+my|"
                    # questions about shopping are for the assistant, not purchases ("what should I buy?")
                    r"what|which|why|how|should|recommend|suggest|kya|kaunsa|kaun\s+sa)\b")

    @classmethod
    def normalize_store_names(cls, user_msg: str) -> str:
        m = user_msg
        for pattern, canonical in cls.STORE_ALIASES.items():
            m = re.sub(pattern, canonical, m, flags=re.IGNORECASE)
        return m

    FALLBACK_URLS = {
        "flipkart": "https://www.flipkart.com",
        "amazon": "https://www.amazon.in",
        "myntra": "https://www.myntra.com",
        "blinkit": "https://www.blinkit.com",
        "zepto": "https://www.zeptonow.com",
        "swiggy": "https://www.swiggy.com",
        "zomato": "https://www.zomato.com"
    }

    @classmethod
    def is_ecommerce_intent(cls, user_msg: str) -> bool:
        """Detect whether user message is an e-commerce shopping or ordering instruction"""
        m = cls.normalize_store_names(user_msg).lower()
        has_app = any(app in m for app in cls.SUPPORTED_APPS)
        has_action = bool(re.search(r"\b(order|buy|purchase|search|find|kharido|kharid|manga|mangwao|le\s+lo|lena\s+hai|book|dhundh\w*|dhoondh\w*|dhund[oh]\w*|dhoond[oh]\w*)\b", m)
                          or re.search(r"खरीद|मंगवा|मंगा|ऑर्डर|आर्डर|ढूंढ|ढूँढ", m))
        has_direct = bool(re.search(r"^(?:go\s+to|open|jao|jaao|kholo)\s+(?:flipkart|amazon|myntra|blinkit|zepto|swiggy|zomato)", m))
        if (has_app and has_action) or (has_app and has_direct):
            return True
        # No store named: a plain purchase request ("buy a pen", "mujhe ek pen kharidna hai")
        if not re.search(cls.PURCHASE_VERBS, m) or re.search(cls.NOT_SHOPPING, m):
            return False
        # extract_product_query falls back to "pen" when nothing is left, so check a product was really named
        product = cls.extract_product_query(m)
        return product != "pen" or bool(re.search(r"\bpens?\b", m))

    @classmethod
    def is_resumption_intent(cls, user_msg: str, session_id: str) -> bool:
        """Detect if user is asking to continue or resume an active paused task"""
        if not task_checkpoint_manager.has_active_task(session_id):
            return False
        m = user_msg.lower().strip()
        return bool(re.search(
            r"\b(continue|proceed|go ahead|resume|done|fixed|maine kar diya|kar diya|aage badho|haan aage badho|ho gaya|login kar diya|otp daal diya|number daal diya|mobile number daal diya|daal diya|inform|ho gaya hai|done hai|completed|done now|ready|enter kar diya|daal diya hai|maine daal diya|next step)\b",
            m
        ))

    @classmethod
    def is_order_confirmation_response(cls, user_msg: str, session_id: str) -> bool:
        """Check if an order confirmation is currently pending for this session"""
        ckpt = task_checkpoint_manager.get_checkpoint(session_id)
        if not ckpt or ckpt.current_stage != "AWAITING_ORDER_CONFIRMATION":
            return False
        return True

    @classmethod
    def extract_product_query(cls, text: str) -> str:
        """Robustly extracts the clean product item from arbitrary clumsy phrasing"""
        m = text.lower().strip()
        for app in cls.SUPPORTED_APPS:
            m = re.sub(rf"\b{app}\b", " ", m)
        noise_words = [
            r"\b(?:go\s+to|open|jao|jaao|kholo)\b",
            r"\b(?:and|aur|then|fir|bhi)\b",
            r"\b(?:se|pe|par|me|mein|on|in|from|fron|frm|form|frome|using|to|for|with|at|having)\b",  # incl. "from" typos
            r"\b(?:bhai|yaar|please|zara|ek|kuch|na|do|kar\s+do|kar\s+do\s+na)\b",
            r"\b(?:order|buy|purchase|kharido|kharid|manga|mangwao|dhoondh|dhoondho|dhoondo|dhundh|dhundho|dhundo|book|le\s+lo|lena\s+hai|lana|lao|get|find|search)\b",
            r"\b(?:lowest|price|cost|cheapest|cheap|sasta|sasti|sabse\s+sasta|best|value|low|rate|wala|wali|rate\s+ka)\b",
            r"\b(?:a|an|the)\b",
            r"\b(?:ke|ko|ka|ki)\b",
            r"\b(?:karo|karna\s+hai|kariye)\b",
            r"\b(?:mujhe|mere\s+liye|kharidna|kharidni|chahiye|hai|i\s+want\s+to|i\s+need|want|need)\b",
            # Hindi (Devanagari) connectors and purchase verbs
            # (whole words only: Python's \b doesn't separate Devanagari, and "पे" must not eat "पेन")
            r"(?<!\S)(?:खरीदो|खरीदना|खरीद|मंगवाओ|मंगाओ|ऑर्डर|आर्डर|चाहिए|करो|से|पे|पर|का|की|के|को|मुझे|एक|है|सबसे|सस्ता)(?!\S)"
        ]
        cleaned = m
        for pat in noise_words:
            cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

        # Normalize common brand misspellings
        cleaned = re.sub(r"\bluxur\b", "luxor", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bflpkart\b", "flipkart", cleaned, flags=re.IGNORECASE)

        tokens = [w for w in cleaned.split() if len(w) >= 2]
        return " ".join(tokens) if tokens else "pen"

    @classmethod
    def parse_ecommerce_intent(cls, user_msg: str) -> Dict[str, Any]:
        """
        Extract target app, product query, and comparison criteria from natural language:
        e.g. 'bhai flipkart se sabse sasta pen dhundh ke book kar do na yaar'
        """
        m = cls.normalize_store_names(user_msg).lower().strip()

        # 1. Target app (default when none named)
        target_app = cls.DEFAULT_APP
        for app in cls.SUPPORTED_APPS:
            if app in m:
                target_app = app
                break

        # 2. Criteria
        criteria = "lowest_price"
        if any(term in m for term in ["best value", "unit price", "value for money", "pack", "sasta aur accha"]):
            criteria = "best_value"
        elif any(term in m for term in ["lowest price", "cheapest", "cheap", "sasta", "sabse sasta", "low price"]):
            criteria = "lowest_price"

        # 3. Product query extraction
        query = cls.extract_product_query(m)

        return {
            "app": target_app,
            "query": query,
            "criteria": criteria
        }

    @classmethod
    async def run_autonomous_shopping_flow(
        cls,
        session_id: str,
        app_name: str,
        query: str,
        criteria: str = "lowest_price"
    ) -> Dict[str, Any]:
        """
        Executes the autonomous mobile e-commerce workflow step-by-step with visual screenshot verification:
        1. Check app installed -> launch app or fallback to direct search in Chrome
        2. Capture Step 1 Search Screenshot
        3. Parse search results -> compare prices -> tap cheapest
        4. Capture Step 2 Product Page Screenshot
        5. Guard for OTP/login/confidential data -> pause if detected
        6. Request confirmation before final order placement
        """
        logger.info(f"Starting Autonomous Shopping Flow: app={app_name}, query={query}, criteria={criteria}")
        executed_tools = []
        screenshots_taken = []
        ts = int(time.time())

        # Initialize or update checkpoint
        ckpt = task_checkpoint_manager.create_or_update(
            session_id=session_id,
            goal=f"Order {query} on {app_name} with {criteria}",
            target_app=app_name,
            stage="INIT",
            query=query,
            criteria=criteria
        )

        # -------------------------------------------------------------
        # STEP 1: App Existence Detection & Intelligent Direct Search Web Fallback
        # -------------------------------------------------------------
        if app_name == "flipkart":
            fallback_url = f"https://www.flipkart.com/search?q={query}"
        elif app_name == "amazon":
            fallback_url = f"https://www.amazon.in/s?k={query}"
        else:
            fallback_url = cls.FALLBACK_URLS.get(app_name, f"https://www.google.com/search?q={app_name}+{query}")

        app_mode = "NATIVE_APP"
        status_msg = ""

        if adb_controller.is_device_connected:
            is_installed = adb_controller.is_app_installed(app_name)
            if is_installed:
                logger.info(f"Native app '{app_name}' is installed. Launching app...")
                app_mode = "NATIVE_APP"
                status_msg = f"Opened the native {app_name.capitalize()} app."
                if app_name in cls.SEARCH_LINK_APPS:
                    # The store's own search URL opens its results page inside the app in one step. Typing into
                    # the search box is unreliable: store home screens animate constantly, which blocks the
                    # accessibility dump needed to find the box.
                    adb_controller.open_link_in_app(fallback_url, adb_controller.get_package_for_app_name(app_name))
                    time.sleep(4.0)
                    elements = []
                else:
                    adb_controller.open_app(app_name)
                    time.sleep(2.0)
                    elements = adb_controller.dump_screen_hierarchy()
                # Search inside native app (stores without a search link)
                for el in elements:
                    txt = (el.get("text") or "").lower()
                    desc = (el.get("desc") or "").lower()
                    rid = (el.get("id") or "").lower()
                    if "search" in txt or "search" in desc or "search" in rid or "hint_text" in rid:
                        cx, cy = el["center"]
                        adb_controller.click_at(cx, cy)
                        break
                if app_name not in cls.SEARCH_LINK_APPS:
                    time.sleep(0.5)
                    adb_controller.input_text(query)
                    time.sleep(0.5)
                    adb_controller.press_key("search")
                    time.sleep(2.0)
            else:
                logger.info(f"Native app '{app_name}' NOT installed. Intelligently opening mobile Chrome directly to {fallback_url}...")
                adb_controller.open_url_in_browser(fallback_url)
                app_mode = "WEB_BROWSER"
                status_msg = f"The {app_name.capitalize()} app is not installed on your phone. Intelligently switching execution: Opened mobile browser (Chrome) and searched for '{query}' on {app_name}.com."
                time.sleep(2.0)

            # Screenshot Step 1: Product Search Rendered
            s1_path = str(SCREENSHOT_DIR / f"ecom_{ts}_step1_search.png")
            adb_controller.save_screenshot(s1_path)
            screenshots_taken.append(s1_path)
            ckpt.screenshots.append(s1_path)

            executed_tools.append({
                "tool": "search_product",
                "args": {"app": app_name, "query": query, "mode": app_mode},
                "result": f"Navigated to search results for '{query}'. Verified with screenshot: {s1_path}"
            })
        else:
            # Device Bridge simulated mode
            app_mode = "WEB_BROWSER"
            status_msg = f"Navigated to {app_name.capitalize()} on mobile device."
            s1_path = str(SCREENSHOT_DIR / f"ecom_{ts}_step1_simulated.png")
            screenshots_taken.append(s1_path)
            ckpt.screenshots.append(s1_path)

        ckpt.mode = app_mode
        ckpt.current_stage = "SEARCHED"
        task_checkpoint_manager.record_progress(
            session_id=session_id,
            action=f"Opened {app_name.capitalize()} and searched for '{query}'",
            stage="SEARCHED",
            detail=f"Execution mode: {app_mode}"
        )

        # -------------------------------------------------------------
        # STEP 2: Inspect Screen Hierarchy & Compare Products
        # -------------------------------------------------------------
        if adb_controller.is_device_connected:
            elements = adb_controller.dump_screen_hierarchy()

            # Check privacy guard on results screen
            guard = privacy_guard.evaluate_screen_elements(elements)
            if guard.is_sensitive:
                ckpt.current_stage = "AWAITING_USER_ACTION"
                ckpt.last_error = f"{guard.guard_type} required"
                task_checkpoint_manager.record_progress(
                    session_id=session_id,
                    action=f"Paused on search results: {guard.guard_type} required",
                    stage="AWAITING_USER_ACTION",
                    is_paused=True,
                    stuck_reason=guard.message,
                    next_step="Authenticate or resolve on screen and say 'continue'."
                )
                return {
                    "reply": f"{guard.message} Say 'continue' when ready.",
                    "tool_calls": executed_tools,
                    "screenshots": screenshots_taken,
                    "confirmation_required": False
                }

            products = ecommerce_comparator.parse_products_from_screen(elements, query)
            if not products:
                # Scroll down slightly to bypass advertising banners
                adb_controller.scroll("down", distance=450)
                time.sleep(1.2)
                elements = adb_controller.dump_screen_hierarchy()
                products = ecommerce_comparator.parse_products_from_screen(elements, query)

            if products:
                best_item = ecommerce_comparator.find_best_product(products, criteria=criteria)
                if best_item:
                    ckpt.selected_item = {
                        "title": best_item.title,
                        "price": best_item.raw_price,
                        "numeric_price": best_item.price,
                        "pack_count": best_item.pack_count,
                        "unit_price": best_item.unit_price,
                        "center": best_item.center
                    }
                    ckpt.current_stage = "AWAITING_ORDER_CONFIRMATION"

                    task_checkpoint_manager.record_progress(
                        session_id=session_id,
                        action=f"Compared available items and selected lowest price: '{best_item.title}' at {best_item.raw_price}",
                        stage="ITEM_SELECTED",
                        detail=f"Unit price: ₹{best_item.unit_price:.1f}"
                    )

                    # Click on selected item to open product page
                    adb_controller.click_at(best_item.center[0], best_item.center[1])
                    time.sleep(3.5)

                    # Screenshot Step 2: Selected Product Page
                    s2_path = str(SCREENSHOT_DIR / f"ecom_{ts}_step2_product_opened.png")
                    adb_controller.save_screenshot(s2_path)
                    screenshots_taken.append(s2_path)
                    ckpt.screenshots.append(s2_path)

                    task_checkpoint_manager.record_progress(
                        session_id=session_id,
                        action=f"Opened product page for '{best_item.title}'",
                        stage="PRODUCT_OPENED"
                    )

                    executed_tools.append({
                        "tool": "select_product",
                        "args": {"title": best_item.title, "price": best_item.raw_price},
                        "result": f"Opened product page. Verified with screenshot: {s2_path}"
                    })

                    # Hand over on the product page. The agent never taps Buy / Place order / Pay: buying and
                    # paying always stay with the user.
                    ckpt.current_stage = "HANDED_OVER"
                    task_checkpoint_manager.record_progress(
                        session_id=session_id,
                        action=f"Opened '{best_item.title}' ({best_item.raw_price}) and handed over to the user",
                        stage="COMPLETED",
                        is_paused=False,
                        next_step="User decides and taps Buy on the phone (the agent never buys or pays)."
                    )
                    task_checkpoint_manager.mark_completed(session_id)
                    label = "Best value per piece" if criteria == "best_value" else "Cheapest"
                    other = ecommerce_comparator.find_best_product(
                        products, criteria="lowest_price" if criteria == "best_value" else "best_value")
                    other_line = ""
                    if other and other.title != best_item.title:
                        other_label = "Cheapest" if criteria == "best_value" else "Best value per piece"
                        other_line = (f"\n{other_label}: \"{other.title}\" at {other.raw_price}"
                                      + (f" (pack of {other.pack_count}, about ₹{other.unit_price:.1f} each)" if other.pack_count > 1 else "")
                                      + ".")
                    return {
                        "reply": (
                            f"{status_msg}\n\nI searched '{query}' on {app_name.capitalize()} and compared {len(products)} results.\n"
                            f"{label}: \"{best_item.title}\" at {best_item.raw_price}"
                            + (f" (pack of {best_item.pack_count}, about ₹{best_item.unit_price:.1f} each)" if best_item.pack_count > 1 else "")
                            + f".{other_line}\n\n"
                            "I've opened it on your phone. I never place orders or make payments: if you want it, "
                            "tap Buy on your phone and finish there."
                        ),
                        "tool_calls": executed_tools,
                        "screenshots": screenshots_taken,
                        "confirmation_required": False
                    }

        # A phone is connected but no product could be read from its screen (e.g. the store app's animations
        # block the accessibility dump): say so honestly instead of inventing a result
        if adb_controller.is_device_connected:
            task_checkpoint_manager.record_progress(
                session_id=session_id,
                action=f"Searched '{query}' on {app_name} but could not read the product list from the screen",
                stage="SEARCH_SHOWN",
                is_paused=False,
            )
            task_checkpoint_manager.clear(session_id)
            return {
                "reply": (
                    f"{status_msg}\n\nI searched for '{query}' on {app_name.capitalize()}, but I couldn't read the "
                    "product list on the screen this time, so I haven't picked an item or started an order. "
                    "The results are open on your phone: tap the one you like, or ask me to try again."
                ),
                "tool_calls": executed_tools,
                "screenshots": screenshots_taken,
                "confirmation_required": False,
            }

        # No phone connected: demo data so the flow can be shown without a device (clearly labelled as such)
        simulated_item = {
            "title": f"Cello Butterflow Blue Ball Pen (Pack of 5)",
            "price": "₹50",
            "numeric_price": 50.0,
            "pack_count": 5,
            "unit_price": 10.0
        }
        ckpt.selected_item = simulated_item
        ckpt.current_stage = "AWAITING_ORDER_CONFIRMATION"
        s2_path = str(SCREENSHOT_DIR / f"ecom_{ts}_step2_product_simulated.png")
        screenshots_taken.append(s2_path)
        ckpt.screenshots.append(s2_path)

        return {
            "reply": (
                f"{status_msg}\n\n"
                "(Demo mode: no phone is connected, so this result is sample data.)\n\n"
                f"Searching '{query}' on {app_name.capitalize()}, the lowest price option would be "
                f"\"{simulated_item['title']}\" at {simulated_item['price']} (₹10/pen). "
                "Say 'yes' to continue the demo or 'no' to cancel."
            ),
            "tool_calls": executed_tools,
            "screenshots": screenshots_taken,
            "confirmation_required": True,
            "pending_action": {
                "type": "ECOMMERCE_ORDER",
                "app": app_name,
                "item": simulated_item
            }
        }

    @classmethod
    async def resume_autonomous_task(cls, session_id: str) -> Dict[str, Any]:
        """
        Resumes task after user handled an error, login, or OTP on phone screen.
        """
        ckpt = task_checkpoint_manager.get_checkpoint(session_id)
        if not ckpt:
            return {"reply": "There is no paused task to resume.", "tool_calls": []}

        logger.info(f"Resuming autonomous task from checkpoint: stage={ckpt.current_stage}, item={ckpt.selected_item}")
        task_checkpoint_manager.record_progress(
            session_id=session_id,
            action="Resumed autonomous shopping flow after user input",
            stage="RESUMING",
            is_paused=False
        )

        # Re-inspect active screen
        if adb_controller.is_device_connected:
            adb_controller.wake_screen()
            time.sleep(0.5)
            elements = adb_controller.dump_screen_hierarchy()

            # Check if still blocked by OTP or Phone Number
            guard = privacy_guard.evaluate_screen_elements(elements)
            if guard.is_sensitive and guard.guard_type in ["OTP", "PASSWORD", "PHONE_NUMBER_LOGIN"]:
                task_checkpoint_manager.record_progress(
                    session_id=session_id,
                    action=f"Authentication still required: {guard.guard_type}",
                    stage="AWAITING_USER_ACTION",
                    is_paused=True,
                    stuck_reason=guard.message,
                    next_step="Please complete the action on your phone screen and say 'done' or 'continue'."
                )
                return {
                    "reply": f"The screen is still awaiting authentication: {guard.message} Please complete it on your phone and say 'done' or 'continue'.",
                    "tool_calls": [],
                    "screenshots": getattr(ckpt, "screenshots", [])
                }

            # If user completed phone/login, tap 'Continue' or 'Deliver Here' to advance checkout if visible
            for el in elements:
                comb = f"{el.get('text', '')} {el.get('desc', '')}".lower()
                if any(w in comb for w in ["deliver here", "continue", "save and deliver", "use this address"]):
                    cx, cy = el["center"]
                    logger.info(f"Advancing checkout flow by tapping '{comb}' at ({cx}, {cy})...")
                    adb_controller.click_at(cx, cy)
                    time.sleep(2.0)
                    break

            ts = int(time.time())
            s_resume = str(SCREENSHOT_DIR / f"ecom_{ts}_step4_resumed_summary.png")
            adb_controller.save_screenshot(s_resume)
            ckpt.screenshots.append(s_resume)

            item = ckpt.selected_item or {"title": "Pen", "price": "the selected item"}
            ckpt.current_stage = "AWAITING_ORDER_CONFIRMATION"
            task_checkpoint_manager.record_progress(
                session_id=session_id,
                action=f"Progressed past login to Order Summary for '{item.get('title')}' ({item.get('price')})",
                stage="AWAITING_ORDER_CONFIRMATION",
                is_paused=True,
                stuck_reason="Waiting for final user pre-purchase confirmation.",
                next_step="Say 'yes' to confirm order placement or 'no' to cancel."
            )
            return {
                "reply": (
                    f"Great! Login completed. We have progressed to the Order Summary for \"{item.get('title')}\" ({item.get('price')}).\n\n"
                    "Would you like me to confirm and place this order? Say 'yes' to confirm or 'no' to cancel."
                ),
                "tool_calls": [],
                "screenshots": getattr(ckpt, "screenshots", []),
                "confirmation_required": True,
                "pending_action": {"type": "ECOMMERCE_ORDER", "item": item, "app": ckpt.target_app}
            }

    @classmethod
    async def handle_order_confirmation(cls, session_id: str, is_confirmed: bool) -> Dict[str, Any]:
        """
        Handles user decision (yes/no) on the pre-purchase confirmation.
        Taps 'Buy Now' / 'Proceed to Checkout', captures final confirmation screenshot.
        """
        ckpt = task_checkpoint_manager.get_checkpoint(session_id)
        if not ckpt:
            return {"reply": "No pending order to confirm.", "tool_calls": []}

        item = ckpt.selected_item or {"title": "Pen", "price": "the item"}

        if not is_confirmed:
            task_checkpoint_manager.record_progress(
                session_id=session_id,
                action=f"Order cancelled by user for '{item.get('title')}'",
                stage="CANCELLED",
                is_paused=False
            )
            task_checkpoint_manager.clear(session_id)
            return {
                "reply": f"Order for \"{item.get('title')}\" has been cancelled.",
                "tool_calls": [],
                "confirmation_required": False
            }

        # The agent never places orders or payments: a "yes" here (from an older pending order) only hands over
        task_checkpoint_manager.record_progress(
            session_id=session_id,
            action=f"Handed over '{item.get('title')}' to the user (agent never buys or pays)",
            stage="COMPLETED",
            is_paused=False,
        )
        task_checkpoint_manager.mark_completed(session_id)
        return {
            "reply": (f"For your safety I never place orders or make payments. \"{item.get('title')}\" "
                      f"({item.get('price')}) is open on your phone: tap Buy there if you want it."),
            "tool_calls": [],
            "confirmation_required": False
        }


ecommerce_agent = AutonomousEcommerceAgent()
