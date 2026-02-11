"""Serial AT modem adapter for SIM7600G-H and compatible modems.

Uses pyserial for direct AT command communication over serial port (USB dongle).
Tested with: SIMCOM SIM7600G-H 4G LTE USB dongle.

AT commands used:
- AT         : verify connection
- AT+CMGF=1  : set SMS text mode
- AT+CMGS    : send SMS
- AT+CMGL    : list/receive SMS
- AT+CMGD    : delete SMS
- AT+CSQ     : signal strength
- ATI        : modem info
- AT+CPIN?   : SIM status
- AT+COPS?   : network operator
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from eskimos.adapters.modem.base import (
    BaseModemAdapter,
    ModemConnectionError,
    ModemReceiveError,
    ModemSendError,
    ModemTimeoutError,
)
from eskimos.core.entities.modem import ModemStatus
from eskimos.core.entities.sms import IncomingSMS, SMSResult, generate_key

logger = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None  # type: ignore


@dataclass
class SerialModemConfig:
    """Configuration for serial AT modem."""

    phone_number: str = "886480453"
    port: str = "COM6"
    baudrate: int = 115200
    timeout: float = 3.0
    at_timeout: float = 5.0
    sms_timeout: float = 15.0


class SerialModemAdapter(BaseModemAdapter):
    """Modem adapter using AT commands over serial port.

    Works with SIM7600G-H and other AT-compatible USB modems.
    Serial operations are synchronous (pyserial) and wrapped
    with run_in_executor for async compatibility.
    """

    def __init__(self, config: SerialModemConfig) -> None:
        super().__init__(phone_number=config.phone_number)
        self.config = config
        self._serial: serial.Serial | None = None  # type: ignore

    # ==================== AT Command Helpers ====================

    def _at_send_sync(self, cmd: str, timeout: float | None = None) -> str:
        """Send AT command and read response (synchronous).

        Args:
            cmd: AT command string (e.g. "AT+CSQ")
            timeout: Response timeout in seconds

        Returns:
            Decoded response string
        """
        if not self._serial or not self._serial.is_open:
            raise ModemConnectionError("Serial port not open")

        timeout = timeout or self.config.at_timeout
        self._serial.reset_input_buffer()
        self._serial.write((cmd + "\r\n").encode())
        time.sleep(0.5)

        end_time = time.time() + timeout
        response = b""
        while time.time() < end_time:
            if self._serial.in_waiting:
                response += self._serial.read(self._serial.in_waiting)
                if b"OK" in response or b"ERROR" in response or b">" in response:
                    break
            time.sleep(0.1)

        return response.decode("utf-8", errors="replace").strip()

    async def _at_send(self, cmd: str, timeout: float | None = None) -> str:
        """Send AT command (async wrapper)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._at_send_sync, cmd, timeout)

    # ==================== ModemAdapter Interface ====================

    async def connect(self) -> None:
        """Open serial port and verify modem responds to AT."""
        if not HAS_SERIAL:
            raise ModemConnectionError(
                "pyserial not installed. Run: pip install pyserial",
                modem_number=self._phone_number,
            )

        def _connect_sync() -> None:
            try:
                self._serial = serial.Serial(
                    port=self.config.port,
                    baudrate=self.config.baudrate,
                    timeout=self.config.timeout,
                    write_timeout=self.config.timeout,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                )
            except Exception as e:
                raise ModemConnectionError(
                    f"Cannot open {self.config.port}: {e}",
                    modem_number=self._phone_number,
                )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _connect_sync)

        # Verify modem responds
        resp = await self._at_send("AT")
        if "OK" not in resp:
            await self.disconnect()
            raise ModemConnectionError(
                f"Modem not responding on {self.config.port}: {resp}",
                modem_number=self._phone_number,
            )

        # Set SMS text mode
        resp = await self._at_send("AT+CMGF=1")
        if "OK" not in resp:
            logger.warning("Failed to set text mode: %s", resp)

        self._status = ModemStatus.ONLINE
        self._connected = True
        logger.info(
            "Serial modem connected on %s (%s baud)",
            self.config.port,
            self.config.baudrate,
        )

    async def disconnect(self) -> None:
        """Close serial port."""

        def _close_sync() -> None:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _close_sync)
        self._status = ModemStatus.OFFLINE
        self._connected = False
        logger.info("Serial modem disconnected")

    async def send_sms(
        self,
        recipient: str,
        message: str,
        *,
        timeout: float = 30.0,
    ) -> SMSResult:
        """Send SMS via AT+CMGS command.

        Sequence:
        1. AT+CMGS="recipient" → wait for ">"
        2. Send message text + Ctrl+Z (0x1A)
        3. Wait for +CMGS: <id> response
        """
        if not self._connected:
            raise ModemSendError(
                "Not connected", modem_number=self._phone_number
            )

        # Clean recipient number
        clean_recipient = re.sub(r"[^\d]", "", recipient)
        if clean_recipient.startswith("48") and len(clean_recipient) == 11:
            clean_recipient = clean_recipient[2:]

        def _send_sync() -> SMSResult:
            ser = self._serial
            if not ser or not ser.is_open:
                return SMSResult(
                    success=False,
                    error="Serial port closed",
                    modem_number=self._phone_number,
                )

            try:
                # Step 1: Send AT+CMGS command
                ser.reset_input_buffer()
                ser.write(f'AT+CMGS="{clean_recipient}"\r\n'.encode())
                time.sleep(2)

                prompt = ser.read(ser.in_waiting)
                if b">" not in prompt:
                    return SMSResult(
                        success=False,
                        error=f"No prompt received: {prompt.decode(errors='replace')}",
                        modem_number=self._phone_number,
                    )

                # Step 2: Send message + Ctrl+Z
                ser.write(message.encode("utf-8") + b"\x1a")

                # Step 3: Wait for +CMGS response
                end_time = time.time() + self.config.sms_timeout
                response = b""
                while time.time() < end_time:
                    if ser.in_waiting:
                        response += ser.read(ser.in_waiting)
                        if b"OK" in response or b"ERROR" in response:
                            break
                    time.sleep(0.2)

                resp_str = response.decode("utf-8", errors="replace")

                if "ERROR" in resp_str:
                    return SMSResult(
                        success=False,
                        error=f"AT+CMGS error: {resp_str}",
                        modem_number=self._phone_number,
                    )

                # Parse message ID from +CMGS: <id>
                msg_id = None
                match = re.search(r"\+CMGS:\s*(\d+)", resp_str)
                if match:
                    msg_id = f"sms_at_{match.group(1)}"
                else:
                    msg_id = f"sms_{generate_key()}"

                return SMSResult(
                    success=True,
                    message_id=msg_id,
                    sent_at=datetime.utcnow(),
                    modem_number=self._phone_number,
                )

            except Exception as e:
                return SMSResult(
                    success=False,
                    error=str(e),
                    modem_number=self._phone_number,
                )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _send_sync)

        if result.success:
            logger.info("SMS sent to %s via serial (ID: %s)", clean_recipient, result.message_id)
        else:
            logger.error("SMS send failed to %s: %s", clean_recipient, result.error)

        return result

    async def receive_sms(self) -> list[IncomingSMS]:
        """Receive SMS from modem via AT+CMGL.

        Reads all messages, parses them, and deletes read ones.
        """
        if not self._connected:
            raise ModemReceiveError(
                "Not connected", modem_number=self._phone_number
            )

        def _receive_sync() -> list[IncomingSMS]:
            messages: list[IncomingSMS] = []

            try:
                # List all SMS
                resp = self._at_send_sync('AT+CMGL="ALL"', timeout=10.0)

                # Parse +CMGL responses
                # Format: +CMGL: <index>,<stat>,<sender>,,<date>\r\n<content>
                pattern = re.compile(
                    r'\+CMGL:\s*(\d+),"([^"]*?)","([^"]*?)",[^,]*,"([^"]*?)"\r?\n([^\r\n]+)',
                )
                for match in pattern.finditer(resp):
                    idx, status, sender, timestamp, content = match.groups()
                    messages.append(
                        IncomingSMS(
                            sender=sender,
                            recipient=self._phone_number,
                            content=content.strip(),
                            received_at=datetime.utcnow(),
                            raw_data={
                                "index": int(idx),
                                "status": status,
                                "timestamp": timestamp,
                            },
                        )
                    )

                # Delete read messages if any were found
                if messages:
                    self._at_send_sync("AT+CMGD=1,4", timeout=10.0)

            except Exception as e:
                logger.error("Failed to receive SMS: %s", e)

            return messages

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _receive_sync)

    async def health_check(self) -> bool:
        """Check modem responds to AT command."""
        if not self._connected or not self._serial:
            return False

        try:
            resp = await self._at_send("AT", timeout=3.0)
            return "OK" in resp
        except Exception:
            return False

    async def get_signal_strength(self) -> int | None:
        """Get signal strength via AT+CSQ.

        AT+CSQ returns: +CSQ: <rssi>,<ber>
        rssi: 0-31 (mapped to 0-100), 99 = not known
        """
        if not self._connected:
            return None

        try:
            resp = await self._at_send("AT+CSQ")
            match = re.search(r"\+CSQ:\s*(\d+)", resp)
            if match:
                rssi = int(match.group(1))
                if rssi == 99:
                    return None
                # Map 0-31 → 0-100
                return min(100, int((rssi / 31) * 100))
        except Exception:
            pass
        return None

    # ==================== Extra AT Helpers ====================

    async def get_modem_info(self) -> dict:
        """Get modem model and firmware info via ATI."""
        if not self._connected:
            return {}

        resp = await self._at_send("ATI")
        info: dict = {"raw": resp}

        for line in resp.split("\n"):
            line = line.strip()
            if line.startswith("Manufacturer:"):
                info["manufacturer"] = line.split(":", 1)[1].strip()
            elif line.startswith("Model:"):
                info["model"] = line.split(":", 1)[1].strip()
            elif line.startswith("Revision:"):
                info["revision"] = line.split(":", 1)[1].strip()
            elif line.startswith("IMEI:"):
                info["imei"] = line.split(":", 1)[1].strip()

        return info

    async def get_network_info(self) -> dict:
        """Get network operator info via AT+COPS?."""
        if not self._connected:
            return {}

        resp = await self._at_send("AT+COPS?")
        info: dict = {"raw": resp}

        match = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)",(\d+)', resp)
        if match:
            info["operator"] = match.group(1)
            act = int(match.group(2))
            act_names = {0: "GSM", 2: "UTRAN", 3: "EDGE", 4: "HSDPA", 7: "LTE"}
            info["technology"] = act_names.get(act, f"Unknown({act})")

        return info

    async def get_sim_status(self) -> str:
        """Get SIM card status via AT+CPIN?."""
        if not self._connected:
            return "unknown"

        resp = await self._at_send("AT+CPIN?")
        match = re.search(r"\+CPIN:\s*(.+)", resp)
        if match:
            return match.group(1).strip()
        return "unknown"
