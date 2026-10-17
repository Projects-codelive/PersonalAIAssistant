import re
import logging
from typing import Optional, Dict, Any
from langchain_core.tools import tool
from app.db.mongo import find_contact_by_name, log_call_event
from app.device.device_bridge import device_bridge
from app.agent.tools.phone_tools import clean_phone_number

logger = logging.getLogger(__name__)

@tool
async def send_sms(phone_number: str, message: str, contact_name: Optional[str] = None) -> str:
    """
    Send an SMS/Text message to a contact or phone number.
    Only execute AFTER user confirmation.

    Args:
        phone_number: The recipient's phone number.
        message: The text body to send.
        contact_name: Optional name of the recipient (e.g. 'Alice', 'Mom').
    """
    from app.device.adb_controller import adb_controller

    normalized_number = clean_phone_number(phone_number)
    display_name = contact_name or normalized_number

    logger.info(f"Sending SMS to {display_name} ({normalized_number}): {message}")

    # 1. Dispatch on connected Android phone via ADB
    if adb_controller.is_device_connected:
        adb_controller.send_sms(normalized_number, message)

    # 2. Dispatch via WebSocket to mobile device / web client
    await device_bridge.trigger_sms(
        phone_number=normalized_number,
        message=message,
        contact_name=display_name
    )

    # 3. Log event
    try:
        await log_call_event(
            phone_number=normalized_number,
            contact_name=display_name,
            status=f"sms_sent: {message[:30]}",
            initiated_by="agent_sms"
        )
    except Exception as e:
        logger.debug(f"Error logging SMS event: {e}")

    return f"SUCCESS: SMS sent to {display_name} ({normalized_number}): \"{message}\""

@tool
async def send_whatsapp_message(phone_number: str, message: str, contact_name: Optional[str] = None) -> str:
    """
    Send a WhatsApp message to a contact or phone number.
    Only execute AFTER user confirmation.

    Args:
        phone_number: The recipient's phone number.
        message: The message text to send.
        contact_name: Optional name of the recipient (e.g. 'Alice', 'Mom').
    """
    from app.device.adb_controller import adb_controller

    normalized_number = clean_phone_number(phone_number)
    display_name = contact_name or normalized_number

    logger.info(f"Sending WhatsApp message to {display_name} ({normalized_number}): {message}")

    # 1. Dispatch on connected Android phone via ADB
    if adb_controller.is_device_connected:
        adb_controller.send_whatsapp(normalized_number, message)

    # 2. Dispatch via WebSocket to mobile device / web client
    await device_bridge.trigger_whatsapp(
        phone_number=normalized_number,
        message=message,
        contact_name=display_name
    )

    # 3. Log event
    try:
        await log_call_event(
            phone_number=normalized_number,
            contact_name=display_name,
            status=f"whatsapp_sent: {message[:30]}",
            initiated_by="agent_whatsapp"
        )
    except Exception as e:
        logger.debug(f"Error logging WhatsApp event: {e}")

    return f"SUCCESS: WhatsApp message sent to {display_name} ({normalized_number}): \"{message}\""

@tool
async def send_email(to_email: str, message: str, subject: Optional[str] = "Message from AgentTool Assistant", contact_name: Optional[str] = None) -> str:
    """
    Send an Email message to a recipient email address.
    Only execute AFTER user confirmation.

    Args:
        to_email: The recipient's validated email address.
        message: The email body content to send.
        subject: Optional email subject line.
        contact_name: Optional name of the recipient.
    """
    from app.device.adb_controller import adb_controller

    display_name = contact_name or to_email
    subj = subject or "Message from AgentTool Assistant"

    logger.info(f"Sending Email to {display_name} <{to_email}> with subject '{subj}': {message}")

    # 1. Dispatch on connected Android phone via ADB (Gmail / Email client)
    if adb_controller.is_device_connected:
        adb_controller.send_email(to_email=to_email, message=message, subject=subj)

    # 2. Dispatch via WebSocket to mobile device / web client
    await device_bridge.trigger_email(
        to_email=to_email,
        message=message,
        subject=subj,
        contact_name=display_name
    )

    # 3. Log event
    try:
        await log_call_event(
            phone_number=to_email,
            contact_name=display_name,
            status=f"email_sent: {message[:30]}",
            initiated_by="agent_email"
        )
    except Exception as e:
        logger.debug(f"Error logging email event: {e}")

    return f"SUCCESS: Email sent to {display_name} ({to_email}) with subject \"{subj}\": \"{message}\""

