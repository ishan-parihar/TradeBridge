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
echo "║  Wine:     $(wine --version 2>/dev/null || echo '?')    "
echo "╚══════════════════════════════════════════════════════════╝"

# ── Step 1: Start Xvfb (reduced resolution to save memory) ─────────────────
# Clean stale X lock files from previous run (prevents restart crashes)
rm -f /tmp/.X*-lock /tmp/.X11-unix/X*
echo "[1] Starting Xvfb on $DISPLAY (1024x768x16)..."
Xvfb "$DISPLAY" -screen 0 1024x768x16 -nolisten tcp &
XVFB_PID=$!
sleep 2

# ── Step 1b: Start fluxbox window manager ──────────────────────────────────
echo "[1b] Starting fluxbox window manager..."
fluxbox 2>/dev/null &
sleep 1

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
	
	# Deploy pre-authenticated Botles config (registry + MT5 accounts/servers)
	# This makes MT5 auto-login to Exness without manual GUI auth
	if [ -d "/opt/tradebridge/bottles-config" ]; then
		echo "[3] Deploying pre-authenticated Bottles config..."
		cp -f /opt/tradebridge/bottles-config/user.reg "$WINEPREFIX/" 2>/dev/null || true
		cp -f /opt/tradebridge/bottles-config/system.reg "$WINEPREFIX/" 2>/dev/null || true
		cp -f /opt/tradebridge/bottles-config/userdef.reg "$WINEPREFIX/" 2>/dev/null || true
		# Deploy MT5 Config (accounts.dat + servers.dat)
		MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"
		mkdir -p "$MT5_DIR/Config"
		cp -f /opt/tradebridge/bottles-config/Config/* "$MT5_DIR/Config/" 2>/dev/null || true
		echo "[3] Bottles config deployed"
	fi
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

# ── Step 5: Install MT5 from pre-copied binaries ─────────────────────────────
if [ -f "$MT5_EXE" ]; then
	echo "[5] MT5 already installed at $MT5_EXE"
else
	echo "[5] Installing MT5 from pre-copied binaries..."
	echo "[5] Setting Wine to Windows 10 mode..."
	wine winecfg -v win10 2>/dev/null || true
	mkdir -p "$MT5_DIR"
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

# ── Step 6: Install + patch TradeBridge EA ───────────────────────────────────
EA_DIR="$MT5_DIR/MQL5/Experts"
mkdir -p "$EA_DIR/TradeBridge"
if [ -d "$EA_SRC" ]; then
	cp "$EA_SRC/BridgeConnectorEA.ex5" "$EA_DIR/" 2>/dev/null || true
	cp "$EA_SRC"/*.mq5 "$EA_DIR/TradeBridge/" 2>/dev/null || true
	cp "$EA_SRC"/*.mqh "$EA_DIR/TradeBridge/" 2>/dev/null || true
	chmod -R 755 "$EA_DIR"
	echo "[6] Patching EA binary: 127.0.0.1 → $EA_TCP_HOST ..."
	sed -i "s/127\.0\.0\.1/$EA_TCP_HOST/g" "$EA_DIR/BridgeConnectorEA.ex5" 2>/dev/null || true
	if command -v strings &>/dev/null; then
		if strings "$EA_DIR/BridgeConnectorEA.ex5" 2>/dev/null | grep -q "$EA_TCP_HOST"; then
			echo "  ✅ EA patched: connects to $EA_TCP_HOST:$EA_TCP_PORT"
		else
			echo "  ⚠️  Could not verify EA patch"
		fi
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
	[ "$i" -eq 60 ] && echo "  ⚠️  TCP Bridge not reachable after 60s. Starting MT5 anyway..."
	sleep 1
done

# ── Step 8: Start MT5 terminal ──────────────────────────────────────────────
echo "[8] Launching MT5 Terminal..."
cd "$MT5_DIR"

TERMINAL_CMD=("wine" "terminal64.exe" "/portable")
# /login:/password: flags — officially supported by MT5 terminal64.exe
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

# ── Step 8a: Auto-attach EA to chart using xdotool ──────────────────────────
echo "[8a] Waiting for MT5 window to initialize..."
EA_ATTACHED=false
for i in $(seq 1 30); do
	WIN_ID=$(xdotool search --name "MetaTrader" 2>/dev/null | head -1)
	if [ -n "$WIN_ID" ]; then
		echo "  MT5 window found: $WIN_ID (after ${i}s)"
		xdotool windowactivate "$WIN_ID" 2>/dev/null
		sleep 3

		# Open Navigator (Ctrl+N)
		xdotool key ctrl+n
		sleep 2

		# Navigate to Expert Advisors section in the Navigator tree
		# Tab multiple times to reach the tree, then arrow down to find the EA
		xdotool key Tab Tab Tab Tab Tab Tab Tab Tab Tab Tab
		sleep 1

		# Navigate down in the tree to find BridgeConnectorEA
		for _ in $(seq 1 15); do
			xdotool key Down
			sleep 0.3
		done

		# Press Enter to attach the EA to the chart
		sleep 1
		xdotool key Return
		sleep 3

		# The EA settings dialog should open
		# Press Enter again to accept default settings
		xdotool key Return
		sleep 2

		# Enable auto-trading (Alt+T toggles auto-trading in MT5)
		xdotool key alt+t
		sleep 1

		echo "  ✅ EA attached to chart via xdotool"
		EA_ATTACHED=true
		break
	fi
	sleep 2
done

if [ "$EA_ATTACHED" = false ]; then
	echo "  ⚠️  Could not attach EA via xdotool (no MT5 window found in 60s)"
fi

# ── Step 8c: Auto-login to broker via xdotool GUI ────────────────────────────
if [ -n "$MT5_BROKER_LOGIN" ] && [ -n "$MT5_BROKER_PASSWORD" ]; then
	echo "[8c] Opening login dialog via Ctrl+L..."
	WIN_ID=$(xdotool search --name "MetaTrader" 2>/dev/null | head -1)
	if [ -n "$WIN_ID" ]; then
		xdotool windowactivate "$WIN_ID" 2>/dev/null
		sleep 2
		# Ctrl+L opens the login dialog in MT5 (the login dialog is a child window)
		xdotool key ctrl+l
		sleep 3
		echo "  Filling credentials..."
		xdotool type "$MT5_BROKER_LOGIN"
		sleep 1
		xdotool key Tab
		sleep 1
		xdotool type "$MT5_BROKER_PASSWORD"
		sleep 1
		xdotool key Tab
		sleep 1
		xdotool type "$MT5_BROKER_SERVER"
		sleep 1
		xdotool key Tab Tab
		sleep 1
		xdotool key Return
		echo "  ✅ Broker login submitted: $MT5_BROKER_LOGIN@$MT5_BROKER_SERVER"
	else
		echo "  ⚠️  Could not find MT5 window — broker login skipped"
	fi
fi

# ── Step 8d: Python in Wine — programmatic broker login via MetaTrader5 API ──
# Uses full Python installer (pre-cached in image at /opt/tradebridge/python-installer.exe)
# with MetaTrader5 pip package for reliable broker login (works where /login: CLI flags fail)
if [ -n "$MT5_BROKER_LOGIN" ] && [ -n "$MT5_BROKER_PASSWORD" ]; then
	echo "[8d] Python in Wine for MT5 login..."
	
	PY_INSTALLER="/opt/tradebridge/python-installer.exe"
	GET_PIP="/opt/tradebridge/get-pip.py"
	
	# Install full Python if not present (use pre-cached installer, no download needed)
	if ! $wine_executable python --version 2>/dev/null; then
		if [ -f "$PY_INSTALLER" ]; then
			echo "  Installing Python 3.9.13 (from cached installer)..."
			# DISPLAY=:99 from the running Xvfb — do NOT use xvfb-run (causes conflict)
			$wine_executable "$PY_INSTALLER" /quiet InstallAllUsers=1 PrependPath=1 2>/dev/null || true
			sleep 15
			# Bootstrap pip and verify
			if $wine_executable python --version 2>/dev/null; then
				echo "  ✅ Python installed: $($wine_executable python --version 2>&1)"
				$wine_executable python "$GET_PIP" 2>/dev/null || true
			else
				echo "  Checking C:\\Python39 directly..."
				PY_ALT="$WINEPREFIX/drive_c/Python39/python.exe"
				if $wine_executable "$PY_ALT" --version 2>/dev/null; then
					echo "  ✅ Python at alternative path"
					# Can't use PATH, use full path for pip
					$wine_executable "$PY_ALT" "$GET_PIP" 2>/dev/null || true
				fi
			fi
		else
			echo "  Python installer not found in image"
		fi
	fi
	
	# Install MetaTrader5 and login
	if $wine_executable python --version 2>/dev/null; then
		echo "  Installing MetaTrader5 pip package..."
		$wine_executable python -m pip install --no-cache-dir MetaTrader5 2>/dev/null || true
		
		echo "  Logging in via Python MetaTrader5 API..."
		MT5_CMD="import MetaTrader5 as mt5"
		MT5_CMD="$MT5_CMD; mt5.initialize()"
		MT5_CMD="$MT5_CMD; auth=mt5.login($MT5_BROKER_LOGIN, password='$MT5_BROKER_PASSWORD', server='$MT5_BROKER_SERVER')"
		MT5_CMD="$MT5_CMD; print('SUCCESS') if auth else print('FAILED:', mt5.last_error())"
		MT5_CMD="$MT5_CMD; mt5.shutdown()"
		
		LOGIN_RESULT=$($wine_executable python -c "$MT5_CMD" 2>/dev/null)
		echo "  $LOGIN_RESULT"
		if echo "$LOGIN_RESULT" | grep -q "SUCCESS"; then
			echo "  ✅ Broker authenticated via MetaTrader5 API"
		fi
	fi
fi

# ── Step 8b: Reduce memory by killing unnecessary Wine processes ─────────────
echo "[8b] Cleaning up unnecessary Wine processes..."
# Kill explorer.exe (desktop environment, not needed)
for pid in $(pgrep -f "explorer.exe" 2>/dev/null); do
	kill "$pid" 2>/dev/null || true
done
# Kill wineconsole (not needed after startup)
for pid in $(pgrep -f "wineconsole" 2>/dev/null); do
	kill "$pid" 2>/dev/null || true
done
echo "  Done"

# ── Step 9: Monitor ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  MT5 Terminal Running                                   ║"
echo "║  PID:      $MT5_PID                                      "
echo "║  EA:       $EA_TCP_HOST:$EA_TCP_PORT                     "
echo "║  Attached: ${EA_ATTACHED}                                "
echo "║  Broker:   ${MT5_BROKER_LOGIN:-offline}@${MT5_BROKER_SERVER}  "
echo "║  Wine:     $(wine --version 2>/dev/null || echo '?')     "
echo "╚══════════════════════════════════════════════════════════╝"

cleanup() {
	echo "Shutting down..."
	kill $MT5_PID 2>/dev/null || true
	kill $XVFB_PID 2>/dev/null || true
	wait 2>/dev/null || true
	echo "Stopped."
}
trap cleanup SIGTERM SIGINT

RESTART_COUNT=0
while true; do
	if ! kill -0 $MT5_PID 2>/dev/null; then
		RESTART_COUNT=$((RESTART_COUNT + 1))
		echo "WARNING: MT5 process died. Restarting (#$RESTART_COUNT)..."
		cd "$MT5_DIR"
		"${TERMINAL_CMD[@]}" 2>&1 &
		MT5_PID=$!
		echo "  New PID: $MT5_PID"
		sleep 15
	fi
	sleep 30
done
