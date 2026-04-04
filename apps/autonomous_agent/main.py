"""Autonomous 24/7 AI Trading Agent — Main Entry Point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger("autonomous_agent")


def _load_dotenv():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


async def _start_health_server():
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    import uvicorn

    from .health import app as health_app

    config = uvicorn.Config(
        health_app, host="127.0.0.1", port=8090, log_level="warning"
    )
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)
    logger.info("Health endpoint listening on :8090")


async def main():
    _load_dotenv()
    from mt5_mcp.autonomous.react_agent import create_agent_resources
    from mt5_mcp.autonomous.conversation import ConversationManager
    from mt5_mcp.autonomous.scheduler import AgentScheduler
    from mt5_mcp.autonomous.circuit_breaker import CircuitBreaker
    from .health import update_health

    Path.home().joinpath(".mt5-mcp").mkdir(parents=True, exist_ok=True)

    await _start_health_server()

    resources = await create_agent_resources()

    try:
        health = await resources.mcp_client.health_check()
        logger.info("MCP server healthy: %s", health)
    except Exception as exc:
        logger.error("Cannot reach MCP server: %s", exc)
        update_health(phase="ERROR")
        await resources.close()
        sys.exit(1)

    conversation_mgr = ConversationManager()

    try:
        acct = await asyncio.wait_for(
            resources.mcp_client.account_summary(), timeout=10.0
        )
        equity = acct.get("equity", 200.0)
        logger.info("Account: equity=$%.2f", equity)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("Could not fetch account summary: %s", exc)
        equity = 200.0

    circuit_breaker = CircuitBreaker(equity=equity)
    scheduler = AgentScheduler(
        jesse_agent=resources.agent,
        mcp_client=resources.mcp_client,
        circuit_breaker=circuit_breaker,
    )
    scheduler.add_builtin_jobs()
    scheduler.init_heartbeat()

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    bot = None
    if telegram_token and telegram_chat:
        from mt5_mcp.autonomous.telegram_bot import TelegramBot

        bot = TelegramBot(
            bot_token=telegram_token,
            chat_id=telegram_chat,
            scheduler=scheduler,
            mcp_client=resources.mcp_client,
            circuit_breaker=circuit_breaker,
            jesse_agent=resources.agent,
            conversation_mgr=conversation_mgr,
        )
        logger.info("Telegram bot configured")
    else:
        logger.warning(
            "Telegram bot not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)"
        )

    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()
    shutting_down = False

    def handle_signal(sig):
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        logger.info("Signal %s received, shutting down...", sig)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    logger.info("Autonomous Trading Agent starting")
    from mt5_mcp.autonomous.scheduler import get_active_symbols

    logger.info("Active symbols: %s", get_active_symbols())

    update_health(phase="STARTED")

    scheduler.start()
    await scheduler.start_heartbeat()

    bot_task = None
    if bot:
        bot_task = asyncio.create_task(bot.start())

    await shutdown_event.wait()

    update_health(phase="SHUTTING_DOWN")
    scheduler.shutdown()

    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.stop()

    await resources.close()
    update_health(phase="STOPPED")
    logger.info("Agent shut down complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
