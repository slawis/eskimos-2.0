"""CLI command for running the Eskimos Dashboard server.

Usage:
    eskimos serve              # Start on localhost:8000
    eskimos serve --port 8080  # Custom port
    eskimos serve --host 0.0.0.0  # Accessible from network
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def serve_command(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        "-h",
        help="Host to bind to (use 0.0.0.0 for network access)",
    ),
    port: int = typer.Option(
        8000,
        "--port",
        "-p",
        help="Port to bind to",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        "-r",
        help="Enable auto-reload (development mode)",
    ),
) -> None:
    """Start the Eskimos Dashboard web server.

    Runs the FastAPI application with Uvicorn.

    Examples:
        eskimos serve                    # http://localhost:8000
        eskimos serve --port 8080        # http://localhost:8080
        eskimos serve --host 0.0.0.0     # Network accessible
        eskimos serve --reload           # Development mode
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] uvicorn not installed. "
            "Run: pip install uvicorn"
        )
        raise typer.Exit(1)

    # Show startup message
    console.print()
    console.print(
        Panel.fit(
            f"[bold green]Eskimos 2.0 Dashboard[/bold green]\n\n"
            f"Server:  http://{host}:{port}\n"
            f"API:     http://{host}:{port}/api/docs\n"
            f"Health:  http://{host}:{port}/api/health\n\n"
            f"[dim]Press Ctrl+C to stop[/dim]",
            title="🚀 Starting",
            border_style="green",
        )
    )
    console.print()

    # Run uvicorn
    uvicorn.run(
        "eskimos.api.main:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
        log_level="info",
    )
