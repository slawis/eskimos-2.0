"""Heartbeat service - phone home to central server."""

from __future__ import annotations

from datetime import datetime

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.identity import UptimeTracker, get_system_info
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


class HeartbeatService:
    """Send periodic heartbeats to central API."""

    def __init__(
        self,
        config: DaemonConfig,
        modem_status: ModemStatusProvider,
        metrics: SmsMetrics,
        uptime: UptimeTracker,
    ) -> None:
        self.config = config
        self.modem_status = modem_status
        self.metrics = metrics
        self.uptime = uptime

    async def get_sms_metrics(self) -> dict:
        """Get SMS metrics - local counters + pending from PHP API."""
        pending = 0
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.config.php_api}/health.php")
                    if resp.status_code == 200:
                        data = resp.json()
                        queue = data.get("queue", {})
                        pending = queue.get("sms_pending", 0) or 0
        except Exception:
            pass

        result = self.metrics.to_heartbeat_dict()
        result["sms_pending"] = pending
        result["daily_limit"] = self.config.sms_daily_limit
        result["hourly_limit"] = self.config.sms_hourly_limit
        return result

    async def send_heartbeat(self, client_key: str) -> dict:
        """Send heartbeat to central server."""
        if not HAS_HTTPX:
            log("Heartbeat skipped: httpx not installed", self.config.log_file)
            return {}

        try:
            from eskimos import __version__
        except ImportError:
            __version__ = "0.0.0"

        modem = await self.modem_status.get_status()
        metrics = await self.get_sms_metrics()
        system = get_system_info()

        payload = {
            "client_key": client_key,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "version": __version__,
            "uptime_seconds": self.uptime.get_uptime(),
            "modem": modem,
            "metrics": metrics,
            "system": system,
            "auto_reset_in_progress": self.metrics.auto_reset_in_progress,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.config.central_api}/heartbeat",
                    json=payload,
                    headers={
                        "X-Client-Key": client_key,
                        "X-API-Key": self.config.api_key,
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    log(
                        f"Heartbeat OK: v{__version__}, modem={modem.get('status')}",
                        self.config.log_file,
                    )
                    return data
                else:
                    log(
                        f"Heartbeat failed: {response.status_code}",
                        self.config.log_file,
                    )

        except Exception as e:
            log(f"Heartbeat error: {e}", self.config.log_file)

        return {}
