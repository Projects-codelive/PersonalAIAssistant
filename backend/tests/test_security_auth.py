"""API/WebSocket authentication: localhost + USB phone trusted, everyone else needs the token."""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.security import is_request_authorized
from tests.conftest import TEST_TOKEN

LAN_IP = "192.168.1.50"


def make_scope(client="127.0.0.1", host="localhost:8765", origin=None, token_header=None, query=""):
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if token_header:
        headers.append((b"x-agenttool-token", token_header.encode()))
    return {"type": "http", "path": "/api/status", "client": (client, 1234),
            "headers": headers, "query_string": query.encode()}


@pytest.mark.parametrize("scope_kwargs, allowed", [
    # Trusted without a token: this machine, including a USB phone via `adb reverse`
    ({}, True),
    ({"host": "127.0.0.1:8765"}, True),
    ({"client": "::1", "host": "[::1]:8765"}, True),
    ({"origin": "http://localhost:8765"}, True),  # the bundled web client (same origin)
    # Token works from anywhere
    ({"client": LAN_IP, "host": f"{LAN_IP}:8765", "token_header": TEST_TOKEN}, True),
    ({"client": LAN_IP, "host": f"{LAN_IP}:8765", "query": f"token={TEST_TOKEN}"}, True),
    # Rejected
    ({"client": LAN_IP, "host": f"{LAN_IP}:8765"}, False),                          # LAN, no token
    ({"client": LAN_IP, "host": f"{LAN_IP}:8765", "token_header": "wrong"}, False),  # LAN, wrong token
    ({"origin": "http://evil.example"}, False),                                     # another web page in the local browser
    ({"host": "evil.example:8765", "origin": "http://evil.example:8765"}, False),    # DNS rebinding
])
def test_is_request_authorized(scope_kwargs, allowed):
    assert is_request_authorized(make_scope(**scope_kwargs)) is allowed


def test_localhost_api_works_without_token(make_client):
    assert make_client().get("/api/status").status_code == 200


def test_lan_api_requires_token(make_client):
    lan = make_client(client_ip=LAN_IP, base_url=f"http://{LAN_IP}:8765")
    assert lan.get("/api/status").status_code == 401
    assert lan.get("/api/status", headers={"X-AgentTool-Token": TEST_TOKEN}).status_code == 200
    assert lan.get(f"/api/status?token={TEST_TOKEN}").status_code == 200


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/device/call", {"phone_number": "+15550192834"}),
    ("post", "/api/device/tap", {"x": 10, "y": 10}),
    ("get", "/api/device/screen/live", None),
    ("post", "/api/chat", {"message": "call mom"}),
])
def test_lan_cannot_drive_the_phone_without_token(make_client, method, path, body):
    lan = make_client(client_ip=LAN_IP, base_url=f"http://{LAN_IP}:8765")
    response = getattr(lan, method)(path, json=body) if body else getattr(lan, method)(path)
    assert response.status_code == 401


def test_cross_site_page_cannot_use_localhost_trust(make_client):
    r = make_client().post("/api/device/call", json={"phone_number": "100"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 401


def test_static_client_page_stays_public(make_client):
    lan = make_client(client_ip=LAN_IP, base_url=f"http://{LAN_IP}:8765")
    assert lan.get("/").status_code == 200


def test_status_does_not_leak_mongodb_uri(make_client):
    body = make_client().get("/api/status").json()
    assert "uri" not in body["mongodb"]


def test_websocket_rejected_from_lan_without_token(make_client):
    lan = make_client(client_ip=LAN_IP, base_url=f"http://{LAN_IP}:8765")
    with pytest.raises(WebSocketDisconnect) as exc:
        with lan.websocket_connect("/ws/device/attacker"):
            pass
    assert exc.value.code == 1008


def test_websocket_accepted_with_token(make_client):
    lan = make_client(client_ip=LAN_IP, base_url=f"http://{LAN_IP}:8765")
    with lan.websocket_connect(f"/ws/device/phone1?token={TEST_TOKEN}") as ws:
        ws.send_json({"type": "PING"})
        assert ws.receive_json() == {"type": "PONG"}
