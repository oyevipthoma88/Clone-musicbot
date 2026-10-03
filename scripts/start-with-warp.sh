#!/bin/bash
# Start WARP-plus in background, then launch the bot
set -e

WARP_BIN="/app/vendor/warp-plus/warp-plus"
WARP_PORT="${WARP_PORT:-8086}"

if [ -x "$WARP_BIN" ] && [ "${ENABLE_WARP:-0}" = "1" ]; then
    echo "🌐 Starting WARP-plus on 127.0.0.1:${WARP_PORT}..."
    "$WARP_BIN" --bind "127.0.0.1:${WARP_PORT}" > /tmp/warp.log 2>&1 &
    WARP_PID=$!
    sleep 5
    if kill -0 "$WARP_PID" 2>/dev/null; then
        echo "✅ WARP-plus running (PID $WARP_PID)"
    else
        echo "⚠️ WARP-plus died — check /tmp/warp.log"
        tail -20 /tmp/warp.log
    fi
fi

exec python -m melody
