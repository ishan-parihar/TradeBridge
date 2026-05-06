"""Manage Vibe-Trading MCP server subprocess lifecycle."""

import asyncio
import os
import signal
import subprocess
from pathlib import Path

import httpx
import structlog

from .config import get_vibe_env_overrides, get_vibe_mcp_port, get_vibe_trading_dir

logger = structlog.get_logger(__name__)


class VibeBridgeLifecycle:
    """Manages the Vibe-Trading MCP server process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._port = get_vibe_mcp_port()
        self._vibe_dir: Path | None = get_vibe_trading_dir()
        self._started = False
        self._disabled = self._vibe_dir is None

    @property
    def is_running(self) -> bool:
        """Check if Vibe-Trading MCP server is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    @property
    def sse_url(self) -> str:
        """Get the SSE endpoint URL for MCP communication."""
        return f"http://127.0.0.1:{self._port}"

    async def start(self) -> bool:
        """Start Vibe-Trading MCP server with SSE transport.

        Returns True if server is ready, False if it failed to start.
        """
        if self._disabled:
            logger.warning("Vibe-Trading not configured — skipping start")
            return False

        assert self._vibe_dir is not None  # guaranteed by _disabled check
        vibe_dir: Path = self._vibe_dir

        if self.is_running:
            logger.info("vibe-trading-mcp already running")
            return True

        mcp_server_path = vibe_dir / "mcp_server.py"
        if not mcp_server_path.exists():
            logger.error(
                "Vibe-Trading mcp_server.py not found", path=str(mcp_server_path)
            )
            return False

        env = os.environ.copy()
        env.update(get_vibe_env_overrides())
        env["PYTHONPATH"] = str(vibe_dir) + os.pathsep + env.get("PYTHONPATH", "")

        try:
            self._process = subprocess.Popen(
                [
                    "python",
                    str(mcp_server_path),
                    "--transport",
                    "sse",
                    "--port",
                    str(self._port),
                ],
                env=env,
                cwd=str(vibe_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            logger.info(
                "Started Vibe-Trading MCP server",
                pid=self._process.pid,
                port=self._port,
            )
        except Exception as e:
            logger.error("Failed to start Vibe-Trading MCP server", error=str(e))
            return False

        # Wait for server to be ready (up to 15 seconds)
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await self._check_health():
                self._started = True
                logger.info("Vibe-Trading MCP server ready", url=self.sse_url)
                return True

        logger.error("Vibe-Trading MCP server failed to start within 15s timeout")
        await self.stop()
        return False

    async def stop(self) -> None:
        """Stop the Vibe-Trading MCP server gracefully."""
        if self._process is None:
            return

        if self._process.poll() is None:
            logger.info("Stopping Vibe-Trading MCP server", pid=self._process.pid)
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                self._process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                self._process.wait(timeout=3)

        self._process = None
        self._started = False
        logger.info("Vibe-Trading MCP server stopped")

    async def _check_health(self) -> bool:
        """Check if the SSE endpoint is responding."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.sse_url}/sse")
                return resp.status_code == 200
        except Exception:
            return False

    async def ensure_running(self) -> bool:
        """Start if not running, return health status."""
        if self.is_running:
            return await self._check_health()
        return await self.start()

    def get_status(self) -> dict:
        """Get current status of Vibe-Trading subprocess."""
        return {
            "running": self.is_running,
            "started": self._started,
            "pid": self._process.pid if self._process else None,
            "port": self._port,
            "sse_url": self.sse_url if self.is_running else None,
            "exit_code": self._process.poll() if self._process else None,
        }
