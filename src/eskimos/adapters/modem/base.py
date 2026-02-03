"""Base modem adapter interface (Protocol).

This module defines the abstract interface that all modem adapters must implement.
Using Python's Protocol for structural subtyping (duck typing).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from eskimos.core.entities.modem import ModemStatus
from eskimos.core.entities.sms import IncomingSMS, SMSResult


class ModemError(Exception):
    """Base exception for modem-related errors."""

    def __init__(self, message: str, modem_number: str | None = None):
        self.message = message
        self.modem_number = modem_number
        super().__init__(message)


class ModemConnectionError(ModemError):
    """Failed to connect to modem."""

    pass


class ModemSendError(ModemError):
    """Failed to send SMS."""

    pass


class ModemReceiveError(ModemError):
    """Failed to receive SMS."""

    pass


class ModemTimeoutError(ModemError):
    """Operation timed out."""

    pass


@runtime_checkable
class ModemAdapter(Protocol):
    """Protocol for GSM modem adapters.

    Every modem (IK41VE1/Puppeteer, Dinstar HTTP, mock) must implement this interface.
    This enables the Adapter pattern - same code works with different hardware.

    Example usage:
        adapter = PuppeteerModemAdapter(config)
        await adapter.connect()

        result = await adapter.send_sms("123456789", "Hello!")
        if result.success:
            print(f"SMS sent: {result.message_id}")

        incoming = await adapter.receive_sms()
        for sms in incoming:
            print(f"Received from {sms.sender}: {sms.content}")

        await adapter.disconnect()
    """

    @property
    def phone_number(self) -> str:
        """Phone number of this modem (9 digits)."""
        ...

    @property
    def status(self) -> ModemStatus:
        """Current operational status."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether modem is currently connected."""
        ...

    async def connect(self) -> None:
        """Connect to the modem.

        Raises:
            ModemConnectionError: If connection fails
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the modem."""
        ...

    async def send_sms(
        self,
        recipient: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> SMSResult:
        """Send an SMS message.

        Args:
            recipient: Phone number (9 digits, Polish format)
            message: Message content (max 640 characters for 4 SMS)
            timeout: Operation timeout in seconds

        Returns:
            SMSResult with success/failure info

        Raises:
            ModemSendError: If sending fails
            ModemTimeoutError: If operation times out
        """
        ...

    async def receive_sms(self) -> list[IncomingSMS]:
        """Receive pending SMS messages from modem.

        Messages are typically deleted from modem after retrieval.

        Returns:
            List of incoming SMS messages

        Raises:
            ModemReceiveError: If receiving fails
        """
        ...

    async def health_check(self) -> bool:
        """Check if modem is healthy and responsive.

        Returns:
            True if modem is working properly
        """
        ...

    async def get_signal_strength(self) -> int | None:
        """Get GSM signal strength.

        Returns:
            Signal strength 0-100, or None if not available
        """
        ...


class BaseModemAdapter(ABC):
    """Abstract base class for modem adapters.

    Provides common functionality and enforces interface implementation.
    Prefer using this over Protocol when you need shared code.
    """

    def __init__(self, phone_number: str, host: str = "192.168.1.1", port: int = 80):
        self._phone_number = phone_number
        self._host = host
        self._port = port
        self._status = ModemStatus.OFFLINE
        self._connected = False

    @property
    def phone_number(self) -> str:
        """Phone number of this modem."""
        return self._phone_number

    @property
    def status(self) -> ModemStatus:
        """Current operational status."""
        return self._status

    @property
    def is_connected(self) -> bool:
        """Whether modem is connected."""
        return self._connected

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the modem."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the modem."""
        pass

    @abstractmethod
    async def send_sms(
        self,
        recipient: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> SMSResult:
        """Send an SMS message."""
        pass

    @abstractmethod
    async def receive_sms(self) -> list[IncomingSMS]:
        """Receive pending SMS messages."""
        pass

    async def health_check(self) -> bool:
        """Check if modem is healthy."""
        return self._connected and self._status == ModemStatus.ONLINE

    async def get_signal_strength(self) -> int | None:
        """Get signal strength (override in subclass if supported)."""
        return None

    async def __aenter__(self) -> "BaseModemAdapter":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()
