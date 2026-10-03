#!/bin/bash
set -e

WARP_DIR="/tmp/warp-plus"
WARP_BIN="$WARP_DIR/warp-plus"
WARP_PORT="${WARP_PORT:-8086}"
WARP_URL="https://github.com/bepass-org/warp-plus/releases/latest/download/warp-plus-linux-amd64"

# ⚠️ Proxy को pehle hata do — sirf tab set karo jab WARP chale
unset YTDLP_PROXY

if [ "${ENABLE_WARP:-0}" = "1" ]; then
    echo "🌐 WARP enabled — checking binary..."

    # Download WARP if missing
    if [ ! -x "$WARP_BIN" ]; then
        echo "📥 Downloading WARP-plus..."
        mkdir -p "$WARP_DIR"
        if ! curl -fL --retry 3 --retry-delay 5 --max-time 60 \
              -o "$WARP_BIN" "$WARP_URL"; then
            echo "❌ WARP download FAILED (URL: $WARP_URL)"
            echo "⚠️ Continuing without proxy"
            exec python -m melody
        fi
        chmod +x "$WARP_BIN"
        echo "✅ WARP binary downloaded ($(du -h "$WARP_BIN" | cut -f1))"
    fi

    # Start WARP
    echo "🌐 Starting WARP-plus on 127.0.0.1:${WARP_PORT}..."
    "$WARP_BIN" --bind "127.0.0.1:${WARP_PORT}" > /tmp/warp.log 2>&1 &
    WARP_PID=$!

    # Health check (25s)
    WARP_OK=0
    for i in $(seq 1 25); do
        sleep 1
        if curl -s --max-time 3 --socks5-hostname "127.0.0.1:${WARP_PORT}" \
             https://www.youtube.com -o /dev/null 2>/dev/null; then
            WARP_OK=1
            break
        fi
        if ! kill -0 "$WARP_PID" 2>/dev/null; then
            echo "❌ WARP-plus died. Log tail:"
            tail -30 /tmp/warp.log
            break
        fi
    done

    if [ "$WARP_OK" = "1" ]; then
        echo "✅ WARP UP — proxy enabled: socks5://127.0.0.1:${WARP_PORT}"
        export YTDLP_PROXY="socks5://127.0.0.1:${WARP_PORT}"
    else
        echo "⚠️ WARP not reachable in 25s — running WITHOUT proxy"
        echo "----- warp.log (last 30) -----"
        tail -30 /tmp/warp.log 2>/dev/null || echo "(no log)"
        echo "------------------------------"
    fi
else
    echo "ℹ️ WARP disabled (ENABLE_WARP=${ENABLE_WARP:-0})"
fi

exec python -m melody
