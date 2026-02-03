"""Mock modem adapter for testing.

Provides an in-memory implementation of ModemAdapter for unit tests
and development without real hardware.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from eskimos.adapters.modem.base import BaseModemAdapter, ModemSendError
from eskimos.core.entities.modem import ModemStatus
from eskimos.core.entities.sms import IncomingSMS, SMSResult, generate_key


@dataclass
class MockModemConfig:
    """Configuration for mock modem."""

    phone_number: str = "886480453"

    # Simulate failures
    success_rate: float = 1.0  # 0.0 to 1.0
    fail_on_numbers: list[str] = field(default_factory=list)

    # Simulate delays
    min_send_delay_ms: int = 100
    max_send_delay_ms: int = 500

    # Auto-reply simulation
    auto_reply_enabled: bool = False
    auto_reply_content: str = "Auto-reply from mock"


class MockModemAdapter(BaseModemAdapter):
    """Mock modem adapter for testing.

    Features:
    - In-memory message queue
    - Configurable success/failure rates
    - Simulated delays
    - Auto-reply simulation
    - Full tracking of sent/received messages

    Example:
        config = MockModemConfig(phone_number="123456789", success_rate=0.9)
        adapter = MockModemAdapter(config)
        await adapter.connect()

        # Send SMS (may fail 10% of time)
        result = await adapter.send_sms("987654321", "Test message")

        # Simulate incoming SMS
        adapter.simulate_incoming("555555555", "Hello!")
        incoming = await adapter.receive_sms()
    """

    def __init__(self, config: MockModemConfig | None = None):
        self.config = config or MockModemConfig()
        super().__init__(
            phone_number=self.config.phone_number,
            host="mock",
            port=0,
        )

        # Message queues
        self._outbox: list[dict] = []  # Sent messages
        self._inbox: deque[IncomingSMS] = deque()  # Received messages
        self._signal_strength = 75

    @property
    def outbox(self) -> list[dict]:
        """Get list of sent messages (for testing assertions)."""
        return self._outbox.copy()

    @property
    def inbox_size(self) -> int:
        """Number of pending incoming messages."""
        return len(self._inbox)

    async def connect(self) -> None:
        """Connect (no-op for mock)."""
        self._connected = True
        self._status = ModemStatus.ONLINE

    async def disconnect(self) -> None:
        """Disconnect (no-op for mock)."""
        self._connected = False
        self._status = ModemStatus.OFFLINE

    async def send_sms(
        self,
        recipient: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> SMSResult:
        """Send SMS (simulated).

        Simulates sending with configurable success rate and delays.
        """
        if not self._connected:
            raise ModemSendError("Not connected", self.phone_number)

        # Simulate network delay
        delay = random.randint(
            self.config.min_send_delay_ms,
            self.config.max_send_delay_ms,
        )
        await asyncio.sleep(delay / 1000)

        # Check if should fail
        should_fail = (
            random.random() > self.config.success_rate
            or recipient in self.config.fail_on_numbers
        )

        if should_fail:
            self._outbox.append({
                "recipient": recipient,
                "message": message,
                "success": False,
                "timestamp": datetime.utcnow(),
            })
            return SMSResult(
                success=False,
                message_id=None,
                error="Simulated failure",
                sent_at=None,
                modem_number=self.phone_number,
            )

        # Success
        message_id = f"mock_{generate_key(10)}"
        sent_at = datetime.utcnow()

        self._outbox.append({
            "recipient": recipient,
            "message": message,
            "message_id": message_id,
            "success": True,
            "timestamp": sent_at,
        })

        # Simulate auto-reply
        if self.config.auto_reply_enabled:
            self._inbox.append(IncomingSMS(
                sender=recipient,
                recipient=self.phone_number,
                content=self.config.auto_reply_content,
                received_at=datetime.utcnow(),
            ))

        return SMSResult(
            success=True,
            message_id=message_id,
            error=None,
            sent_at=sent_at,
            modem_number=self.phone_number,
        )

    async def receive_sms(self) -> list[IncomingSMS]:
        """Receive pending SMS messages."""
        if not self._connected:
            return []

        messages = list(self._inbox)
        self._inbox.clear()
        return messages

    async def health_check(self) -> bool:
        """Check health (always True for mock when connected)."""
        return self._connected

    async def get_signal_strength(self) -> int | None:
        """Get simulated signal strength."""
        return self._signal_strength if self._connected else None

    # Testing helpers

    def simulate_incoming(self, sender: str, content: str) -> None:
        """Simulate an incoming SMS (for testing).

        Args:
            sender: Phone number of sender
            content: Message content
        """
        self._inbox.append(IncomingSMS(
            sender=sender,
            recipient=self.phone_number,
            content=content,
            received_at=datetime.utcnow(),
        ))

    def clear_outbox(self) -> None:
        """Clear sent messages history."""
        self._outbox.clear()

    def clear_inbox(self) -> None:
        """Clear incoming messages queue."""
        self._inbox.clear()

    def set_signal_strength(self, strength: int) -> None:
        """Set simulated signal strength (0-100)."""
        self._signal_strength = max(0, min(100, strength))

    def get_last_sent(self) -> dict | None:
        """Get last sent message (for testing)."""
        return self._outbox[-1] if self._outbox else None

    def was_sent_to(self, recipient: str) -> bool:
        """Check if SMS was sent to recipient (for testing)."""
        return any(m["recipient"] == recipient for m in self._outbox)
