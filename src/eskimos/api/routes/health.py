"""Health check endpoints."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from eskimos import __version__

router = APIRouter()

# Modem configuration from environment
MODEM_HOST = os.environ.get("MODEM_HOST", "192.168.1.1")
MODEM_PORT = int(os.environ.get("MODEM_PORT", "80"))
MODEM_PHONE = os.environ.get("MODEM_PHONE_NUMBER", "886480453")
MODEM_TYPE = os.environ.get("MODEM_TYPE", "puppeteer")
MODEM_PROBE_TIMEOUT = float(os.environ.get("MODEM_PROBE_TIMEOUT", "3.0"))


class ModemHealthInfo(BaseModel):
    """Modem connectivity details."""

    connected: bool = False
    phone_number: str = ""
    host: str = ""
    adapter_type: str = ""


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime
    modem_connected: bool = False
    modem: ModemHealthInfo = ModemHealthInfo()
    api_available: bool = True


async def probe_modem(host: str, port: int, timeout: float) -> bool:
    """Check if modem is reachable via TCP connection.

    When RNDIS modem is plugged into USB, its network adapter exists
    and the modem's web panel is reachable at its IP.
    When unplugged, the adapter disappears and connection fails.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check API health and modem connectivity.

    Probes the modem's IP address to determine if the USB modem
    is physically connected and reachable.
    """
    modem_reachable = await probe_modem(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)

    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.utcnow(),
        modem_connected=modem_reachable,
        modem=ModemHealthInfo(
            connected=modem_reachable,
            phone_number=MODEM_PHONE if modem_reachable else "",
            host=MODEM_HOST,
            adapter_type=MODEM_TYPE,
        ),
        api_available=True,
    )


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint."""
    return {"pong": True}
