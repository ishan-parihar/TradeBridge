# syntax=docker/dockerfile:1
#
# Headless MetaTrader 5 Terminal — Wine + Xvfb
#
# Based on patterns from gmag11/MetaTrader5-Docker but without KasmVNC.
# Uses Xvfb (virtual framebuffer) for headless display, saving ~3 GB.
#
# Ports:
#   5900   — VNC access to the virtual desktop (via x11vnc, optional)
#   8025   — TCP Bridge connection (from EA inside MT5 to TradeBridge)
#   8001   — mt5linux RPyC bridge (optional, for Python MT5 API access)

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

ARG MT5_BROKER_SERVER=MetaQuotes-Demo
ARG MT5_BROKER_LOGIN=
ARG MT5_BROKER_PASSWORD=
ARG EA_TCP_HOST=tradebridge
ARG EA_TCP_PORT=8025

ENV WINEPREFIX=/home/mt5/.wine \
    WINEDLLOVERRIDES="mscoree,mshtml=," \
    DISPLAY=:99 \
    DEBIAN_FRONTEND=noninteractive \
    MT5_BROKER_SERVER=${MT5_BROKER_SERVER} \
    EA_TCP_HOST=${EA_TCP_HOST} \
    EA_TCP_PORT=${EA_TCP_PORT}

WORKDIR /opt/mt5

# ============================================================================
# Install Wine + Xvfb + VNC + utilities
# ============================================================================
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        wget \
        xvfb \
        x11vnc \
        x11-utils \
        xdotool \
        wine \
        wine32 \
        wine64 \
        libwine \
        libwine:i386 \
        winbind \
        xauth \
        netcat-openbsd \
        procps \
        binutils \
    && rm -rf /var/lib/apt/lists/*

# ============================================================================
# Install Wine Mono + Gecko (needed for .NET apps in Wine)
# ============================================================================
RUN mkdir -p /usr/share/wine/mono && \
    wget -q https://dl.winehq.org/wine/wine-mono/9.4.0/wine-mono-9.4.0-x86.msi \
        -O /tmp/wine-mono.msi && \
    cp /tmp/wine-mono.msi /usr/share/wine/mono/wine-mono-9.4.0-x86.msi && \
    rm /tmp/wine-mono.msi

# ============================================================================
# Create mt5 user
# ============================================================================
RUN groupadd -r mt5 && \
    useradd -r -g mt5 -d /home/mt5 -s /bin/bash mt5 && \
    mkdir -p /home/mt5 && \
    chown -R mt5:mt5 /home/mt5 /opt/mt5

# ============================================================================
# Initialize Wine prefix under Xvfb (runs as mt5 user)
# ============================================================================
USER mt5
RUN xvfb-run -a timeout 120 wine wineboot --init 2>&1 || true && \
    timeout 30 wineserver -w 2>&1 || true && \
    echo "Wine prefix initialized"

# ============================================================================
# Download MT5 terminal installer
# ============================================================================
USER root
# Pre-compiled .ex5 for immediate use (avoids MetaEditor dependency)
COPY --chown=mt5:mt5 deploy/mt5-terminal/BridgeConnectorEA.ex5 /home/mt5/source/
# Source .mq5 + .mqh for auto-compilation if MT5 needs to re-compile
COPY --chown=mt5:mt5 deploy/mt5-terminal/BridgeConnectorEA.mq5 /home/mt5/source/
COPY --chown=mt5:mt5 deploy/mt5-terminal/BracketManager.mqh /home/mt5/source/
COPY --chown=mt5:mt5 deploy/mt5-terminal/PositionTimeManager.mqh /home/mt5/source/
COPY --chown=mt5:mt5 deploy/mt5-terminal/TrailingStopManager.mqh /home/mt5/source/

RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" \
    -O /opt/mt5/mt5setup.exe && \
    chmod +x /opt/mt5/mt5setup.exe

# ============================================================================
# Install MT5 silently
# ============================================================================
USER mt5
RUN xvfb-run -a wine /opt/mt5/mt5setup.exe /auto 2>&1 | tail -10 || true

# Wait for install to complete, then install EA into MT5 directory
# .ex5 goes to MQL5/Experts/ so MT5 finds it immediately
# .mq5/.mqh go to a subdirectory for source reference
RUN sleep 5 && \
    MT5_DIR=$(find "$WINEPREFIX/drive_c" -name "terminal64.exe" -path "*/MetaTrader 5/*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null) && \
    if [ -z "$MT5_DIR" ]; then \
        MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"; \
    fi && \
    echo "MT5 directory: $MT5_DIR" && \
    # Install compiled .ex5 in MT5 Experts root (MT5 loads from here)
    cp /home/mt5/source/BridgeConnectorEA.ex5 "$MT5_DIR/MQL5/Experts/" && \
    # Install source files for reference/auto-compilation
    mkdir -p "$MT5_DIR/MQL5/Experts/TradeBridge" && \
    cp /home/mt5/source/BridgeConnectorEA.mq5 "$MT5_DIR/MQL5/Experts/TradeBridge/" && \
    cp /home/mt5/source/*.mqh "$MT5_DIR/MQL5/Experts/TradeBridge/" && \
    echo "EA installed:" && \
    ls -la "$MT5_DIR/MQL5/Experts/BridgeConnectorEA.ex5" && \
    ls -la "$MT5_DIR/MQL5/Experts/TradeBridge/"

USER root
RUN rm -f /opt/mt5/mt5setup.exe

# ============================================================================
# Add startup script
# ============================================================================
COPY deploy/mt5-terminal/start.sh /opt/mt5/start.sh
RUN chmod +x /opt/mt5/start.sh

# ============================================================================
# Ports
# ============================================================================
EXPOSE 5900
EXPOSE 8001
EXPOSE 8025

USER mt5
CMD ["/opt/mt5/start.sh"]
