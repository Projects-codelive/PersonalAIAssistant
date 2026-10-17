import re
import logging
from typing import Optional, Dict, Any, Awaitable, Callable
from langchain_core.tools import tool
from app.db.mongo import find_contact_by_name, log_call_event
from app.device.device_bridge import device_bridge
from app.agent.tools.phone_tools import clean_phone_number

logger = logging.getLogger(__name__)


async def dispatch_message(
    channel: str,
    target: str,
    message: str,
    adb_send: Callable[[], bool],
    bridge_send: Callable[[], Awaitable[Dict[str, Any]]],
    log_kind: str,
    log_target: str,
) -> str:
    """
    Send through exactly one path and report what really happened:
    ADB on a USB phone if connected, otherwise the WebSocket device app / web client.

    Result prefixes: SUCCESS (Send tapped on the phone), NOT_CONFIRMED (composer opened, Send not
    confirmed), DISPATCHED (handed to a connected device app to complete), FAILED (nothing reached a phone).
    """
    from app.device.adb_controller import adb_controller

    if adb_controller.is_device_connected:
        if adb_send():
            status, result = "sent", f"SUCCESS: {channel} sent to {target}: \"{message}\""
        else:
            status = "not_confirmed"
            result = (f"NOT_CONFIRMED: Opened the {channel} composer for {target} on your phone, but could not "
                      f"confirm it was sent. Please check the phone and tap Send if needed.")
    else:
        bridge = await bridge_send()
        if bridge.get("recipients", 0) > 0:
            status = "dispatched"
            result = f"DISPATCHED: Handed the {channel} for {target} to your connected device to complete: \"{message}\""
        else:
            status = "failed"
            result = f"FAILED: No phone is connected (USB/ADB or the AgentTool app), so the {channel} to {target} was not sent."

    try:
        await log_call_event(
            phone_number=log_target,
            contact_name=target,
            status=f"{log_kind}_{status}: {message[:30]}",
            initiated_by=f"agent_{log_kind}"
        )
    except Exception as e:
        logger.debug(f"Error logging {channel} event: {e}")

    logger.info(result)
    return result


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

    return await dispatch_message(
        channel="SMS",
        target=f"{display_name} ({normalized_number})",
        message=message,
        adb_send=lambda: adb_controller.send_sms(normalized_number, message),
        bridge_send=lambda: device_bridge.trigger_sms(phone_number=normalized_number, message=message, contact_name=display_name),
        log_kind="sms",
        log_target=normalized_number,
    )

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

    return await dispatch_message(
        channel="WhatsApp message",
        target=f"{display_name} ({normalized_number})",
        message=message,
        adb_send=lambda: adb_controller.send_whatsapp(normalized_number, message),
        bridge_send=lambda: device_bridge.trigger_whatsapp(phone_number=normalized_number, message=message, contact_name=display_name),
        log_kind="whatsapp",
        log_target=normalized_number,
    )

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

    return await dispatch_message(
        channel="Email",
        target=f"{display_name} ({to_email})",
        message=message,
        adb_send=lambda: adb_controller.send_email(to_email=to_email, message=message, subject=subj),
        bridge_send=lambda: device_bridge.trigger_email(to_email=to_email, message=message, subject=subj, contact_name=display_name),
        log_kind="email",
        log_target=to_email,
    )
