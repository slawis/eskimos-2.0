"""Daemon configuration - pure Python dataclass, no pydantic dependency.

Loads settings from config/.env file and environment variables.
Works in both full install and portable PyInstaller bundle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Root directory: in portable = EskimosGateway/, in dev = src/
PORTABLE_ROOT = Path(__file__).parent.parent.parent.parent


def _load_env_file(path: Path) -> None:
    """Load .env file into os.environ (no external deps needed)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class DaemonConfig:
    """Daemon configuration loaded from environment.

    Pure Python dataclass - works without pydantic in portable bundle.
    All settings have sensible defaults.
    """

    # Paths
    portable_root: Path = field(default_factory=lambda: PORTABLE_ROOT)
    client_key_file: Path = field(default_factory=lambda: PORTABLE_ROOT / ".client_key")
    log_file: Path = field(default_factory=lambda: PORTABLE_ROOT / "daemon.log")
    pid_file: Path = field(default_factory=lambda: PORTABLE_ROOT / ".daemon.pid")
    config_file: Path = field(default_factory=lambda: PORTABLE_ROOT / "config" / ".env")
    backup_dir: Path = field(default_factory=lambda: PORTABLE_ROOT / "_backups")
    update_dir: Path = field(default_factory=lambda: PORTABLE_ROOT / "_updates")
    processed_sms_file: Path = field(default_factory=lambda: PORTABLE_ROOT / ".processed_sms.json")

    # API
    central_api: str = ""
    php_api: str = ""
    api_key: str = ""

    # Intervals (seconds)
    heartbeat_interval: int = 60
    command_poll_interval: int = 60
    update_check_interval: int = 3600
    sms_poll_interval: int = 15
    incoming_sms_interval: int = 15
    sms_storage_check_interval: int = 3600

    # Rate limits
    sms_daily_limit: int = 100
    sms_hourly_limit: int = 20

    # Modem
    modem_host: str = "192.168.1.1"
    modem_port: int = 80
    modem_phone: str = ""
    modem_type: str = "ik41"
    serial_port: str = "auto"
    serial_baudrate: int = 115200
    gateway_port: int = 8000

    # Features
    auto_update_enabled: bool = True
    sms_storage_auto_reset: bool = True
    sms_storage_warn_percent: int = 80

    # WebSocket tunnel
    ws_enabled: bool = False
    ws_url: str = ""
    ws_reconnect_interval: int = 10
    ws_ping_interval: int = 30

    @classmethod
    def from_env(cls) -> DaemonConfig:
        """Load config from .env file + environment variables."""
        env_file = PORTABLE_ROOT / "config" / ".env"
        _load_env_file(env_file)

        def _env(key: str, default: str = "") -> str:
            return os.getenv(key, default)

        def _env_int(key: str, default: int) -> int:
            return int(os.getenv(key, str(default)))

        def _env_bool(key: str, default: bool = True) -> bool:
            return os.getenv(key, str(default)).lower() == "true"

        return cls(
            central_api=_env("ESKIMOS_CENTRAL_API", "https://app.ninjabot.pl/api/eskimos"),
            php_api=_env("ESKIMOS_PHP_API", "https://eskimos.ninjabot.pl/api/v2"),
            api_key=_env("ESKIMOS_API_KEY", "eskimos-daemon-2026"),
            heartbeat_interval=_env_int("ESKIMOS_HEARTBEAT_INTERVAL", 60),
            command_poll_interval=_env_int("ESKIMOS_COMMAND_POLL_INTERVAL", 60),
            update_check_interval=_env_int("ESKIMOS_UPDATE_CHECK_INTERVAL", 3600),
            sms_poll_interval=_env_int("ESKIMOS_SMS_POLL_INTERVAL", 15),
            incoming_sms_interval=_env_int("ESKIMOS_INCOMING_SMS_INTERVAL", 15),
            auto_update_enabled=_env_bool("ESKIMOS_AUTO_UPDATE", True),
            sms_daily_limit=_env_int("ESKIMOS_SMS_DAILY_LIMIT", 100),
            sms_hourly_limit=_env_int("ESKIMOS_SMS_HOURLY_LIMIT", 20),
            modem_host=_env("ESKIMOS_MODEM_HOST", "192.168.1.1"),
            modem_port=_env_int("ESKIMOS_MODEM_PORT", 80),
            modem_phone=_env("ESKIMOS_MODEM_PHONE", ""),
            modem_type=_env("ESKIMOS_MODEM_TYPE", "ik41"),
            serial_port=_env("ESKIMOS_SERIAL_PORT", "auto"),
            serial_baudrate=_env_int("ESKIMOS_SERIAL_BAUDRATE", 115200),
            gateway_port=_env_int("ESKIMOS_GATEWAY_PORT", 8000),
            ws_enabled=_env_bool("ESKIMOS_WS_ENABLED", False),
            ws_url=_env("ESKIMOS_WS_URL", ""),
            ws_reconnect_interval=_env_int("ESKIMOS_WS_RECONNECT_INTERVAL", 10),
            ws_ping_interval=_env_int("ESKIMOS_WS_PING_INTERVAL", 30),
        )
