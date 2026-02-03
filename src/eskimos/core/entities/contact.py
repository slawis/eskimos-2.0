"""Contact entity and related types."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from eskimos.core.entities.sms import generate_key


class ContactStatus(str, Enum):
    """Status of a contact."""

    ACTIVE = "active"  # Can receive SMS
    BLACKLISTED = "blacklisted"  # On blacklist (STOP received)
    UNSUBSCRIBED = "unsubscribed"  # Opted out
    INVALID = "invalid"  # Invalid phone number
    BOUNCED = "bounced"  # SMS couldn't be delivered


class InterestLevel(str, Enum):
    """Interest level detected by AI."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"
    UNKNOWN = "unknown"


class Contact(BaseModel):
    """Contact (SMS recipient) entity.

    Represents a person who can receive SMS messages.
    """

    id: str = Field(default_factory=lambda: f"cont_{generate_key()}")
    phone: str = Field(..., min_length=9, max_length=12)
    status: ContactStatus = ContactStatus.ACTIVE

    # Personal data (for personalization)
    name: str | None = None
    company: str | None = None
    position: str | None = None
    email: str | None = None

    # Custom fields for personalization
    custom_fields: dict[str, str] = Field(default_factory=dict)

    # Campaign state
    current_campaign_id: str | None = None
    current_step: int = 0
    last_contact_at: datetime | None = None

    # History counters
    total_sms_received: int = 0
    total_sms_sent: int = 0
    total_replies: int = 0

    # AI analysis
    sentiment_score: float | None = None  # -1.0 to 1.0
    interest_level: InterestLevel = InterestLevel.UNKNOWN
    ai_notes: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    source: str | None = None  # Where contact came from
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Normalize phone number to 9 digits."""
        if not isinstance(v, str):
            v = str(v)
        cleaned = re.sub(r"[^\d]", "", v)
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]
        if len(cleaned) != 9:
            raise ValueError(f"Invalid phone number: {v}")
        return cleaned

    @property
    def display_name(self) -> str:
        """Get display name or phone if no name."""
        if self.name:
            return self.name
        return self.phone

    @property
    def can_receive_sms(self) -> bool:
        """Check if contact can receive SMS."""
        return self.status == ContactStatus.ACTIVE

    model_config = {"from_attributes": True}


class BlacklistReason(str, Enum):
    """Reason for blacklisting a number."""

    KEYWORD_STOP = "keyword_stop"  # Sent STOP/NIE DZWON
    USER_REQUEST = "user_request"  # Manual request
    MANUAL = "manual"  # Admin added
    BOUNCE = "bounce"  # SMS couldn't be delivered
    COMPLAINT = "complaint"  # Reported as spam


class Blacklist(BaseModel):
    """Blacklist entry.

    Numbers on blacklist will never receive SMS.
    """

    id: str = Field(default_factory=lambda: f"bl_{generate_key()}")
    phone: str = Field(..., min_length=9, max_length=12)
    reason: BlacklistReason
    reason_detail: str | None = None

    # Source tracking
    source_campaign_id: str | None = None
    source_sms_id: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str | None = None  # User ID who added

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Normalize phone number."""
        if not isinstance(v, str):
            v = str(v)
        cleaned = re.sub(r"[^\d]", "", v)
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]
        return cleaned

    model_config = {"from_attributes": True}


# STOP keywords (Polish)
STOP_KEYWORDS = frozenset([
    "stop",
    "koniec",
    "nie dzwon",
    "niedzwon",
    "wypisz",
    "rezygnuje",
    "rezygnuję",
    "nie pisz",
    "niepisz",
    "usun",
    "usuń",
    "anuluj",
])


def is_stop_message(content: str) -> bool:
    """Check if message content contains STOP keyword."""
    content_lower = content.lower().strip()

    # Exact match
    if content_lower in STOP_KEYWORDS:
        return True

    # Contains keyword (for messages like "STOP prosze")
    for keyword in STOP_KEYWORDS:
        if keyword in content_lower:
            return True

    return False
