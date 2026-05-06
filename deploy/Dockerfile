# syntax=docker/dockerfile:1

###############################################################################
# Stage 1: Build - Install dependencies
###############################################################################
FROM python:3.12-slim AS build

ARG TARGET=tcp-bridge

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy dependency files first (layer caching)
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev deps, no extras)
RUN poetry install --no-interaction --no-ansi --no-root --without dev

# Copy application source
COPY apps/ ./apps/
COPY src/ ./src/
COPY scripts/ ./scripts/

###############################################################################
# Stage 2: Runtime - Slim production image
###############################################################################
FROM python:3.12-slim AS runtime

ARG TARGET=tcp-bridge

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH" \
    PYTHONPATH="/app/src:/app"

# Create non-root user
RUN groupadd -r mt5bridge && \
    useradd -r -g mt5bridge -d /app -s /sbin/nologin mt5bridge

WORKDIR /app

# Install runtime system dependencies (curl, netcat for health checks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

# Copy Python packages from build stage
COPY --from=build /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=build /usr/local/bin/ /usr/local/bin/

# Copy application code
COPY --from=build /build/apps/ ./apps/
COPY --from=build /build/src/ ./src/
COPY --from=build /build/scripts/ ./scripts/

# Set ownership
RUN chown -R mt5bridge:mt5bridge /app

# Expose ports based on target
EXPOSE 8010
EXPOSE 8020
EXPOSE 8025

# Set stop signal for graceful shutdown
STOPSIGNAL SIGTERM

USER mt5bridge

# ============================================================================
# Target-specific health checks and entrypoints
# ============================================================================

# tcp-bridge: expose TCP port for MT5 EA and MCP sub-protocol ports
FROM runtime AS tcp-bridge-target
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD nc -z 127.0.0.1 8025 || exit 1
CMD ["python", "-m", "apps.tcp_bridge.main"]

# http-gateway: FastAPI on port 8020
FROM runtime AS http-gateway-target
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import json, urllib.request; req = urllib.request.Request('http://127.0.0.1:8020/bridge/health', method='POST', data=b'{}'); urllib.request.urlopen(req, timeout=3)" || exit 1
CMD ["uvicorn", "apps.bridge_gateway.main:app", "--host", "0.0.0.0", "--port", "8020", "--no-access-log"]

# mcp-server: FastAPI on port 8010
FROM runtime AS mcp-server-target
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3)" || exit 1
CMD ["uvicorn", "apps.mcp_server.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
