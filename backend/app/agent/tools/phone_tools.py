import re
import logging
from typing import Optional, Dict, Any
from langchain_core.tools import tool
from app.db.mongo import find_contact_by_name, log_call_event, get_contacts, add_contact
from app.device.device_bridge import device_bridge

logger = logging.getLogger(__name__)

def clean_phone_number(phone: str) -> str:
    """Normalize phone number to standard format"""
    # Keep digits and leading plus
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    return cleaned

@tool
async def make_phone_call(phone_number: str, contact_name: Optional[str] = None) -> str:
    """
    Initiate an automated phone call on the connected mobile device to a specific phone number or person.
    Use this whenever the user asks to 'call [name]', 'make a call to [number]', 'dial [number]', or 'phone [person]'.

    Args:
        phone_number: The phone number to call (e.g. '+15550192834', '911', or '555-019-2834').
        contact_name: Optional name of the person being called (e.g. 'Alice', 'Mom', 'Doctor').
    """
    from app.device.adb_controller import adb_controller

    # If phone_number is not a valid number (e.g. user passed a name), search phone directly
    digits = re.sub(r"[^\d]", "", phone_number)
    resolved_contact = None

    if len(digits) < 3:
        # Search device contact list in real time without copying to backend DB
        if adb_controller.is_device_connected:
            resolved_contact = adb_controller.search_phone_contact_on_device(phone_number)

        if not resolved_contact:
            from app.db.mongo import find_contact_by_name
            resolved_contact = await find_contact_by_name(phone_number)

        if resolved_contact:
            contact_name = resolved_contact["name"]
            phone_number = resolved_contact["phone"]
        else:
            return f"NOT_FOUND: Could not find contact '{phone_number}' in your contact list."
    elif contact_name:
        if adb_controller.is_device_connected:
            resolved_contact = adb_controller.search_phone_contact_on_device(contact_name)
            if resolved_contact and not phone_number:
                phone_number = resolved_contact["phone"]

    normalized_number = clean_phone_number(phone_number)
    display_name = contact_name or (resolved_contact["name"] if resolved_contact else normalized_number)

    if len(re.sub(r"[^\d]", "", normalized_number)) < 3:
        return f"NOT_FOUND: Could not find contact with a valid phone number for '{display_name}'."

    logger.info(f"Opening mobile dialer for: {display_name} ({normalized_number})")

    # Dispatch through exactly one path so a single confirmation never dials more than once:
    # 1. USB phone via ADB: open the dialer and tap Call
    if adb_controller.is_device_connected:
        if not adb_controller.open_mobile_dialer(normalized_number):
            return f"FAILED: Could not open the dialer on your phone to call {display_name} at {normalized_number}."
        call_tapped = adb_controller.click_call_button()
        logger.info(f"Mobile dialer opened via ADB for {normalized_number}; call button tapped: {call_tapped}")
        # Asynchronously monitor when user cuts the call to reroute back to app
        import asyncio
        asyncio.create_task(adb_controller.watch_call_termination(normalized_number))
        if call_tapped:
            return f"SUCCESS: Phone call placed to {display_name} at {normalized_number}."
        return (
            f"NOT_CONFIRMED: Opened the dialer for {display_name} at {normalized_number}, but could not "
            f"confirm the call started. Please tap Call on your phone."
        )

    # 2. Otherwise hand the call to the connected AgentTool app / web client over WebSocket
    bridge_result = await device_bridge.trigger_call(
        phone_number=normalized_number,
        contact_name=display_name
    )
    if bridge_result.get("recipients", 0) > 0:
        return f"DISPATCHED: Sent the call to {display_name} at {normalized_number} to your connected device to dial."
    return f"FAILED: No phone is connected (USB/ADB or the AgentTool app), so {display_name} at {normalized_number} was not called."

@tool
async def search_contacts(query: str) -> str:
    """
    Search saved contacts in the database by name or phone number.
    Use this when the user asks for someone's number, or before making a call if the phone number is not known.

    Args:
        query: The name, partial name, or number to search for (e.g. 'Alice', 'Mom').
    """
    results = await get_contacts(search=query)
    if not results:
        return f"No contacts found matching '{query}'. You can add them using add_contact."
    
    formatted = []
    for c in results:
        notes_str = f" ({c['notes']})" if c.get("notes") else ""
        formatted.append(f"• {c['name']}: {c['phone']}{notes_str}")
    
    return "Found contacts:\n" + "\n".join(formatted)

@tool
async def save_contact(name: str, phone_number: str, notes: Optional[str] = "") -> str:
    """
    Save a new contact into the MongoDB contacts database.
    Use this when the user says 'remember Alice's number is 555-0123' or 'add contact'.

    Args:
        name: Full name of the contact.
        phone_number: Phone number of the contact.
        notes: Optional note or relationship (e.g. 'Dentist', 'Colleague').
    """
    contact = await add_contact(name=name, phone=phone_number, notes=notes or "")
    return f"Successfully saved contact: {contact['name']} with number {contact['phone']}."
