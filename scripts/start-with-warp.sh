#!/bin/bash
set -e

WARP_DIR="/tmp/warp-plus"
WARP_BIN="$WARP_DIR/warp-plus"
WARP_PORT="${WARP_PORT:-8086}"
WARP_URL="https://github.com/bepass-org/warp-plus/releases/latest/download/warp-plus_linux-amd64.zip"

unset YTDLP_PROXY

if [ "${ENABLE_WARP:-0}" = "1" ]; then
    echo "🌐 WARP enabled — checking binary..."

    if [ ! -x "$WARP_BIN" ]; then
        echo "📥 Downloading WARP-plus..."
        mkdir -p "$WARP_DIR"
        cd "$WARP_DIR"
        if ! curl -fL --retry 3 --retry-delay 5 --max-time 90 \
              -o warp.zip "$WARP_URL"; then
            echo "❌ WARP download FAILED"
            cd /app
            exec python -m melody
        fi
        if ! unzip -o warp.zip; then
            echo "❌ WARP unzip FAILED"
            cd /app
            exec python -m melody
        fi
        # Binary is already at WARP_BIN after unzip — just chmod
        chmod +x "$WARP_BIN" 2>/dev/null || chmod +x warp 2>/dev/null || true
        # If unzip produced 'warp' instead of 'warp-plus', rename it
        if [ ! -x "$WARP_BIN" ] && [ -f warp ]; then
            mv -f warp "$WARP_BIN"
            chmod +x "$WARP_BIN"
        fi
        rm -f warp.zip README.md LICENSE
        cd /app
        if [ -x "$WARP_BIN" ]; then
            echo "✅ WARP binary ready ($(du -h "$WARP_BIN" | cut -f1))"
        else
            echo "❌ WARP binary missing after unzip — running without proxy"
            exec python -m melody
        fi
    fi

    echo "🌐 Starting WARP-plus on 127.0.0.1:${WARP_PORT}..."
    "$WARP_BIN" --bind "127.0.0.1:${WARP_PORT}" > /tmp/warp.log 2>&1 &
    WARP_PID=$!

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
        tail -30 /tmp/warp.log 2>/dev/null || echo "(no log)"
    fi
else
    echo "ℹ️ WARP disabled (ENABLE_WARP=${ENABLE_WARP:-0})"
fi

cd /app
exec python -m melody
