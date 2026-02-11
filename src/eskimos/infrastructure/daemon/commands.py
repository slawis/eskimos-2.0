"""Command polling - poll pending commands from central server and dispatch."""

from __future__ import annotations

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log

# Lazy httpx
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


class CommandPoller:
    """Poll commands from central API, dispatch to handlers, acknowledge."""

    def __init__(self, config: DaemonConfig) -> None:
        self.config = config

    async def poll(self, client_key: str) -> list:
        """Poll pending commands from central server."""
        if not HAS_HTTPX:
            return []

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.config.central_api}/commands/{client_key}",
                    headers={
                        "X-Client-Key": client_key,
                        "X-API-Key": self.config.api_key,
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    commands = data.get("commands", [])
                    if commands:
                        log(
                            f"Received {len(commands)} command(s)",
                            self.config.log_file,
                        )
                    return commands

        except Exception as e:
            log(f"Command poll error: {e}", self.config.log_file)

        return []

    async def acknowledge(
        self,
        client_key: str,
        command_id: str,
        success: bool,
        error: str = None,
        result: dict = None,
    ) -> None:
        """Acknowledge command execution with optional result data."""
        if not HAS_HTTPX:
            return

        try:
            payload = {"success": success, "error": error}
            if result is not None:
                payload["result"] = result

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.config.central_api}/commands/{command_id}/ack",
                    json=payload,
                    headers={
                        "X-Client-Key": client_key,
                        "X-API-Key": self.config.api_key,
                    },
                    timeout=10.0,
                )
        except Exception as e:
            log(f"Command ack error: {e}", self.config.log_file)
