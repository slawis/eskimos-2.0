"""SMS management endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


# ==================== Request/Response Models ====================

class SendSMSRequest(BaseModel):
    """Request to send SMS."""

    recipient: str = Field(..., min_length=9, max_length=12, description="Phone number")
    message: str = Field(..., max_length=640, description="SMS content")
    modem_type: Literal["mock", "puppeteer", "auto"] = "auto"


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
async def send_sms(request: SendSMSRequest) -> SendSMSResponse:
    """Send an SMS message.

    Sends SMS through the configured modem adapter.
    """
    from eskimos.adapters.modem.mock import MockModemAdapter, MockModemConfig

    try:
        # For now, use mock adapter
        # TODO: Select adapter based on modem_type and config
        if request.modem_type == "mock" or request.modem_type == "auto":
            config = MockModemConfig(phone_number="886480453")
            adapter = MockModemAdapter(config)
        elif request.modem_type == "puppeteer":
            try:
                from eskimos.adapters.modem.puppeteer import (
                    PuppeteerModemAdapter,
                    PuppeteerConfig,
                )
                config = PuppeteerConfig(phone_number="886480453")
                adapter = PuppeteerModemAdapter(config)
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="Puppeteer adapter not available",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown modem type: {request.modem_type}",
            )

        # Send SMS
        await adapter.connect()
        result = await adapter.send_sms(request.recipient, request.message)
        await adapter.disconnect()

        return SendSMSResponse(
            success=result.success,
            message_id=result.message_id,
            error=result.error,
            sent_at=result.sent_at,
            modem_used=result.modem_number,
        )

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
