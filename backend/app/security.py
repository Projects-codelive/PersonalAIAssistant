import hmac
import logging
from urllib.parse import parse_qs, urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger("agenttool.security")

TOKEN_HEADER = "x-agenttool-token"
PROTECTED_PREFIXES = ("/api/", "/ws/")
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
LOOPBACK_HOSTNAMES = {"127.0.0.1", "::1", "[::1]", "localhost"}


def _hostname(host_header: str) -> str:
    """'localhost:8765' -> 'localhost', '[::1]:8765' -> '[::1]'"""
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


def is_request_authorized(scope: Scope) -> bool:
    """
    Authorize an /api or /ws request.

    - A valid token (header or ?token=) always works: Wi-Fi phones, LAN browsers.
    - Without a token, only requests from this machine are trusted (local browser, USB phone via
      `adb reverse`), and only if they are not cross-site: the Host must be a loopback name (blocks DNS
      rebinding) and any Origin must match the Host (blocks other web pages driving the phone).
    """
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    supplied = headers.get(TOKEN_HEADER) or (query.get("token") or [""])[0]
    if supplied and hmac.compare_digest(supplied, settings.API_TOKEN):
        return True

    client_host = (scope.get("client") or ("", 0))[0]
    if client_host not in LOOPBACK_CLIENTS:
        return False
    host = headers.get("host", "")
    if _hostname(host) not in LOOPBACK_HOSTNAMES:
        return False
    origin = headers.get("origin")
    if origin and origin != "null" and urlsplit(origin).netloc != host:
        return False
    return True


class TokenAuthMiddleware:
    """ASGI middleware enforcing is_request_authorized() on HTTP and WebSocket API routes"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith(PROTECTED_PREFIXES):
            return await self.app(scope, receive, send)
        if is_request_authorized(scope):
            return await self.app(scope, receive, send)

        client_host = (scope.get("client") or ("?", 0))[0]
        logger.warning(f"Rejected unauthorized {scope['type']} request to {scope['path']} from {client_host}")
        if scope["type"] == "websocket":
            # Close the handshake with policy-violation code before accepting
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse({"detail": "Unauthorized: missing or invalid AgentTool token"}, status_code=401)
        await response(scope, receive, send)
