import logging
import re
from typing import Optional, Set, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.facebook.com"
MAX_TEXT_LENGTH = 4096  # WhatsApp text message body limit


class RecipientNotAllowed(Exception):
    """Raised before any network call when a reply would go to a number outside the allowlist"""


def normalize_number(number: str) -> str:
    """'+91 78877-31349' -> '917887731349' (WhatsApp IDs are country code + digits)"""
    return re.sub(r"\D", "", number or "")


class WhatsAppCloudClient:
    """Minimal WhatsApp Cloud API client: send text, mark read, download voice notes."""

    def __init__(self):
        self.access_token = settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.base_url = f"{GRAPH_BASE_URL}/{settings.WHATSAPP_API_VERSION}"
        self.allowed_numbers: Set[str] = {
            normalize_number(n) for n in settings.WHATSAPP_ALLOWED_NUMBERS.split(",") if normalize_number(n)
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id and settings.WHATSAPP_APP_SECRET and self.allowed_numbers)

    def is_allowed(self, number: str) -> bool:
        return normalize_number(number) in self.allowed_numbers

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    async def _post_message(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{self.base_url}/{self.phone_number_id}/messages", headers=self._headers,
                                  json={"messaging_product": "whatsapp", **payload})
        if r.status_code >= 400:
            error = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            raise RuntimeError(f"WhatsApp API error {r.status_code}: {error.get('message', r.text[:200])}")
        return r.json()

    async def send_text(self, to: str, body: str) -> dict:
        """Send a text reply. Only ever to an allowlisted number."""
        to = normalize_number(to)
        if not self.is_allowed(to):
            raise RecipientNotAllowed(f"Refusing to message {to}: not in WHATSAPP_ALLOWED_NUMBERS")
        if len(body) > MAX_TEXT_LENGTH:
            body = body[: MAX_TEXT_LENGTH - 1] + "…"
        result = await self._post_message({
            "recipient_type": "individual", "to": to, "type": "text",
            "text": {"preview_url": False, "body": body},
        })
        logger.info(f"WhatsApp reply sent to {to} ({len(body)} chars)")
        return result

    async def send_buttons(self, to: str, body: str, buttons) -> dict:
        """Reply with up to 3 quick-reply buttons [(id, title)]. Only ever to an allowlisted number."""
        to = normalize_number(to)
        if not self.is_allowed(to):
            raise RecipientNotAllowed(f"Refusing to message {to}: not in WHATSAPP_ALLOWED_NUMBERS")
        result = await self._post_message({
            "recipient_type": "individual", "to": to, "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},  # interactive body limit
                "action": {"buttons": [{"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                                       for bid, title in buttons[:3]]},
            },
        })
        logger.info(f"WhatsApp button reply sent to {to}")
        return result

    async def mark_read(self, message_id: str, typing: bool = True) -> None:
        """Blue ticks + typing indicator while the agent works. Best effort."""
        payload = {"status": "read", "message_id": message_id}
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        try:
            await self._post_message(payload)
        except Exception as e:
            logger.debug(f"Could not mark WhatsApp message read: {e}")

    async def download_media(self, media_id: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch a voice note / media file: resolve its short-lived URL, then download with the token."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            meta = await client.get(f"{self.base_url}/{media_id}", headers=self._headers)
            if meta.status_code >= 400:
                logger.warning(f"WhatsApp media lookup failed ({meta.status_code})")
                return None, None
            info = meta.json()
            media = await client.get(info["url"], headers=self._headers)
            if media.status_code >= 400:
                logger.warning(f"WhatsApp media download failed ({media.status_code})")
                return None, None
            return media.content, info.get("mime_type")


whatsapp_client = WhatsAppCloudClient()
