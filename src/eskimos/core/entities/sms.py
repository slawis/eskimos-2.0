"""SMS entity and related types."""

from __future__ import annotations

import re
import secrets
import string
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def generate_key(length: int = 20) -> str:
    """Generate a random alphanumeric key."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class SMSStatus(str, Enum):
    """Status of an SMS message."""

    PENDING = "pending"  # Waiting in queue
    QUEUED = "queued"  # Added to modem queue
    SENDING = "sending"  # Being sent by modem
    SENT = "sent"  # Successfully sent
    DELIVERED = "delivered"  # Delivery confirmed (if supported)
    FAILED = "failed"  # Failed to send


class SMSDirection(str, Enum):
    """Direction of SMS message."""

    OUTBOUND = "outbound"  # Sent by us
    INBOUND = "inbound"  # Received from external


class SMS(BaseModel):
    """SMS message entity.

    Represents a single SMS message in the system, either outbound or inbound.
    """

    id: str = Field(default_factory=lambda: f"sms_{generate_key()}")
    direction: SMSDirection
    status: SMSStatus = SMSStatus.PENDING

    # Phone numbers (9 digits, Polish format)
    sender: str = Field(..., min_length=9, max_length=12)
    recipient: str = Field(..., min_length=9, max_length=12)

    # Content
    content: str = Field(..., max_length=640)
    content_original: str | None = None  # Before AI personalization

    # Campaign relations
    campaign_id: str | None = None
    campaign_step: int | None = None
    conversation_id: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None

    # Metadata
    modem_number: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sender", "recipient", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Normalize phone number to 9 digits."""
        if not isinstance(v, str):
            v = str(v)

        # Remove all non-digits
        cleaned = re.sub(r"[^\d]", "", v)

        # Remove Polish country code if present
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]
        elif cleaned.startswith("+48"):
            cleaned = cleaned[3:]

        if len(cleaned) != 9:
            raise ValueError(f"Invalid phone number: {v} (must be 9 digits)")

        return cleaned

    model_config = {"from_attributes": True}


class IncomingSMS(BaseModel):
    """Incoming SMS from modem.

    Represents a raw SMS received from the GSM modem before processing.
    """

    sender: str
    recipient: str  # Modem's phone number
    content: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    raw_data: dict[str, Any] | None = None

    @field_validator("sender", "recipient", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Normalize phone number."""
        if not isinstance(v, str):
            v = str(v)
        cleaned = re.sub(r"[^\d]", "", v)
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]
        return cleaned


class SMSResult(BaseModel):
    """Result of sending an SMS.

    Returned by modem adapters after attempting to send.
    """

    success: bool
    message_id: str | None = None
    error: str | None = None
    sent_at: datetime | None = None
    modem_number: str

    @property
    def failed(self) -> bool:
        """Check if sending failed."""
        return not self.success
