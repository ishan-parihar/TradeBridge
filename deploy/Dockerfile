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

# Install runtime system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
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

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

USER mt5bridge

# Entry point based on TARGET
CMD ["sh", "-c", "\
    if [ \"$TARGET\" = \"tcp-bridge\" ]; then \
        python -m apps.tcp_bridge.main; \
    elif [ \"$TARGET\" = \"http-gateway\" ]; then \
        uvicorn apps.bridge_gateway.main:app --host 0.0.0.0 --port 8020; \
    elif [ \"$TARGET\" = \"mcp-server\" ]; then \
        uvicorn apps.mcp_server.main:app --host 0.0.0.0 --port 8010; \
    else \
        echo \"Unknown TARGET: $TARGET\"; \
        exit 1; \
    fi \
"]
