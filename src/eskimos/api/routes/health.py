"""Health check endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from eskimos import __version__

router = APIRouter()
logger = logging.getLogger(__name__)

# Modem configuration from environment
MODEM_HOST = os.environ.get("MODEM_HOST", "192.168.1.1")
MODEM_PORT = int(os.environ.get("MODEM_PORT", "80"))
MODEM_PHONE = os.environ.get("MODEM_PHONE_NUMBER", "886480453")
MODEM_TYPE = os.environ.get("MODEM_TYPE", "puppeteer")
MODEM_PROBE_TIMEOUT = float(os.environ.get("MODEM_PROBE_TIMEOUT", "3.0"))

# Cache for hardware detection
_modem_hw_cache: Optional[dict] = None


class ModemHealthInfo(BaseModel):
    """Modem connectivity details."""

    connected: bool = False
    phone_number: str = ""
    host: str = ""
    adapter_type: str = ""
    model: str = ""
    manufacturer: str = ""
    connection_type: str = ""


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime
    modem_connected: bool = False
    modem: ModemHealthInfo = ModemHealthInfo()
    api_available: bool = True


def detect_modem_hardware() -> dict:
    """Detect USB modem model and manufacturer from Windows PnP devices.

    Uses wmic to scan network adapters for RNDIS/modem devices.
    Wrapped in try/except at every level - safe for Windows Service context.
    """
    global _modem_hw_cache
    if _modem_hw_cache is not None:
        return _modem_hw_cache

    result = {"model": "", "manufacturer": "", "connection_type": ""}

    if sys.platform != "win32":
        _modem_hw_cache = result
        return result

    try:
        import subprocess

        # Query network adapters for RNDIS/modem devices
        proc = subprocess.run(
            ["wmic", "nic", "get", "Name,Manufacturer,PNPDeviceID", "/format:csv"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().split("\n"):
                line_lower = line.lower()
                if any(kw in line_lower for kw in ["ndis", "rndis", "mobile", "lte", "4g", "gsm"]):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        result["manufacturer"] = parts[1] or ""
                        result["connection_type"] = "RNDIS/USB"
                        adapter_name = parts[2]
                        if adapter_name:
                            result["model"] = adapter_name
                        break

        # Try to get more detailed USB device info
        if result["manufacturer"] or result["model"]:
            try:
                proc2 = subprocess.run(
                    ["wmic", "path", "Win32_PnPEntity", "where",
                     "Name like '%NDIS%' or Name like '%RNDIS%' or Name like '%Mobile%' or Name like '%LTE%'",
                     "get", "Name,Manufacturer,Description", "/format:csv"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000,
                )
                if proc2.returncode == 0:
                    for line in proc2.stdout.strip().split("\n"):
                        line_lower = line.lower()
                        if any(kw in line_lower for kw in ["ndis", "rndis", "mobile", "lte"]):
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) >= 4:
                                if parts[2]:
                                    result["manufacturer"] = parts[2]
                                if parts[3]:
                                    result["model"] = parts[3]
                            break
            except Exception:
                pass  # PnP query is optional

    except Exception as e:
        logger.warning(f"Modem hardware detection failed: {e}")

    _modem_hw_cache = result
    return result


def clear_modem_cache() -> None:
    """Clear hardware cache (call when modem might have changed)."""
    global _modem_hw_cache
    _modem_hw_cache = None


async def probe_modem(host: str, port: int, timeout: float) -> bool:
    """Check if modem is reachable via TCP connection."""
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
    """Check API health, modem connectivity and hardware info."""
    modem_reachable = await probe_modem(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)

    try:
        hw = detect_modem_hardware()
    except Exception:
        hw = {"model": "", "manufacturer": "", "connection_type": ""}

    if not modem_reachable:
        clear_modem_cache()

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
            model=hw.get("model", ""),
            manufacturer=hw.get("manufacturer", ""),
            connection_type=hw.get("connection_type", ""),
        ),
        api_available=True,
    )


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint."""
    return {"pong": True}
