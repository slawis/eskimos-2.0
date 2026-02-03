"""Health check endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from eskimos import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime
    modem_connected: bool = False
    api_available: bool = True


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check API health and status.

    Returns basic health information including version and connectivity status.
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.utcnow(),
        modem_connected=False,  # TODO: Check actual modem status
        api_available=True,
    )


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint."""
    return {"pong": True}
