"""SMS incoming service - receive SMS from modem and forward to PHP API."""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from datetime import datetime
from pathlib import Path

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


class SmsDedup:
    """Track processed SMS IDs to prevent duplicates.

    Persists to disk so dedup survives daemon restarts.
    Needed because IK41 modem doesn't support DeleteSMS.
    """

    MAX_IDS = 10000
    KEEP_IDS = 5000

    def __init__(self, persistence_path: Path, log_file: Path | None = None) -> None:
        self._ids: set = set()
        self._path = persistence_path
        self._log_file = log_file
        self._load()

    def is_processed(self, sms_id) -> bool:
        return sms_id in self._ids

    def mark_processed(self, sms_id) -> None:
        self._ids.add(sms_id)
        self._save()

    def clear(self) -> None:
        self._ids.clear()
        self._save()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._ids = set(data.get("ids", []))
                log(f"Loaded {len(self._ids)} processed SMS IDs from disk", self._log_file)
            except Exception as e:
                log(f"Error loading processed SMS IDs: {e}", self._log_file)

    def _save(self) -> None:
        try:
            if len(self._ids) > self.MAX_IDS:
                sorted_ids = sorted(self._ids)
                self._ids = set(sorted_ids[-self.KEEP_IDS:])

            data = {
                "ids": list(self._ids),
                "count": len(self._ids),
                "updated_at": datetime.now().isoformat(),
            }
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            log(f"Error saving processed SMS IDs: {e}", self._log_file)


class SmsIncomingService:
    """Receive SMS from modem and forward to PHP API."""

    def __init__(
        self,
        config: DaemonConfig,
        metrics: SmsMetrics,
        at_helper: AtCommandHelper,
        modem_status: ModemStatusProvider,
        dedup: SmsDedup,
    ) -> None:
        self.config = config
        self.metrics = metrics
        self.at_helper = at_helper
        self.modem_status = modem_status
        self.dedup = dedup

    async def poll_incoming(self) -> int:
        """Check modem for incoming SMS and forward to PHP API.

        Returns number of messages received.
        """
        if not HAS_HTTPX:
            return 0

        try:
            if self.config.modem_type == "serial":
                messages = await self._receive_serial()
            else:
                messages = await self._receive_direct()
            if not messages:
                return 0

            count = 0
            async with httpx.AsyncClient(timeout=10.0) as client:
                for msg in messages:
                    try:
                        await client.post(
                            f"{self.config.php_api}/receive-sms.php",
                            json={
                                "sms_message": msg["content"],
                                "sms_from": msg["sender"],
                                "sms_to": self.config.modem_phone,
                            },
                        )
                        count += 1
                        self.metrics.record_received()
                        log(
                            f"SMS RECEIVED: from={msg['sender']}, len={len(msg['content'])}",
                            self.config.log_file,
                        )
                    except Exception as e:
                        log(f"Incoming SMS forward error: {e}", self.config.log_file)

            if count > 0:
                log(f"Total incoming SMS processed: {count}", self.config.log_file)
            return count

        except Exception as e:
            log(f"Incoming SMS poll error: {e}", self.config.log_file)
            traceback.print_exc()
            return 0

    async def _receive_direct(self) -> list:
        """Read incoming SMS from IK41 modem via JSON-RPC."""
        base_url = f"http://{self.config.modem_host}:{self.config.modem_port}"

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if not m:
                log("Incoming SMS: cannot extract modem token", self.config.log_file)
                return []

            token = m.group(1)
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{self.config.modem_host}/index.html",
            }

            resp = await client.post(
                f"{base_url}/jrd/webapi",
                json={"jsonrpc": "2.0", "method": "Login",
                      "params": {"UserName": "admin", "Password": "admin"}, "id": "1"},
                headers=headers)
            login_data = resp.json()
            if "error" in login_data:
                log(f"Incoming SMS: login failed: {login_data}", self.config.log_file)
                return []

            messages = []
            try:
                resp = await client.post(
                    f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                          "params": {"Page": 0, "ContactNum": 100}, "id": "2"},
                    headers=headers)
                contacts_data = resp.json()
                result = contacts_data.get("result") or {}
                contact_list = result.get("SMSContactList") or []

                if not contact_list:
                    return []

                req_id = 3
                for contact in contact_list:
                    contact_id = contact.get("ContactId")
                    phone_raw = contact.get("PhoneNumber", "")
                    if isinstance(phone_raw, list):
                        phone_number = phone_raw[0] if phone_raw else ""
                    else:
                        phone_number = str(phone_raw)
                    if not contact_id:
                        continue

                    resp = await client.post(
                        f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "GetSMSContentList",
                              "params": {"ContactId": contact_id, "Page": 0},
                              "id": str(req_id)},
                        headers=headers)
                    req_id += 1
                    sms_list = (resp.json().get("result") or {}).get("SMSContentList") or []

                    for sms in sms_list:
                        sms_type = sms.get("SMSType", 0)
                        sms_id = sms.get("SMSId")
                        if sms_type == 0 and not self.dedup.is_processed(sms_id):
                            messages.append({
                                "sender": phone_number,
                                "content": sms.get("SMSContent", ""),
                            })
                            self.dedup.mark_processed(sms_id)

            finally:
                try:
                    await client.post(
                        f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "Logout",
                              "params": {}, "id": "99"},
                        headers=headers)
                except Exception:
                    pass

            return messages

    async def _receive_serial(self) -> list:
        """Read incoming SMS via serial AT commands (SIM7600G-H)."""
        port = await self.modem_status._resolve_serial_port()
        if not port:
            return []

        at_send = self.at_helper.at_send_sync
        baudrate = self.config.serial_baudrate
        log_file = self.config.log_file

        def _receive():
            try:
                ser = serial_mod.Serial(port, baudrate, timeout=3)
                at_send(ser, "AT+CMGF=1")
                resp = at_send(ser, 'AT+CMGL="REC UNREAD"', timeout=10)

                messages = []
                pattern = r'\+CMGL:\s*\d+,"[^"]*","([^"]+)".*?\r\n(.+?)(?=\r\n\+CMGL:|\r\nOK|\r\n$)'
                for match in re.finditer(pattern, resp, re.DOTALL):
                    sender = match.group(1).strip()
                    if sender.startswith("+48"):
                        sender = sender[3:]
                    messages.append({
                        "sender": sender,
                        "content": match.group(2).strip(),
                    })

                if messages:
                    at_send(ser, "AT+CMGD=1,3")

                ser.close()
                return messages
            except Exception as e:
                log(f"Serial receive error: {e}", log_file)
                return []

        return await asyncio.get_event_loop().run_in_executor(None, _receive)
