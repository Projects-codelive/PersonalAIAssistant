# AgentTool developer commands (macOS / Linux). Windows: use build_apk.bat / launch_on_phone.bat.

.PHONY: dev phone setup test test-cov stop help

help:
	@echo "make dev     Set up anything missing (venv, deps, MongoDB, model) and start the server"
	@echo "make phone   Same as dev, plus build + install + launch the Android app on the USB phone"
	@echo "make setup   Set up only, don't start the server"
	@echo "make test    Run the pytest suite (offline: never touches a phone, Ollama or MongoDB)"
	@echo "make stop    Stop the MongoDB container"

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
