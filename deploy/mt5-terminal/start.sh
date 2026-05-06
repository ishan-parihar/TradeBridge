#!/bin/bash
# TradeBridge MT5 Terminal — Headless Wine + Xvfb startup
# MT5 installs at first run (persists to /config volume)
set -e

# ── Configuration ────────────────────────────────────────────────────────────
: "${DISPLAY:=:99}"
: "${WINEPREFIX:=/config/.wine}"
: "${MT5_BROKER_LOGIN:=}"
: "${MT5_BROKER_PASSWORD:=}"
: "${MT5_BROKER_SERVER:=MetaQuotes-Demo}"
: "${EA_TCP_HOST:=tradebridge}"
: "${EA_TCP_PORT:=8025}"
: "${MT5_CMD_OPTIONS:=}"
: "${MT5_ENABLE_VNC:=}"

MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
MT5_EXE="$MT5_DIR/terminal64.exe"
MONO_URL="https://dl.winehq.org/wine/wine-mono/10.3.0/wine-mono-10.3.0-x86.msi"
EA_SRC="/opt/tradebridge/ea"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  TradeBridge MT5 Terminal — Headless Wine Container     ║"
echo "║  Broker:   $MT5_BROKER_SERVER                           "
echo "║  EA:       $EA_TCP_HOST:$EA_TCP_PORT                    "
echo "║  MT5:      ${MT5_EXE}                                   "
echo "║  Wine:     $(wine --version 2>/dev/null || echo 'checking...')"
echo "╚══════════════════════════════════════════════════════════╝"

# ── Step 1: Start Xvfb ───────────────────────────────────────────────────────
echo "[1] Starting Xvfb on $DISPLAY..."
Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp &
XVFB_PID=$!
sleep 2

# ── Step 2: Optional VNC ─────────────────────────────────────────────────────
if [ -n "$MT5_ENABLE_VNC" ]; then
	echo "[2] Starting x11vnc on :5900..."
	x11vnc -display "$DISPLAY" -forever -nopw -quiet -bg 2>/dev/null || true
fi

# ── Step 3: Initialize Wine prefix (first run only) ──────────────────────────
if [ ! -f "$WINEPREFIX/system.reg" ]; then
	echo "[3] Initializing Wine prefix..."
	WINEDLLOVERRIDES="mscoree,mshtml=" wine wineboot --init 2>/dev/null || true
	sleep 3
	echo "[3] Wine prefix initialized at $WINEPREFIX"
else
	echo "[3] Wine prefix already exists"
fi

# ── Step 4: Install Wine Mono (first run only) ───────────────────────────────
if [ ! -d "$WINEPREFIX/drive_c/windows/mono" ]; then
	echo "[4] Installing Wine Mono (required by MT5)..."
	curl -sL "$MONO_URL" -o /tmp/mono.msi
	WINEDLLOVERRIDES="mscoree=d" wine msiexec /i /tmp/mono.msi /qn 2>/dev/null || true
	rm -f /tmp/mono.msi
	echo "[4] Wine Mono installed"
else
	echo "[4] Wine Mono already installed"
fi

# ── Step 5: Install MT5 from pre-copied binaries ────────────────────────
if [ -f "$MT5_EXE" ]; then
	echo "[5] MT5 already installed at $MT5_EXE"
else
	echo "[5] Installing MT5 from pre-copied binaries..."

	# Set Windows version to 10 (MT5 requires Win 10+)
	echo "[5] Setting Wine to Windows 10 mode..."
	wine winecfg -v win10 2>/dev/null || true

	# Create MT5 directory under Wine C: drive
	mkdir -p "$MT5_DIR"

	# Copy pre-installed binaries into Wine prefix
	if [ -d "/opt/tradebridge/mt5" ]; then
		cp -r /opt/tradebridge/mt5/* "$MT5_DIR/"
		echo "[5] MT5 binaries copied ($(du -sh "$MT5_DIR" | cut -f1))"
	else
		echo "[5] ERROR: No MT5 binaries at /opt/tradebridge/mt5"
	fi

	if [ -f "$MT5_EXE" ]; then
		echo "[5] MT5 ready: $MT5_EXE"
	else
		echo "[5] WARNING: terminal64.exe not found after copy"
	fi
fi

# ── Step 6: Install TradeBridge EA ────────────────────────────────────────────
EA_DIR="$MT5_DIR/MQL5/Experts"
mkdir -p "$EA_DIR/TradeBridge"

if [ -d "$EA_SRC" ]; then
	# Copy compiled .ex5 to MT5 Experts root
	cp "$EA_SRC/BridgeConnectorEA.ex5" "$EA_DIR/" 2>/dev/null || true
	# Copy source files to subdirectory
	cp "$EA_SRC"/*.mq5 "$EA_DIR/TradeBridge/" 2>/dev/null || true
	cp "$EA_SRC"/*.mqh "$EA_DIR/TradeBridge/" 2>/dev/null || true
	chmod -R 755 "$EA_DIR"

	# Patch .ex5: replace 127.0.0.1 → EA_TCP_HOST in binary
	echo "[6] Patching EA binary: 127.0.0.1 → $EA_TCP_HOST ..."
	sed -i "s/127\.0\.0\.1/$EA_TCP_HOST/g" "$EA_DIR/BridgeConnectorEA.ex5" 2>/dev/null || true
	if strings "$EA_DIR/BridgeConnectorEA.ex5" 2>/dev/null | grep -q "$EA_TCP_HOST"; then
		echo "  ✅ EA patched: connects to $EA_TCP_HOST:$EA_TCP_PORT"
	else
		echo "  ⚠️  Could not verify EA patch (strings not available)"
	fi
else
	echo "[6] WARNING: EA source directory $EA_SRC not found"
fi

# ── Step 7: Wait for TradeBridge TCP bridge ──────────────────────────────────
echo "[7] Waiting for TCP Bridge at $EA_TCP_HOST:$EA_TCP_PORT..."
for i in $(seq 1 60); do
	if nc -z "$EA_TCP_HOST" "$EA_TCP_PORT" 2>/dev/null; then
		echo "  ✅ TCP Bridge reachable after ${i}s"
		break
	fi
	if [ "$i" -eq 60 ]; then
		echo "  ⚠️  TCP Bridge not reachable after 60s. Starting MT5 anyway..."
	fi
	sleep 1
done

# ── Step 8: Start MT5 terminal ──────────────────────────────────────────────
echo "[8] Launching MT5 Terminal..."
cd "$MT5_DIR"

TERMINAL_CMD=("wine" "terminal64.exe" "/portable")
if [ -n "$MT5_BROKER_LOGIN" ] && [ -n "$MT5_BROKER_PASSWORD" ]; then
	TERMINAL_CMD+=("/login:$MT5_BROKER_LOGIN" "/password:$MT5_BROKER_PASSWORD" "/server:$MT5_BROKER_SERVER")
fi
if [ -n "$MT5_CMD_OPTIONS" ]; then
	TERMINAL_CMD+=("$MT5_CMD_OPTIONS")
fi

echo "  Command: ${TERMINAL_CMD[*]}"
"${TERMINAL_CMD[@]}" 2>&1 &
MT5_PID=$!
echo "  PID: $MT5_PID"

# ── Step 9: Monitor ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  MT5 Terminal Running                                   ║"
echo "║  PID:      $MT5_PID                                     "
echo "║  EA:       $EA_TCP_HOST:$EA_TCP_PORT                    "
echo "║  Broker:   ${MT5_BROKER_LOGIN:-offline}@${MT5_BROKER_SERVER}  "
echo "║  Wine:     $(wine --version 2>/dev/null || echo '?')    "
echo "╚══════════════════════════════════════════════════════════╝"

cleanup() {
	echo "Shutting down..."
	kill $MT5_PID 2>/dev/null || true
	kill $XVFB_PID 2>/dev/null || true
	wait 2>/dev/null || true
	echo "Stopped."
}
trap cleanup SIGTERM SIGINT

while true; do
	if ! kill -0 $MT5_PID 2>/dev/null; then
		echo "WARNING: MT5 process died. Restarting..."
		cd "$MT5_DIR"
		"${TERMINAL_CMD[@]}" 2>&1 &
		MT5_PID=$!
		echo "  New PID: $MT5_PID"
		sleep 15
	fi
	sleep 30
done
