# MT5-MCP Bridge

A production-ready bridge between MetaTrader 5 and Model Context Protocol (MCP), enabling AI-driven trading analysis and execution on Linux systems.

## Architecture

### TCP Bridge (Low-Latency, Recommended)

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐     ┌──────────┐
│  MCP Server │────▶│  TCP Bridge      │────▶│   MQL5 EA   │────▶│  MT5     │
│  (Port 8010)│     │  (Port 8025)     │     │  (Wine)     │     │ Terminal │
│             │◀────│  (asyncio TCP)   │◀────│  (sockets)  │◀────│          │
└─────────────┘     └──────────────────┘     └─────────────┘     └──────────┘
     ▲                                                                                    │
     │                                                                                    │
     └──────────────────── AI Agents / CLI ──────────────────────────────────────────────┘
```

**Latency**: ~15-25ms end-to-end (vs ~600ms with HTTP polling)

**Protocol**: Length-prefixed JSON frames over raw TCP sockets (MQL5 `SocketCreate()`/`SocketConnect()`).

### HTTP Bridge (Legacy Fallback)

The original HTTP polling model remains functional. Set `EnableTCPBridge=false` in EA parameters or `MT5_TCP_BRIDGE_ENABLED=false` to use it.

**Key Design Decisions:**
- **Linux-Compatible**: Uses MQL5 EA bridge instead of Python MT5 module (which doesn't work on Linux)
- **TCP Push Model** (default): EA maintains persistent TCP connection to bridge server — commands pushed instantly, no polling
- **HTTP Fallback**: Automatic fallback to HTTP polling if TCP unavailable
- **In-Memory Queue**: Falls back to in-memory if Redis unavailable
- **Demo-First Policy**: Execution tools gated for demo accounts in R&D

## Quick Start

### Prerequisites

```bash
# Python 3.11+
python --version

# Poetry for dependency management
curl -sSL https://install.python-poetry.org | python3 -

# MT5 running under Wine/Bottles (Linux) or native (Windows)
```

### Installation

```bash
# Clone and install
git clone https://github.com/ishanp321/MT5-mcp.git
cd MT5-mcp
poetry install

# Set environment variables (optional)
export MT5_GATEWAY_URL="http://127.0.0.1:8020"
export MT5_REDIS_URL="redis://localhost:6379/0"  # Optional
```

### Start Services

```bash
# Terminal 1: TCP Bridge Server (recommended, port 8025)
poetry run python -m apps.tcp_bridge.main

# Terminal 2: Bridge Gateway (HTTP fallback, port 8020)
poetry run uvicorn apps.bridge_gateway.main:app --host 127.0.0.1 --port 8020

# Terminal 3: MCP Server (port 8010)
poetry run uvicorn apps.mcp_server.main:app --host 127.0.0.1 --port 8010
```

### EA Setup (MT5 under Wine/Bottles)

1. **Compile EA**: Open `ea/BridgeConnectorEA.mq5` in MetaEditor, press F7
2. **Attach to Chart**: Drag EA onto any chart (e.g., XAUUSDm M1)
3. **Configure TCP Bridge** (recommended):
   - In EA inputs, ensure `EnableTCPBridge = true` (default)
   - `TCPBridgeHost = 127.0.0.1`
   - `TCPBridgePort = 8025`
4. **Enable WebRequest**: 
   - Tools → Options → Expert Advisors
   - ✓ Allow WebRequest for listed URL
   - Add: `http://127.0.0.1:8020` (for HTTP fallback)
5. **Verify Connection**:
   ```bash
   # TCP Bridge status
   curl -s http://127.0.0.1:8025/status 2>/dev/null || echo "TCP Bridge not running"
   
   # HTTP Gateway status (fallback)
   curl -s http://127.0.0.1:8020/bridge/terminal/status | jq .
   # Expected: {"connected": true, "login": 123456, ...}
   ```

## API Reference

### MCP Server (Port 8010)

#### Market Data

```bash
# Get OHLCV bars
curl -X POST http://127.0.0.1:8010/tools/get_bars \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","timeframe":"M1","count":100}'

# Get indicator value
curl -X POST http://127.0.0.1:8010/tools/get_indicator \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","timeframe":"H1","indicator":"rsi","period":14}'

# Get indicator series
curl -X POST http://127.0.0.1:8010/tools/get_indicator \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","timeframe":"H1","indicator":"macd","fast":12,"slow":26,"signal":9,"window":100}'

# Get recent ticks
curl -X POST http://127.0.0.1:8010/tools/get_ticks \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","count":50}'

# Get order book (DOM)
curl -X POST http://127.0.0.1:8010/tools/get_order_book \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD"}'
```

#### Supported Indicators

- **Moving Averages**: `sma`, `ema`, `wma`, `smma`
- **Oscillators**: `rsi`, `stoch`, `cci`, `atr`
- **Trend**: `macd`, `adx`, `dmi`, `ichimoku`
- **Volatility**: `bbands` (Bollinger Bands)
- **Volume**: `obv` (On-Balance Volume)

#### Trading Operations

```bash
# Get account summary
curl http://127.0.0.1:8010/tools/get_account_summary

# Get open positions
curl http://127.0.0.1:8010/tools/get_positions

# Get pending orders
curl http://127.0.0.1:8010/tools/get_orders

# Submit market order (demo only)
curl -X POST http://127.0.0.1:8010/tools/submit_market_order_via_bridge \
  -H "Content-Type: application/json" \
  -d '{"intent_id":"demo-1","strategy_id":"scalp","account_id":"demo","symbol":"XAUUSD","side":"buy","order_kind":"market","volume_lots":0.10,"deviation_points":20}'

# Submit pending order
curl -X POST http://127.0.0.1:8010/tools/submit_pending_order \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","side":"buy","volume_lots":0.10,"price":2650.00,"order_kind":"buy_limit"}'

# Modify position SL/TP
curl -X POST http://127.0.0.1:8010/tools/modify_position_sl_tp \
  -H "Content-Type: application/json" \
  -d '{"position_id":12345,"sl":2640.00,"tp":2680.00}'

# Close position
curl -X POST http://127.0.0.1:8010/tools/close_position \
  -H "Content-Type: application/json" \
  -d '{"position_id":12345,"volume_lots":0.05}'

# Close all positions
curl -X POST http://127.0.0.1:8010/tools/close_all_positions \
  -H "Content-Type: application/json" \
  -d '{"scope":"all"}'

# Cancel pending order
curl -X POST http://127.0.0.1:8010/tools/cancel_order \
  -H "Content-Type: application/json" \
  -d '{"order_id":67890}'

# Cancel all pending orders
curl -X POST http://127.0.0.1:8010/tools/cancel_all_orders \
  -H "Content-Type: application/json" \
  -d '{"scope":"all"}'
```

#### Chart Analysis

```bash
# Get chart screenshot (base64 PNG)
curl -X POST http://127.0.0.1:8010/tools/get_chart_screenshot \
  -H "Content-Type: application/json" \
  -d '{"symbol":"XAUUSD","timeframe":"H1","width":1920,"height":1080}'
```

### Bridge Gateway (Port 8020)

#### Health & Status

```bash
# Terminal status (from EA heartbeat)
curl http://127.0.0.1:8020/bridge/terminal/status

# Health check
curl http://127.0.0.1:8020/bridge/health

# Prometheus metrics
curl http://127.0.0.1:8020/metrics
```

#### Direct Command Queue Access

```bash
# Enqueue command
curl -X POST "http://127.0.0.1:8020/bridge/commands/enqueue?type=get_bars&symbol=XAUUSD&timeframe=M1&count=10"

# Get command result
curl http://127.0.0.1:8020/bridge/results/{request_id}
```

## Project Structure

```
MT5-mcp/
├── apps/
│   ├── mcp_server/          # MCP server (port 8010)
│   │   └── main.py
│   └── bridge_gateway/      # Bridge gateway (port 8020)
│       └── main.py
├── ea/
│   └── BridgeConnectorEA.mq5  # MQL5 EA for MT5
├── src/
│   └── mt5_mcp/
│       ├── schemas/         # Pydantic models
│       ├── services/        # Business logic
│       └── settings/        # Configuration
├── tests/                   # Test suite
├── pyproject.toml           # Poetry dependencies
└── README.md
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MT5_GATEWAY_URL` | `http://127.0.0.1:8020` | Bridge gateway endpoint |
| `MT5_REDIS_URL` | - | Redis URL (optional, falls back to in-memory) |
| `MT5_COMMAND_SECRET` | - | Secret for command authorization (optional) |

### EA Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GatewayBaseURL` | `http://127.0.0.1:8020` | Gateway base URL |
| `HeartbeatSeconds` | `5` | Heartbeat interval |

## Testing

```bash
# Run test suite
poetry run pytest

# Test specific module
poetry run pytest tests/test_bridge_gateway.py -v
```

## Monitoring

### Prometheus Metrics

```bash
# Queue depth
curl http://127.0.0.1:8020/metrics | grep mt5_queue_depth

# Heartbeat age
curl http://127.0.0.1:8020/metrics | grep mt5_heartbeat_age_seconds
```

### Health Checks

```bash
# Gateway health
curl http://127.0.0.1:8020/bridge/terminal/status | jq '.connected'

# MCP health
curl http://127.0.0.1:8010/health
```

## Troubleshooting

### EA Not Connecting

1. Check WebRequest permissions in MT5 (Tools → Options → Expert Advisors)
2. Verify gateway is running: `curl http://127.0.0.1:8020/bridge/terminal/status`
3. Check MT5 Experts tab for connection errors
4. Ensure no firewall blocking port 8020

### Commands Not Processing

1. Check queue: `curl http://127.0.0.1:8020/bridge/commands/next`
2. Verify EA is attached to chart and running
3. Check gateway logs for `missing_type` errors
4. Restart MT5 terminal if EA appears frozen

### Python Import Errors

```bash
# Reinstall dependencies
poetry install --no-cache

# Verify installation
poetry run python -c "from mt5_mcp.schemas.models import Bars; print('OK')"
```

## Security Notes

- **Demo-First**: Execution tools are gated for demo accounts in R&D mode
- **Secret Enforcement**: Optional `MT5_COMMAND_SECRET` for command authorization
- **No External Exposure**: Services bind to `127.0.0.1` by default
- **Policy Layer**: MCP enforces account-level execution policies

---

## Jesse — Autonomous Trading Agent

Named after **Jesse Livermore**, the greatest tape reader in history who made $100M+ (≈$1.5B today) purely from price action.

Jesse is a 24/7 autonomous AI trading agent built on LangChain ReAct with persistent memory, circuit breakers, and Telegram control.

### Architecture

```
┌──────────────┐     ┌─────────────┐     ┌──────────────┐     ┌──────────┐
│  Telegram    │────▶│  Jesse      │────▶│  MT5-MCP     │────▶│  MT5 EA  │
│  Bot         │◀────│  Agent      │◀────│  Server :8010│◀────│  (Wine)  │
│  (commands)  │     │  (ReAct)    │     │  (35 tools)  │     │          │
└──────────────┘     └─────────────┘     └──────────────┘     └──────────┘
                          │
                     ┌────▼─────┐
                     │  Memory  │
                     │ ChromaDB │
                     │  + SQLite│
                     └──────────┘
```

### Trading Schedule

| Days | Symbols | Session |
|---|---|---|
| **Mon–Fri** | EURUSD, USDJPY, GBPJPY, AUDUSD, US30, XAUUSD, USOIL, BTCUSD | London/NY overlap active |
| **Sat–Sun** | BTCUSD, ETHUSD | Crypto 24/7 |

### Self-Management

| Condition | Check Interval |
|---|---|
| 3+ consecutive losses | 2 hours (cool-off) |
| 2 consecutive losses | 30 min |
| Position open | 5 min (tight monitoring) |
| Good setups forming | 15 min |
| Quiet market | 60 min |
| Weekend | 2 hours |

### Circuit Breakers

- **3 consecutive losses** → 2-hour cool-off
- **Daily loss >5%** → stop trading for 24h
- **Max 10 trades/day** → stop
- **Max 3 open positions** → stop
- **3 bridge failures** → stop

### Memory Architecture

| Layer | Technology | Purpose |
|---|---|---|
| **Episodic** | SQLite journal | Trade history, PnL, decisions |
| **Semantic** | ChromaDB vector store | Learned patterns (e.g., "Avoid BTCUSD in ranging — 20% win rate") |
| **Procedural** | Pattern extraction | Regime/symbol/emotion rules with decay |

Jesse learns from every trade. After 10+ trades, it consolidates patterns like:
- *"Avoid EURUSD in ranging regime — win rate 25% over 12 trades"*
- *"Favor XAUUSD in trending_up — win rate 68% over 15 trades"*
- *"When confidence <0.4, win rate drops to 30%. Wait for higher conviction."*

### Telegram Commands

| Command | Description |
|---|---|
| `/start` | Agent info |
| `/status` | Agent + circuit breaker status |
| `/sleep` | Pause trading cycles |
| `/wake` | Resume trading cycles |
| `/chart [SYMBOLS...]` | Send chart screenshots (base64 PNG) |
| `/positions` | List open positions with PnL |
| `/pnl` | 7-day performance summary |
| `/scan [SYMBOLS...]` | Quick market scan (price, ATR, regime) |
| `/close` | Close all positions |
| `/help` | Command reference |

### Quick Start

```bash
# 1. Ensure MT5-MCP services are running (ports 8010, 8020, 8025)

# 2. Start Jesse
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
.venv/bin/python -m apps.autonomous_agent.main

# Or use the one-click installer
sudo bash deploy/systemd/install-autonomous.sh
sudo systemctl start jesse-agent
sudo journalctl -u jesse-agent -f
```

### Health Check

```bash
curl http://127.0.0.1:8090/health
# {"status":"healthy","phase":"SCAN","open_positions":0,"daily_pnl":0.0,...}
```

### Data Directory

All state stored in `~/.mt5-mcp/`:

| File | Purpose |
|---|---|
| `chroma/` | Semantic memory (ChromaDB persistent store) |
| `agent_wake_plan.json` | Next wake schedule and reason |
| `trading_journal.db` | Trade decision log |

### Configuration

Environment variables:

| Variable | Required | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — |
| `TELEGRAM_CHAT_ID` | Yes | — |
| `MT5_MCP_URL` | No | `http://127.0.0.1:8010` |
| `PYTHONUNBUFFERED` | No | `1` |

### Project Structure

```
MT5-mcp/
├── apps/
│   ├── autonomous_agent/       # Jesse entry point
│   │   ├── main.py             # Agent bootstrap + signal handling
│   │   └── health.py           # FastAPI health endpoint (:8090)
│   ├── mcp_server/             # MCP server (:8010)
│   ├── tcp_bridge/             # TCP bridge (:8025)
│   └── bridge_gateway/         # HTTP gateway (:8020)
├── src/mt5_mcp/autonomous/     # Jesse core
│   ├── mcp_client.py           # MCP server tool wrappers
│   ├── react_agent.py          # ReAct autonomous agent (LangChain)
│   ├── agent_tools.py          # LangChain tool definitions
│   ├── scheduler.py            # APScheduler + weekday/weekend switching
│   ├── heartbeat_engine.py     # Event-driven adaptive heartbeat
│   ├── circuit_breaker.py      # 5 circuit breakers (persistent)
│   ├── semantic_memory.py      # ChromaDB vector store
│   ├── consolidation.py        # Pattern extraction from trades
│   ├── decay.py                # Ebbinghaus decay + pruning
│   ├── telegram_bot.py         # Bidirectional Telegram bot
│   ├── market_event_bus.py     # Pub/sub event bus
│   ├── price_alert_monitor.py  # Threshold-based price alerts
│   ├── volatility_monitor.py   # ATR-based volatility detection
│   ├── news_event_monitor.py   # Economic calendar monitoring
│   └── session_manager.py      # Trading session detection
├── deploy/systemd/             # Production deployment
│   ├── mt5-autonomous-agent.service  # Jesse systemd unit
│   └── install-autonomous.sh         # One-click installer
└── ARCHITECTURE.md             # Full architecture spec
```

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `poetry run pytest`
4. Submit a pull request
