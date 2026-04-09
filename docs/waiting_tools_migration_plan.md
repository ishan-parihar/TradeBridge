# Migration Plan: Waiting Tools → Official MCP SDK

**Date**: 2026-04-09
**Scope**: 4 waiting tools only — no changes to trading/execution tools

---

## 1. Goals

1. Fix all confirmed bugs in waiting tools during migration
2. Create new MCP server module using FastMCP from `mcp.server.fastmcp`
3. Maintain backward compatibility — existing FastAPI endpoints in `main.py` untouched
4. Follow MCP SDK best practices: Pydantic inputs, tool annotations, structured output

## 2. Architecture Decision

### New File: `apps/mcp_server/wait_tools.py`

A standalone FastMCP server module that can be:
- Run as a standalone stdio MCP server (for MCP-compatible clients)
- Imported and its tools registered alongside the existing FastAPI app
- Used as a reference implementation for future MCP SDK migrations

### Tool Naming Convention

Prefix with `mt5_` to follow service_prefix convention:
- `mt5_wait_delay`
- `mt5_wait_indicator`
- `mt5_wait_trade_monitor`
- `mt5_wait_for_price`

## 3. Implementation Plan

### Phase 1: Dependencies
- Add `mcp` package to `pyproject.toml`

### Phase 2: Module Structure

```
apps/mcp_server/wait_tools.py
├── Imports (from main.py helpers + MCP SDK)
├── Pydantic input models (with validation constraints)
├── mt5_wait_delay() — @mcp.tool with annotations
├── mt5_wait_indicator() — @mcp.tool with annotations
├── mt5_wait_trade_monitor() — @mcp.tool with annotations
├── mt5_wait_for_price() — @mcp.tool with annotations
└── create_wait_mcp_server() — factory function
```

### Phase 3: Bug Fixes Applied During Migration

| Bug | Fix |
|-----|-----|
| BUG-001 (silent exception) | Add logging, include error context in result |
| BUG-002 (crosses always triggers) | Track previous_value, detect actual crossing |
| BUG-003 (silent except: pass) | Log warnings with error details |
| BUG-004 (price crosses always triggers) | Track previous_price, detect actual crossing |
| BUG-005 (silent except: pass) | Log warnings with error details |
| BUG-006 (no duration validation) | Add `Field(ge=1, le=3600)` constraint |
| BUG-007 (inline model) | Define proper Pydantic model in new module |
| BUG-008 (empty regime data) | Document limitation, preserve behavior |

### Phase 4: Tool Annotations

All wait tools:
- `readOnlyHint=True` — they observe state, don't modify market/positions
- `idempotentHint=False` — each call is a distinct wait operation
- `openWorldHint=True` — depends on external market data

### Phase 5: Helper Function Imports

Import from `apps/mcp_server/main.py`:
- `_first_bid_ask`
- `tool_get_order_book`
- `tool_get_indicator`
- `normalize_symbol`

Import from `src/mt5_mcp/`:
- `detect_regime` from `services/market_regime`
- Schemas from `schemas/tools.py`

## 4. Testing Strategy

- Syntax check: `python -m py_compile apps/mcp_server/wait_tools.py`
- Import check: verify all imports resolve
- LSP diagnostics: clean on changed files
- No git commit (per requirements)

## 5. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Breaking existing HTTP wrapper | New module is additive; main.py untouched |
| MCP SDK version incompatibility | Pin version in pyproject.toml |
| Import cycle with main.py | Import specific functions, not modules |

## 6. Out of Scope

- Migrating trading/execution tools
- Rewriting the FastAPI app as a full MCP server
- Changing the existing HTTP wrapper behavior
- Fixing the detect_regime empty data issue (noted but not fixed)
