

from mcp.types import ToolAnnotations

from . import mcp
from apps.vibe_bridge import tools as vibe_tools

_VIBE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


@mcp.tool(name="vibe_list_skills", annotations=_VIBE_ANNOTATIONS)
async def vibe_list_skills() -> str:
    """List all Vibe-Trading finance skills (69 skills across 7 categories).

    Skills cover trading strategy, risk management, market analysis, and more.
    """
    return await vibe_tools.vibe_list_skills()


@mcp.tool(name="vibe_get_market_data", annotations=_VIBE_ANNOTATIONS)
async def vibe_get_market_data(
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
) -> str:
    """Fetch cross-market data via Vibe-Trading (A-shares, HK/US equities, crypto, futures, forex).

    Use for pre-trade analysis before MT5 execution.
    """
    return await vibe_tools.vibe_get_market_data(codes, start_date, end_date, source, interval)


@mcp.tool(name="vibe_run_swarm", annotations=_VIBE_ANNOTATIONS)
async def vibe_run_swarm(preset: str, variables: dict[str, str]) -> str:
    """Run a Vibe-Trading multi-agent swarm team for collaborative market analysis.

    Presets: investment_committee, crypto_trading_desk, quant_strategy_desk,
    risk_committee, global_allocation_committee.
    """
    return await vibe_tools.vibe_run_swarm(preset, variables)


@mcp.tool(name="vibe_backtest", annotations=_VIBE_ANNOTATIONS)
async def vibe_backtest(run_dir: str) -> str:
    """Run a backtest via Vibe-Trading.

    run_dir must contain config.json and signal_engine.py.
    """
    return await vibe_tools.vibe_backtest(run_dir)


@mcp.tool(name="vibe_swarm_to_signal", annotations=_VIBE_ANNOTATIONS)
async def vibe_swarm_to_signal(report: str, preset: str = "") -> str:
    """Extract tradeable BUY/SELL signal from a Vibe-Trading swarm report.

    Returns TradeBridge-compatible order parameters with entry, SL, TP levels.
    """
    return await vibe_tools.vibe_swarm_to_signal(report, preset)


@mcp.tool(name="vibe_web_search", annotations=_VIBE_ANNOTATIONS)
async def vibe_web_search(query: str, max_results: int = 5) -> str:
    """Search the web for market news, sentiment, and events via Vibe-Trading."""
    return await vibe_tools.vibe_web_search(query, max_results)
