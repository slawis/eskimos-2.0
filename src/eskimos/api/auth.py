"""API Key authentication for Eskimos 2.0 REST API.

Protects SMS and modem endpoints with X-API-Key header validation.
Dashboard and health endpoints remain public.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from eskimos.infrastructure.config import get_settings

# Header name for API key
API_KEY_HEADER = APIKeyHeader(name="x-api-key", auto_error=False)


async def require_api_key(
    api_key: str | None = Security(API_KEY_HEADER),
) -> str:
    """Validate API key from X-API-Key header.

    Raises:
        HTTPException: 401 if key is missing or invalid.

    Returns:
        The validated API key.
    """
    settings = get_settings()
    expected_key = settings.eskimos_api_key

    if not expected_key:
        # No key configured = auth disabled (development mode)
        return "dev-mode"

    if not api_key or api_key != expected_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Provide X-API-Key header.",
        )

    return api_key
