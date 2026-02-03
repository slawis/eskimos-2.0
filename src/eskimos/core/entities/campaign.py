"""Campaign entity and related types."""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from eskimos.core.entities.sms import generate_key


class CampaignStatus(str, Enum):
    """Status of a campaign."""

    DRAFT = "draft"  # Being created/edited
    SCHEDULED = "scheduled"  # Ready to start at scheduled time
    RUNNING = "running"  # Currently sending
    PAUSED = "paused"  # Temporarily paused
    COMPLETED = "completed"  # All messages sent
    CANCELLED = "cancelled"  # Manually cancelled


class ConditionType(str, Enum):
    """Condition type for campaign step execution."""

    ALWAYS = "always"  # Always execute
    IF_NO_REPLY = "if_no_reply"  # Only if no reply to previous
    IF_POSITIVE = "if_positive"  # Only if positive sentiment
    IF_NEGATIVE = "if_negative"  # Only if negative sentiment
    IF_QUESTION = "if_question"  # Only if question detected


class CampaignStep(BaseModel):
    """A step in a campaign sequence (funnel).

    Represents one message in a multi-step SMS sequence.
    """

    step_number: int = Field(..., ge=1)
    message_template: str = Field(..., max_length=640)

    # Delay from previous step
    delay_hours: int = Field(default=0, ge=0)
    delay_days: int = Field(default=0, ge=0)

    # Execution conditions
    condition_type: ConditionType = ConditionType.ALWAYS
    condition_value: str | None = None

    # AI personalization
    use_ai_personalization: bool = False
    ai_style: str = "professional"  # professional, casual, formal

    @property
    def total_delay_seconds(self) -> int:
        """Total delay in seconds."""
        return (self.delay_days * 86400) + (self.delay_hours * 3600)


class CampaignSchedule(BaseModel):
    """Schedule configuration for a campaign.

    Controls when SMS messages can be sent.
    """

    start_date: datetime
    end_date: datetime | None = None

    # Sending hours (default 9:00-20:00 - legal requirement in Poland)
    send_time_start: time = Field(default=time(9, 0))
    send_time_end: time = Field(default=time(20, 0))

    # Allowed days (0=Monday, 6=Sunday)
    allowed_days: list[int] = Field(default=[0, 1, 2, 3, 4])  # Mon-Fri

    # Rate limiting per campaign
    max_sms_per_hour: int = Field(default=60, ge=1, le=500)
    max_sms_per_day: int = Field(default=500, ge=1, le=5000)

    # Jitter (random delays for human-like sending)
    min_delay_seconds: int = Field(default=30, ge=0)
    max_delay_seconds: int = Field(default=180, ge=0)

    def is_within_time_window(self, dt: datetime | None = None) -> bool:
        """Check if given time is within sending window."""
        if dt is None:
            dt = datetime.now()

        current_time = dt.time()
        current_day = dt.weekday()

        # Check day of week
        if current_day not in self.allowed_days:
            return False

        # Check time window
        if not (self.send_time_start <= current_time <= self.send_time_end):
            return False

        # Check date range
        if self.end_date and dt > self.end_date:
            return False

        return True


class Campaign(BaseModel):
    """SMS Campaign with sequence of steps.

    Represents a complete SMS marketing campaign with multiple steps (funnel).
    """

    id: str = Field(default_factory=lambda: f"camp_{generate_key()}")
    user_id: str
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    status: CampaignStatus = CampaignStatus.DRAFT

    # Sequence steps (funnel)
    steps: list[CampaignStep] = Field(default_factory=list)

    # Schedule
    schedule: CampaignSchedule

    # Contact list
    contact_list_id: str | None = None
    total_contacts: int = 0

    # Statistics
    sent_count: int = 0
    delivered_count: int = 0
    reply_count: int = 0
    conversion_count: int = 0
    unsubscribe_count: int = 0

    # AI settings
    enable_ai_replies: bool = False
    ai_reply_prompt: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Check if campaign is currently active."""
        return self.status in (CampaignStatus.RUNNING, CampaignStatus.SCHEDULED)

    @property
    def delivery_rate(self) -> float:
        """Calculate delivery rate percentage."""
        if self.sent_count == 0:
            return 0.0
        return (self.delivered_count / self.sent_count) * 100

    @property
    def reply_rate(self) -> float:
        """Calculate reply rate percentage."""
        if self.sent_count == 0:
            return 0.0
        return (self.reply_count / self.sent_count) * 100

    model_config = {"from_attributes": True}
