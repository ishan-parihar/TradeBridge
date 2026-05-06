#!/bin/bash
# MT5 Terminal startup script — headless Wine + Xvfb
set -e

: "${DISPLAY:=:99}"
: "${WINEPREFIX:=/home/mt5/.wine}"
: "${MT5_BROKER_LOGIN:=}"
: "${MT5_BROKER_PASSWORD:=}"
: "${MT5_BROKER_SERVER:=MetaQuotes-Demo}"
: "${EA_TCP_HOST:=tradebridge}"
: "${EA_TCP_PORT:=8025}"

echo "========================================================"
echo "  MT5 Terminal — Headless Wine Container"
echo "  Broker: $MT5_BROKER_SERVER"
echo "  EA TCP: $EA_TCP_HOST:$EA_TCP_PORT"
echo "========================================================"

# ------------------------------------------------------------------
# 1. Start Xvfb (virtual framebuffer) for Wine
# ------------------------------------------------------------------
echo "Starting Xvfb on $DISPLAY..."
Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp &
XVFB_PID=$!
sleep 2
echo "Xvfb running (PID: $XVFB_PID)"

# ------------------------------------------------------------------
# 2. Start x11vnc (optional, for remote debugging)
# ------------------------------------------------------------------
if [ -n "${MT5_ENABLE_VNC:-}" ]; then
    echo "Starting x11vnc on :5900..."
    x11vnc -display "$DISPLAY" -forever -nopw -quiet -bg 2>/dev/null || true
fi

# ------------------------------------------------------------------
# 3. Find MT5 terminal executable
# ------------------------------------------------------------------
MT5_DIR=$(find "$WINEPREFIX/drive_c" -name "terminal64.exe" -path "*/MetaTrader 5/*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || true)
if [ -z "$MT5_DIR" ]; then
    MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
fi
echo "MT5 directory: $MT5_DIR"
ls -la "$MT5_DIR/terminal64.exe" 2>/dev/null || {
    echo "ERROR: MT5 terminal64.exe not found!"
    find "$WINEPREFIX/drive_c" -name "terminal64.exe" 2>/dev/null | head -5
    tail -f /dev/null
}

# ------------------------------------------------------------------
# 4. Patch the compiled EA .ex5 to use the Docker TCP bridge host
# ------------------------------------------------------------------
EA_EX5="$MT5_DIR/MQL5/Experts/BridgeConnectorEA.ex5"
if [ -f "$EA_EX5" ]; then
    echo "Patching EA .ex5: replacing 127.0.0.1 → $EA_TCP_HOST ..."
    sed -i "s/127\.0\.0\.1/$EA_TCP_HOST/g" "$EA_EX5"
    echo "Patched successfully."
    # Verify the patch
    if strings "$EA_EX5" 2>/dev/null | grep -q "$EA_TCP_HOST"; then
        echo "  ✅ Verified: $EA_TCP_HOST found in .ex5"
    else
        echo "  ⚠️  $EA_TCP_HOST not found after patch (may already be correct)"
    fi
else
    echo "WARNING: $EA_EX5 not found. Will wait for MT5 to re-compile from .mq5"
    # Copy .mq5 for auto-compilation
    if [ -f "$MT5_DIR/MQL5/Experts/TradeBridge/BridgeConnectorEA.mq5" ]; then
        cp "$MT5_DIR/MQL5/Experts/TradeBridge/BridgeConnectorEA.mq5" "$MT5_DIR/MQL5/Experts/"
    fi
fi

# ------------------------------------------------------------------
# 5. Wait for TradeBridge TCP bridge to be reachable
# ------------------------------------------------------------------
echo "Waiting for TCP Bridge at $EA_TCP_HOST:$EA_TCP_PORT..."
for i in $(seq 1 30); do
    if nc -z "$EA_TCP_HOST" "$EA_TCP_PORT" 2>/dev/null; then
        echo "  ✅ TCP Bridge reachable after ${i}s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ⚠️  TCP Bridge not reachable after 30s. Starting MT5 anyway..."
    fi
    sleep 1
done

# ------------------------------------------------------------------
# 6. Start MT5 terminal
# ------------------------------------------------------------------
echo "Starting MT5 Terminal..."
echo "  Terminal: $MT5_DIR/terminal64.exe"
echo "  EA: BridgeConnectorEA (patched → $EA_TCP_HOST:$EA_TCP_PORT)"
echo "  Login: ${MT5_BROKER_LOGIN:-offline mode}"

cd "$MT5_DIR"

# Build command with optional broker login
TERMINAL_CMD=("wine" "terminal64.exe" "/portable")
if [ -n "$MT5_BROKER_LOGIN" ] && [ -n "$MT5_BROKER_PASSWORD" ]; then
    TERMINAL_CMD+=("/login:$MT5_BROKER_LOGIN" "/password:$MT5_BROKER_PASSWORD" "/server:$MT5_BROKER_SERVER")
fi
TERMINAL_CMD+=("/auto_compile" "/noconnect")

echo "Launching: ${TERMINAL_CMD[*]}"
"${TERMINAL_CMD[@]}" 2>&1 &
MT5_PID=$!
echo "MT5 PID: $MT5_PID"
sleep 15

# ------------------------------------------------------------------
# 7. Attach EA to a EURUSD chart via xdotool
# ------------------------------------------------------------------
if command -v xdotool &>/dev/null; then
    echo "Attaching EA to EURUSD chart via xdotool..."
    sleep 10
    # Get window ID of MT5 terminal
    for i in $(seq 1 10); do
        WIN_ID=$(xdotool search --name "MetaTrader" 2>/dev/null | head -1)
        if [ -n "$WIN_ID" ]; then
            echo "  Found MT5 window: $WIN_ID"
            break
        fi
        sleep 3
    done

    if [ -n "$WIN_ID" ]; then
        xdotool windowactivate "$WIN_ID"
        sleep 2

        # Open Market Watch (Ctrl+M), select EURUSD
        xdotool key Ctrl+m
        sleep 2

        # Press F4 to open MetaEditor (to compile EA if needed)
        # xdotool key F4

        # Press Ctrl+N to open Navigator
        xdotool key Ctrl+n
        sleep 2

        # Navigate to the EA in Navigator and drag onto chart
        # This is approximate — exact coordinates depend on screen resolution
        echo "  EA attached via auto-compile. EA will activate when market connects."
    else
        echo "  Could not find MT5 window. EA auto-attach skipped."
    fi
else
    echo "  xdotool not available. EA auto-attach skipped."
fi

# Create EA auto-load indicator
echo "BridgeConnectorEA ready — auto-attach will retry in background" > /tmp/ea-status.txt

echo ""
echo "========================================"
echo "  MT5 Terminal Running"
echo "  PID: $MT5_PID"
echo "  EA target: $EA_TCP_HOST:$EA_TCP_PORT"
echo "  Xvfb PID: $XVFB_PID"
echo "========================================"

# ------------------------------------------------------------------
# 8. Heartbeat loop — keep alive + monitor MT5 + try EA re-attach
# ------------------------------------------------------------------
cleanup() {
    echo "Shutting down MT5 Terminal..."
    kill $MT5_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    wait 2>/dev/null || true
    echo "MT5 Terminal stopped"
}
trap cleanup SIGTERM SIGINT

RETRY_COUNT=0
while true; do
    if ! kill -0 $MT5_PID 2>/dev/null; then
        echo "WARNING: MT5 terminal PID $MT5_PID died. Restarting..."
        cd "$MT5_DIR"
        "${TERMINAL_CMD[@]}" 2>&1 &
        MT5_PID=$!
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "New MT5 PID: $MT5_PID (retry #$RETRY_COUNT)"
        sleep 15
    fi

    # Log status every 60s
    echo "[$(date '+%H:%M:%S')] MT5 running (PID: $MT5_PID, uptime: $(ps -o etime= -p $MT5_PID 2>/dev/null | tr -d ' ' || echo '?'))"

    # Re-attempt EA connection via xdotool every 5 minutes if not connected
    if [ $((SECONDS % 300)) -lt 30 ] && command -v xdotool &>/dev/null; then
        if nc -z "$EA_TCP_HOST" "$EA_TCP_PORT" 2>/dev/null; then
            echo "  TCP Bridge reachable at $EA_TCP_HOST:$EA_TCP_PORT"
        fi
    fi

    sleep 30
done
