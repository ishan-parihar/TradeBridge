from typing import Any, Optional
from mcp.types import ToolAnnotations
from . import mcp

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

_data_store = None


def _get_data_store():
    global _data_store
    if _data_store is None:
        try:
            from mt5_mcp.services.data_store import DataStore

            _data_store = DataStore()
        except Exception as e:
            _data_store = None
            return None
    return _data_store


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_data_import(
    data_type: str,
    format: str,
    content: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Import data into the data store."""
    try:
        store = _get_data_store()
        if store is None:
            return {"error": "Data store unavailable"}
        if data_type == "bars" and format == "csv":
            result = store.import_bars_csv(content, symbol, timeframe)
            return {
                "imported": result.get("imported", 0),
                "duplicates_skipped": result.get("duplicates_skipped", 0),
            }
        if data_type == "bars" and format == "json":
            result = store.import_bars_json(content)
            return {
                "imported": result.get("imported", 0),
                "duplicates_skipped": result.get("duplicates_skipped", 0),
            }
        if data_type == "ticks" and format == "csv":
            result = store.import_ticks_csv(content, symbol)
            return {
                "imported": result.get("imported", 0),
                "duplicates_skipped": result.get("duplicates_skipped", 0),
            }
        if data_type == "ticks" and format == "json":
            return {"error": "JSON ticks import not yet supported"}
        if data_type == "deals" and format == "json":
            result = store.import_deals_json(content)
            return {
                "imported": result.get("imported", 0),
                "duplicates_skipped": result.get("duplicates_skipped", 0),
            }
        if data_type == "deals" and format == "csv":
            return {"error": "CSV deals import not yet supported"}
        return {
            "error": f"Unsupported data_type/format combination: {data_type}/{format}"
        }
    except Exception as e:
        return {
            "error": str(e),
            "imported": 0,
            "duplicates_skipped": 0,
            "errors": [str(e)],
        }


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_data_bars(
    symbol: str,
    timeframe: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Query bars data from the data store."""
    try:
        store = _get_data_store()
        if store is None:
            return {"error": "Data store unavailable", "data": []}
        result = store.query_bars(symbol, timeframe, start_time, end_time, limit)
        return {"data": result}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_data_ticks(
    symbol: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 100,
) -> dict:
    """Query ticks data from the data store."""
    try:
        store = _get_data_store()
        if store is None:
            return {"error": "Data store unavailable", "data": []}
        result = store.query_ticks(symbol, start_time_ms, end_time_ms, limit)
        return {"data": result}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_data_deals(symbol: Optional[str] = None, limit: int = 100) -> dict:
    """Query deals data from the data store."""
    try:
        store = _get_data_store()
        if store is None:
            return {"error": "Data store unavailable", "data": []}
        result = store.query_deals(symbol, limit)
        return {"data": result}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_data_stats() -> dict:
    """Get data store statistics."""
    try:
        store = _get_data_store()
        if store is None:
            return {"error": "Data store unavailable"}
        return store.get_stats()
    except Exception as e:
        return {"error": str(e)}
