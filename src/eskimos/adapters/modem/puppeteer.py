"""Puppeteer modem adapter for IK41VE1.

This adapter controls the legacy IK41VE1 GSM modem through its web interface
using Pyppeteer (Python port of Puppeteer).

The modem has a web UI at 192.168.1.1 that we automate to send/receive SMS.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from eskimos.adapters.modem.base import (
    BaseModemAdapter,
    ModemConnectionError,
    ModemReceiveError,
    ModemSendError,
    ModemTimeoutError,
)
from eskimos.core.entities.modem import ModemStatus
from eskimos.core.entities.sms import IncomingSMS, SMSResult, generate_key

if TYPE_CHECKING:
    from pyppeteer.browser import Browser
    from pyppeteer.page import Page

logger = logging.getLogger(__name__)


@dataclass
class PuppeteerConfig:
    """Configuration for Puppeteer modem adapter."""

    phone_number: str = "886480453"
    host: str = "192.168.1.1"
    port: int = 80

    # Browser settings
    headless: bool = False  # Show browser for debugging
    slow_mo: int = 0  # Slow down operations (ms)

    # Chromium executable path (auto-detected from env or None for default)
    chromium_path: str | None = None

    # Timeouts (seconds)
    navigation_timeout: float = 30.0
    action_timeout: float = 10.0

    # Delays between actions (milliseconds)
    delay_after_navigation: int = 3000
    delay_after_click: int = 2000
    delay_after_type: int = 1000

    def __post_init__(self) -> None:
        """Auto-detect bundled Chromium path if not specified."""
        import os
        if self.chromium_path is None:
            self.chromium_path = os.environ.get("PYPPETEER_EXECUTABLE_PATH")


# DOM Selectors for IK41VE1 web interface
class IK41VE1Selectors:
    """CSS selectors for IK41VE1 modem web interface."""

    # SMS List page
    SMS_LIST_URL = "default.html#sms/smsList.html"
    SMS_LIST_TABLE = "#ContactListTable li"
    SMS_ITEM_VALUE = "value"  # attribute with contact_id

    # SMS Read page
    SMS_READ_URL = "default.html#sms/smsRead.html?&doAction=reply&contact_id="
    SMS_CONTENT = ".sms-box .sms-text h3"
    SMS_SENDER = "h2 .contact-number"

    # SMS Write page
    SMS_WRITE_URL = "default.html#sms/smsWrite.html?list=inbox&doAction=new"
    RECIPIENT_INPUT = "#chosen-search-field-input"
    MESSAGE_INPUT = "#messageContent"
    SEND_BUTTON = "#btnSent"

    # Common
    CONFIRM_BUTTON = "#btnPopUpOk"
    DELETE_BUTTON = ".sms-icon.trash"


class PuppeteerModemAdapter(BaseModemAdapter):
    """Puppeteer-based adapter for IK41VE1 modem.

    Controls the modem through its web interface using browser automation.
    This is a legacy solution - prefer Dinstar HTTP API for new deployments.

    Example:
        config = PuppeteerConfig(phone_number="886480453")
        adapter = PuppeteerModemAdapter(config)

        async with adapter:
            result = await adapter.send_sms("123456789", "Hello!")
            print(f"Sent: {result.success}")

            incoming = await adapter.receive_sms()
            for sms in incoming:
                print(f"From {sms.sender}: {sms.content}")
    """

    def __init__(self, config: PuppeteerConfig | None = None):
        self.config = config or PuppeteerConfig()
        super().__init__(
            phone_number=self.config.phone_number,
            host=self.config.host,
            port=self.config.port,
        )

        self._browser: Browser | None = None
        self._page: Page | None = None

    @property
    def base_url(self) -> str:
        """Base URL of modem web interface."""
        return f"http://{self._host}:{self._port}"

    async def connect(self) -> None:
        """Connect to modem by launching browser and verifying access."""
        try:
            # Import pyppeteer here to allow graceful fallback
            from pyppeteer import launch

            logger.info(f"Connecting to modem at {self.base_url}")

            # Build launch options
            launch_options: dict = {
                "headless": self.config.headless,
                "slowMo": self.config.slow_mo,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ],
            }

            # Use bundled Chromium if available
            if self.config.chromium_path:
                logger.info(f"Using bundled Chromium: {self.config.chromium_path}")
                launch_options["executablePath"] = self.config.chromium_path

            self._browser = await launch(**launch_options)

            self._page = await self._browser.newPage()
            self._page.setDefaultNavigationTimeout(
                int(self.config.navigation_timeout * 1000)
            )

            # Test connection by loading main page
            await self._page.goto(self.base_url, waitUntil="networkidle0")
            await asyncio.sleep(self.config.delay_after_navigation / 1000)

            self._connected = True
            self._status = ModemStatus.ONLINE
            logger.info(f"Connected to modem {self.phone_number}")

        except Exception as e:
            self._status = ModemStatus.ERROR
            raise ModemConnectionError(
                f"Failed to connect to modem: {e}",
                self.phone_number,
            ) from e

    async def disconnect(self) -> None:
        """Disconnect by closing browser."""
        try:
            if self._page:
                await self._page.close()
                self._page = None

            if self._browser:
                await self._browser.close()
                self._browser = None

            self._connected = False
            self._status = ModemStatus.OFFLINE
            logger.info(f"Disconnected from modem {self.phone_number}")

        except Exception as e:
            logger.error(f"Error disconnecting: {e}")

    async def send_sms(
        self,
        recipient: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> SMSResult:
        """Send SMS through modem web interface.

        Steps:
        1. Navigate to SMS compose page
        2. Enter recipient number
        3. Enter message content
        4. Click send button
        5. Wait for confirmation
        """
        if not self._connected or not self._page:
            raise ModemSendError("Not connected to modem", self.phone_number)

        self._status = ModemStatus.BUSY
        message_id = f"ik41_{generate_key(10)}"

        try:
            logger.info(f"Sending SMS to {recipient}")

            # Navigate to compose page
            write_url = f"{self.base_url}/{IK41VE1Selectors.SMS_WRITE_URL}"
            await self._page.goto(write_url)
            await asyncio.sleep(self.config.delay_after_navigation / 1000)

            # Wait for input fields
            await self._page.waitForSelector(IK41VE1Selectors.RECIPIENT_INPUT)
            await asyncio.sleep(self.config.delay_after_click / 1000)

            # Enter recipient
            await self._page.click(IK41VE1Selectors.RECIPIENT_INPUT)
            await asyncio.sleep(self.config.delay_after_click / 1000)
            await self._page.click(IK41VE1Selectors.RECIPIENT_INPUT)
            await asyncio.sleep(self.config.delay_after_click / 1000)
            await self._page.focus(IK41VE1Selectors.RECIPIENT_INPUT)
            await self._page.keyboard.type(recipient)
            await asyncio.sleep(self.config.delay_after_type / 1000)
            await self._page.keyboard.press("Enter")
            await asyncio.sleep(500 / 1000)

            # Enter message
            await self._page.focus(IK41VE1Selectors.MESSAGE_INPUT)
            await self._page.keyboard.type(message)
            await asyncio.sleep(self.config.delay_after_type / 1000)

            # Click send
            await self._page.click(IK41VE1Selectors.SEND_BUTTON)
            await asyncio.sleep(5)  # Wait for send to complete

            self._status = ModemStatus.ONLINE
            logger.info(f"SMS sent to {recipient}: {message_id}")

            return SMSResult(
                success=True,
                message_id=message_id,
                error=None,
                sent_at=datetime.utcnow(),
                modem_number=self.phone_number,
            )

        except asyncio.TimeoutError as e:
            self._status = ModemStatus.ONLINE
            raise ModemTimeoutError(
                f"Timeout sending SMS to {recipient}",
                self.phone_number,
            ) from e

        except Exception as e:
            self._status = ModemStatus.ERROR
            logger.error(f"Error sending SMS: {e}")
            return SMSResult(
                success=False,
                message_id=None,
                error=str(e),
                sent_at=None,
                modem_number=self.phone_number,
            )

    async def receive_sms(self) -> list[IncomingSMS]:
        """Receive pending SMS from modem inbox.

        Steps:
        1. Navigate to SMS list page
        2. Get all unread SMS IDs
        3. For each SMS:
           a. Navigate to read page
           b. Extract sender and content
           c. Delete from modem
        """
        if not self._connected or not self._page:
            return []

        messages: list[IncomingSMS] = []

        try:
            # Navigate to SMS list
            list_url = f"{self.base_url}/{IK41VE1Selectors.SMS_LIST_URL}"
            await self._page.goto(list_url, waitUntil="networkidle0")
            await asyncio.sleep(self.config.delay_after_navigation / 1000)

            # Get all SMS items
            sms_items = await self._page.querySelectorAllEval(
                IK41VE1Selectors.SMS_LIST_TABLE,
                "elements => elements.map(el => el.getAttribute('value'))"
            )

            if not sms_items:
                return []

            logger.info(f"Found {len(sms_items)} SMS in inbox")

            for contact_id in sms_items:
                try:
                    # Navigate to read page
                    read_url = (
                        f"{self.base_url}/{IK41VE1Selectors.SMS_READ_URL}{contact_id}"
                    )
                    await self._page.goto(read_url, waitUntil="networkidle0")
                    await asyncio.sleep(self.config.delay_after_navigation / 1000)

                    # Extract content
                    content_elements = await self._page.querySelectorAllEval(
                        IK41VE1Selectors.SMS_CONTENT,
                        "elements => elements.map(el => el.textContent)"
                    )

                    # Extract sender
                    sender_element = await self._page.querySelector(
                        IK41VE1Selectors.SMS_SENDER
                    )
                    sender = ""
                    if sender_element:
                        sender = await self._page.evaluate(
                            "(el) => el.textContent",
                            sender_element,
                        )

                    # Create IncomingSMS for each message
                    for content in content_elements:
                        if content and content.strip():
                            messages.append(IncomingSMS(
                                sender=sender.strip(),
                                recipient=self.phone_number,
                                content=content.strip(),
                                received_at=datetime.utcnow(),
                                raw_data={"contact_id": contact_id},
                            ))

                    # Delete SMS from modem
                    await self._page.goto(list_url, waitUntil="networkidle0")
                    await asyncio.sleep(self.config.delay_after_navigation / 1000)

                    delete_selector = (
                        f".sms-li[value='{contact_id}'] {IK41VE1Selectors.DELETE_BUTTON}"
                    )
                    await self._page.click(delete_selector)
                    await asyncio.sleep(self.config.delay_after_click / 1000)

                    # Confirm deletion (click OK twice)
                    await self._page.click(IK41VE1Selectors.CONFIRM_BUTTON)
                    await asyncio.sleep(self.config.delay_after_click / 1000)
                    await self._page.click(IK41VE1Selectors.CONFIRM_BUTTON)
                    await asyncio.sleep(self.config.delay_after_click / 1000)

                except Exception as e:
                    logger.error(f"Error processing SMS {contact_id}: {e}")
                    continue

            logger.info(f"Received {len(messages)} SMS messages")
            return messages

        except Exception as e:
            logger.error(f"Error receiving SMS: {e}")
            raise ModemReceiveError(
                f"Failed to receive SMS: {e}",
                self.phone_number,
            ) from e

    async def health_check(self) -> bool:
        """Check if modem web interface is accessible."""
        if not self._connected or not self._page:
            return False

        try:
            response = await self._page.goto(self.base_url, waitUntil="networkidle0")
            return response is not None and response.ok

        except Exception:
            return False

    async def get_signal_strength(self) -> int | None:
        """Get signal strength (not available for IK41VE1)."""
        # IK41VE1 doesn't expose signal strength through web UI
        return None
