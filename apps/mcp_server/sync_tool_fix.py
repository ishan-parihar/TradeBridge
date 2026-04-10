"""Patch FastMCP to run sync tools in a thread pool.

Root cause: FastMCP calls sync tools directly in the async event loop.
Our sync tools block via future.result() on TCP calls, which blocks the
entire event loop including stdio transport I/O. The MCP client times out
with -32001 because no response can be written to stdout.

Fix: Monkey-patch FuncMetadata.call_fn_with_arg_validation to run sync
functions via asyncio.to_thread(), preventing event loop blocking.
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable

from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata

_original_call = FuncMetadata.call_fn_with_arg_validation


async def _patched_call_fn_with_arg_validation(
    self: FuncMetadata,
    fn: Callable[..., Any],
    fn_is_async: bool,
    arguments_to_validate: dict[str, Any],
    arguments_to_pass_directly: dict[str, Any] | None,
) -> Any:
    if fn_is_async:
        return await _original_call(
            self, fn, fn_is_async, arguments_to_validate, arguments_to_pass_directly
        )

    # Run sync function in thread pool to avoid blocking the event loop
    return await asyncio.to_thread(
        fn, **arguments_to_validate, **(arguments_to_pass_directly or {})
    )


FuncMetadata.call_fn_with_arg_validation = _patched_call_fn_with_arg_validation
