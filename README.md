# MT5-MCP Bridge

A production-ready bridge between MetaTrader 5 and Model Context Protocol (MCP), enabling AI-driven trading analysis and execution on Linux systems.

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
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐     ┌──────────┐
│  MCP Server │────▶│  TCP Bridge      │────▶│   MQL5 EA   │────▶│  MT5     │
│  (Port 8010)│     │  (Port 8025)     │     │  (Wine)     │     │ Terminal │
│             │◀────│  (asyncio TCP)   │◀────│  (sockets)  │◀────│          │
└─────────────┘     └──────────────────┘     └─────────────┘     └──────────┘
```

**Latency:** ~15-25ms end-to-end via TCP (vs ~600ms HTTP fallback)

**Key Design:**
- **Linux-Compatible**: MQL5 EA bridge (no Python MT5 module)
- **TCP Push Model**: Persistent TCP connection, instant command delivery
- **HTTP Fallback**: Automatic if TCP unavailable
- **Demo-First**: Execution tools gated for demo accounts in R&D mode

## Quick Start

```bash
# Prerequisites: Python 3.11+, Poetry, MT5 under Wine/Bottles
git clone https://github.com/ishanp321/MT5-mcp.git
cd MT5-mcp
poetry install

# Start services (3 terminals):
poetry run python -m apps.tcp_bridge.main           # TCP Bridge (port 8025)
poetry run uvicorn apps.bridge_gateway.main:app --host 127.0.0.1 --port 8020  # Gateway
poetry run uvicorn apps.mcp_server.main:app --host 127.0.0.1 --port 8010      # MCP Server
```

### EA Setup

1. Compile `ea/BridgeConnectorEA.mq5` in MetaEditor (F7)
2. Attach to any chart (e.g., XAUUSDm M1)
3. Ensure `EnableTCPBridge = true`, `TCPBridgeHost = 127.0.0.1`, `TCPBridgePort = 8025`
4. Verify: `curl -s http://127.0.0.1:8025/status`

## Project Structure

```
MT5-mcp/
├── apps/
│   ├── mcp_server/main.py          # MCP server (port 8010) — ALL tool endpoints
│   ├── bridge_gateway/main.py      # Bridge gateway (port 8020)
│   └── tcp_bridge/main.py          # TCP bridge (port 8025)
├── ea/BridgeConnectorEA.mq5        # MQL5 EA for MT5
├── src/mt5_mcp/
│   ├── schemas/                    # Pydantic models
│   ├── services/                   # Business logic (regime detection, momentum, etc.)
│   ├── policy/                     # Trade policy enforcement
│   └── observability/              # Logging & metrics
├── skills/mt5-trading/             # AI agent skill (SKILL.md + references)
└── tests/                          # Test suite
```

## API Reference

**For humans:** See `mcp_server/main.py` for all endpoint definitions (~100 endpoints across market data, trading, analysis, wait tools, and management).

**For agents:** The SKILL.md (`~/.agents/skills/mt5-trading/SKILL.md`) provides complete tool documentation with usage context, trading workflow, and decision frameworks.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MT5_GATEWAY_URL` | `http://127.0.0.1:8020` | Bridge gateway endpoint |
| `MT5_REDIS_URL` | - | Redis (optional, falls back to in-memory) |

## Monitoring

```bash
curl http://127.0.0.1:8020/bridge/terminal/status  # EA connection
curl http://127.0.0.1:8020/bridge/health             # Gateway
curl http://127.0.0.1:8010/health                    # MCP server
```

## Testing

```bash
poetry run pytest
```

## Security

- Services bind to `127.0.0.1` only — no external exposure
- Execution tools gated for demo accounts in R&D mode
- Optional `MT5_COMMAND_SECRET` for command authorization
- Policy layer enforces account-level execution policies

---

## Related Projects

- **[Jesse](https://github.com/ishanp321/jesse)** — Autonomous AI Trading Agent (LangChain ReAct) consuming MT5-MCP as trading backend

## License

MIT
