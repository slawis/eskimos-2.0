"""SMS outgoing service - poll queue from PHP API and send via modem."""

from __future__ import annotations

import asyncio
import re
import time
import traceback
from datetime import datetime

from eskimos.infrastructure.daemon.at_commands import AtCommandHelper, HAS_SERIAL, serial_mod
from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log
from eskimos.infrastructure.daemon.modem_status import ModemStatusProvider
from eskimos.infrastructure.daemon.sms_metrics import SmsMetrics

# Lazy httpx
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


class SmsOutgoingService:
    """Poll SMS queue from PHP API and send via modem."""

    def __init__(
        self,
        config: DaemonConfig,
        metrics: SmsMetrics,
        at_helper: AtCommandHelper,
        modem_status: ModemStatusProvider,
    ) -> None:
        self.config = config
        self.metrics = metrics
        self.at_helper = at_helper
        self.modem_status = modem_status

    async def poll_and_send(self) -> bool:
        """Poll SMS queue and send one SMS. Returns True if sent."""
        if not HAS_HTTPX:
            return False

        allowed, reason = self.metrics.check_rate_limit(
            self.config.sms_daily_limit, self.config.sms_hourly_limit)
        if not allowed:
            log(f"SMS rate limited: {reason}", self.config.log_file)
            return False

        sms_key = None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.config.php_api}/get-sms.php",
                    params={"from": self.config.modem_phone},
                )

                if resp.status_code != 200:
                    self.metrics.record_error(f"API {resp.status_code}")
                    log(f"SMS poll: API returned {resp.status_code}", self.config.log_file)
                    return False

                data = resp.json()
                if not data or not isinstance(data, list) or not data[0].get("isset"):
                    return False

                sms = data[0]
                sms_key = sms.get("sms_key")
                sms_to = sms.get("sms_to")
                sms_message = sms.get("sms_message")

                if not sms_key or not sms_to or not sms_message:
                    self.metrics.record_error(f"incomplete data key={sms_key}")
                    log(f"SMS poll: incomplete data - key={sms_key}", self.config.log_file)
                    return False

                log(
                    f"SMS queued: to={sms_to}, key={sms_key[:12]}..., len={len(sms_message)}",
                    self.config.log_file,
                )

                if self.config.modem_type == "serial":
                    success, error = await self._send_serial(sms_to, sms_message)
                else:
                    success, error = await self._send_direct(sms_to, sms_message)

                if success:
                    await client.post(
                        f"{self.config.php_api}/update-sms.php",
                        json={
                            "SMS_KEY": sms_key,
                            "SMS_FROM": self.config.modem_phone,
                            "SMS_IS_REPLY": sms.get("sms_is_reply", 0),
                        },
                    )
                    self.metrics.record_sent()
                    log(
                        f"SMS SENT: to={sms_to}, key={sms_key[:12]}... "
                        f"(today: {self.metrics.sent_today}, hour: {self.metrics.hourly_count})",
                        self.config.log_file,
                    )
                    return True
                else:
                    self.metrics.record_error(f"send failed: {error}")
                    log(f"SMS send FAILED: {error}", self.config.log_file)
                    return False

        except Exception as e:
            self.metrics.record_error(f"exception: {e}")
            log(f"SMS poll error: {e}", self.config.log_file)
            traceback.print_exc()
            return False

    async def _send_direct(self, recipient: str, message: str) -> tuple:
        """Send SMS via IK41 JSON-RPC."""
        base_url = f"http://{self.config.modem_host}:{self.config.modem_port}"

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if not m:
                return False, "Cannot extract modem token"

            token = m.group(1)
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{self.config.modem_host}/index.html",
            }

            login_body = {
                "jsonrpc": "2.0", "method": "Login",
                "params": {"UserName": "admin", "Password": "admin"}, "id": "1",
            }
            resp = await client.post(
                f"{base_url}/jrd/webapi", json=login_body, headers=headers)
            login_data = resp.json()
            if "error" in login_data:
                return False, f"Login failed: {login_data}"

            log(f"Modem login OK, sending SMS to {recipient}", self.config.log_file)

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sms_body = {
                "jsonrpc": "2.0", "method": "SendSMS",
                "params": {
                    "SMSId": -1,
                    "SMSContent": message,
                    "PhoneNumber": [recipient],
                    "SMSTime": now,
                }, "id": "2",
            }
            resp = await client.post(
                f"{base_url}/jrd/webapi", json=sms_body, headers=headers)
            sms_result = resp.json()

            try:
                await client.post(
                    f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "Logout",
                          "params": {}, "id": "3"},
                    headers=headers)
            except Exception:
                pass

            if "error" in sms_result:
                return False, f"SendSMS error: {sms_result.get('error')}"

            return True, None

    async def _send_serial(self, recipient: str, message: str) -> tuple:
        """Send SMS via serial AT commands (SIM7600G-H)."""
        port = await self.modem_status._resolve_serial_port()
        if not port:
            return False, "Serial port not found"

        at_send = self.at_helper.at_send_sync
        baudrate = self.config.serial_baudrate

        def _send():
            try:
                ser = serial_mod.Serial(port, baudrate, timeout=3)
                at_send(ser, "AT")
                at_send(ser, "AT+CMGF=1")

                ser.reset_input_buffer()
                ser.write(f'AT+CMGS="{recipient}"\r\n'.encode())
                time.sleep(1)
                ser.write(message.encode("utf-8"))
                ser.write(b"\x1a")

                end_time = time.time() + 15
                response = b""
                while time.time() < end_time:
                    if ser.in_waiting:
                        response += ser.read(ser.in_waiting)
                        if b"+CMGS:" in response or b"ERROR" in response:
                            break
                    time.sleep(0.2)
                ser.close()

                text = response.decode("utf-8", errors="replace")
                if "+CMGS:" in text:
                    return True, None
                return False, f"AT error: {text[:200]}"
            except Exception as e:
                return False, f"Serial error: {e}"

        return await asyncio.get_event_loop().run_in_executor(None, _send)
