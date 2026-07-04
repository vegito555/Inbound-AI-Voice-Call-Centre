#!/usr/bin/env bash
# OutboundAI production startup.
#
# Single source of truth for configuration: real environment variables
# injected by the host (Coolify, Docker, systemd, ...). This script never
# reads a .env file in production.
#
# Two processes run in this container:
#   1) FastAPI dashboard     -> port 8000 (foreground-ish, restarted by run_loop)
#   2) LiveKit agent worker  -> outbound websocket to LiveKit Cloud
#
# If either dies, the container exits non-zero so the orchestrator restarts it.

set -euo pipefail
cd "$(dirname "$0")"

# Resolve TRUNK_ID from either INBOUND_TRUNK_ID or OUTBOUND_TRUNK_ID
TRUNK_ID="${INBOUND_TRUNK_ID:-${OUTBOUND_TRUNK_ID:-}}"
export INBOUND_TRUNK_ID="${TRUNK_ID}"
export OUTBOUND_TRUNK_ID="${TRUNK_ID}"

echo "🚀 Starting InboundAI (single source of truth: VPS env vars)"
echo "   LIVEKIT_URL      = ${LIVEKIT_URL:-<missing>}"
echo "   GEMINI_MODEL     = ${GEMINI_MODEL:-gemini-3.1-flash-live-preview}"
echo "   GEMINI_TTS_VOICE = ${GEMINI_TTS_VOICE:-Aoede}"
echo "   SUPABASE_URL     = ${SUPABASE_URL:-<missing>}"
echo "   INBOUND_TRUNK_ID = ${TRUNK_ID:-<missing>}"

# Fail fast if the bare-minimum credentials are not present.
require=( LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET GOOGLE_API_KEY \
          SUPABASE_URL SUPABASE_SERVICE_KEY )
missing=()
for v in "${require[@]}"; do
    if [ -z "${!v:-}" ]; then
        missing+=("$v")
    fi
done
if [ -z "${TRUNK_ID}" ]; then
    missing+=("INBOUND_TRUNK_ID")
fi
if [ ${#missing[@]} -gt 0 ]; then
    echo "❌ Required environment variables missing: ${missing[*]}"
    echo "   Set them in your VPS / Coolify dashboard, then redeploy."
    exit 1
fi

# Forward shutdown signals to children so SIGTERM cleanly stops everything.
SERVER_PID=""
AGENT_PID=""
shutdown() {
    echo "🛑 Caught signal — stopping children…"
    [ -n "$SERVER_PID" ] && kill -TERM "$SERVER_PID" 2>/dev/null || true
    [ -n "$AGENT_PID" ]  && kill -TERM "$AGENT_PID"  2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap shutdown SIGINT SIGTERM

echo "🌐 Starting FastAPI on 0.0.0.0:8000…"
uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' &
SERVER_PID=$!

# Give uvicorn a moment to bind before launching the agent worker.
sleep 2

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "❌ FastAPI failed to start"
    exit 1
fi

echo "🤖 Starting LiveKit agent worker…"
python agent.py start &
AGENT_PID=$!

# Wait on whichever child exits first; if either dies the container exits
# non-zero and the orchestrator (Coolify / Docker) restarts the whole thing.
wait -n "$SERVER_PID" "$AGENT_PID"
EXIT_CODE=$?

echo "⚠️  A child process exited with code $EXIT_CODE — shutting down"
shutdown
exit $EXIT_CODE
