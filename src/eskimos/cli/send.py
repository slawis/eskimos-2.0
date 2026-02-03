"""Send SMS command for Eskimos CLI.

Usage:
    eskimos send 123456789 "Hello World"
    eskimos send 123456789 "Test" --dry-run
    eskimos send 123456789 "Test" --modem mock
"""

from __future__ import annotations

import asyncio
from enum import Enum

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


class ModemTypeOption(str, Enum):
    """Available modem types for CLI."""

    PUPPETEER = "puppeteer"
    MOCK = "mock"
    AUTO = "auto"


async def _send_sms(
    recipient: str,
    message: str,
    modem_type: ModemTypeOption,
    dry_run: bool,
) -> bool:
    """Internal async function to send SMS."""
    from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

    # Dry run - just show what would happen
    if dry_run:
        console.print(Panel(
            f"[bold]Recipient:[/bold] {recipient}\n"
            f"[bold]Message:[/bold] {message}\n"
            f"[bold]Modem:[/bold] {modem_type.value}\n"
            f"[bold]Length:[/bold] {len(message)} chars",
            title="[yellow]DRY RUN[/yellow]",
            border_style="yellow",
        ))
        return True

    # Select adapter based on modem type
    if modem_type == ModemTypeOption.MOCK:
        config = MockModemConfig(phone_number="886480453")
        adapter = MockModemAdapter(config)
    elif modem_type == ModemTypeOption.PUPPETEER:
        try:
            from eskimos.adapters.modem.puppeteer import (
                PuppeteerModemAdapter,
                PuppeteerConfig,
            )
            config = PuppeteerConfig(phone_number="886480453")
            adapter = PuppeteerModemAdapter(config)
        except ImportError:
            console.print("[red]Puppeteer not available. Install pyppeteer.[/red]")
            return False
    else:  # AUTO - try puppeteer, fall back to mock
        try:
            from eskimos.adapters.modem.puppeteer import (
                PuppeteerModemAdapter,
                PuppeteerConfig,
            )
            config = PuppeteerConfig(phone_number="886480453")
            adapter = PuppeteerModemAdapter(config)
        except ImportError:
            console.print("[yellow]Puppeteer not available, using mock.[/yellow]")
            config = MockModemConfig(phone_number="886480453")
            adapter = MockModemAdapter(config)

    # Send with progress indicator
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Connecting to modem...", total=None)

        try:
            await adapter.connect()
            progress.update(task, description="Sending SMS...")

            result = await adapter.send_sms(recipient, message)

            await adapter.disconnect()

            if result.success:
                console.print(Panel(
                    f"[green]SMS sent successfully![/green]\n\n"
                    f"[bold]Message ID:[/bold] {result.message_id}\n"
                    f"[bold]Recipient:[/bold] {recipient}\n"
                    f"[bold]Sent at:[/bold] {result.sent_at}",
                    title="[green]Success[/green]",
                    border_style="green",
                ))
                return True
            else:
                console.print(Panel(
                    f"[red]Failed to send SMS[/red]\n\n"
                    f"[bold]Error:[/bold] {result.error}",
                    title="[red]Error[/red]",
                    border_style="red",
                ))
                return False

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return False


def send_command(
    recipient: str = typer.Argument(
        ...,
        help="Recipient phone number (9 digits)",
    ),
    message: str = typer.Argument(
        ...,
        help="SMS message content (max 640 chars)",
    ),
    modem: ModemTypeOption = typer.Option(
        ModemTypeOption.AUTO,
        "--modem",
        "-m",
        help="Modem type to use",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be sent without actually sending",
    ),
) -> None:
    """Send an SMS message.

    Examples:
        eskimos send 123456789 "Hello World"
        eskimos send 123456789 "Test message" --dry-run
        eskimos send 123456789 "Test" --modem mock
    """
    # Validate recipient
    if len(recipient) != 9 or not recipient.isdigit():
        console.print("[red]Error: Recipient must be 9 digits[/red]")
        raise typer.Exit(1)

    # Validate message length
    if len(message) > 640:
        console.print(f"[red]Error: Message too long ({len(message)} > 640)[/red]")
        raise typer.Exit(1)

    # Run async send
    success = asyncio.run(_send_sms(recipient, message, modem, dry_run))

    if not success:
        raise typer.Exit(1)
