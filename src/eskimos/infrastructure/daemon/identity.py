"""Daemon identity - client key generation and uptime tracking."""

from __future__ import annotations

import platform
import secrets
import time
from pathlib import Path

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log


def get_or_create_client_key(config: DaemonConfig) -> str:
    """Get existing client key or generate new one."""
    if config.client_key_file.exists():
        return config.client_key_file.read_text().strip()

    key = f"esk_{secrets.token_hex(32)}"
    config.client_key_file.parent.mkdir(parents=True, exist_ok=True)
    config.client_key_file.write_text(key)
    log(f"Generated new client key: {key[:12]}...", config.log_file)
    return key


def get_system_info() -> dict:
    """Get system information (CPU, memory, disk)."""
    try:
        import psutil
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu = psutil.cpu_percent(interval=0.1)

        return {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "memory_mb": memory.used // (1024 * 1024),
            "memory_percent": memory.percent,
            "disk_free_gb": disk.free // (1024 ** 3),
            "cpu_percent": cpu,
        }
    except ImportError:
        return {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
        }


class UptimeTracker:
    """Track daemon uptime."""

    def __init__(self) -> None:
        self._start_time = time.time()

    def get_uptime(self) -> int:
        """Get daemon uptime in seconds."""
        return int(time.time() - self._start_time)
