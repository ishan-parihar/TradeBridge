#!/bin/bash
# Prepare MT5 terminal Docker build by copying binaries from Bottles
# Usage: bash deploy/mt5-terminal/prepare-build.sh

set -e

# Find MT5 binary in Bottles or standard Wine prefix
MT5_SOURCE=""
if [ -f "$HOME/.local/share/bottles/bottles/Apps/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
	MT5_SOURCE="$HOME/.local/share/bottles/bottles/Apps/drive_c/Program Files/MetaTrader 5"
elif [ -f "$HOME/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
	MT5_SOURCE="$HOME/.wine/drive_c/Program Files/MetaTrader 5"
else
	echo "ERROR: MT5 terminal64.exe not found in Bottles or Wine prefix."
	echo "Install MetaTrader 5 first via Bottles or Wine."
	exit 1
fi

DEST="$(cd "$(dirname "$0")/../.." && pwd)/deploy/mt5-terminal/mt5-bin"
mkdir -p "$DEST"

echo "Copying MT5 binaries from:"
echo "  Source: $MT5_SOURCE"
echo "  Dest:   $DEST"

# Copy terminal executable
cp "$MT5_SOURCE/terminal64.exe" "$DEST/"
echo "  ✅ terminal64.exe ($(du -h "$MT5_SOURCE/terminal64.exe" | cut -f1))"

# Copy minimal config
cp -r "$MT5_SOURCE/Config/" "$DEST/Config/"
echo "  ✅ Config/ ($(du -sh "$MT5_SOURCE/Config/" | cut -f1))"

# Copy EA files (if compiled)
if [ -f "$MT5_SOURCE/MQL5/Experts/BridgeConnectorEA.ex5" ]; then
	cp "$MT5_SOURCE/MQL5/Experts/BridgeConnectorEA.ex5" "$(dirname "$0")/BridgeConnectorEA.ex5"
	echo "  ✅ EA binary updated"
fi

echo ""
echo "Done. MT5 binaries prepared for Docker build."
echo "Total: $(du -sh "$DEST" | cut -f1)"
