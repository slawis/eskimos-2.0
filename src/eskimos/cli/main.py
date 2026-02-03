"""Main CLI entry point for Eskimos 2.0.

This module provides the Typer CLI application with all commands.

Usage:
    eskimos --help
    eskimos send 123456789 "Hello World"
    eskimos modem status
    eskimos modem test --phone 123456789
    eskimos serve --port 8000
"""

from __future__ import annotations

import typer
from rich.console import Console

from eskimos import __version__
from eskimos.cli.send import send_command
from eskimos.cli.modem import modem_app
from eskimos.cli.serve import serve_command

# Create main app
app = typer.Typer(
    name="eskimos",
    help="Eskimos 2.0 - SMS Gateway with AI",
    add_completion=False,
    rich_markup_mode="rich",
)

# Create console for rich output
console = Console()


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print(f"[bold green]Eskimos[/bold green] version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Eskimos 2.0 - SMS Gateway with AI.

    Professional Python implementation for automated SMS marketing
    with AI-powered personalization and multi-modem support.

    Examples:
        eskimos send 123456789 "Hello!"
        eskimos modem status
        eskimos modem test --phone 123456789
    """
    pass


# Add send command
app.command(name="send")(send_command)

# Add modem subcommands
app.add_typer(modem_app, name="modem", help="Modem management commands")

# Add serve command
app.command(name="serve")(serve_command)


# Quick commands as aliases
@app.command(name="status")
def status_command() -> None:
    """Quick status check (alias for 'modem status')."""
    import asyncio
    from eskimos.cli.modem import status_command as modem_status
    asyncio.run(modem_status())


@app.command(name="test")
def test_command(
    phone: str = typer.Option(
        "886480453",
        "--phone",
        "-p",
        help="Phone number to send test SMS to",
    ),
) -> None:
    """Quick test (alias for 'modem test')."""
    import asyncio
    from eskimos.cli.modem import test_command as modem_test
    asyncio.run(modem_test(phone=phone))


if __name__ == "__main__":
    app()
