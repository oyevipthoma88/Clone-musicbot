#!/bin/bash
set -e

WARP_DIR="/tmp/warp-plus"
WARP_BIN="$WARP_DIR/warp-plus"
WARP_PORT="${WARP_PORT:-8086}"
ARCH="amd64"
WARP_URL="https://github.com/bepass-org/warp-plus/releases/latest/download/warp-plus_linux-${ARCH}.zip"

# ⚠️ Proxy ko pehle hata do — sirf tab set karo jab WARP chale
unset YTDLP_PROXY

if [ "${ENABLE_WARP:-0}" = "1" ]; then
    echo "🌐 WARP enabled — checking binary..."

    # Download + unzip WARP if missing
    if [ ! -x "$WARP_BIN" ]; then
        echo "📥 Downloading WARP-plus..."
        mkdir -p "$WARP_DIR"
        cd "$WARP_DIR"
        if ! curl -fL --retry 3 --retry-delay 5 --max-time 90 \
              -o warp.zip "$WARP_URL"; then
            echo "❌ WARP download FAILED (URL: $WARP_URL)"
            echo "⚠️ Continuing without proxy"
            exec python -m melody
        fi
        if ! unzip -o warp.zip; then
            echo "❌ WARP unzip FAILED"
            exec python -m melody
        fi
        chmod +x warp-plus 2>/dev/null || chmod +x warp 2>/dev/null || true
        # Binary name can be 'warp-plus' or 'warp'
        if [ -f warp-plus ]; then mv warp-plus "$WARP_BIN"; 
        elif [ -f warp ]; then mv warp "$WARP_BIN"; fi
        rm -f warp.zip README.md LICENSE
        echo "✅ WARP binary ready ($(du -h "$WARP_BIN" 2>/dev/null | cut -f1))"
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
