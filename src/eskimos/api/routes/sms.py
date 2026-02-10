"""SMS management endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Form
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# ==================== Helpers ====================

_env_loaded = False


def _load_env_file():
    """Load config/.env into os.environ (for Gateway process)."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    from pathlib import Path
    env_file = Path(__file__).parent.parent.parent.parent / "config" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _get_env(key: str, default: str = "") -> str:
    """Get env var, loading config/.env first if needed."""
    _load_env_file()
    return os.getenv(key, default)


_cached_serial_port = None


def _auto_detect_serial_port() -> str | None:
    """Auto-detect SIMCOM serial port."""
    global _cached_serial_port
    if _cached_serial_port:
        return _cached_serial_port
    try:
        import serial
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if "SIMCOM" in desc or "SIM7600" in desc:
                if "AT" in desc:  # prefer AT PORT
                    _cached_serial_port = p.device
                    return p.device
        # fallback: any SIMCOM port
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if "SIMCOM" in desc or "SIM7600" in desc:
                _cached_serial_port = p.device
                return p.device
    except ImportError:
        pass
    return None


# ==================== Request/Response Models ====================

class SendSMSRequest(BaseModel):
    """Request to send SMS."""

    recipient: str = Field(..., min_length=9, max_length=12, description="Phone number")
    message: str = Field(..., max_length=640, description="SMS content")
    modem_type: Literal["mock", "puppeteer", "serial", "auto"] = "auto"


class SendSMSResponse(BaseModel):
    """Response after sending SMS."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    sent_at: datetime | None = None
    modem_used: str | None = None


class SMSHistoryItem(BaseModel):
    """SMS history item."""

    id: str
    recipient: str
    content: str
    status: str
    sent_at: datetime | None = None
    modem: str | None = None


class SMSHistoryResponse(BaseModel):
    """SMS history response."""

    items: list[SMSHistoryItem]
    total: int
    page: int
    per_page: int


# ==================== Endpoints ====================

@router.post("/send", response_model=SendSMSResponse)
async def send_sms(
    recipient: str = Form(...),
    message: str = Form(...),
    modem_type: str = Form("auto"),
) -> SendSMSResponse:
    """Send an SMS message.

    Sends SMS through the configured modem adapter.
    """
    from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

    try:
        modem_phone = os.getenv("MODEM_PHONE_NUMBER", "886480453")

        if modem_type == "serial" or (modem_type == "auto" and _get_env("MODEM_TYPE") == "serial"):
            try:
                from eskimos.adapters.modem.serial_at import (
                    SerialModemAdapter,
                    SerialModemConfig,
                )
                serial_port = _get_env("SERIAL_PORT") or "auto"
                if serial_port == "auto":
                    serial_port = _auto_detect_serial_port()
                if not serial_port:
                    raise HTTPException(
                        status_code=500,
                        detail="Serial modem not found - no SIMCOM COM port detected",
                    )
                config = SerialModemConfig(
                    phone_number=modem_phone,
                    port=serial_port,
                )
                adapter = SerialModemAdapter(config)
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="Serial adapter not available (pyserial missing)",
                )
        elif modem_type == "mock" or modem_type == "auto":
            config = MockModemConfig(phone_number=modem_phone)
            adapter = MockModemAdapter(config)
        elif modem_type == "puppeteer":
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number=modem_phone)
                adapter = PuppeteerModemAdapter(config)
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="Puppeteer adapter not available",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown modem type: {modem_type}",
            )

        # Send SMS
        await adapter.connect()
        result = await adapter.send_sms(recipient, message)
        await adapter.disconnect()

        response = SendSMSResponse(
            success=result.success,
            message_id=result.message_id,
            error=result.error,
            sent_at=result.sent_at,
            modem_used=result.modem_number,
        )

        # Webhook callback to NinjaBot (fire-and-forget)
        webhook_url = os.getenv("NINJABOT_WEBHOOK_URL")
        webhook_key = os.getenv("ESKIMOS_API_KEY", "")
        if webhook_url and result.success:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{webhook_url}/api/webhooks/eskimos/sms-status",
                        json={
                            "message_id": result.message_id,
                            "status": "sent",
                            "recipient": recipient,
                            "sent_at": result.sent_at.isoformat() if result.sent_at else None,
                        },
                        headers={"x-api-key": webhook_key},
                    )
            except Exception as wh_err:
                logger.warning(f"Webhook callback failed: {wh_err}")

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=SMSHistoryResponse)
async def get_sms_history(
    page: int = 1,
    per_page: int = 20,
) -> SMSHistoryResponse:
    """Get SMS sending history.

    Returns paginated list of sent SMS messages.
    """
    # TODO: Implement actual history from database
    # For now, return empty list
    return SMSHistoryResponse(
        items=[],
        total=0,
        page=page,
        per_page=per_page,
    )


@router.get("/incoming")
async def get_incoming_sms(limit: int = 50) -> dict:
    """Get incoming SMS messages.

    Returns list of received SMS from modem inbox.
    """
    # TODO: Implement receiving from actual modem
    return {
        "items": [],
        "total": 0,
    }
