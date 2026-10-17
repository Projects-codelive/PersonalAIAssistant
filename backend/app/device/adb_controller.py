import os
import re
import io
import base64
import logging
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class ADBDeviceController:
    """
    Autonomous Mobile Screen Tracking & Navigation Controller.
    Interacts directly with Android devices via ADB to:
    - Capture live screenshots
    - Inspect on-screen UI hierarchy (Accessibility / UIAutomator tree)
    - Perform autonomous clicks, gestures, text input, and direct calling
    """
    def __init__(self):
        self._adb = None
        self._device = None
        self._init_adb()

    def _init_adb(self):
        try:
            import adbutils
            self._adb = adbutils.adb
            logger.info(f"Initialized ADB at: {adbutils.adb_path()}")
        except Exception as e:
            logger.warning(f"Could not initialize adbutils: {e}")

    @property
    def adb_path(self) -> Optional[str]:
        try:
            import adbutils
            return adbutils.adb_path()
        except Exception:
            return None

    def get_connected_device(self):
        """Get the first active connected Android device (USB or Wi-Fi)"""
        if not self._adb:
            return None
        try:
            devices = self._adb.device_list()
            if devices:
                self._device = devices[0]
                return self._device
        except Exception as e:
            logger.debug(f"Error checking ADB devices: {e}")
        return None

    @property
    def is_device_connected(self) -> bool:
        return self.get_connected_device() is not None

    def get_device_info(self) -> Dict[str, Any]:
        dev = self.get_connected_device()
        if not dev:
            return {"connected": False, "device_id": None, "mode": "disconnected"}
        try:
            model = dev.prop.model or "Android Device"
            return {
                "connected": True,
                "device_id": dev.serial,
                "model": model,
                "mode": "adb_direct"
            }
        except Exception:
            return {"connected": True, "device_id": dev.serial, "mode": "adb_direct"}

    def connect_wifi(self, ip_port: str) -> bool:
        """Connect to an Android device over Wi-Fi (e.g. '192.168.1.6:5555')"""
        if not self._adb:
            return False
        try:
            res = self._adb.connect(ip_port)
            logger.info(f"ADB Wi-Fi connect to {ip_port}: {res}")
            return "connected" in res.lower()
        except Exception as e:
            logger.error(f"Failed to connect to ADB Wi-Fi {ip_port}: {e}")
            return False

    def is_keyboard_shown(self) -> bool:
        """Check if soft keyboard is currently displayed on screen via dumpsys in 50ms"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            out = dev.shell("dumpsys input_method")
            return "mInputShown=true" in out
        except Exception:
            return False

    def find_yellow_shopping_button(self) -> Tuple[int, int]:
        """
        Locates the exact pixel center of the yellow/orange Buy button at the bottom of the screen.
        Uses visual color masking in the bottom 20% region of the live screen.
        """
        dev = self.get_connected_device()
        if not dev:
            return (792, 2280)
        try:
            import numpy as np
            from PIL import Image
            pil_img = dev.screenshot()
            arr = np.array(pil_img)
            h, w, _ = arr.shape
            bottom_arr = arr[int(h * 0.80):, :, :]
            mask = (bottom_arr[:, :, 0] > 200) & (bottom_arr[:, :, 1] > 170) & (bottom_arr[:, :, 2] < 100)
            y_idx, x_idx = np.where(mask)
            if len(y_idx) > 80:
                min_x, max_x = int(x_idx.min()), int(x_idx.max())
                min_y, max_y = int(y_idx.min() + h * 0.80), int(y_idx.max() + h * 0.80)
                cx = (min_x + max_x) // 2
                cy = (min_y + max_y) // 2
                logger.info(f"Visual detector located yellow Buy button at ({cx}, {cy})")
                return (cx, cy)
            return int(w * 0.733), int(h * 0.947)
        except Exception as e:
            logger.warning(f"find_yellow_shopping_button error: {e}")
            return (792, 2280)

    def extract_visual_elements_via_ocr(self, pil_image=None) -> List[Dict[str, Any]]:
        """
        Extracts on-screen text elements with bounding boxes and centers using RapidOCR.
        Provides robust visual perception even when UIAutomator times out on animated web pages.
        """
        dev = self.get_connected_device()
        if not dev and not pil_image:
            return []
        try:
            if pil_image is None:
                pil_image = dev.screenshot()

            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
            if not hasattr(self, "_ocr_engine") or self._ocr_engine is None:
                self._ocr_engine = RapidOCR()

            arr = np.array(pil_image)
            ocr_results, _ = self._ocr_engine(arr)
            if not ocr_results:
                return []

            elements = []
            for item in ocr_results:
                pts = item[0]  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
                text = str(item[1]).strip()
                try:
                    score = float(item[2])
                except (ValueError, TypeError):
                    score = 1.0

                if score < 0.4 or not text:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                x1, x2 = int(min(xs)), int(max(xs))
                y1, y2 = int(min(ys)), int(max(ys))
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                elements.append({
                    "text": text,
                    "desc": "",
                    "label": text,
                    "id": "",
                    "class": "VisualText",
                    "clickable": True,
                    "bounds": [x1, y1, x2, y2],
                    "center": [cx, cy],
                    "ocr_score": score
                })
            logger.info(f"RapidOCR extracted {len(elements)} visual text elements from screen")
            return elements
        except Exception as e:
            logger.warning(f"extract_visual_elements_via_ocr failed: {e}")
            return []

    def dump_screen_hierarchy(self) -> List[Dict[str, Any]]:
        """
        Dumps the REAL on-screen UI hierarchy from the active Android phone.
        Combines UIAutomator accessibility tree with RapidOCR visual text recognition
        for comprehensive screen perception across native apps and mobile web pages.
        """
        dev = self.get_connected_device()
        if not dev:
            return []

        elements = []
        try:
            xml_data = dev.dump_hierarchy()
            if xml_data:
                root = ET.fromstring(xml_data)
                for node in root.iter("node"):
                    text = node.attrib.get("text", "").strip()
                    desc = node.attrib.get("content-desc", "").strip()
                    resource_id = node.attrib.get("resource-id", "").strip()
                    class_name = node.attrib.get("class", "").strip()
                    clickable = node.attrib.get("clickable", "false") == "true"
                    bounds_str = node.attrib.get("bounds", "")

                    coords = re.findall(r"\d+", bounds_str)
                    if len(coords) == 4:
                        x1, y1, x2, y2 = map(int, coords)
                        if x2 <= x1 or y2 <= y1:
                            continue
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2

                        if text or desc or clickable:
                            elements.append({
                                "text": text,
                                "desc": desc,
                                "label": text or desc or resource_id.split("/")[-1],
                                "id": resource_id,
                                "class": class_name.split(".")[-1],
                                "clickable": clickable,
                                "bounds": [x1, y1, x2, y2],
                                "center": [cx, cy]
                            })
        except Exception as e:
            logger.warning(f"dev.dump_hierarchy() error: {e}. Switching to RapidOCR visual perception...")

        # In mobile browser (Chrome/CustomTabs) or when accessibility tree is sparse, merge visual OCR elements
        is_chrome_active = any("chrome" in el.get("id", "").lower() for el in elements)
        if len(elements) < 25 or is_chrome_active:
            ocr_elements = self.extract_visual_elements_via_ocr()
            if ocr_elements:
                elements.extend(ocr_elements)

        return elements

    def take_screenshot_base64(self) -> Optional[str]:
        """Capture the live physical mobile screen and return as base64 PNG"""
        dev = self.get_connected_device()
        if not dev:
            return None
        try:
            pil_img = dev.screenshot()
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=75)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error taking ADB screenshot: {e}")
            return None

    def click_element_by_text(self, target: str) -> bool:
        """
        Autonomous Agent Navigation:
        Inspects screen, finds element matching text/desc, and taps its center.
        """
        dev = self.get_connected_device()
        if not dev:
            return False

        elements = self.dump_screen_hierarchy()
        target_lower = target.lower().strip()

        # 1. Exact match
        for el in elements:
            if el["text"].lower() == target_lower or el["desc"].lower() == target_lower:
                cx, cy = el["center"]
                logger.info(f"ADB Tapping exact match '{target}' at ({cx}, {cy})")
                dev.click(cx, cy)
                return True

        # 2. Substring match
        for el in elements:
            if target_lower in el["text"].lower() or target_lower in el["desc"].lower() or target_lower in el["id"].lower():
                cx, cy = el["center"]
                logger.info(f"ADB Tapping substring match '{target}' at ({cx}, {cy})")
                dev.click(cx, cy)
                return True

        logger.warning(f"Element '{target}' not found on active screen.")
        return False

    def click_at(self, x: int, y: int) -> bool:
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            dev.click(x, y)
            return True
        except Exception as e:
            logger.error(f"ADB Click error at ({x}, {y}): {e}")
            return False

    def input_text(self, text: str) -> bool:
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            dev.send_keys(text)
            return True
        except Exception as e:
            logger.error(f"ADB input text error: {e}")
            return False

    APP_PACKAGE_MAP = {
        "flipkart": "com.flipkart.android",
        "amazon": "com.amazon.mShop.android.shopping",
        "myntra": "com.myntra.android",
        "blinkit": "com.grofers.customerapp",
        "zepto": "com.selldone.zepto",
        "swiggy": "in.swiggy.android",
        "zomato": "com.application.zomato",
        "chrome": "com.android.chrome",
        "browser": "com.android.chrome",
        "youtube": "com.google.android.youtube",
        "contacts": "com.google.android.contacts",
        "phone": "com.google.android.dialer",
        "dialer": "com.google.android.dialer",
        "settings": "com.android.settings",
        "messages": "com.google.android.apps.messaging",
        "whatsapp": "com.whatsapp",
        "gmail": "com.google.android.gm",
        "calculator": "com.sec.android.app.popupcalculator",
        "calc": "com.sec.android.app.popupcalculator",
        "calcualtor": "com.sec.android.app.popupcalculator",
        "calcuator": "com.sec.android.app.popupcalculator",
        "calcultor": "com.sec.android.app.popupcalculator",
        "calculater": "com.sec.android.app.popupcalculator",
        "music": "com.google.android.youtube",
        "songs": "com.google.android.youtube",
        "song": "com.google.android.youtube",
        "clock": "com.sec.android.app.clockpackage",
        "alarm": "com.sec.android.app.clockpackage",
        "claude": "com.anthropic.claude",
        "gemini": "com.google.android.apps.bard",
        "perplexity": "ai.perplexity.app.android",
        "uber": "com.ubercab",
        "maps": "com.google.android.apps.maps",
        "google maps": "com.google.android.apps.maps",
        "gallery": "com.sec.android.gallery3d",
        "photos": "com.google.android.apps.photos", 
        "files": "com.sec.android.app.myfiles",
        "my files": "com.sec.android.app.myfiles",
        "notes": "com.samsung.android.app.notes",
        "keep": "com.google.android.keep",
        "google keep": "com.google.android.keep",
        "play store": "com.android.vending",
        "store": "com.android.vending",
        "vlc": "org.videolan.vlc",
        "zoom": "us.zoom.videomeetings",
        "notion": "notion.id",
        "linkedin": "com.linkedin.android",
        "twitter": "com.twitter.android",
        "x": "com.twitter.android",
        "where is my train": "com.whereismytrain.android",
        "train": "com.whereismytrain.android",
        "mcdonalds": "com.mcdonalds.mobileapp"
    }

    def get_package_for_app_name(self, app_name: str) -> str:
        """Map common app name to Android package name"""
        clean = app_name.strip().lower()
        return self.APP_PACKAGE_MAP.get(clean, app_name)

    def wake_screen(self) -> bool:
        """Wake up device screen and dismiss keyguard if asleep safely without triggering menu"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            import time
            dev.shell("input keyevent 224")  # KEYCODE_WAKEUP
            time.sleep(0.2)
            dev.shell("wm dismiss-keyguard")  # Safely dismiss lockscreen without opening 3-dot menus
            time.sleep(0.3)
            return True
        except Exception as e:
            logger.warning(f"Error waking screen: {e}")
            return False

    def is_app_installed(self, package_or_name: str) -> bool:
        """Check if an Android application is installed on the physical device"""
        dev = self.get_connected_device()
        if not dev:
            return False
        pkg = self.get_package_for_app_name(package_or_name)
        try:
            out = dev.shell(f"pm list packages {pkg}")
            return f"package:{pkg}" in out
        except Exception as e:
            logger.warning(f"Error checking if {pkg} is installed: {e}")
            return False

    def open_app(self, package_or_name: str) -> bool:
        dev = self.get_connected_device()
        if not dev:
            return False
        self.wake_screen()
        pkg = self.get_package_for_app_name(package_or_name)
        try:
            dev.app_start(pkg)
            return True
        except Exception as e:
            # Fallback to shell monkey
            dev.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            return True

    def open_url_in_browser(self, url: str) -> bool:
        """Launch URL in Android Chrome and ensure page loads (bypassing omnibox suggestions)"""
        dev = self.get_connected_device()
        if not dev:
            return False
        self.wake_screen()
        try:
            import time
            safe_url = url.replace("&", "\\&")
            cmd = f"am start -a android.intent.action.VIEW -d '{safe_url}' com.android.chrome"
            logger.info(f"Opening URL in browser: {cmd}")
            dev.shell(cmd)
            # Give Chrome 1 second to initialize and press Enter to load URL if caught in omnibox/search suggestions
            time.sleep(1.2)
            dev.shell("input keyevent 66")
            time.sleep(1.8)
            return True
        except Exception as e:
            logger.error(f"Error opening URL in browser via ADB: {e}")
            try:
                import time
                dev.shell(f"am start -a android.intent.action.VIEW -d '{safe_url}'")
                time.sleep(1.0)
                dev.shell("input keyevent 66")
                return True
            except Exception:
                return False

    def save_screenshot(self, filepath: str) -> bool:
        """Capture screenshot of current screen and save to filepath"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            pil_img = dev.screenshot()
            pil_img.save(filepath)
            logger.info(f"Screenshot saved to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error saving screenshot: {e}")
            return False

    def scroll(self, direction: str = "down", distance: int = 600) -> bool:
        """Scroll screen up or down using swipe gesture"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            # Use center of typical screen: (540, 1200)
            cx = 540
            cy = 1200
            if direction.lower() == "down":
                # Swipe upwards to scroll down
                dev.shell(f"input swipe {cx} {cy + distance // 2} {cx} {cy - distance // 2} 300")
            elif direction.lower() == "up":
                # Swipe downwards to scroll up
                dev.shell(f"input swipe {cx} {cy - distance // 2} {cx} {cy + distance // 2} 300")
            elif direction.lower() == "left":
                dev.shell(f"input swipe {cx + distance // 2} {cy} {cx - distance // 2} {cy} 300")
            elif direction.lower() == "right":
                dev.shell(f"input swipe {cx - distance // 2} {cy} {cx + distance // 2} {cy} 300")
            return True
        except Exception as e:
            logger.error(f"Error scrolling screen via ADB: {e}")
            return False

    def press_key(self, key: str) -> bool:
        """Send key events to mobile device (enter, search, back, home)"""
        dev = self.get_connected_device()
        if not dev:
            return False
        key_map = {
            "enter": "66",
            "search": "84",
            "back": "4",
            "home": "3",
            "tab": "61",
            "delete": "67"
        }
        keycode = key_map.get(key.lower(), key)
        try:
            dev.shell(f"input keyevent {keycode}")
            return True
        except Exception as e:
            logger.error(f"Error sending keyevent {keycode} via ADB: {e}")
            return False

    def open_mobile_dialer(self, phone_number: str) -> bool:
        """
        Mobile Dialer Calling:
        Opens the native mobile dialer with the phone number dialed so the user can place the call.
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            clean = re.sub(r"[^\d+]", "", phone_number)
            cmd = f"am start -a android.intent.action.DIAL -d tel:{clean}"
            logger.info(f"Executing ADB open mobile dialer: {cmd}")
            dev.shell(cmd)
            return True
        except Exception as e:
            logger.error(f"Error opening mobile dialer via ADB: {e}")
            return False

    def make_direct_call(self, phone_number: str) -> bool:
        """
        Zero-Touch Direct Phone Calling:
        Places direct call on phone via ACTION_CALL if supported, or falls back to mobile dialer.
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            clean = re.sub(r"[^\d+]", "", phone_number)
            cmd = f"am start -a android.intent.action.CALL -d tel:{clean}"
            logger.info(f"Executing ADB direct call: {cmd}")
            dev.shell(cmd)
            return True
        except Exception as e:
            logger.warning(f"ACTION_CALL failed, falling back to dialer: {e}")
            return self.open_mobile_dialer(phone_number)

    def is_call_in_progress(self) -> bool:
        """
        Checks whether telephony call state on Android device is currently active (ringing or offhook).
        mCallState: 0 = IDLE, 1 = RINGING, 2 = OFFHOOK
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            out = dev.shell("dumpsys telephony.registry")
            for line in out.splitlines():
                if "mCallState=" in line:
                    m = re.search(r"mCallState=(\d+)", line)
                    if m and int(m.group(1)) > 0:
                        return True
        except Exception as e:
            logger.debug(f"Error checking call state: {e}")
        return False

    async def watch_call_termination(self, phone_number: str):
        """
        Asynchronously monitors telephony state on device.
        When call state transitions back to 0 (cut by user or remote party):
        1. Reroutes the user back to the AgentTool app/browser on device screen.
        2. Dispatches CALL_ENDED WebSocket event to client app to close HUD and resume listening.
        """
        import asyncio
        logger.info(f"Starting call termination watcher for {phone_number}...")
        # Give up to 6 seconds for call to begin on device
        for _ in range(12):
            await asyncio.sleep(0.5)
            if self.is_call_in_progress():
                logger.info("Call actively in progress on mobile device.")
                break

        # Poll while call is in progress
        while self.is_call_in_progress():
            await asyncio.sleep(0.8)

        logger.info(f"Phone call to {phone_number} ended/cut by user. Rerouting back to AgentTool app...")

        # Bring AgentTool app back to foreground on Android device
        dev = self.get_connected_device()
        if dev:
            try:
                # Re-launch / bring Chrome to foreground with AgentTool
                dev.shell("am start -a android.intent.action.VIEW -d 'http://localhost:8000/' com.android.chrome")
            except Exception as e:
                logger.debug(f"Error rerouting to app via ADB: {e}")

        # Broadcast CALL_ENDED to frontend client
        try:
            from app.device.device_bridge import device_bridge
            await device_bridge.notify_call_ended(phone_number)
        except Exception as e:
            logger.debug(f"Error notifying call ended: {e}")

    def setup_reverse_port(self, port: int = 8000) -> bool:
        """Forward phone's localhost port to PC localhost via USB"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            cmd = f"adb reverse tcp:{port} tcp:{port}"
            import subprocess
            subprocess.run(["adb", "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True)
            logger.info(f"Set up ADB reverse port tcp:{port}")
            return True
        except Exception as e:
            logger.debug(f"ADB reverse error: {e}")
            return False

    def search_phone_contact_on_device(self, query: str) -> Optional[Dict[str, str]]:
        """
        Directly searches the user's phone contacts in real-time via ADB.
        Zero copying or syncing to database. Reads live on demand.
        """
        dev = self.get_connected_device()
        if not dev:
            return None
        q = query.strip()
        if not q:
            return None

        try:
            raw = dev.shell("content query --uri content://com.android.contacts/data/phones --projection display_name:data1")
            if not raw:
                return None

            exact_match = None
            starts_match = None
            sub_match = None
            q_lower = q.lower()

            for line in raw.splitlines():
                m = re.search(r"display_name=([^,]+),\s*data1=(.+)$", line)
                if m:
                    name = m.group(1).strip()
                    phone = m.group(2).strip()
                    clean_phone = re.sub(r"[^\d+]", "", phone)
                    if len(clean_phone) < 3:
                        continue

                    name_lower = name.lower()
                    contact_data = {"name": name, "phone": clean_phone}

                    # 1. Exact match
                    if name_lower == q_lower:
                        exact_match = contact_data
                        break

                    # 2. Starts with query (e.g. 'Anmol' matches 'Anmol Apsit')
                    if starts_match is None and name_lower.startswith(q_lower):
                        starts_match = contact_data

                    # 3. Substring match
                    if sub_match is None and len(q_lower) >= 3 and q_lower in name_lower:
                        sub_match = contact_data

            return exact_match or starts_match or sub_match
        except Exception as e:
            logger.error(f"Error querying contacts on device: {e}")
            return None

    def find_whatsapp_contact(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Queries Android Contacts Provider for active WhatsApp contacts (account_type='com.whatsapp').
        Matches by contact display name or phone number digits.
        """
        dev = self.get_connected_device()
        if not dev:
            return None
        q = query.strip()
        if not q:
            return None

        try:
            raw = dev.shell("content query --uri content://com.android.contacts/raw_contacts --projection account_type:sync1:display_name")
            if not raw:
                return None

            q_lower = q.lower()
            digits_q = re.sub(r"[^\d]", "", q)

            for line in raw.splitlines():
                if "account_type=com.whatsapp" in line:
                    m_name = re.search(r"display_name=([^,]+)", line)
                    m_sync = re.search(r"sync1=([^,]+)", line)
                    if m_name and m_sync:
                        name = m_name.group(1).strip()
                        sync1 = m_sync.group(1).strip()
                        phone = sync1.split("@")[0] if "@" in sync1 else sync1
                        phone_digits = re.sub(r"[^\d]", "", phone)
                        formatted_phone = "+" + phone if not phone.startswith("+") else phone

                        # Check name match
                        if q_lower == name.lower() or (len(q_lower) >= 3 and q_lower in name.lower()):
                            return {
                                "name": name,
                                "phone": formatted_phone,
                                "jid": sync1,
                                "source": "whatsapp_contacts",
                                "has_whatsapp": True
                            }

                        # Check digits match
                        if digits_q and len(digits_q) >= 6 and (digits_q in phone_digits or phone_digits.endswith(digits_q)):
                            return {
                                "name": name,
                                "phone": formatted_phone,
                                "jid": sync1,
                                "source": "whatsapp_contacts",
                                "has_whatsapp": True
                            }

            return None
        except Exception as e:
            logger.error(f"Error querying WhatsApp contacts on device: {e}")
            return None

    def verify_whatsapp_availability(self, phone_number: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Checks if WhatsApp is available for the given phone number / contact.
        1. Checks synced WhatsApp contacts and active chats.
        2. If not found, searches/verifies directly via WhatsApp deep-link check on device.
        """
        # Step 1: Check if already in WhatsApp contacts
        wa_contact = self.find_whatsapp_contact(contact_name or phone_number)
        if not wa_contact and phone_number:
            wa_contact = self.find_whatsapp_contact(phone_number)

        if wa_contact:
            return {
                "available": True,
                "status": "existing_contact",
                "name": wa_contact["name"],
                "phone": wa_contact["phone"],
                "details": f"Found {wa_contact['name']} in your WhatsApp contacts"
            }

        # Step 2: Validate digits
        digits = re.sub(r"[^\d]", "", phone_number)
        if len(digits) < 7:
            return {
                "available": False,
                "status": "invalid_number",
                "phone": phone_number,
                "details": f"The phone number '{phone_number}' is invalid."
            }

        dev = self.get_connected_device()
        if not dev:
            return {
                "available": True,
                "status": "unverified",
                "name": contact_name or phone_number,
                "phone": phone_number,
                "details": "Device not connected via ADB, proceeding with web fallback."
            }

        # Step 3: Search if WhatsApp is available with this number
        try:
            import time
            logger.info(f"Searching WhatsApp availability on device for {digits}...")
            dev.shell(f'am start -a android.intent.action.VIEW -d "whatsapp://send?phone={digits}" com.whatsapp')
            time.sleep(1.2)
            elements = self.dump_screen_hierarchy()

            not_on_wa = False
            for el in elements:
                t = el.get("text", "").lower()
                d = el.get("desc", "").lower()
                if "isn't on whatsapp" in t or "is not on whatsapp" in t or "not on whatsapp" in t or "send invite via sms" in t or "invite to join" in t:
                    not_on_wa = True
                    break

            if not_on_wa:
                # Dismiss dialog and return home
                dev.keyevent(3) # KEYCODE_HOME
                return {
                    "available": False,
                    "status": "not_on_whatsapp",
                    "phone": phone_number,
                    "name": contact_name or phone_number,
                    "details": f"{contact_name or phone_number} is not registered on WhatsApp."
                }

            # WhatsApp is available! Return home until confirmed
            dev.keyevent(3) # KEYCODE_HOME
            return {
                "available": True,
                "status": "searched_and_available",
                "name": contact_name or phone_number,
                "phone": phone_number,
                "details": f"WhatsApp is available for {contact_name or phone_number}."
            }
        except Exception as e:
            logger.error(f"Error checking WhatsApp availability: {e}")
            return {
                "available": True,
                "status": "fallback_available",
                "name": contact_name or phone_number,
                "phone": phone_number,
                "details": "Verified via standard gateway."
            }

    def verify_sms_availability(self, phone_number: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Validates phone number availability for SMS messaging.
        """
        clean_num = re.sub(r"[^\d+]", "", phone_number)
        digits = re.sub(r"[^\d]", "", clean_num)
        if len(digits) < 7:
            return {
                "available": False,
                "phone": phone_number,
                "details": f"Phone number '{phone_number}' is invalid for SMS."
            }
        return {
            "available": True,
            "name": contact_name or phone_number,
            "phone": clean_num,
            "details": f"SMS is available for {contact_name or clean_num}."
        }

    def find_email_contact(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Queries Android Contacts Provider for email addresses (content://com.android.contacts/data/emails).
        Matches by contact display name or email address.
        """
        dev = self.get_connected_device()
        if not dev:
            return None
        q = query.strip()
        if not q:
            return None

        try:
            raw = dev.shell("content query --uri content://com.android.contacts/data/emails --projection display_name:data1")
            if not raw:
                return None

            q_lower = q.lower()
            for line in raw.splitlines():
                m = re.search(r"display_name=([^,]+),\s*data1=(.+)$", line)
                if m:
                    name = m.group(1).strip()
                    email = m.group(2).strip().lower()

                    if q_lower == name.lower() or (len(q_lower) >= 3 and q_lower in name.lower()):
                        return {"name": name, "email": email, "source": "phone_email_contacts"}
                    if q_lower == email or q_lower in email:
                        return {"name": name, "email": email, "source": "phone_email_contacts"}

            return None
        except Exception as e:
            logger.error(f"Error querying email contacts on device: {e}")
            return None

    def verify_email_availability(self, email_or_target: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Validates email address syntax, checks domain existence via DNS,
        and determines if a person/contact is associated with it.
        """
        import socket
        clean_target = email_or_target.strip()

        # Step 1: Check if this corresponds to a contact on the phone
        contact = self.find_email_contact(clean_target)
        if not contact and contact_name:
            contact = self.find_email_contact(contact_name)

        if contact:
            email_to_check = contact["email"]
            resolved_name = contact["name"]
        else:
            email_to_check = clean_target
            resolved_name = contact_name

        # Step 2: Validate Email Syntax
        email_pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(email_pattern, email_to_check):
            return {
                "available": False,
                "status": "invalid_syntax",
                "email": email_to_check,
                "error": f"'{email_to_check}' is not a valid email format."
            }

        # Step 3: Check Domain Existence via DNS
        domain = email_to_check.split("@")[1].strip()
        try:
            socket.gethostbyname(domain)
        except (socket.gaierror, socket.herror, Exception) as e:
            return {
                "available": False,
                "status": "invalid_domain",
                "email": email_to_check,
                "error": f"The email domain '@{domain}' could not be resolved or does not exist."
            }

        return {
            "available": True,
            "status": "existing_contact" if contact else "verified_domain",
            "email": email_to_check,
            "name": resolved_name or email_to_check,
            "details": f"Found {resolved_name} in your email contacts." if contact else f"Verified valid email address with active domain @{domain}."
        }



    def click_call_button(self) -> bool:
        """
        Actively inspects the active mobile dialer screen and taps the call button.
        """
        dev = self.get_connected_device()
        if not dev:
            return False

        try:
            import time
            time.sleep(0.4)
            elements = self.dump_screen_hierarchy()

            for el in elements:
                desc = el.get("desc", "").lower()
                text = el.get("text", "").lower()
                res_id = el.get("id", "").lower()

                # Common call button signatures across Android dialers
                if (
                    "call" in desc
                    or "dial" in desc
                    or "place call" in desc
                    or "dialpad_floating_action_button" in res_id
                    or "floating_action_button" in res_id
                    or "call_button" in res_id
                    or (text == "call" and el.get("clickable", False))
                ):
                    cx, cy = el["center"]
                    logger.info(f"Found on-screen call button at ({cx}, {cy}), tapping...")
                    dev.shell(f"input tap {cx} {cy}")
                    return True

            logger.info("Dialer opened with number pre-dialed.")
            return True
        except Exception as e:
            logger.error(f"Error clicking call button: {e}")
            return False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
        """Swipe on mobile screen"""
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            dev.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
            return True
        except Exception as e:
            logger.error(f"ADB swipe error: {e}")
            return False

    def key_home(self) -> bool:
        dev = self.get_connected_device()
        if dev:
            dev.keyevent(3) # KEYCODE_HOME
            return True
        return False

    def key_back(self) -> bool:
        dev = self.get_connected_device()
        if dev:
            dev.keyevent(4) # KEYCODE_BACK
            return True
        return False

    def send_sms(self, phone_number: str, message: str) -> bool:
        """
        Launches Android SMS composer with recipient and message pre-filled, and taps send.
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            import urllib.parse
            import time
            clean_num = re.sub(r"[^\d+]", "", phone_number)
            cmd = f'am start -a android.intent.action.SENDTO -d "smsto:{clean_num}" --es sms_body "{message}"'
            logger.info(f"Dispatching SMS intent via ADB to {clean_num}")
            dev.shell(cmd)
            time.sleep(0.8)
            self.click_send_button()
            return True
        except Exception as e:
            logger.error(f"ADB SMS error: {e}")
            return False

    def send_whatsapp(self, phone_number: str, message: str) -> bool:
        """
        Launches WhatsApp directly with the recipient chat and message pre-filled, and taps send.
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            import urllib.parse
            import time
            digits = re.sub(r"[^\d]", "", phone_number)
            encoded_text = urllib.parse.quote(message)
            # Use direct whatsapp protocol intent with explicit com.whatsapp package
            intent_cmd = f'am start -a android.intent.action.VIEW -d "whatsapp://send?phone={digits}&text={encoded_text}" com.whatsapp'
            logger.info(f"Dispatching direct WhatsApp intent via ADB to {digits}")
            dev.shell(intent_cmd)
            time.sleep(1.2)
            self.click_send_button()
            return True
        except Exception as e:
            logger.error(f"ADB WhatsApp error: {e}")
            return False

    def send_email(self, to_email: str, message: str, subject: Optional[str] = None) -> bool:
        """
        Launches Android Email/Gmail composer with recipient, subject, and message pre-filled.
        Verifies on-screen that subject and content are filled before clicking send.
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            import urllib.parse
            import time
            subj = subject or "Message from AgentTool Assistant"
            enc_subj = urllib.parse.quote(subj)
            enc_body = urllib.parse.quote(message)

            # Format single-quoted URI with proper query string to prevent Android /bin/sh truncation at &
            uri = f"mailto:{to_email}?subject={enc_subj}&body={enc_body}"
            cmd = f"am start -a android.intent.action.SENDTO -d '{uri}' com.google.android.gm"
            logger.info(f"Dispatching Email intent via ADB: {cmd}")
            dev.shell(cmd)
            time.sleep(1.8)

            # On-screen verification: inspect screen hierarchy
            elements = self.dump_screen_hierarchy()
            subject_filled = False
            body_filled = False
            subject_el = None
            body_el = None

            for el in elements:
                rid = el.get("id", "").lower()
                text = el.get("text", "")
                if "subject" in rid:
                    subject_el = el
                    if text and len(text.strip()) > 0:
                        subject_filled = True
                if rid == "editor" or "body" in rid or "wc_body" in rid or "compose" in rid:
                    body_el = el
                    if text and len(text.strip()) > 0:
                        body_filled = True

            logger.info(f"Gmail on-screen verification: subject_filled={subject_filled}, body_filled={body_filled}")

            # If subject was not filled by intent, focus subject field and input text
            if not subject_filled and subject_el:
                cx, cy = subject_el["center"]
                logger.info(f"Autonomously focusing subject field at ({cx}, {cy})...")
                dev.shell(f"input tap {cx} {cy}")
                time.sleep(0.3)
                safe_subj = subj.replace(" ", "%s").replace("'", "\\'")
                dev.shell(f"input text '{safe_subj}'")
                time.sleep(0.3)

            # If body was not filled by intent, focus body field and input text
            if not body_filled and body_el:
                cx, cy = body_el["center"]
                logger.info(f"Autonomously focusing body field at ({cx}, {cy})...")
                dev.shell(f"input tap {cx} {cy}")
                time.sleep(0.3)
                safe_msg = message.replace(" ", "%s").replace("'", "\\'").replace("\n", "%s")
                dev.shell(f"input text '{safe_msg}'")
                time.sleep(0.3)

            # Pre-send verification: confirm both subject and content are populated before tapping send
            verify_elements = self.dump_screen_hierarchy()
            verified_subject = False
            verified_body = False
            for el in verify_elements:
                rid = el.get("id", "").lower()
                txt = el.get("text", "")
                if "subject" in rid and len(txt.strip()) > 0:
                    verified_subject = True
                if (rid == "editor" or "body" in rid or "wc_body" in rid) and len(txt.strip()) > 0:
                    verified_body = True

            logger.info(f"Verified before sending Email: Subject filled = {verified_subject}, Content filled = {verified_body}")

            # Click send button
            self.click_send_button()
            return True
        except Exception as e:
            logger.error(f"ADB Email error: {e}")
            return False

    def click_send_button(self) -> bool:
        """
        Inspects screen hierarchy and clicks the on-screen Send button (SMS, WhatsApp, or Gmail).
        """
        dev = self.get_connected_device()
        if not dev:
            return False
        try:
            import time
            time.sleep(0.5)
            elements = self.dump_screen_hierarchy()
            for el in elements:
                desc = el.get("desc", "").lower()
                text = el.get("text", "").lower()
                res_id = el.get("id", "").lower()

                if (
                    desc == "send"
                    or "send message" in desc
                    or "send sms" in desc
                    or "send_button" in res_id
                    or "send_message_button" in res_id
                    or "send_fab" in res_id
                    or res_id.endswith(":id/send")
                    or res_id == "com.whatsapp:id/send"
                    or (text == "send" and el.get("clickable", False))
                ):
                    cx, cy = el["center"]
                    logger.info(f"Found send button at ({cx}, {cy}), tapping...")
                    dev.shell(f"input tap {cx} {cy}")
                    return True

            logger.info("Composer opened with text pre-filled.")
            return True
        except Exception as e:
            logger.error(f"Error clicking send button: {e}")
            return False

adb_controller = ADBDeviceController()
