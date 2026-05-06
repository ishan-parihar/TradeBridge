# TradeBridge

A production-ready bridge between MetaTrader 5 and Model Context Protocol (MCP), enabling AI-driven trading analysis and execution on Linux systems. Runs as a single combined container or a full stack with a headless MT5 terminal in Docker.

**Resource footprint:** ~64 MB for Python services, ~210 MB with MT5 terminal. Fits easily on a 512 MB VPS.

## For AI Agents

**Start here:** `~/.agents/skills/mt5-trading/SKILL.md` — Complete trading workflow, tool usage guide, polling protocol, and decision framework.

The SKILL.md contains everything an AI trading agent needs:
- 12-phase trading cycle (State Triage → Continuous Cycling)
- Tool availability and usage patterns
- Polling tiers and wait protocols
- Risk management and position sizing
- Analysis pipeline with fallback behavior
- Metacognition and decision journaling

## Architecture

```
                                        ┌─────────────┐
                                        │  AI Agent   │
                                        │  (MCP/Hermes)│
                                        └──────┬──────┘
                                               │ MCP (:8010)
                                               ▼
┌──────────────────────────────────────────────────────────┐
│              Combined TradeBridge (1 container)           │
│                                                          │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ MCP Server │  │ HTTP Gateway │  │  TCP Bridge      │  │
│  │ (:8010)    │  │ (:8020)      │  │  (:8025)         │  │
│  └────────────┘  └──────────────┘  └─────────┬────────┘  │
└──────────────────────────────────────────────┼───────────┘
                                               │ TCP (:8025)
                                               ▼
                        ┌──────────────────────────────┐
                        │   MT5 Terminal (1 container) │
                        │   WineHQ 11 + Xvfb + EA     │
                        │   ~210 MB RSS               │
                        └──────────────────────────────┘
```

**Latency:** ~15-25ms end-to-end via TCP (~600ms HTTP fallback)

**Key Design:**
- **Linux-Compatible**: MQL5 EA bridge (no Python MT5 module)
- **TCP Push Model**: Persistent TCP connection, instant command delivery
- **HTTP Fallback**: Automatic if TCP unavailable
- **Demo-First**: Execution tools gated for demo accounts in R&D mode

## Quick Start — Docker (Recommended)

### Prerequisites

- Docker and Docker Compose
- MT5 broker account credentials (optional, for trading)

### Single-container TradeBridge (Python services only)

```bash
# Build the combined image
docker build -f deploy/Dockerfile --target combined-target -t tradebridge:latest .

# Start with Redis
docker compose -f deploy/docker-compose.full.yml up -d redis tradebridge

# Verify health
curl http://localhost:8010/health
# → {"status":"healthy","bridge_connected":false,"tcp_bridge_connected":false,...}
```

### Full stack (with MT5 terminal)

```bash
# Build both images
docker build -f deploy/Dockerfile --target combined-target -t tradebridge:latest .
docker build -f deploy/mt5-terminal/Dockerfile -t mt5-terminal:latest .

# Set broker credentials (optional)
export MT5_BROKER_LOGIN="your_account"
export MT5_BROKER_PASSWORD="your_password"
export MT5_BROKER_SERVER="YourBroker-Server"

# Start everything
docker compose -f deploy/docker-compose.full.yml up -d

# Monitor MT5 terminal setup (first run: ~2-3 min for Wine Mono + binary copy)
docker logs mt5-terminal --tail 20 -f
```

### Resource Usage (measured)

| Service | RSS | Container | Purpose |
|---------|-----|-----------|---------|
| TradeBridge (combined) | **64 MB** | 1 container | MCP + HTTP + TCP bridge |
| MT5 Terminal | **210 MB** | 1 container | WineHQ 11 + Xvfb + MT5 EA |
| Redis | **9 MB** | 1 container | Message queue / cache |
| **Total** | **~283 MB** | **3 containers** | |

### Deploy to VPS

```bash
# Clone and build
git clone git@github.com:ishan-parihar/TradeBridge.git /opt/TradeBridge
cd /opt/TradeBridge
docker build -f deploy/Dockerfile --target combined-target -t tradebridge:latest .
docker compose -f deploy/docker-compose.full.yml up -d redis tradebridge

# Register with Hermes (MCP server manager)
# ~/.hermes/config.yaml:
# mcp_servers:
#   tradebridge:
#     type: http
#     url: http://localhost:8010
#     description: "MT5 Trading Bridge — positions, orders, analysis, market data"
```

## Quick Start — Systemd (for local dev with Bottles)

Requires Python 3.11+, Poetry, and MT5 under Wine/Bottles.

```bash
git clone https://github.com/ishanp321/TradeBridge.git
cd TradeBridge
poetry install

# Start services (3 separate processes):
poetry run python -m apps.tcp_bridge.main           # TCP Bridge (port 8025)
poetry run uvicorn apps.bridge_gateway.main:app --host 127.0.0.1 --port 8020  # Gateway
poetry run uvicorn apps.mcp_server.main:app --host 127.0.0.1 --port 8010      # MCP Server

# Or unified (single process, ~76 MB):
poetry run python -m apps.bridge.main
```

### EA Setup (for Bottles/Wine)

1. Compile `ea/BridgeConnectorEA.mq5` in MetaEditor (F7)
2. Attach to any chart (e.g., EURUSD M1)
3. Ensure `EnableTCPBridge = true`, `TCPBridgeHost = 127.0.0.1`, `TCPBridgePort = 8025`
4. Verify: `curl -s http://127.0.0.1:8010/health`

## Project Structure

```
TradeBridge/
├── apps/
│   ├── bridge/main.py              # Combined: TCP + Gateway + MCP (1 process)
│   ├── mcp_server/main.py          # MCP server (port 8010) — ALL tool endpoints
│   ├── bridge_gateway/main.py      # Bridge gateway (port 8020)
│   └── tcp_bridge/main.py          # TCP bridge (port 8025)
├── ea/BridgeConnectorEA.mq5        # MQL5 EA for MT5
├── src/mt5_mcp/
│   ├── schemas/                    # Pydantic models
│   ├── services/                   # Business logic (regime detection, momentum, etc.)
│   ├── policy/                     # Trade policy enforcement
│   └── observability/              # Logging & metrics
├── deploy/
│   ├── Dockerfile                  # Combined & per-service Docker targets
│   ├── docker-compose.full.yml     # Full stack: redis + tradebridge + mt5-terminal
│   ├── docker-compose.yml          # Separate services (legacy)
│   ├── mt5-terminal/
│   │   ├── Dockerfile              # Headless MT5 (WineHQ 11 + Xvfb)
│   │   ├── start.sh                # Runtime setup & EA patching
│   │   └── BridgeConnectorEA.ex5   # Pre-compiled EA binary
│   └── systemd/                    # Systemd service files
├── skills/mt5-trading/             # AI agent skill (SKILL.md + references)
└── tests/                          # Test suite
```

## Docker Build Reference

```bash
# Build targets
docker build --target combined-target     -t tradebridge:latest .    # All-in-one (recommended)
docker build --target tcp-bridge-target   -t tradebridge-tcp:latest .
docker build --target http-gateway-target -t tradebridge-gateway:latest .
docker build --target mcp-server-target   -t tradebridge-mcp:latest .
docker build -f deploy/mt5-terminal/Dockerfile -t mt5-terminal:latest .

# Configuration via environment variables
export MT5_MCP_PORT=8010          # MCP server port
export MT5_GATEWAY_PORT=8020      # Bridge gateway port
export MT5_TCP_BRIDGE_PORT=8025    # TCP bridge port
export MT5_REDIS_URL=redis://redis:6379/0  # Redis connection
```

## API Reference

The combined container exposes **96+ REST endpoints** across categories:

| Category | Examples | Requires MT5 |
|----------|----------|-------------|
| Health & Status | `/health`, `bridge/status`, `terminal/status` | No |
| Market Data | `/resources/symbols/*/info`, `/tools/get_bars` | Yes |
| Trading | `/resources/positions/open`, `/tools/trading/order*` | Yes |
| Analysis | `/tools/data/stats`, `/tools/ml/models` | No |
| Vibe-Trading | `/vibe/*` | Optional |

**For AI agents:** The MCP protocol endpoint (`POST /tools/*` and `GET /resources/*`) is the primary interface. Register with Hermes for automatic tool discovery.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MT5_GATEWAY_URL` | `http://127.0.0.1:8020` | Bridge gateway endpoint |
| `MT5_REDIS_URL` | - | Redis (optional, falls back to in-memory) |
| `MT5_MCP_HOST` | `0.0.0.0` | MCP server bind address |
| `MT5_GATEWAY_HOST` | `0.0.0.0` | Gateway bind address |
| `MT5_TCP_BRIDGE_HOST` | `0.0.0.0` | TCP bridge bind address |
| `MT5_TCP_BRIDGE_PORT` | `8025` | TCP bridge port |

## Monitoring

```bash
# Health checks
curl http://localhost:8010/health                          # Combined service
curl http://localhost:8020/bridge/health                    # Gateway (GET)

# Docker container stats
docker stats --no-stream mt5-combined mt5-terminal mt5-redis
```

## Testing

```bash
# Python tests
poetry run pytest

# Docker end-to-end
docker compose -f deploy/docker-compose.full.yml up -d
curl http://localhost:8010/health
```

## Security

- Python services bind to `127.0.0.1` on systemd, `0.0.0.0` in Docker
- MT5 terminal uses `seccomp:unconfined` (required for Wine socket operations)
- Execution tools gated for demo accounts in R&D mode
- Optional `MT5_COMMAND_SECRET` for command authorization
- Policy layer enforces account-level execution policies

## Related Projects

- **[Jesse](https://github.com/ishanp321/jesse)** — Autonomous AI Trading Agent (LangChain ReAct) consuming TradeBridge as trading backend

## License

MIT
