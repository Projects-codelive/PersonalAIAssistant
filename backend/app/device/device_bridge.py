import logging
import asyncio
from typing import Dict, Any, Optional, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class DeviceBridge:
    def __init__(self):
        # Connected mobile websockets: device_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # Latest telemetry / screen state from device
        self.device_states: Dict[str, Dict[str, Any]] = {}
        # Pending action queues for devices
        self.pending_actions: Dict[str, asyncio.Queue] = {}
        # Event loop that owns the device websockets (the server loop)
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        self._ws_loop = asyncio.get_running_loop()
        self.active_connections[device_id] = websocket
        if device_id not in self.pending_actions:
            self.pending_actions[device_id] = asyncio.Queue()
        self.device_states[device_id] = {
            "status": "online",
            "last_seen": asyncio.get_event_loop().time(),
            "current_screen": "home",
            "elements": []
        }
        logger.info(f"Mobile device connected: {device_id}")

    def disconnect(self, device_id: str):
        if device_id in self.active_connections:
            del self.active_connections[device_id]
        if device_id in self.device_states:
            self.device_states[device_id]["status"] = "offline"
        logger.info(f"Mobile device disconnected: {device_id}")

    async def broadcast_action(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send an action to all connected mobile devices (or the primary device)"""
        # Websockets can only be used on the loop that accepted them; the agent worker runs on its own loop
        ws_loop = self._ws_loop
        if ws_loop is not None and ws_loop is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self._broadcast(action_type, payload), ws_loop)
            return await asyncio.wrap_future(future)
        return await self._broadcast(action_type, payload)

    async def _broadcast(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = {
            "type": action_type,
            "data": payload
        }
        recipients = list(self.active_connections.keys())
        if not recipients:
            logger.warning("No mobile devices currently connected via WebSocket. Action queued for mobile web client.")
            return {"status": "dispatched_local", "recipients": 0, "event": event}

        delivered = 0
        for dev_id, ws in list(self.active_connections.items()):
            try:
                await ws.send_json(event)
                delivered += 1
            except Exception as e:
                logger.error(f"Failed to send event to device {dev_id}: {e}")

        return {"status": "sent" if delivered else "failed", "recipients": delivered, "event": event}

    async def trigger_call(self, phone_number: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """Trigger native phone dialer or call on connected mobile devices"""
        return await self.broadcast_action(
            action_type="TRIGGER_PHONE_CALL",
            payload={
                "phone_number": phone_number,
                "contact_name": contact_name or "Unknown",
                "mode": "direct_call"
            }
        )

    async def trigger_sms(self, phone_number: str, message: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """Trigger native SMS composer or send on connected mobile devices"""
        return await self.broadcast_action(
            action_type="TRIGGER_SMS",
            payload={
                "phone_number": phone_number,
                "message": message,
                "contact_name": contact_name or "Unknown"
            }
        )

    async def trigger_whatsapp(self, phone_number: str, message: str, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """Trigger WhatsApp messaging on connected mobile devices"""
        return await self.broadcast_action(
            action_type="TRIGGER_WHATSAPP",
            payload={
                "phone_number": phone_number,
                "message": message,
                "contact_name": contact_name or "Unknown"
            }
        )

    async def trigger_email(self, to_email: str, message: str, subject: Optional[str] = None, contact_name: Optional[str] = None) -> Dict[str, Any]:
        """Trigger Email sending on connected mobile devices or web client"""
        return await self.broadcast_action(
            action_type="TRIGGER_EMAIL",
            payload={
                "to_email": to_email,
                "message": message,
                "subject": subject or "Message from AgentTool Assistant",
                "contact_name": contact_name or to_email
            }
        )

    async def notify_call_ended(self, phone_number: Optional[str] = None) -> Dict[str, Any]:
        """Notify mobile device and web client that the phone call was terminated/cut"""
        logger.info(f"Broadcasting CALL_ENDED event for {phone_number}")
        return await self.broadcast_action(
            action_type="CALL_ENDED",
            payload={"phone_number": phone_number, "status": "ended"}
        )

    def update_screen_state(self, device_id: str, screen_data: Dict[str, Any]):
        """Update screen telemetry sent by mobile accessibility or visual tracker"""
        if device_id not in self.device_states:
            self.device_states[device_id] = {}
        self.device_states[device_id].update(screen_data)
        self.device_states[device_id]["last_seen"] = asyncio.get_event_loop().time()

    def get_screen_state(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """Get the latest screen state reported by mobile device"""
        if device_id and device_id in self.device_states:
            return self.device_states[device_id]
        # Return first available online device state
        for dev_id, state in self.device_states.items():
            if state.get("status") == "online":
                return state
        # Simulated default mobile screen state
        return {
            "status": "simulated",
            "screen_name": "HomeScreen",
            "app_in_foreground": "com.android.launcher",
            "interactive_elements": [
                {"id": "phone_app", "type": "AppIcon", "label": "Phone", "bounds": [50, 800, 150, 900]},
                {"id": "contacts_app", "type": "AppIcon", "label": "Contacts", "bounds": [170, 800, 270, 900]},
                {"id": "messages_app", "type": "AppIcon", "label": "Messages", "bounds": [290, 800, 390, 900]},
                {"id": "browser_app", "type": "AppIcon", "label": "Chrome", "bounds": [410, 800, 510, 900]}
            ]
        }

device_bridge = DeviceBridge()
