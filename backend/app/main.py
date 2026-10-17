import os
import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.db.mongo import (
    connect_to_mongo, close_mongo_connection, db,
    get_contacts, add_contact, get_call_logs, log_call_event
)
from app.agent.gemma_agent import agent
from app.agent.agent_worker import agent_worker
from app.voice.elevenlabs_service import voice_service
from app.device.device_bridge import device_bridge
from app.security import TokenAuthMiddleware
from app.whatsapp.webhook import router as whatsapp_router
from app.whatsapp.cloud_api import whatsapp_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agenttool")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Connect to MongoDB
    await connect_to_mongo()
    # Auto-configure USB reverse port forwarding for connected Android phone
    try:
        from app.device.adb_controller import adb_controller
        adb_controller.setup_reverse_port(settings.PORT)
    except Exception as e:
        logger.debug(f"ADB reverse setup: {e}")
    # Load Gemma in the background so startup isn't delayed and the first message is fast
    from app.agent.gemma_agent import warm_up_model
    warm_up_task = asyncio.create_task(warm_up_model())
    yield
    warm_up_task.cancel()
    # Shutdown: Close DB
    await close_mongo_connection()

app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous Mobile Agent powered by Gemma, ElevenLabs Voice, LangChain, and MongoDB",
    version="1.0.0",
    lifespan=lifespan
)

# Token auth on /api and /ws (localhost + USB phone trusted, LAN needs the token)
app.add_middleware(TokenAuthMiddleware)

# CORS: the bundled client is same-origin, so only explicitly configured extra origins are allowed.
# Added after auth so it is the outer layer and answers preflights for those origins.
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-AgentTool-Token"],
    )

# Request Models
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    voice_response: Optional[bool] = True

class CallTriggerRequest(BaseModel):
    phone_number: str
    contact_name: Optional[str] = None

class AddContactRequest(BaseModel):
    name: str
    phone: str
    notes: Optional[str] = ""

class SearchRequest(BaseModel):
    query: str
    source: Optional[str] = "auto"  # auto, wikipedia, duckduckgo, arxiv, pubmed

# API Endpoints

@app.get("/api/status")
async def get_system_status():
    """Health check and configuration status"""
    return {
        "status": "online",
        "mongodb": {
            "connected": db.is_connected,
            "database": settings.MONGODB_DB_NAME
        },
        "gemma": {
            "provider": settings.GEMMA_PROVIDER,
            "model": settings.GEMMA_MODEL_NAME,
            "active": agent.llm is not None
        },
        "elevenlabs": {
            "configured": voice_service.is_configured,
            "voice_id": settings.ELEVENLABS_VOICE_ID
        },
        "whatsapp": {
            "configured": whatsapp_client.is_configured,
            "allowed_numbers": len(whatsapp_client.allowed_numbers)
        },
        "connected_devices": len(device_bridge.active_connections)
    }

@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest, request: Request):
    """
    Main conversational agent endpoint.
    Processes user instruction (e.g. 'Call Alice' or 'Make a call to 911'),
    invokes LangChain Gemma agent with tool calling, and generates ElevenLabs voice response.
    """
    user_msg = payload.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not payload.session_id or payload.session_id == "default":
        # Never let different clients share one session: a "yes" from one would confirm another's action
        payload.session_id = f"client_{request.client.host if request.client else 'unknown'}"

    logger.info(f"Received chat message: '{user_msg}' (Session: {payload.session_id})")

    # 1. Run Gemma Agent safely
    try:
        # Runs on the agent worker thread: its blocking ADB/sleep calls must not freeze the server loop
        result = await agent_worker.run(agent.chat, user_msg, session_id=payload.session_id)
        reply_text = result["reply"]
        tool_calls = result.get("tool_calls", [])
    except Exception as e:
        logger.error(f"Error processing chat in agent: {e}", exc_info=True)
        return {
            "reply": f"I ran into an issue: {str(e)}. Please try again or rephrase your request.",
            "tool_calls": [],
            "call_action": None,
            "message_action": None,
            "confirmation_required": False,
            "pending_action": None,
            "audio_base64": None,
            "use_client_tts": False
        }

    # 2. Check if a call or message was initiated in the tool calls
    call_action = None
    message_action = None
    # These describe what the tool already did on the phone (for the call HUD / status line).
    # The device itself was driven exactly once by the tool, so clients must not re-dial or re-send.
    for tc in tool_calls:
        tool_name = tc.get("tool")
        res_str = str(tc.get("result", ""))
        outcome = res_str.split(":", 1)[0] if ":" in res_str else ""
        if outcome not in ("SUCCESS", "NOT_CONFIRMED", "DISPATCHED"):
            continue
        if tool_name == "make_phone_call":
            import re
            num_match = re.search(r"at\s+([+\d\s-]{4,})", res_str)
            resolved_num = num_match.group(1).strip() if num_match else tc["args"].get("phone_number")
            call_action = {
                "type": "CALL",
                "status": outcome,
                "phone_number": resolved_num,
                "contact_name": tc["args"].get("contact_name")
            }
        elif tool_name in ["send_whatsapp_message", "send_sms"]:
            message_action = {
                "type": "WHATSAPP" if "whatsapp" in tool_name else "SMS",
                "status": outcome,
                "phone_number": tc["args"].get("phone_number"),
                "contact_name": tc["args"].get("contact_name"),
                "message": tc["args"].get("message")
            }
        elif tool_name == "send_email":
            message_action = {
                "type": "EMAIL",
                "status": outcome,
                "to_email": tc["args"].get("to_email"),
                "contact_name": tc["args"].get("contact_name"),
                "subject": tc["args"].get("subject"),
                "message": tc["args"].get("message")
            }

    # 3. Generate ElevenLabs TTS speech audio if enabled
    audio_base64 = None
    if payload.voice_response and voice_service.is_configured:
        try:
            audio_bytes, mime = await voice_service.text_to_speech(reply_text)
            if audio_bytes:
                audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as e:
            logger.error(f"Error generating ElevenLabs audio: {e}")

    return {
        "reply": reply_text,
        "tool_calls": tool_calls,
        "call_action": call_action,
        "message_action": message_action,
        "confirmation_required": result.get("confirmation_required", False),
        "pending_action": result.get("pending_action", None),
        "audio_base64": audio_base64,
        "use_client_tts": not bool(audio_base64)
    }

@app.post("/api/voice/stt")
async def speech_to_text_endpoint(file: UploadFile = File(...)):
    """
    Transcribe recorded mobile voice audio using ElevenLabs Scribe API.
    """
    audio_bytes = await file.read()
    filename = file.filename or "audio.webm"
    content_type = file.content_type
    text = await voice_service.speech_to_text(audio_bytes, filename=filename, content_type=content_type)
    if not text:
        return {"text": "", "status": "no_transcription_available"}
    return {"text": text, "status": "success"}

@app.post("/api/voice/tts")
async def text_to_speech_endpoint(text: str = Form(...)):
    """
    Direct TTS stream endpoint using ElevenLabs.
    """
    audio_bytes, mime = await voice_service.text_to_speech(text)
    if not audio_bytes:
        raise HTTPException(status_code=503, detail="ElevenLabs TTS not configured or unavailable")
    return Response(content=audio_bytes, media_type=mime)

@app.post("/api/search")
async def search_endpoint(payload: SearchRequest):
    """
    Direct knowledge and web search tool endpoint.
    Calls Wikipedia, DuckDuckGo, Arxiv, or PubMed tools, logs the site and info taken,
    and reframes the response using local Gemma LLM.
    """
    from app.agent.tools.search_tools import search_knowledge_service
    q = payload.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    
    src = None if payload.source in ["auto", "", None] else payload.source
    res = await agent_worker.run(search_knowledge_service.search_and_reframe, user_msg=q, query=q, explicit_source=src)
    return res

@app.get("/api/contacts")
async def list_contacts_endpoint(search: Optional[str] = Query(None)):
    """Retrieve contacts list from MongoDB"""
    return await get_contacts(search)

@app.post("/api/contacts")
async def add_contact_endpoint(payload: AddContactRequest):
    """Save new contact into MongoDB"""
    res = await add_contact(payload.name, payload.phone, payload.notes)
    return {"status": "success", "contact": res}

@app.get("/api/calls")
async def list_calls_endpoint(limit: int = 50):
    """Retrieve recent call logs from MongoDB"""
    return await get_call_logs(limit)

@app.post("/api/device/call")
async def trigger_call_endpoint(payload: CallTriggerRequest):
    """Directly trigger phone call on connected mobile devices using mobile dialer"""
    import re
    target = payload.phone_number.strip()
    digits = re.sub(r"[^\d]", "", target)

    if len(digits) < 3:
        from app.db.mongo import find_contact_by_name
        resolved_contact = await find_contact_by_name(target)
        if resolved_contact:
            target = resolved_contact["phone"]
            payload.contact_name = resolved_contact["name"]
        else:
            return {"status": "error", "message": f"Could not find contact '{target}' in contact list."}
    elif payload.contact_name:
        from app.db.mongo import find_contact_by_name
        resolved_contact = await find_contact_by_name(payload.contact_name)
        if resolved_contact and not target:
            target = resolved_contact["phone"]

    # Open mobile dialer on ADB physical device if connected
    from app.device.adb_controller import adb_controller
    # One dispatch path only: the USB phone via ADB, otherwise the WebSocket device app
    if await asyncio.to_thread(lambda: adb_controller.is_device_connected):
        opened = await asyncio.to_thread(adb_controller.open_mobile_dialer, target)
        res = {"status": "dialer_opened" if opened else "failed", "via": "adb"}
    else:
        res = await device_bridge.trigger_call(target, payload.contact_name)
    if res.get("status") == "failed" or res.get("recipients") == 0:
        return {"status": "error", "message": "No phone is connected to open the dialer.", "phone_number": target}
    await log_call_event(
        phone_number=target,
        contact_name=payload.contact_name or target,
        status="initiated",
        initiated_by="mobile_dialer"
    )
    return {"status": "success", "result": res, "phone_number": target}

class ADBConnectRequest(BaseModel):
    ip_port: Optional[str] = None

class ScreenTapRequest(BaseModel):
    target: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None

@app.get("/api/device/screen")
async def get_screen_state_endpoint():
    """Get the active mobile screen inspection tree"""
    return device_bridge.get_screen_state()

@app.get("/api/device/screen/live")
async def get_live_screen_endpoint():
    """
    Get live screen capture and extracted UI tree.
    If physical Android phone is connected via ADB, captures the REAL physical phone screen!
    """
    from app.device.adb_controller import adb_controller
    if await asyncio.to_thread(lambda: adb_controller.is_device_connected):
        elements, screenshot_b64, dev_info = await asyncio.to_thread(
            lambda: (adb_controller.dump_screen_hierarchy(), adb_controller.take_screenshot_base64(), adb_controller.get_device_info())
        )
        return {
            "mode": "adb_live",
            "device": dev_info,
            "has_screenshot": bool(screenshot_b64),
            "screenshot_base64": screenshot_b64,
            "interactive_elements": elements
        }
    # Fallback to simulated / WebSocket state
    state = device_bridge.get_screen_state()
    return {
        "mode": "web_simulator",
        "device": {"connected": False, "mode": "simulator"},
        "has_screenshot": False,
        "screenshot_base64": None,
        "interactive_elements": state.get("interactive_elements", [])
    }

@app.post("/api/device/connect-adb")
async def connect_adb_endpoint(payload: ADBConnectRequest):
    """Connect to Android phone over Wi-Fi (e.g. 192.168.1.6:5555) or detect USB"""
    from app.device.adb_controller import adb_controller
    if payload.ip_port:
        success = await asyncio.to_thread(adb_controller.connect_wifi, payload.ip_port)
        return {"status": "success" if success else "failed", "device": await asyncio.to_thread(adb_controller.get_device_info)}
    dev = await asyncio.to_thread(adb_controller.get_connected_device)
    return {"status": "detected" if dev else "none", "device": await asyncio.to_thread(adb_controller.get_device_info)}

@app.post("/api/device/tap")
async def tap_screen_endpoint(payload: ScreenTapRequest):
    """Click on live screen by text label or (x, y) coordinates"""
    from app.device.adb_controller import adb_controller
    if await asyncio.to_thread(lambda: adb_controller.is_device_connected):
        if payload.target:
            success = await asyncio.to_thread(adb_controller.click_element_by_text, payload.target)
            return {"status": "success" if success else "not_found", "target": payload.target}
        elif payload.x is not None and payload.y is not None:
            success = await asyncio.to_thread(adb_controller.click_at, payload.x, payload.y)
            return {"status": "success" if success else "failed", "coords": [payload.x, payload.y]}
    # Fallback to websocket bridge
    await device_bridge.broadcast_action("SCREEN_TAP", {"target": payload.target or f"{payload.x},{payload.y}"})
    return {"status": "dispatched_bridge", "target": payload.target}


# WebSocket for mobile device real-time connection
@app.websocket("/ws/device/{device_id}")
async def websocket_device_endpoint(websocket: WebSocket, device_id: str):
    await device_bridge.connect(device_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type in ["SCREEN_TELEMETRY", "screen_state"]:
                device_bridge.update_screen_state(device_id, data.get("hierarchy", data.get("data", {})))
            elif msg_type == "device_register":
                logger.info(f"Android mobile device registered: {device_id} ({data.get('platform')})")
            elif msg_type == "action_result":
                logger.info(f"Action result from {device_id}: {data}")
            elif msg_type == "CALL_STATUS":
                call_info = data.get("data", {})
                await log_call_event(
                    phone_number=call_info.get("phone_number", ""),
                    contact_name=call_info.get("contact_name", ""),
                    status=call_info.get("status", "unknown"),
                    initiated_by="device_report"
                )
            elif msg_type == "PING":
                await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        device_bridge.disconnect(device_id)
    except Exception as e:
        logger.error(f"WebSocket error for {device_id}: {e}")
        device_bridge.disconnect(device_id)

# Explicit APK download route for mobile browsers
@app.get("/AgentTool.apk")
@app.get("/download/AgentTool.apk")
@app.get("/download/agent-tool.apk")
async def download_apk():
    apk_path = os.path.join(CLIENT_DIR, "AgentTool.apk")
    if not os.path.exists(apk_path):
        root_apk = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "AgentTool.apk"))
        if os.path.exists(root_apk):
            apk_path = root_apk
        else:
            raise HTTPException(status_code=404, detail="AgentTool.apk not built yet. Run build_apk.bat")
    return FileResponse(
        path=apk_path,
        media_type="application/vnd.android.package-archive",
        filename="AgentTool.apk"
    )

# WhatsApp Cloud API webhook (signature-verified; must be registered before the static "/" mount)
app.include_router(whatsapp_router)

# Mount the static client files
CLIENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "client"))
if os.path.exists(CLIENT_DIR):
    # Mount at /client as well
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
    # Mount at root / so /, /style.css, /app.js, /manifest.json load seamlessly
    app.mount("/", StaticFiles(directory=CLIENT_DIR, html=True), name="static_root")

