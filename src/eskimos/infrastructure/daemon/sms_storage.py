"""SMS storage monitoring - auto-reset modem when storage > threshold."""

from __future__ import annotations

import re

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log
from eskimos.infrastructure.daemon.sms_incoming import SmsDedup
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


class SmsStorageMonitor:
    """Monitor modem SMS storage and trigger auto-reset when full."""

    def __init__(
        self,
        config: DaemonConfig,
        metrics: SmsMetrics,
        dedup: SmsDedup,
        modem_control=None,  # Injected later to avoid circular import
    ) -> None:
        self.config = config
        self.metrics = metrics
        self.dedup = dedup
        self._modem_control = modem_control

    def set_modem_control(self, modem_control) -> None:
        """Set modem control service (breaks circular dependency)."""
        self._modem_control = modem_control

    async def check_storage(self) -> None:
        """Check modem SMS storage. Auto-reset if > threshold."""
        if not HAS_HTTPX:
            return

        if self.metrics.auto_reset_in_progress:
            log("SMS storage check skipped: auto-reset in progress", self.config.log_file)
            return

        base_url = f"http://{self.config.modem_host}:{self.config.modem_port}"

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(base_url)
                m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
                if not m:
                    return

                token = m.group(1)
                headers = {
                    "_TclRequestVerificationKey": token,
                    "Referer": f"http://{self.config.modem_host}/index.html",
                }

                resp = await client.post(
                    f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "GetSMSStorageState",
                          "params": {}, "id": "1"},
                    headers=headers)

                result = resp.json().get("result") or {}
                self.metrics.storage_max = result.get("MaxCount", 100)
                self.metrics.storage_used = result.get("TUseCount", 0)
                left = result.get("LeftCount",
                                  self.metrics.storage_max - self.metrics.storage_used)

                percent = (
                    (self.metrics.storage_used / self.metrics.storage_max * 100)
                    if self.metrics.storage_max > 0 else 0
                )
                log(
                    f"SMS storage: {self.metrics.storage_used}/{self.metrics.storage_max} "
                    f"({percent:.0f}%), {left} free",
                    self.config.log_file,
                )

                if percent >= self.config.sms_storage_warn_percent:
                    self.metrics.last_error = (
                        f"SMS storage {percent:.0f}% full "
                        f"({self.metrics.storage_used}/{self.metrics.storage_max})"
                    )

                    if self.config.sms_storage_auto_reset and self._modem_control:
                        log(
                            f"AUTO-RESET: SMS storage {percent:.0f}% full, "
                            f"triggering factory reset...",
                            self.config.log_file,
                        )
                        self.metrics.auto_reset_in_progress = True
                        try:
                            reset_result = await self._modem_control.factory_reset()
                            sms_before = reset_result.get("sms_before", "?")
                            sms_after = reset_result.get("sms_after", "?")
                            success = reset_result.get("success", False)
                            log(
                                f"AUTO-RESET complete: SMS {sms_before} -> {sms_after}, "
                                f"success={success}",
                                self.config.log_file,
                            )

                            if success:
                                self.metrics.storage_used = 0
                                self.metrics.last_error = ""
                                self.dedup.clear()

                                try:
                                    async with httpx.AsyncClient(timeout=10.0) as api_client:
                                        del_resp = await api_client.delete(
                                            f"{self.config.central_api}/sms/received/all",
                                            headers={
                                                "X-Dashboard-Key": self.config.api_key},
                                            timeout=10.0,
                                        )
                                        if del_resp.status_code == 200:
                                            del_data = del_resp.json()
                                            log(
                                                f"AUTO-RESET: Cleared "
                                                f"{del_data.get('deleted', 0)} SMS from DB",
                                                self.config.log_file,
                                            )
                                        else:
                                            log(
                                                f"AUTO-RESET: DB cleanup failed: "
                                                f"{del_resp.status_code}",
                                                self.config.log_file,
                                            )
                                except Exception as db_err:
                                    log(
                                        f"AUTO-RESET: DB cleanup error: {db_err}",
                                        self.config.log_file,
                                    )
                            else:
                                log(
                                    f"AUTO-RESET FAILED: "
                                    f"{reset_result.get('error', 'unknown')}",
                                    self.config.log_file,
                                )
                        except Exception as reset_err:
                            log(f"AUTO-RESET error: {reset_err}", self.config.log_file)
                        finally:
                            self.metrics.auto_reset_in_progress = False
                    else:
                        log(
                            f"WARNING: SMS storage {percent:.0f}% full! "
                            f"Only {left} slots remaining. Auto-reset disabled.",
                            self.config.log_file,
                        )

        except Exception as e:
            log(f"SMS storage check error: {e}", self.config.log_file)
