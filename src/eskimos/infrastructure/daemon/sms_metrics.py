"""SMS metrics - counters, rate limiting, storage monitoring state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SmsMetrics:
    """Mutable SMS metrics container - shared by all SMS services.

    Replaces 12+ module-level globals from original daemon.py.
    """

    # Send counters
    sent_today: int = 0
    sent_total: int = 0
    hourly_count: int = 0
    hourly_reset_time: float = field(default_factory=time.time)
    rate_limited: bool = False

    # Receive counters
    received_today: int = 0
    received_total: int = 0

    # Error tracking
    last_error: str = ""
    modem_ref: object = None  # Optional modem adapter reference

    # Storage monitoring
    storage_used: int = 0
    storage_max: int = 100
    auto_reset_in_progress: bool = False

    def check_rate_limit(self, daily_limit: int, hourly_limit: int) -> tuple:
        """Check if SMS sending is within rate limits.

        Returns (allowed: bool, reason: str).
        """
        now = time.time()

        # Reset hourly counter every hour
        if now - self.hourly_reset_time >= 3600:
            self.hourly_count = 0
            self.hourly_reset_time = now

        # Check daily limit
        if self.sent_today >= daily_limit:
            self.rate_limited = True
            return False, f"Daily limit reached: {self.sent_today}/{daily_limit}"

        # Check hourly limit
        if self.hourly_count >= hourly_limit:
            self.rate_limited = True
            return False, f"Hourly limit reached: {self.hourly_count}/{hourly_limit}"

        self.rate_limited = False
        return True, ""

    def record_sent(self) -> None:
        """Record a successfully sent SMS."""
        self.sent_today += 1
        self.sent_total += 1
        self.hourly_count += 1
        self.last_error = ""

    def record_received(self) -> None:
        """Record a received SMS."""
        self.received_today += 1
        self.received_total += 1

    def record_error(self, error: str) -> None:
        """Record an SMS error."""
        self.last_error = error

    def to_heartbeat_dict(self) -> dict:
        """Format metrics for heartbeat payload."""
        return {
            "sms_sent_today": self.sent_today,
            "sms_sent_total": self.sent_total,
            "sms_received_today": self.received_today,
            "sms_received_total": self.received_total,
            "sms_hourly_count": self.hourly_count,
            "sms_rate_limited": self.rate_limited,
            "sms_last_error": self.last_error,
            "sms_storage_used": self.storage_used,
            "sms_storage_max": self.storage_max,
            "sms_auto_reset_in_progress": self.auto_reset_in_progress,
        }
