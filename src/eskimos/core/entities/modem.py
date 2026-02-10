"""Modem entity and related types."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from eskimos.core.entities.sms import generate_key


class ModemType(str, Enum):
    """Type of modem/gateway."""

    IK41VE1_PUPPETEER = "ik41ve1_puppeteer"  # Legacy modem via Puppeteer
    DINSTAR_HTTP = "dinstar_http"  # Dinstar UC2000 via HTTP API
    SIM7600_SERIAL = "sim7600_serial"  # SIM7600G-H via AT commands (serial)
    MOCK = "mock"  # Mock modem for testing


class ModemStatus(str, Enum):
    """Operational status of modem."""

    ONLINE = "online"  # Ready to send/receive
    OFFLINE = "offline"  # Not connected
    BUSY = "busy"  # Currently processing
    ERROR = "error"  # Error state
    MAINTENANCE = "maintenance"  # Under maintenance


class ModemHealthStatus(str, Enum):
    """Health status of modem."""

    HEALTHY = "healthy"  # All good
    DEGRADED = "degraded"  # Working but with issues
    UNHEALTHY = "unhealthy"  # Not working properly
    UNKNOWN = "unknown"  # Status not determined


class Modem(BaseModel):
    """GSM Modem configuration and state.

    Represents a physical modem/gateway device.
    """

    id: str = Field(default_factory=lambda: f"mod_{generate_key()}")
    phone_number: str = Field(..., min_length=9, max_length=12)
    modem_type: ModemType
    name: str | None = None  # Human-readable name

    # Connection settings
    host: str = "192.168.1.1"
    port: int = 80
    username: str | None = None
    password: str | None = None

    # State
    is_active: bool = True
    status: ModemStatus = ModemStatus.OFFLINE
    health_status: ModemHealthStatus = ModemHealthStatus.UNKNOWN
    signal_strength: int | None = None  # 0-100

    # Statistics
    total_sent: int = 0
    total_received: int = 0
    total_errors: int = 0
    last_activity_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_message: str | None = None

    # Rate limiting per modem
    max_sms_per_hour: int = Field(default=30, ge=1, le=100)
    current_hour_count: int = 0
    hour_reset_at: datetime | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """Normalize phone number."""
        if not isinstance(v, str):
            v = str(v)
        cleaned = re.sub(r"[^\d]", "", v)
        if cleaned.startswith("48") and len(cleaned) == 11:
            cleaned = cleaned[2:]
        return cleaned

    @property
    def display_name(self) -> str:
        """Get display name."""
        if self.name:
            return f"{self.name} ({self.phone_number})"
        return self.phone_number

    @property
    def is_available(self) -> bool:
        """Check if modem is available for sending."""
        if not self.is_active:
            return False
        if self.status not in (ModemStatus.ONLINE,):
            return False
        if self.current_hour_count >= self.max_sms_per_hour:
            return False
        return True

    @property
    def utilization_percent(self) -> float:
        """Get current hour utilization percentage."""
        if self.max_sms_per_hour == 0:
            return 100.0
        return (self.current_hour_count / self.max_sms_per_hour) * 100

    def record_send(self) -> None:
        """Record a successful send."""
        self.total_sent += 1
        self.current_hour_count += 1
        self.last_activity_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def record_receive(self) -> None:
        """Record a received SMS."""
        self.total_received += 1
        self.last_activity_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def record_error(self, message: str) -> None:
        """Record an error."""
        self.total_errors += 1
        self.last_error_at = datetime.utcnow()
        self.last_error_message = message
        self.updated_at = datetime.utcnow()

    def reset_hour_count(self) -> None:
        """Reset hourly counter."""
        self.current_hour_count = 0
        self.hour_reset_at = datetime.utcnow()

    model_config = {"from_attributes": True}


class ModemPool(BaseModel):
    """Pool of modems for load balancing.

    Manages multiple modems for round-robin sending.
    """

    id: str = Field(default_factory=lambda: f"pool_{generate_key()}")
    name: str
    modems: list[Modem] = Field(default_factory=list)

    # Round-robin state
    current_index: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def available_modems(self) -> list[Modem]:
        """Get list of available modems."""
        return [m for m in self.modems if m.is_available]

    @property
    def total_capacity_per_hour(self) -> int:
        """Total SMS capacity per hour."""
        return sum(m.max_sms_per_hour for m in self.modems if m.is_active)

    def get_next_modem(self) -> Modem | None:
        """Get next available modem (round-robin)."""
        available = self.available_modems
        if not available:
            return None

        # Simple round-robin
        self.current_index = (self.current_index + 1) % len(available)
        return available[self.current_index]

    def get_least_used_modem(self) -> Modem | None:
        """Get modem with lowest current hour usage."""
        available = self.available_modems
        if not available:
            return None

        return min(available, key=lambda m: m.current_hour_count)

    model_config = {"from_attributes": True}
