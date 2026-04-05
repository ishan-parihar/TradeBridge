"""Full system audit and quick fixes."""

import time

import typer
from .utils import (
    DATA_DIR,
    HEALTH_URL,
    MCP_URL,
    PROJECT_DIR,
    VENV_PYTHON,
    check_health,
    check_http_gateway,
    check_mcp_health,
    check_tcp_bridge,
    get_env_config,
    get_service_status,
)

doctor_app = typer.Typer(help="Full system audit and quick fixes")


@doctor_app.command("run")
def run(
    fix: bool = typer.Option(False, "--fix", help="Attempt to fix issues"),
):
    """Run full system audit."""
    issues = []
    warnings = []
    ok_count = 0

    typer.echo("=" * 50)
    typer.echo("  Jesse Doctor — System Audit")
    typer.echo("=" * 50)
    typer.echo()

    typer.echo("[1] Python Virtual Environment")
    if (PROJECT_DIR / ".venv" / "bin" / "python").exists():
        typer.echo("    OK: .venv exists")
        ok_count += 1
    else:
        issues.append("Virtual environment not found")
        typer.echo("    FAIL: .venv not found")

    typer.echo("[2] MCP Server (:8010)")
    mcp = check_mcp_health()
    if mcp:
        typer.echo(f"    OK: MCP server healthy")
        ok_count += 1
    else:
        issues.append("MCP server not reachable on :8010")
        typer.echo("    FAIL: MCP server not reachable")

    typer.echo("[3] TCP Bridge (:8025)")
    bridge = check_tcp_bridge()
    if bridge:
        typer.echo("    OK: TCP bridge responding")
        ok_count += 1
    else:
        issues.append("TCP bridge not reachable on :8025")
        typer.echo("    FAIL: TCP bridge not reachable")

    typer.echo("[4] HTTP Gateway (:8020)")
    gw = check_http_gateway()
    if gw:
        typer.echo("    OK: HTTP gateway healthy")
        ok_count += 1
    else:
        issues.append("HTTP gateway not reachable on :8020")
        typer.echo("    FAIL: HTTP gateway not reachable")

    typer.echo("[5] Jesse Agent Health (:8090)")
    h = check_health()
    if h:
        typer.echo(f"    OK: Agent healthy (phase: {h.get('phase', 'unknown')})")
        ok_count += 1
    else:
        issues.append("Jesse agent health endpoint not reachable on :8090")
        typer.echo("    FAIL: Agent not running or health endpoint unreachable")

    typer.echo("[6] Process Status")
    svc = get_service_status()
    if svc["agent_pid"]:
        typer.echo(f"    OK: Agent running (PID {svc['agent_pid']})")
        ok_count += 1
    else:
        warnings.append("Agent process not detected")
        typer.echo("    WARN: Agent process not running")
    if svc["bridge_pid"]:
        typer.echo(f"    OK: Bridge running (PID {svc['bridge_pid']})")
        ok_count += 1
    else:
        warnings.append("Bridge process not detected")
        typer.echo("    WARN: Bridge process not running")

    typer.echo("[7] Data Directory (~/.mt5-mcp)")
    if DATA_DIR.exists():
        files = list(DATA_DIR.iterdir())
        typer.echo(f"    OK: {len(files)} items in data directory")
        ok_count += 1
    else:
        warnings.append("Data directory not found")
        typer.echo("    WARN: ~/.mt5-mcp not found")

    typer.echo("[8] Telegram Configuration")
    env = get_env_config()
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        typer.echo("    OK: Telegram bot configured")
        ok_count += 1
    else:
        warnings.append("Telegram bot not configured")
        typer.echo("    WARN: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")

    typer.echo("[9] MT5 Connection")
    if mcp:
        typer.echo("    OK: MT5 bridge connected (via MCP health)")
        ok_count += 1
    else:
        issues.append("Cannot verify MT5 connection — MCP server unreachable")
        typer.echo("    FAIL: Cannot verify MT5 connection")

    typer.echo()
    typer.echo("=" * 50)
    typer.echo(
        f"  Results: {ok_count} OK, {len(warnings)} warnings, {len(issues)} errors"
    )
    typer.echo("=" * 50)

    if warnings:
        typer.echo()
        typer.echo("Warnings:")
        for w in warnings:
            typer.echo(f"  - {w}")

    if issues:
        typer.echo()
        typer.echo("Errors:")
        for i in issues:
            typer.echo(f"  - {i}")

        if fix:
            typer.echo()
            typer.echo("Attempting fixes...")
            if (
                not svc["agent_pid"]
                and (PROJECT_DIR / ".venv" / "bin" / "python").exists()
            ):
                typer.echo("  Starting agent...")
                import subprocess

                subprocess.run(
                    f"cd {PROJECT_DIR} && nohup {VENV_PYTHON} -m apps.autonomous_agent.main > /tmp/jesse.log 2>&1 &",
                    shell=True,
                )
                time.sleep(3)
                h2 = check_health()
                if h2:
                    typer.echo("  Agent started successfully")
                else:
                    typer.echo("  Failed to start agent — check /tmp/jesse.log")

    if not issues:
        typer.echo("\nAll checks passed!")
    elif not fix:
        typer.echo("\nRun 'jesse doctor run --fix' to attempt automatic fixes")
