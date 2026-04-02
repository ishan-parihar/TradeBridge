# MT5-MCP Bridge

A production-ready bridge between MetaTrader 5 and Model Context Protocol (MCP), enabling AI-driven trading analysis and execution on Linux systems.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│  MCP Server │────▶│   Bridge     │────▶│   MQL5 EA   │────▶│  MT5     │
│  (Port 8010)│     │   Gateway    │     │  (Wine)     │     │ Terminal │
│             │◀────│  (Port 8020) │◀────│             │◀────│          │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────┘
     ▲                                                                                    │
     │                                                                                    │
     └──────────────────── AI Agents / CLI ──────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Linux-Compatible**: Uses MQL5 EA bridge instead of Python MT5 module (which doesn't work on Linux)
- **Polling Model**: EA polls gateway for commands, posts results back (no reverse connections needed)
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
# Terminal 1: Bridge Gateway
poetry run uvicorn apps.bridge_gateway.main:app --host 127.0.0.1 --port 8020

# Terminal 2: MCP Server
poetry run uvicorn apps.mcp_server.main:app --host 127.0.0.1 --port 8010
```

### EA Setup (MT5 under Wine/Bottles)

1. **Compile EA**: Open `ea/BridgeConnectorEA.mq5` in MetaEditor, press F7
2. **Attach to Chart**: Drag EA onto any chart (e.g., XAUUSDm M1)
3. **Enable WebRequest**: 
   - Tools → Options → Expert Advisors
   - ✓ Allow WebRequest for listed URL
   - Add: `http://127.0.0.1:8020`
4. **Verify Connection**:
   ```bash
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

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `poetry run pytest`
4. Submit a pull request
