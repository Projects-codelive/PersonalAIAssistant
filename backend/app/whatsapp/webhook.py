"""
WhatsApp → agent bridge.

Meta calls GET /webhooks/whatsapp once to verify the subscription, then POSTs every incoming message.
Each POST is authenticated by its X-Hub-Signature-256 HMAC (app secret), so this route is exempt from
the /api token check. Only allowlisted senders are processed; others are ignored without a reply.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.whatsapp.cloud_api import whatsapp_client, normalize_number

logger = logging.getLogger(__name__)
router = APIRouter()

WEBHOOK_PATH = "/webhooks/whatsapp"
UNSUPPORTED_REPLY = "I can follow text messages and voice notes. Try: \"open calculator\" or send a voice note."
ERROR_REPLY = "Sorry, something went wrong on my side while doing that. Please try again."

# Meta retries deliveries it thinks failed; remember recent message ids so a command never runs twice
_seen_message_ids: "OrderedDict[str, float]" = OrderedDict()
SEEN_TTL_SECONDS = 24 * 3600
SEEN_MAX = 2000
# Keep references to in-flight tasks so they aren't garbage-collected mid-run
_tasks: Set[asyncio.Task] = set()


def signature_is_valid(raw_body: bytes, signature_header: Optional[str]) -> bool:
    secret = settings.WHATSAPP_APP_SECRET
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1])


def _first_delivery(message_id: str) -> bool:
    now = time.monotonic()
    while _seen_message_ids and (now - next(iter(_seen_message_ids.values())) > SEEN_TTL_SECONDS
                                 or len(_seen_message_ids) > SEEN_MAX):
        _seen_message_ids.popitem(last=False)
    if message_id in _seen_message_ids:
        return False
    _seen_message_ids[message_id] = now
    return True


def schedule(coro) -> None:
    """Run a message handler in the background (kept referenced until it finishes)"""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get(WEBHOOK_PATH)
async def verify_subscription(request: Request):
    params = request.query_params
    token = params.get("hub.verify_token", "")
    if (params.get("hub.mode") == "subscribe" and settings.WHATSAPP_VERIFY_TOKEN
            and hmac.compare_digest(token, settings.WHATSAPP_VERIFY_TOKEN)):
        logger.info("WhatsApp webhook subscription verified")
        return PlainTextResponse(params.get("hub.challenge", ""))
    return Response(status_code=403)


@router.post(WEBHOOK_PATH)
async def receive_messages(request: Request):
    raw = await request.body()
    if not signature_is_valid(raw, request.headers.get("x-hub-signature-256")):
        logger.warning("Rejected WhatsApp webhook with missing/invalid signature")
        return Response(status_code=401)

    try:
        payload = json.loads(raw)
    except ValueError:
        return Response(status_code=400)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            # Only messages addressed to our business number (the WABA may have several numbers)
            if value.get("metadata", {}).get("phone_number_id") not in (None, settings.WHATSAPP_PHONE_NUMBER_ID):
                continue
            for message in value.get("messages", []):
                sender = normalize_number(message.get("from", ""))
                if not whatsapp_client.is_allowed(sender):
                    logger.warning(f"Ignored WhatsApp message from non-allowlisted number ending {sender[-4:]}")
                    continue
                if not _first_delivery(message.get("id", "")):
                    continue
                # Acknowledge Meta immediately; the agent may take a while to drive the phone
                schedule(handle_message(sender, message))

    return {"status": "ok"}


# Quick-reply button ids -> the words the agent's confirmation step understands
BUTTON_COMMANDS = {"confirm_yes": "yes", "confirm_no": "no"}
CONFIRM_BUTTONS = [("confirm_yes", "✅ Yes"), ("confirm_no", "❌ No")]
PROGRESS_AFTER_SECONDS = 8.0
PROGRESS_REPLY = "⏳ Working on it on your phone…"
HELP_COMMANDS = {"help", "menu", "commands", "start", "?", "madad"}
HELP_REPLY = (
    "🤖 *AgentTool*: I control your Android phone. Send text or a voice note:\n\n"
    "🧮 *open calculator*, then *25 times 4*\n"
    "📱 *open settings* · *open youtube*\n"
    "🎵 *play song kesariya*\n"
    "⏰ *set alarm for 7 am*\n"
    "🔎 *search wikipedia for Taj Mahal*\n"
    "📞 *call <number>* · 💬 *send sms to <number> saying …* (I ask before doing it)\n"
    "🛒 *flipkart se sabse sasta pen dhundho*\n"
    "🗣️ Hindi / Hinglish work too: *2 aur 3 ko jodo*"
)
COMMUNICATION_TOOLS = {"make_phone_call", "send_sms", "send_whatsapp_message", "send_email"}


async def extract_command(message: Dict[str, Any]) -> Optional[str]:
    """Text of a text message, the meaning of a tapped button, or the transcript of a voice note"""
    kind = message.get("type")
    if kind == "text":
        return (message.get("text", {}).get("body") or "").strip() or None
    if kind == "interactive":
        reply = message.get("interactive", {})
        choice = reply.get("button_reply") or reply.get("list_reply") or {}
        return BUTTON_COMMANDS.get(choice.get("id"), choice.get("title"))
    if kind == "button":
        return message.get("button", {}).get("text")
    if kind == "audio":
        from app.voice.elevenlabs_service import voice_service
        audio, mime = await whatsapp_client.download_media(message.get("audio", {}).get("id", ""))
        if not audio:
            return None
        ext = "ogg" if "ogg" in (mime or "") else "mp3"
        transcript = await voice_service.speech_to_text(audio, filename=f"voice_note.{ext}", content_type=mime)
        return (transcript or "").strip() or None
    return None


def _allowed_target_numbers() -> Set[str]:
    raw = settings.WHATSAPP_ALLOWED_TARGETS or settings.WHATSAPP_ALLOWED_NUMBERS
    return {normalize_number(n)[-10:] for n in raw.split(",") if len(normalize_number(n)) >= 10}


def target_refusal(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """
    Policy for actions triggered from WhatsApp: calls/SMS/WhatsApp only to allowlisted numbers, email
    only to allowlisted addresses. Returns a refusal message, or None if allowed.
    """
    if tool_name not in COMMUNICATION_TOOLS:
        return None
    if tool_name == "send_email":
        allowed_emails = {e.strip().lower() for e in settings.WHATSAPP_ALLOWED_EMAILS.split(",") if e.strip()}
        if "*" in allowed_emails or (args.get("to_email") or "").strip().lower() in allowed_emails:
            return None
        return "For safety, email can't be sent from WhatsApp to that address."
    # "*" = the allowlisted sender may call/message anyone (every action still needs their Yes)
    if settings.WHATSAPP_ALLOWED_TARGETS.strip() == "*":
        return None
    digits = normalize_number(args.get("phone_number", ""))
    if len(digits) >= 10 and digits[-10:] in _allowed_target_numbers():
        return None
    allowed = ", ".join(f"+{n}" if len(n) > 10 else n for n in sorted(
        {normalize_number(x) for x in (settings.WHATSAPP_ALLOWED_TARGETS or settings.WHATSAPP_ALLOWED_NUMBERS).split(",") if x.strip()}))
    return f"For safety, from WhatsApp I can only call or message {allowed}."


async def run_guarded_command(command: str, session_id: str) -> Dict[str, Any]:
    """
    Run one WhatsApp command on the agent worker with the target policy active.
    A confirmation for a disallowed contact is discarded before it is ever offered; the tool-level
    guard is a second line of defence for any path that executes without asking.
    """
    from app.agent.gemma_agent import agent

    agent.action_guard = target_refusal
    try:
        result = await agent.chat(command, session_id=session_id)
    finally:
        agent.action_guard = None

    pending = result.get("pending_action") if result.get("confirmation_required") else None
    if pending:
        refusal = target_refusal(pending.get("action", ""), pending.get("args", {}))
        if refusal:
            agent.pending_confirmations.pop(session_id, None)
            return {"reply": f"🔒 {refusal}", "tool_calls": [], "confirmation_required": False}
    for call in result.get("tool_calls", []):
        if str(call.get("result", "")).startswith("BLOCKED:"):
            return {**result, "reply": "🔒 " + str(call["result"]).split(":", 1)[1].strip()}
    return result


async def handle_message(sender: str, message: Dict[str, Any]) -> None:
    from app.agent.agent_worker import agent_worker

    await whatsapp_client.mark_read(message.get("id", ""))
    buttons = None
    try:
        command = await extract_command(message)
        if not command:
            reply = (UNSUPPORTED_REPLY if message.get("type") != "audio"
                     else "I couldn't make out that voice note. Could you send it again or type it?")
        elif command.lower().strip(" !.") in HELP_COMMANDS:
            reply = HELP_REPLY
        else:
            logger.info(f"WhatsApp command from {sender[-4:]}: type={message.get('type')}, {len(command)} chars")
            task = asyncio.ensure_future(agent_worker.run(run_guarded_command, command, f"whatsapp_{sender}"))
            done, _ = await asyncio.wait({task}, timeout=PROGRESS_AFTER_SECONDS)
            if not done:
                # Long phone task: tell the user it's in progress (the typing indicator only lasts ~25s)
                try:
                    await whatsapp_client.send_text(sender, PROGRESS_REPLY)
                except Exception as e:
                    logger.debug(f"Could not send progress message: {e}")
            result = await task
            reply = result.get("reply") or "Done."
            if result.get("confirmation_required"):
                buttons = CONFIRM_BUTTONS
            if message.get("type") == "audio":
                reply = f"🎙️ \"{command}\"\n\n{reply}"
    except Exception as e:
        logger.error(f"WhatsApp command failed: {e}", exc_info=True)
        reply = ERROR_REPLY

    try:
        if buttons:
            try:
                await whatsapp_client.send_buttons(sender, reply, buttons)
                return
            except Exception as e:
                logger.warning(f"Button reply failed ({e}); falling back to text")
        await whatsapp_client.send_text(sender, reply)
    except Exception as e:
        logger.error(f"Could not send WhatsApp reply: {e}")
