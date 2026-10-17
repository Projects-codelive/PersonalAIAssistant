#!/usr/bin/env bash
# ============================================================================
# AgentTool one-command start (macOS / Linux)
#
#   ./scripts/dev.sh            set up everything that's missing, then run the server
#   ./scripts/dev.sh --apk      also build + install + launch the Android app on the USB phone
#   ./scripts/dev.sh --setup    set up only (venv, deps, MongoDB, model check), don't start
#   ./scripts/dev.sh --deps     only create/update the Python venv (used by make test)
#   ./scripts/dev.sh --stop-tunnel   close the WhatsApp tunnel (its URL is kept across server restarts)
#
# Environment overrides: PORT, GEMMA_MODEL_NAME, ANDROID_SERIAL (pick a phone if several),
#   WHATSAPP=1 (also open a public HTTPS tunnel for the WhatsApp webhook)
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
ANDROID_DIR="$ROOT/android"
RUN_DIR="$ROOT/.dev"                 # logs and stamps (git-ignored)
PHONE_PORT=8000                      # port the Android app's default URL uses on the phone
MONGO_CONTAINER="agenttool-mongo"
APP_ID="com.agenttool.assistant"

WITH_APK=0
SETUP_ONLY=0
DEPS_ONLY=0
STOP_TUNNEL=0
for arg in "$@"; do
  case "$arg" in
    --apk) WITH_APK=1 ;;
    --setup) SETUP_ONLY=1 ;;
    --deps) DEPS_ONLY=1 ;;
    --stop-tunnel) STOP_TUNNEL=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

mkdir -p "$RUN_DIR"
step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
setup_python() {
  step "Python environment"
  local py="$BACKEND/.venv/bin/python"
  if [ ! -x "$py" ]; then
    if have uv; then
      uv venv -q -p 3.12 "$BACKEND/.venv"
    else
      local created=0
      for candidate in python3.12 python3.13 python3.11 python3; do
        if have "$candidate" && "$candidate" -m venv "$BACKEND/.venv" 2>/dev/null; then created=1; break; fi
        rm -rf "$BACKEND/.venv"
      done
      [ "$created" = 1 ] || die "Could not create a virtualenv. Install Python 3.11-3.13 or uv (brew install uv)."
    fi
    ok "Created backend/.venv"
  fi

  # Reinstall only when the requirement files change
  local stamp="$RUN_DIR/requirements.sha"
  local digest
  digest="$(cat "$BACKEND/requirements.txt" "$BACKEND/requirements-dev.txt" | shasum | cut -d' ' -f1)"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$digest" ]; then
    if have uv; then
      uv pip install -q -p "$py" -r "$BACKEND/requirements.txt" -r "$BACKEND/requirements-dev.txt"
    else
      "$py" -m pip install -q --upgrade pip
      "$py" -m pip install -q -r "$BACKEND/requirements.txt" -r "$BACKEND/requirements-dev.txt"
    fi
    echo "$digest" > "$stamp"
    ok "Installed dependencies"
  else
    ok "Dependencies up to date ($("$py" --version))"
  fi
}

# ---------------------------------------------------------------------------
start_mongo() {
  step "MongoDB"
  if nc -z localhost 27017 2>/dev/null; then
    ok "Already running on localhost:27017"
    return
  fi
  if ! have docker || ! docker info >/dev/null 2>&1; then
    warn "MongoDB isn't running and Docker isn't available: the server will use its in-memory fallback."
    return
  fi
  # A Docker failure must not stop the launch: the server falls back to its in-memory store
  if docker container inspect "$MONGO_CONTAINER" >/dev/null 2>&1; then
    if ! docker start "$MONGO_CONTAINER" >/dev/null 2>"$RUN_DIR/docker.err"; then
      warn "Could not start the MongoDB container: $(tail -1 "$RUN_DIR/docker.err" | cut -c1-90)"
      warn "Continuing with the in-memory store (history not saved). Restart Docker Desktop to fix."
      return
    fi
  else
    if ! docker run -d --name "$MONGO_CONTAINER" -p 27017:27017 -v agenttool-mongo-data:/data/db mongo:7 >/dev/null 2>"$RUN_DIR/docker.err"; then
      warn "Could not create the MongoDB container; continuing with the in-memory store."
      return
    fi
  fi
  for _ in $(seq 1 30); do
    if docker exec "$MONGO_CONTAINER" mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; then
      ok "Started Docker container '$MONGO_CONTAINER' (data kept in volume agenttool-mongo-data)"
      return
    fi
    sleep 1
  done
  warn "MongoDB container started but did not answer yet; the server falls back to memory if it can't connect."
}

# ---------------------------------------------------------------------------
pick_model() {
  step "Local Gemma model (Ollama)"
  if ! curl -sf -m 3 http://localhost:11434/api/tags >/dev/null; then
    if have ollama; then
      nohup ollama serve > "$RUN_DIR/ollama.log" 2>&1 &
      for _ in $(seq 1 15); do curl -sf -m 2 http://localhost:11434/api/tags >/dev/null && break; sleep 1; done
    fi
  fi
  local tags
  if ! tags="$(curl -sf -m 3 http://localhost:11434/api/tags)"; then
    warn "Ollama isn't reachable on localhost:11434 (install from https://ollama.com). Only regex intents will work."
    GEMMA_MODEL_NAME="${GEMMA_MODEL_NAME:-gemma:2b}"
    return
  fi

  local wanted="${GEMMA_MODEL_NAME:-$(grep -E '^GEMMA_MODEL_NAME=' "$BACKEND/.env" 2>/dev/null | cut -d= -f2- || true)}"
  GEMMA_MODEL_NAME="$(TAGS="$tags" WANTED="$wanted" "$BACKEND/.venv/bin/python" - <<'PY'
import json, os
models = json.loads(os.environ["TAGS"]).get("models", [])
names = [m["name"] for m in models]
wanted = os.environ.get("WANTED", "")
if wanted and wanted in names:
    print(wanted)
else:
    # Prefer a local (non-cloud) Gemma that supports tool calling, smallest first
    local = [m for m in models if "cloud" not in m["name"]]
    gemma_tools = [m for m in local if m["name"].startswith("gemma") and "tools" in (m.get("capabilities") or [])]
    pool = gemma_tools or [m for m in local if m["name"].startswith("gemma")] or local
    print(min(pool, key=lambda m: m.get("size") or 0)["name"] if pool else wanted)
PY
)"
  if [ -n "$wanted" ] && [ "$wanted" != "$GEMMA_MODEL_NAME" ]; then
    warn "'$wanted' is not installed; using '$GEMMA_MODEL_NAME' instead (or: ollama pull $wanted)"
  fi
  [ -n "$GEMMA_MODEL_NAME" ] || die "No Ollama models installed. Run: ollama pull gemma3:4b"
  ok "Using $GEMMA_MODEL_NAME"
}

# ---------------------------------------------------------------------------
pick_port() {
  step "Server port"
  if [ -n "${PORT:-}" ]; then
    port_in_use "$PORT" && die "Port $PORT is already in use."
  else
    PORT=""
    # With WHATSAPP=1, keep the port the saved tunnel points at, so its URL (saved in Meta) stays valid
    if [ "${WHATSAPP:-0}" = 1 ] && [ -f "$RUN_DIR/tunnel_port" ] && ! port_in_use "$(cat "$RUN_DIR/tunnel_port")"; then
      PORT="$(cat "$RUN_DIR/tunnel_port")"
    fi
    for candidate in 8000 8765 8766 8767 8768 8769; do
      [ -n "$PORT" ] && break
      if ! port_in_use "$candidate"; then PORT="$candidate"; break; fi
    done
    [ -n "$PORT" ] || die "No free port found; set PORT=<port>."
  fi
  ok "Using port $PORT"
}

# ---------------------------------------------------------------------------
adb_device() {
  have adb || return 1
  if [ -n "${ANDROID_SERIAL:-}" ]; then echo "$ANDROID_SERIAL"; return 0; fi
  adb devices | awk 'NR>1 && $2=="device" {print $1; exit}'
}

connect_phone() {
  step "Android phone (USB)"
  local serial
  serial="$(adb_device || true)"
  if [ -z "$serial" ]; then
    warn "No USB phone found (enable USB debugging, then re-run). Web client still works on this machine."
    return
  fi
  # The app's default URL is ws://127.0.0.1:8000 on the phone; map it to whatever port the server uses
  adb -s "$serial" reverse "tcp:$PHONE_PORT" "tcp:$PORT" >/dev/null
  [ "$PORT" != "$PHONE_PORT" ] && adb -s "$serial" reverse "tcp:$PORT" "tcp:$PORT" >/dev/null
  ok "$(adb -s "$serial" shell getprop ro.product.model | tr -d '\r') ($serial): phone port $PHONE_PORT → this machine's port $PORT"
}

# ---------------------------------------------------------------------------
build_and_install_apk() {
  step "Android app"
  local serial
  serial="$(adb_device || true)"
  [ -n "$serial" ] || die "--apk needs a USB phone with USB debugging enabled."

  local sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
  [ -d "$sdk" ] || die "Android SDK not found; set ANDROID_HOME."
  [ -f "$ANDROID_DIR/local.properties" ] || echo "sdk.dir=$sdk" > "$ANDROID_DIR/local.properties"

  # AGP needs JDK 17+; prefer Android Studio's bundled JDK when no suitable JAVA_HOME is set
  local studio_jbr="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
  if [ -z "${JAVA_HOME:-}" ] && [ -d "$studio_jbr" ]; then export JAVA_HOME="$studio_jbr"; fi

  (cd "$ANDROID_DIR" && ./gradlew -q :app:assembleDebug --console=plain) || die "Gradle build failed."
  adb -s "$serial" install -r "$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk" >/dev/null
  ok "Installed $APP_ID"
  for perm in CALL_PHONE READ_CONTACTS RECORD_AUDIO READ_PHONE_STATE POST_NOTIFICATIONS; do
    adb -s "$serial" shell pm grant "$APP_ID" "android.permission.$perm" >/dev/null 2>&1 || true
  done
  ok "Granted phone, contacts, microphone and notification permissions"
  warn "Accessibility and overlay permissions must be enabled once in the app (or by you in Settings)."
  adb -s "$serial" shell am start -n "$APP_ID/.MainActivity" >/dev/null
  ok "Launched the app: tap 'Connect to Gemma Agent' once the server is up"
}

# ---------------------------------------------------------------------------
start_whatsapp_tunnel() {
  step "WhatsApp webhook tunnel"
  have cloudflared || die "WHATSAPP=1 needs cloudflared (brew install cloudflared)."
  local log="$RUN_DIR/cloudflared.log" url="" pid=""
  # Reuse a running tunnel for this port, so the Callback URL saved in Meta stays valid across restarts
  if [ -f "$RUN_DIR/cloudflared.pid" ] && [ -f "$RUN_DIR/tunnel_url" ] && [ "$(cat "$RUN_DIR/tunnel_port" 2>/dev/null)" = "$PORT" ]; then
    pid="$(cat "$RUN_DIR/cloudflared.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      ok "Reusing tunnel: $(cat "$RUN_DIR/tunnel_url")/webhooks/whatsapp (same URL as before)"
      return
    fi
  fi
  stop_whatsapp_tunnel
  # Quick tunnel: free, no account; a new https://*.trycloudflare.com URL each time it starts.
  # The Host header stays the public name, so /api and /ws still require the token through it.
  nohup cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" > "$log" 2>&1 &
  echo $! > "$RUN_DIR/cloudflared.pid"
  echo "$PORT" > "$RUN_DIR/tunnel_port"
  for _ in $(seq 1 30); do
    # -a: the cloudflared log can contain binary bytes, which would make plain grep print "Binary file matches"
    url="$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)"
    [ -n "$url" ] && break
    sleep 1
  done
  [ -n "$url" ] || die "Tunnel did not start (see $log)."
  echo "$url" > "$RUN_DIR/tunnel_url"
  ok "NEW webhook URL: $url/webhooks/whatsapp"
  warn "Paste it into Meta > WhatsApp > Configuration > Callback URL (or: make whatsapp-on). It stays the same until 'make tunnel-stop'."
}

stop_whatsapp_tunnel() {
  if [ -f "$RUN_DIR/cloudflared.pid" ]; then
    kill "$(cat "$RUN_DIR/cloudflared.pid")" 2>/dev/null || true
    rm -f "$RUN_DIR/cloudflared.pid" "$RUN_DIR/tunnel_url" "$RUN_DIR/tunnel_port"
  fi
}

# ---------------------------------------------------------------------------
if [ "$STOP_TUNNEL" = 1 ]; then stop_whatsapp_tunnel; echo "WhatsApp tunnel stopped"; exit 0; fi
setup_python
[ "$DEPS_ONLY" = 1 ] && exit 0
start_mongo
pick_model
if [ "$SETUP_ONLY" = 1 ]; then
  step "Setup complete. Start with: make dev"
  exit 0
fi
pick_port
connect_phone
[ "$WITH_APK" = 1 ] && build_and_install_apk
[ "${WHATSAPP:-0}" = 1 ] && start_whatsapp_tunnel

step "Starting AgentTool on http://localhost:$PORT  (Ctrl+C to stop)"
cd "$BACKEND"
export PORT GEMMA_MODEL_NAME
exec "$BACKEND/.venv/bin/python" run.py
