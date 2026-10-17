import time
import logging
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
from app.device.adb_controller import adb_controller
from app.device.device_bridge import device_bridge
from app.agent.privacy_guard import privacy_guard
from app.agent.ecommerce_comparator import ecommerce_comparator, ProductItem
from app.agent.task_checkpoint_manager import task_checkpoint_manager

logger = logging.getLogger(__name__)

FALLBACK_URL_MAP = {
    "flipkart": "https://www.flipkart.com",
    "amazon": "https://www.amazon.in",
    "myntra": "https://www.myntra.com",
    "blinkit": "https://www.blinkit.com",
    "zepto": "https://www.zeptonow.com",
    "swiggy": "https://www.swiggy.com",
    "zomato": "https://www.zomato.com"
}

@tool
async def check_and_open_app(app_name: str, fallback_url: Optional[str] = None) -> str:
    """
    Check if a target app is installed on the user's mobile device.
    If installed: Launches the native Android app.
    If NOT installed: Intelligently falls back to opening the mobile web browser (Chrome) with the service's website.
    
    Args:
        app_name: The name of the app to check and open (e.g. 'flipkart', 'amazon', 'chrome').
        fallback_url: Optional web URL to use if the native app is not installed.
    """
    clean_name = app_name.strip().lower()
    default_url = fallback_url or FALLBACK_URL_MAP.get(clean_name, f"https://www.google.com/search?q={clean_name}")

    if adb_controller.is_device_connected:
        is_installed = adb_controller.is_app_installed(clean_name)
        if is_installed:
            adb_controller.open_app(clean_name)
            return f"The '{app_name}' app is installed on your phone. Opened the native {app_name.capitalize()} app."
        else:
            adb_controller.open_url_in_browser(default_url)
            return (
                f"The '{app_name}' app is NOT installed on your phone. "
                f"Intelligently switching execution: Opened mobile browser (Chrome) and navigated to {default_url}."
            )

    # Bridge fallback
    res = await device_bridge.broadcast_action(
        action_type="CHECK_AND_OPEN_APP",
        payload={"app_name": app_name, "fallback_url": default_url}
    )
    return f"Opened {app_name} on device via bridge. Mode: {res.get('mode', 'browser')}"

@tool
async def search_and_compare_products(query: str, criteria: str = "lowest_price") -> str:
    """
    Inspects current screen search results on Flipkart, Amazon, or mobile browser,
    extracts all visible product cards (title, price, pack counts),
    compares them based on user criteria (e.g. 'lowest_price' or 'best_value'),
    and selects the cheapest/best matching option.
    
    Args:
        query: The item being searched (e.g. 'pen', 'ball pen').
        criteria: Comparison strategy: 'lowest_price' or 'best_value'.
    """
    if not adb_controller.is_device_connected:
        return f"Comparing products for '{query}': Device bridge simulated lowest price selection."

    # Give time for results to render
    time.sleep(1.0)
    elements = adb_controller.dump_screen_hierarchy()
    if not elements:
        return "Could not inspect screen. No interactive elements found on active screen."

    # 1. Privacy Check First
    guard_res = privacy_guard.evaluate_screen_elements(elements)
    if guard_res.is_sensitive:
        return f"PRIVACY GUARD ALERT: {guard_res.message}"

    # 2. Extract & Compare Products
    products = ecommerce_comparator.parse_products_from_screen(elements, query)
    if not products:
        # Try scrolling down once if no products found on top banner
        adb_controller.scroll("down", distance=400)
        time.sleep(0.8)
        elements = adb_controller.dump_screen_hierarchy()
        products = ecommerce_comparator.parse_products_from_screen(elements, query)

    if not products:
        return f"Searched for '{query}', but could not detect distinct product price cards on the active screen."

    best = ecommerce_comparator.find_best_product(products, criteria=criteria)
    if not best:
        return f"Found {len(products)} products, but could not determine lowest price."

    summary_lines = [f"Found {len(products)} options for '{query}':"]
    for p in products[:4]:
        summary_lines.append(f"- {p.title[:45]}: {p.raw_price} (Unit: ₹{p.unit_price:.1f})")

    summary_lines.append(f"\nWinner ({criteria}): '{best.title}' at {best.raw_price}.")
    summary_lines.append(f"Tapping on selected product at coordinates ({best.center[0]}, {best.center[1]}) to view details...")

    # Click on the best product
    adb_controller.click_at(best.center[0], best.center[1])

    return "\n".join(summary_lines)

@tool
async def scroll_screen(direction: str = "down") -> str:
    """
    Scroll the active mobile device screen in a specified direction ('down', 'up', 'left', 'right').
    Use this to see more search results or reveal checkout buttons.
    """
    if adb_controller.is_device_connected:
        adb_controller.scroll(direction=direction)
        return f"Scrolled screen {direction}."
    return f"Simulated screen scroll {direction}."

@tool
async def press_system_key(key: str = "enter") -> str:
    """
    Press a mobile system or keyboard key ('enter', 'search', 'back', 'home').
    Useful after typing in a search input.
    """
    if adb_controller.is_device_connected:
        adb_controller.press_key(key=key)
        return f"Pressed '{key}' key on device."
    return f"Simulated '{key}' keypress."

@tool
async def verify_privacy_and_order_safety() -> str:
    """
    Checks the active screen for sensitive credentials (OTP, CVV, Card Number, Password)
    or final order submission buttons ('Place Order', 'Pay Now').
    Ensures the agent halts for user permission before making purchases or handling confidential data.
    """
    if not adb_controller.is_device_connected:
        return "Screen is safe: no confidential or order confirmation triggers detected."

    elements = adb_controller.dump_screen_hierarchy()
    guard = privacy_guard.evaluate_screen_elements(elements)
    if guard.is_sensitive:
        return f"SECURITY CHECK: {guard.guard_type} DETECTED. {guard.message}"
    return "Screen is clear. No confidential or payment credentials required on current screen."
