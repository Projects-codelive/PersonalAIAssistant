import logging
from typing import Optional, Dict, Any
from langchain_core.tools import tool
from app.device.device_bridge import device_bridge
from app.device.adb_controller import adb_controller

logger = logging.getLogger(__name__)

@tool
async def read_screen_state() -> str:
    """
    Inspect the current mobile device screen.
    Returns the visible interactive elements, text labels, and buttons on the screen.
    Use this to see what is currently displayed on the user's phone before tapping or navigating.
    """
    # 1. If physical device connected via ADB -> Read REAL screen
    if adb_controller.is_device_connected:
        elements = adb_controller.dump_screen_hierarchy()
        if elements:
            dev_info = adb_controller.get_device_info()
            lines = [f"Physical Device Active: {dev_info.get('model', 'Android')} ({dev_info.get('device_id')})"]
            lines.append("Visible On-Screen Elements:")
            for el in elements[:20]: # show top 20 visible interactive items
                lines.append(f"- [{el['class']}] \"{el['label']}\" at center ({el['center'][0]}, {el['center'][1]}) [clickable={el['clickable']}]")
            return "\n".join(lines)

    # 2. WebSocket Mobile Device Bridge or Simulator
    state = device_bridge.get_screen_state()
    elements = state.get("interactive_elements", [])
    
    if not elements:
        return f"Current screen ({state.get('screen_name', 'Active')}) has no interactive elements detected."
    
    lines = [f"Screen: {state.get('screen_name', 'Active')}, Foreground: {state.get('app_in_foreground', 'App')}"]
    lines.append("Visible elements:")
    for el in elements:
        bounds_str = f" at {el['bounds']}" if 'bounds' in el else ""
        lines.append(f"- [{el.get('type', 'Element')}] '{el.get('label', el.get('id', 'item'))}' (id: {el.get('id', 'N/A')}){bounds_str}")
    
    return "\n".join(lines)

@tool
async def tap_element(label_or_id: str) -> str:
    """
    Simulate a tap or click on an element on the mobile device screen by its label or ID.
    
    Args:
        label_or_id: The text label, button name, or ID of the element to tap (e.g. 'Phone', 'Call', 'Anmol', 'Keypad').
    """
    logger.info(f"Dispatched tap on: {label_or_id}")

    # 1. Physical Device via ADB
    if adb_controller.is_device_connected:
        success = adb_controller.click_element_by_text(label_or_id)
        if success:
            return f"Successfully clicked '{label_or_id}' on physical mobile phone screen via ADB."
        return f"Element '{label_or_id}' not found on active mobile screen."

    # 2. WebSocket Device Bridge
    res = await device_bridge.broadcast_action(
        action_type="SCREEN_TAP",
        payload={"target": label_or_id}
    )
    return f"Tapped element '{label_or_id}' on mobile screen. Result: {res.get('status')}"

@tool
async def open_app(app_name: str) -> str:
    """
    Open an application on the mobile device (e.g. 'Phone', 'Contacts', 'Settings', 'Messages', 'Chrome').
    
    Args:
        app_name: The name or package identifier of the app to launch.
    """
    logger.info(f"Launching app on device: {app_name}")

    # 1. Physical Device via ADB
    if adb_controller.is_device_connected:
        adb_controller.open_app(app_name)
        return f"Launched '{app_name}' application directly on physical mobile device via ADB."

    # 2. WebSocket Device Bridge
    res = await device_bridge.broadcast_action(
        action_type="OPEN_APP",
        payload={"app_name": app_name}
    )
    return f"Launched '{app_name}' application on device."

@tool
async def go_home() -> str:
    """Navigate to the mobile home screen."""
    if adb_controller.is_device_connected:
        adb_controller.key_home()
        return "Navigated to physical mobile home screen via ADB."

    res = await device_bridge.broadcast_action(
        action_type="SYSTEM_KEY",
        payload={"key": "HOME"}
    )
    return "Navigated to mobile home screen."
