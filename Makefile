# AgentTool developer commands (macOS / Linux). Windows: use build_apk.bat / launch_on_phone.bat.

.PHONY: dev phone setup test stop whatsapp-status whatsapp-on whatsapp-off tunnel-stop help

help:
	@echo "make dev     Set up anything missing (venv, deps, MongoDB, model) and start the server"
	@echo "make phone   Same as dev, plus build + install + launch the Android app on the USB phone"
	@echo "make setup   Set up only, don't start the server"
	@echo "make test    Run the pytest suite (offline: never touches a phone, Ollama or MongoDB)"
	@echo "make stop    Stop the MongoDB container"
	@echo ""
	@echo "WhatsApp:  make dev WHATSAPP=1   start with a public HTTPS tunnel for the webhook"
	@echo "           make whatsapp-on      route the test number's messages to this machine"
	@echo "           make whatsapp-off     route them back to the app's default webhook"
	@echo "           make whatsapp-status  show where they go now"
	@echo "           make tunnel-stop      close the tunnel (its URL survives server restarts until then)"

dev:
	@./scripts/dev.sh

phone:
	@./scripts/dev.sh --apk

setup:
	@./scripts/dev.sh --setup

test:
	@./scripts/dev.sh --deps
	@cd backend && .venv/bin/python -m pytest

stop:
	@docker stop agenttool-mongo >/dev/null 2>&1 && echo "Stopped agenttool-mongo" || echo "agenttool-mongo is not running"

whatsapp-status:
	@backend/.venv/bin/python scripts/whatsapp_route.py status

whatsapp-on:
	@backend/.venv/bin/python scripts/whatsapp_route.py on

whatsapp-off:
	@backend/.venv/bin/python scripts/whatsapp_route.py off

tunnel-stop:
	@./scripts/dev.sh --stop-tunnel
