#!/bin/bash
set -e

WARP_BIN="/app/vendor/warp-plus/warp-plus"
WARP_PORT="${WARP_PORT:-8086}"

# ⚠️ Proxy ko pehle hata do — sirf tab set karo jab WARP chale
unset YTDLP_PROXY

if [ -x "$WARP_BIN" ] && [ "${ENABLE_WARP:-0}" = "1" ]; then
    echo "🌐 Starting WARP-plus on 127.0.0.1:${WARP_PORT}..."
    "$WARP_BIN" --bind "127.0.0.1:${WARP_PORT}" > /tmp/warp.log 2>&1 &
    WARP_PID=$!

    WARP_OK=0
    for i in $(seq 1 25); do
        sleep 1
        if curl -s --max-time 3 --socks5 "127.0.0.1:${WARP_PORT}" \
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
        echo "✅ WARP-plus UP — proxy enabled"
        export YTDLP_PROXY="socks5://127.0.0.1:${WARP_PORT}"
    else
        echo "⚠️ WARP not reachable in 25s — running WITHOUT proxy"
        echo "----- warp.log -----"
        tail -30 /tmp/warp.log 2>/dev/null || echo "(no log)"
        echo "--------------------"
    fi
else
    echo "ℹ️ WARP disabled (ENABLE_WARP=${ENABLE_WARP:-0})"
fi

exec python -m melody
