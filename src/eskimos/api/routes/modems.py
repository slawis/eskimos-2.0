"""Modem management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ==================== Models ====================

class ModemInfo(BaseModel):
    """Modem information."""

    id: str
    phone_number: str
    modem_type: str
    name: str | None = None
    status: Literal["online", "offline", "busy", "error"] = "offline"
    signal_strength: int | None = None
    total_sent: int = 0
    total_received: int = 0
    last_activity: datetime | None = None


class ModemListResponse(BaseModel):
    """List of modems."""

    modems: list[ModemInfo]
    total: int


class ModemTestRequest(BaseModel):
    """Request to test modem."""

    phone: str | None = None  # If None, send to self
    message: str = "Test SMS from Eskimos 2.0"


class ModemTestResponse(BaseModel):
    """Modem test result."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    duration_ms: int | None = None


# ==================== Endpoints ====================

@router.get("", response_model=ModemListResponse)
async def list_modems() -> ModemListResponse:
    """List all configured modems.

    Returns list of modems with their current status.
    """
    # TODO: Get actual modem configuration
    # For now, return mock modem
    return ModemListResponse(
        modems=[
            ModemInfo(
                id="modem_ik41ve1",
                phone_number="886480453",
                modem_type="puppeteer",
                name="IK41VE1 (Laptop Finteo)",
                status="offline",  # TODO: Check actual status
            ),
            ModemInfo(
                id="modem_mock",
                phone_number="000000000",
                modem_type="mock",
                name="Mock (Testing)",
                status="online",
            ),
        ],
        total=2,
    )


@router.get("/{modem_id}", response_model=ModemInfo)
async def get_modem(modem_id: str) -> ModemInfo:
    """Get specific modem status.

    Returns detailed information about a single modem.
    """
    # TODO: Implement actual modem lookup
    if modem_id == "modem_ik41ve1":
        return ModemInfo(
            id="modem_ik41ve1",
            phone_number="886480453",
            modem_type="puppeteer",
            name="IK41VE1 (Laptop Finteo)",
            status="offline",
        )
    elif modem_id == "modem_mock":
        return ModemInfo(
            id="modem_mock",
            phone_number="000000000",
            modem_type="mock",
            name="Mock (Testing)",
            status="online",
        )
    else:
        raise HTTPException(status_code=404, detail="Modem not found")


@router.post("/{modem_id}/test", response_model=ModemTestResponse)
async def test_modem(modem_id: str, request: ModemTestRequest) -> ModemTestResponse:
    """Send test SMS through modem.

    Tests modem connectivity by sending a test message.
    """
    import time
    from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

    start_time = time.time()

    try:
        if modem_id == "modem_mock":
            config = MockModemConfig(phone_number="000000000")
            adapter = MockModemAdapter(config)
        elif modem_id == "modem_ik41ve1":
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number="886480453")
                adapter = PuppeteerModemAdapter(config)
            except ImportError:
                return ModemTestResponse(
                    success=False,
                    error="Puppeteer not available",
                )
        else:
            raise HTTPException(status_code=404, detail="Modem not found")

        await adapter.connect()

        # Send to self or specified number
        recipient = request.phone or adapter.phone_number
        result = await adapter.send_sms(recipient, request.message)

        await adapter.disconnect()

        duration_ms = int((time.time() - start_time) * 1000)

        return ModemTestResponse(
            success=result.success,
            message_id=result.message_id,
            error=result.error,
            duration_ms=duration_ms,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ModemTestResponse(
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        )


@router.get("/{modem_id}/signal")
async def get_signal_strength(modem_id: str) -> dict:
    """Get modem signal strength.

    Returns current GSM signal strength if available.
    """
    # TODO: Implement actual signal check
    return {
        "modem_id": modem_id,
        "signal_strength": None,
        "message": "Signal strength not available for this modem type",
    }
