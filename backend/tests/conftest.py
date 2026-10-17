"""
Shared fixtures for the offline test suite.

The suite never touches a real phone, Ollama or MongoDB:
- ADB is cut off at the source and a tripwire fails any test that reaches a real ADB server.
- The LLM is disabled so the agent runs its deterministic (regex) intent paths.
- MongoDB is never connected, so the app uses its in-memory fallback store.
"""
import os
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Must be set before app.config is imported
TEST_TOKEN = "test-token-123"
os.environ["AGENTTOOL_API_TOKEN"] = TEST_TOKEN
os.environ["BUILTIN_CONTACTS_FILE"] = str(BACKEND_DIR / "tests" / "no_builtin_contacts.json")
os.environ["CORS_ORIGINS"] = ""
# Fake WhatsApp settings so the real credentials in backend/.env are never used by tests
os.environ.update({
    "WHATSAPP_ACCESS_TOKEN": "test-wa-access-token",
    "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
    "WHATSAPP_APP_SECRET": "test-wa-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "test-wa-verify-token",
    "WHATSAPP_ALLOWED_NUMBERS": "15550001111",
})

import adbutils._adb
import pytest


def _no_real_adb(*args, **kwargs):
    raise RuntimeError("Tests must never talk to a real ADB server / phone. Use the fake_phone fixture.")


# Tripwire: every adbutils call to the ADB server goes through make_connection
adbutils._adb.BaseClient.make_connection = _no_real_adb

import httpx  # noqa: E402


async def _no_real_network(self, request, *args, **kwargs):
    raise RuntimeError(f"Tests must never call external services ({request.url.host}). Fake the client instead.")


# Tripwire: app code calls Meta (WhatsApp) and ElevenLabs through httpx.AsyncClient
httpx.AsyncClient.send = _no_real_network

from app.device.adb_controller import adb_controller, ADBDeviceController  # noqa: E402
from app.device.device_bridge import device_bridge  # noqa: E402
from app.agent.gemma_agent import agent  # noqa: E402

adb_controller._adb = None  # no ADB client at all: get_connected_device() returns None


@pytest.fixture(autouse=True)
def offline_agent(monkeypatch):
    """No LLM, no phone, no websocket clients, unless a test opts in"""
    monkeypatch.setattr(agent, "llm", None)
    monkeypatch.setattr(agent, "llm_with_tools", None)
    monkeypatch.setattr(device_bridge, "active_connections", {})
    yield


@pytest.fixture
def session_id():
    return f"test_{uuid.uuid4().hex[:10]}"


class FakeDevice:
    """Records shell commands instead of running them on a phone"""

    serial = "FAKE123"

    def __init__(self):
        self.commands = []
        self.shell_output = {}
        self.screen_nodes = []  # (text, content_desc, resource_id, clickable) shown on the fake screen

    def show(self, *nodes):
        """Put UI nodes on the fake screen, e.g. show(("", "Send", "com.whatsapp:id/send", True))"""
        self.screen_nodes = list(nodes)

    def dump_hierarchy(self):
        rows = "".join(
            f'<node text="{t}" content-desc="{d}" resource-id="{r}" class="android.widget.Button" '
            f'clickable="{str(c).lower()}" bounds="[{10 + i * 100},10][{90 + i * 100},90]"/>'
            for i, (t, d, r, c) in enumerate(self.screen_nodes)
        )
        return f"<hierarchy>{rows}</hierarchy>"

    def click(self, x, y):
        self.commands.append(["click", str(x), str(y)])

    def shell(self, cmd):
        self.commands.append(cmd)
        key = cmd if isinstance(cmd, str) else " ".join(cmd)
        return self.shell_output.get(key, "")

    def keyevent(self, key):
        self.commands.append(["keyevent", str(key)])

    def app_start(self, pkg, activity=None):
        self.commands.append(["app_start", pkg])

    def send_keys(self, text):
        self.commands.append(["input", "text", text])


@pytest.fixture
def fake_phone(monkeypatch):
    """A connected fake phone: adb_controller sees a device whose commands are only recorded"""
    dev = FakeDevice()
    monkeypatch.setattr(adb_controller, "get_connected_device", lambda: dev)
    monkeypatch.setattr(adb_controller, "wake_screen", lambda: True)
    monkeypatch.setattr(adb_controller, "extract_visual_elements_via_ocr", lambda *a, **k: [])
    # ADB helpers pause for the real UI to settle; a fake screen doesn't need to wait
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return dev


class FakeBridge:
    """Records websocket broadcasts; `recipients` = how many device apps 'received' them"""

    def __init__(self):
        self.events = []
        self.recipients = 1

    async def broadcast_action(self, action_type, payload):
        self.events.append((action_type, payload))
        return {"status": "sent" if self.recipients else "failed", "recipients": self.recipients}


@pytest.fixture
def fake_bridge(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(device_bridge, "broadcast_action", bridge.broadcast_action)
    return bridge


@pytest.fixture
def make_client():
    """
    Build a TestClient that connects from a given client IP (default: this machine).
    Used without `with`, so the app lifespan (MongoDB connect, adb reverse) never runs.
    """
    from starlette.testclient import TestClient
    import app.main as main

    def _make(client_ip="127.0.0.1", base_url="http://localhost:8765"):
        return TestClient(main.app, client=(client_ip, 50000), base_url=base_url)

    return _make
