"""Modem management commands for Eskimos CLI.

Usage:
    eskimos modem status
    eskimos modem test --phone 123456789
    eskimos modem list
"""

from __future__ import annotations

import asyncio
from enum import Enum

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Create modem subapp
modem_app = typer.Typer(
    name="modem",
    help="Modem management commands",
)


class ModemTypeOption(str, Enum):
    """Available modem types."""

    PUPPETEER = "puppeteer"
    MOCK = "mock"
    AUTO = "auto"


async def _get_modem_status(modem_type: ModemTypeOption) -> dict:
    """Get modem status asynchronously."""
    from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

    result = {
        "type": modem_type.value,
        "connected": False,
        "healthy": False,
        "phone_number": None,
        "signal": None,
        "error": None,
    }

    try:
        if modem_type == ModemTypeOption.MOCK:
            config = MockModemConfig(phone_number="886480453")
            adapter = MockModemAdapter(config)
        elif modem_type == ModemTypeOption.PUPPETEER:
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number="886480453", headless=True)
                adapter = PuppeteerModemAdapter(config)
            except ImportError:
                result["error"] = "Pyppeteer not installed"
                return result
        else:  # AUTO
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number="886480453", headless=True)
                adapter = PuppeteerModemAdapter(config)
                result["type"] = "puppeteer"
            except ImportError:
                config = MockModemConfig(phone_number="886480453")
                adapter = MockModemAdapter(config)
                result["type"] = "mock"

        await adapter.connect()
        result["connected"] = adapter.is_connected
        result["phone_number"] = adapter.phone_number

        result["healthy"] = await adapter.health_check()
        result["signal"] = await adapter.get_signal_strength()

        await adapter.disconnect()

    except Exception as e:
        result["error"] = str(e)

    return result


@modem_app.command(name="status")
def status_command(
    modem: ModemTypeOption = typer.Option(
        ModemTypeOption.AUTO,
        "--modem",
        "-m",
        help="Modem type to check",
    ),
) -> None:
    """Check modem status and connectivity.

    Examples:
        eskimos modem status
        eskimos modem status --modem puppeteer
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking modem status...", total=None)
        status = asyncio.run(_get_modem_status(modem))

    # Build status display
    if status["error"]:
        console.print(Panel(
            f"[red]Error connecting to modem[/red]\n\n"
            f"[bold]Type:[/bold] {status['type']}\n"
            f"[bold]Error:[/bold] {status['error']}",
            title="[red]Modem Status[/red]",
            border_style="red",
        ))
        raise typer.Exit(1)

    # Success
    health_icon = "[green]HEALTHY[/green]" if status["healthy"] else "[yellow]DEGRADED[/yellow]"
    connected_icon = "[green]YES[/green]" if status["connected"] else "[red]NO[/red]"
    signal_str = f"{status['signal']}%" if status["signal"] else "N/A"

    console.print(Panel(
        f"[bold]Type:[/bold] {status['type']}\n"
        f"[bold]Phone:[/bold] {status['phone_number']}\n"
        f"[bold]Connected:[/bold] {connected_icon}\n"
        f"[bold]Health:[/bold] {health_icon}\n"
        f"[bold]Signal:[/bold] {signal_str}",
        title="[green]Modem Status[/green]",
        border_style="green",
    ))


@modem_app.command(name="test")
def test_command(
    phone: str = typer.Option(
        "886480453",
        "--phone",
        "-p",
        help="Phone number to send test SMS to",
    ),
    modem: ModemTypeOption = typer.Option(
        ModemTypeOption.MOCK,  # Default to mock for safety
        "--modem",
        "-m",
        help="Modem type to use",
    ),
) -> None:
    """Send a test SMS to verify modem is working.

    By default uses MOCK modem. Use --modem puppeteer for real hardware.

    Examples:
        eskimos modem test
        eskimos modem test --phone 123456789
        eskimos modem test --modem puppeteer --phone 123456789
    """
    from eskimos.cli.send import _send_sms, ModemTypeOption as SendModemType

    # Map modem type
    send_modem = SendModemType(modem.value)

    console.print(f"[bold]Testing modem ({modem.value})...[/bold]\n")

    test_message = f"Test SMS from Eskimos 2.0 [{modem.value}]"

    success = asyncio.run(_send_sms(
        recipient=phone,
        message=test_message,
        modem_type=send_modem,
        dry_run=False,
    ))

    if not success:
        raise typer.Exit(1)


@modem_app.command(name="list")
def list_command() -> None:
    """List available modem adapters.

    Shows all modem types that can be used with Eskimos.
    """
    table = Table(title="Available Modem Adapters")
    table.add_column("Type", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Description")

    # Mock is always available
    table.add_row(
        "mock",
        "[green]Available[/green]",
        "In-memory mock for testing",
    )

    # Check Puppeteer
    try:
        import pyppeteer  # noqa: F401
        puppeteer_status = "[green]Available[/green]"
    except ImportError:
        puppeteer_status = "[yellow]Not installed[/yellow]"

    table.add_row(
        "puppeteer",
        puppeteer_status,
        "IK41VE1 via browser automation",
    )

    # Dinstar (future)
    table.add_row(
        "dinstar",
        "[dim]Coming soon[/dim]",
        "Dinstar UC2000 via HTTP API",
    )

    console.print(table)
    console.print("\n[dim]Use --modem <type> to select adapter[/dim]")


@modem_app.command(name="receive")
def receive_command(
    modem: ModemTypeOption = typer.Option(
        ModemTypeOption.MOCK,
        "--modem",
        "-m",
        help="Modem type to use",
    ),
) -> None:
    """Receive pending SMS messages from modem.

    Examples:
        eskimos modem receive
        eskimos modem receive --modem puppeteer
    """
    async def _receive() -> list:
        from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

        if modem == ModemTypeOption.MOCK:
            config = MockModemConfig(phone_number="886480453")
            adapter = MockModemAdapter(config)
            # Simulate some incoming messages for demo
            adapter.simulate_incoming("123456789", "Hello from test!")
        elif modem == ModemTypeOption.PUPPETEER:
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number="886480453")
                adapter = PuppeteerModemAdapter(config)
            except ImportError:
                console.print("[red]Pyppeteer not installed[/red]")
                return []
        else:
            config = MockModemConfig(phone_number="886480453")
            adapter = MockModemAdapter(config)

        await adapter.connect()
        messages = await adapter.receive_sms()
        await adapter.disconnect()
        return messages

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Checking for messages...", total=None)
        messages = asyncio.run(_receive())

    if not messages:
        console.print("[dim]No pending messages[/dim]")
        return

    # Display messages
    table = Table(title=f"Received Messages ({len(messages)})")
    table.add_column("From", style="cyan")
    table.add_column("Content")
    table.add_column("Received", style="dim")

    for msg in messages:
        table.add_row(
            msg.sender,
            msg.content[:50] + ("..." if len(msg.content) > 50 else ""),
            msg.received_at.strftime("%H:%M:%S"),
        )

    console.print(table)
