# syntax=docker/dockerfile:1
#
# Headless MetaTrader 5 Terminal for TradeBridge
#
# Design: MT5 + tools install at runtime (faster Docker build, persistent volume)
# Based on patterns from gmag11/MetaTrader5-Docker but without KasmVNC (~3 GB saved)
#
# Ports:
#   5900   — VNC (optional, set MT5_ENABLE_VNC=1)
#   8001   — mt5linux RPyC bridge (optional Python API access)
#   8025   — TCP Bridge connection (EA → TradeBridge)

FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV WINEPREFIX=/config/.wine \
    WINEDEBUG=-all \
    DISPLAY=:99 \
    DEBIAN_FRONTEND=noninteractive \
    TITLE=TradeBridge-MT5

WORKDIR /opt/tradebridge

# ============================================================================
# Install WineHQ + Xvfb + utilities (no Wine runtime ops during build)
# ============================================================================
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg2 \
        wget \
        xvfb \
        x11vnc \
        x11-utils \
        xdotool \
        fluxbox \
        xauth \
        netcat-openbsd \
        procps \
        binutils \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Add WineHQ repository (Debian bookworm) — provides wine 9.x+, not old Debian 8.0
RUN mkdir -pm755 /etc/apt/keyrings && \
    wget -qO- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key && \
    wget -qNP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources && \
    apt-get update && \
    apt-get install -y --install-recommends winehq-stable && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clean up WineHQ key from image
RUN rm -f /etc/apt/keyrings/winehq-archive.key

# ============================================================================
# MT5 binaries (pre-copied from host Bottles installation, avoids installer)
# ============================================================================
COPY deploy/mt5-terminal/mt5-bin/ /opt/tradebridge/mt5/

# ============================================================================
# Pre-cache Python 3.11 embeddable for Wine (works with MetaTrader5 pip)
# ============================================================================
RUN wget -q "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -O /opt/tradebridge/python-embed.zip && \
    wget -q "https://bootstrap.pypa.io/get-pip.py" -O /opt/tradebridge/get-pip.py && \
    echo "Python 3.11 embeddable cached ($(du -h /opt/tradebridge/python-embed.zip | cut -f1))"

# ============================================================================
# Pre-authenticated Bottles config (registry + accounts.dat + server list)
# Makes MT5 auto-login to Exness with existing session state
# ============================================================================
COPY deploy/mt5-terminal/bottles-config/ /opt/tradebridge/bottles-config/

# ============================================================================
# Copy TradeBridge EA files (pre-compiled .ex5 + source)
# ============================================================================
COPY deploy/mt5-terminal/BridgeConnectorEA.ex5 /opt/tradebridge/ea/
COPY deploy/mt5-terminal/BridgeConnectorEA.mq5 /opt/tradebridge/ea/
COPY deploy/mt5-terminal/BracketManager.mqh /opt/tradebridge/ea/
COPY deploy/mt5-terminal/PositionTimeManager.mqh /opt/tradebridge/ea/
COPY deploy/mt5-terminal/TrailingStopManager.mqh /opt/tradebridge/ea/

# ============================================================================
# Startup script
# ============================================================================
COPY deploy/mt5-terminal/start.sh /opt/tradebridge/start.sh
RUN chmod +x /opt/tradebridge/start.sh

# ============================================================================
# Volumes and ports
# ============================================================================
VOLUME /config
EXPOSE 5900 8001 8025

CMD ["/opt/tradebridge/start.sh"]
