#!/usr/bin/env python3
"""
Route the WhatsApp test number's incoming messages to this machine, or back.

    whatsapp_route.py status        show where this number's webhooks go now
    whatsapp_route.py on [URL]      send them to URL (default: tunnel URL saved by `make dev WHATSAPP=1`)
    whatsapp_route.py off           remove the override: back to the app's default webhook

Uses Meta's phone-number-level webhook override, so the app's own webhook setting (used by other
environments) is left untouched; while the override is on, those environments stop receiving this
number's messages. Reads credentials from backend/.env and never prints them.
"""
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = dotenv_values(ROOT / "backend" / ".env")
TUNNEL_URL_FILE = ROOT / ".dev" / "tunnel_url"
WEBHOOK_PATH = "/webhooks/whatsapp"

TOKEN = ENV.get("WHATSAPP_ACCESS_TOKEN", "")
PHONE_ID = ENV.get("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY = ENV.get("WHATSAPP_VERIFY_TOKEN", "")
GRAPH = f"https://graph.facebook.com/{ENV.get('WHATSAPP_API_VERSION') or 'v25.0'}"


def graph(method, path, **kwargs):
    r = httpx.request(method, f"{GRAPH}/{path}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30, **kwargs)
    body = r.json()
    if r.status_code >= 400:
        sys.exit(f"Meta API error {r.status_code}: {body.get('error', {}).get('message')}")
    return body


def status():
    info = graph("GET", PHONE_ID, params={"fields": "display_phone_number,webhook_configuration"})
    config = info.get("webhook_configuration") or {}
    print(f"Number:            {info.get('display_phone_number')}")
    print(f"Number override:   {config.get('phone_number') or 'none'}")
    print(f"Account override:  {config.get('whatsapp_business_account') or 'none'}")
    print(f"App default:       {config.get('application') or 'unknown'}")


def on(url):
    url = url.rstrip("/")
    if not url.endswith(WEBHOOK_PATH):
        url += WEBHOOK_PATH
    if not url.startswith("https://"):
        sys.exit("Meta requires an https:// webhook URL")
    # Meta calls GET <url>?hub.verify_token=... first; the running server must answer it
    graph("POST", PHONE_ID, json={"webhook_configuration": {"override_callback_uri": url, "verify_token": VERIFY}})
    print(f"Incoming messages for this number now go to {url}")
    status()


def off():
    graph("POST", PHONE_ID, json={"webhook_configuration": {"override_callback_uri": ""}})
    print("Override removed: messages go back to the app's default webhook")
    status()


if __name__ == "__main__":
    if not (TOKEN and PHONE_ID and VERIFY):
        sys.exit("Set WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_VERIFY_TOKEN in backend/.env")
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "status":
        status()
    elif command == "on":
        target = sys.argv[2] if len(sys.argv) > 2 else (TUNNEL_URL_FILE.read_text().strip() if TUNNEL_URL_FILE.exists() else "")
        if not target:
            sys.exit("No URL given and no tunnel running. Start one with: make dev WHATSAPP=1")
        on(target)
    elif command == "off":
        off()
    else:
        sys.exit(__doc__)
