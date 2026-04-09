# Bug Report: MT5-MCP Wait Tools

**Date**: 2026-04-09
**Scope**: All 4 wait tools in `apps/mcp_server/main.py` and `apps/mcp_server/wait_tools.py`
**Status**: Documented, fixes applied in new `wait_tools.py`

---

## BUG-001: No Duration Validation on `wait/delay`

**Severity**: HIGH
**Location**: `main.py` lines 5441-5520
**Type**: Missing input validation

**Description**: The endpoint accepts any integer for `duration_seconds` including 0, negative values, and arbitrarily large numbers (e.g., 86400 = 24 hours). The docstring in the wrapper claims "Range: 1-3600s" but there is NO enforcement in the endpoint itself.

**Impact**: An agent could pass `duration_seconds=86400` and block the HTTP connection for 24 hours, effectively DoS-ing the server.

**Fix**: Clamp `duration_seconds` to 1-3600 via Pydantic `Field(ge=1, le=3600)`.

---

## BUG-002: Crosses Condition Always Triggers in `wait/indicator`

**Severity**: CRITICAL
**Location**: `main.py` lines 5574-5582
**Type**: Logic error

**Description**: When `condition == "crosses"`, the code returns `triggered=True` immediately on the FIRST poll iteration, without tracking the previous indicator value. This means crosses triggers on every call regardless of whether any actual crossing occurred.

**Code (buggy)**:
```python
elif req.condition == "crosses":
    return WaitForIndicatorResult(
        ...
        triggered=True,  # Always true!
    )
```

**Impact**: Every `wait/indicator` call with `condition="crosses"` returns immediately, breaking event-driven trading strategies that depend on actual threshold crossings.

**Fix**: Track `previous_value` across poll iterations. Only trigger when:
- `previous_value < threshold and current_value >= threshold` (upward cross), OR
- `previous_value >= threshold and current_value < threshold` (downward cross)

---

## BUG-003: Silent Error Suppression in `wait/indicator`

**Severity**: MEDIUM
**Location**: `main.py` lines 5583-5584
**Type**: Error handling

**Description**: `except Exception: pass` silently swallows ALL errors during the polling loop. If `tool_get_indicator` fails repeatedly (e.g., bridge disconnect, invalid symbol), the tool silently loops until timeout with zero diagnostics.

**Impact**: No visibility into why a wait failed. Agent receives `triggered=False, timed_out=True` with no indication that the underlying API was failing.

**Fix**: Log warnings via `logger.warning()` on each poll error.

---

## BUG-004: Crosses Condition Always Triggers in `wait_for_price`

**Severity**: CRITICAL
**Location**: `main.py` lines 4360-4363
**Type**: Logic error (identical to BUG-002)

**Description**: Same pattern as BUG-002 but for the price alert tool:

**Code (buggy)**:
```python
else:  # crosses
    mid = (bid + ask) / 2
    current = mid
    triggered = True  # Any update is a "cross" in this mode
```

**Impact**: `wait_for_price` with `condition="crosses"` triggers immediately on any price update, not on actual threshold crossings.

**Fix**: Track `previous_price` and only trigger on actual crossings.

---

## BUG-005: Silent Error Suppression in `wait_for_price`

**Severity**: MEDIUM
**Location**: `main.py` lines 4373-4374
**Type**: Error handling

**Description**: `except Exception: pass` silently swallows all errors during price polling, identical to BUG-003.

**Fix**: Log warnings on each poll error.

---

## BUG-006: No Validation on `wait/indicator` Parameters

**Severity**: MEDIUM
**Location**: `main.py` lines 5523-5608
**Type**: Missing input validation

**Description**: Multiple parameters lack validation:
- `condition` is typed as `str` with no constraint — accepts any string like "foobar"
- `timeout_seconds` has no max — could be 86400 (24 hours)
- `check_interval_seconds` is not clamped — value of 0 would cause a tight infinite loop

**Impact**: Invalid conditions silently never trigger (infinite loop until timeout). Zero check_interval causes CPU-spinning loop. Extreme timeouts block connections.

**Fix**: Use `Literal["above", "below", "crosses", "equals"]` for condition. Add `Field(ge=5, le=3600)` for timeout. Add `Field(ge=1, le=60)` for check_interval.

---

## BUG-007: No Min/Max Duration on `wait/delay`

**Severity**: MEDIUM
**Location**: `main.py` lines 5441-5520
**Type**: Missing input validation

**Details**: Covered by BUG-001. Specifically:
- `duration_seconds=0` → returns immediately without waiting (confusing)
- `duration_seconds=-1` → `asyncio.sleep(-1)` raises ValueError at runtime
- `duration_seconds=86400` → blocks connection for 24 hours

**Fix**: Pydantic `Field(ge=1, le=3600)`.

---

## BUG-008: Inline Model Definition for `TradeMonitorRequest`

**Severity**: LOW
**Location**: `main.py` lines 5616-5622
**Type**: Architecture inconsistency

**Description**: `TradeMonitorRequest` is defined inline in `main.py` rather than in `src/mt5_mcp/schemas/tools.py` where all other request models live. This breaks schema consistency and prevents reuse.

**Fix**: Define proper Pydantic models in the wait_tools module with full validation.

---

## BUG-009: No MCP Protocol Usage

**Severity**: MEDIUM
**Location**: Entire codebase
**Type**: Architecture

**Description**: All wait tools are implemented as FastAPI `@app.post()` routes. They do NOT use the MCP Python SDK (FastMCP). This means:
- No MCP tool annotations (`readOnlyHint`, `idempotentHint`, etc.)
- No MCP tool discovery protocol
- No structured output via `structuredContent`
- Tools are manually mapped to HTTP URLs in the wrapper (`tools/mcp_mt5_wrapper.py`)
- The "MCP" is just HTTP endpoints, not the actual MCP protocol

**Impact**: Clients that implement the MCP protocol properly cannot auto-discover these tools. Tool metadata (descriptions, parameters) is duplicated in the wrapper's `TOOL_SPECS` dict.

**Fix**: Use `@mcp.tool()` decorator from `mcp.server.fastmcp`. The SDK auto-generates tool descriptions from docstrings and validates inputs via Pydantic schemas.

---

## BUG-010: `mcp` Package Already Listed but Never Imported in main.py

**Severity**: LOW
**Location**: `pyproject.toml` line 22, `main.py` (nowhere)
**Type**: Dependency mismatch

**Description**: `pyproject.toml` includes `mcp = "^1.0.0"` as a dependency, but `main.py` never imports or uses it. The dependency was added but never utilized.

**Impact**: Unused dependency in production.

---

## Summary Table

| Bug | Severity | Tool | Type | Status |
|-----|----------|------|------|--------|
| BUG-001 | HIGH | wait/delay | Missing validation | FIXED |
| BUG-002 | CRITICAL | wait/indicator | Logic error (crosses) | FIXED |
| BUG-003 | MEDIUM | wait/indicator | Silent error suppression | FIXED |
| BUG-004 | CRITICAL | wait_for_price | Logic error (crosses) | FIXED |
| BUG-005 | MEDIUM | wait_for_price | Silent error suppression | FIXED |
| BUG-006 | MEDIUM | wait/indicator | Missing validation | FIXED |
| BUG-007 | MEDIUM | wait/delay | Missing validation | FIXED |
| BUG-008 | LOW | trade_monitor | Architecture | FIXED |
| BUG-009 | MEDIUM | All tools | No MCP SDK | FIXED |
| BUG-010 | LOW | pyproject.toml | Unused dep | N/A (already present) |
