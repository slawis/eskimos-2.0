"""Configuration management using Pydantic Settings.

Loads configuration from environment variables and .env files.

Usage:
    from eskimos.infrastructure.config import get_settings

    settings = get_settings()
    print(settings.database_url)
"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables
    or a .env file in the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== Database ====================
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/eskimos",
        description="PostgreSQL connection URL",
    )
    database_schema: str = Field(
        default="eskimos_sms",
        description="Database schema name",
    )

    # ==================== Redis ====================
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for caching",
    )

    # ==================== Claude API ====================
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key for Claude",
    )
    claude_model: str = Field(
        default="claude-3-haiku-20240307",
        description="Claude model to use",
    )
    claude_max_tokens: int = Field(
        default=500,
        description="Max tokens for Claude responses",
    )

    # ==================== Modem ====================
    modem_type: Literal["puppeteer", "dinstar", "mock", "serial"] = Field(
        default="mock",
        description="Type of modem adapter to use",
    )
    modem_host: str = Field(
        default="192.168.1.1",
        description="Modem IP address",
    )
    modem_port: int = Field(
        default=80,
        description="Modem port",
    )
    modem_phone_number: str = Field(
        default="886480453",
        description="Modem phone number",
    )

    # Dinstar specific
    dinstar_username: str = Field(
        default="admin",
        description="Dinstar admin username",
    )
    dinstar_password: str = Field(
        default="admin",
        description="Dinstar admin password",
    )

    # Serial AT modem specific (SIM7600G-H)
    serial_port: str = Field(
        default="COM6",
        description="Serial COM port for AT modem",
    )
    serial_baudrate: int = Field(
        default=115200,
        description="Serial baud rate",
    )

    # ==================== Rate Limiting ====================
    rate_limit_sms_per_hour: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Max SMS per hour per modem",
    )
    rate_limit_sms_per_day: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Max SMS per day per modem",
    )

    # ==================== Time Windows ====================
    time_window_start: time = Field(
        default=time(9, 0),
        description="Start of sending window (HH:MM)",
    )
    time_window_end: time = Field(
        default=time(20, 0),
        description="End of sending window (HH:MM)",
    )
    allowed_days: list[int] = Field(
        default=[0, 1, 2, 3, 4],  # Mon-Fri
        description="Allowed days (0=Mon, 6=Sun)",
    )

    # ==================== Jitter ====================
    jitter_min_seconds: int = Field(
        default=30,
        ge=0,
        description="Minimum random delay between SMS",
    )
    jitter_max_seconds: int = Field(
        default=180,
        ge=0,
        description="Maximum random delay between SMS",
    )

    # ==================== API ====================
    api_host: str = Field(
        default="0.0.0.0",
        description="FastAPI host",
    )
    api_port: int = Field(
        default=8000,
        description="FastAPI port",
    )
    api_debug: bool = Field(
        default=False,
        description="Enable API debug mode",
    )

    # ==================== Logging ====================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )
    log_format: Literal["json", "console"] = Field(
        default="console",
        description="Log output format",
    )

    # ==================== Legacy API ====================
    legacy_api_url: str = Field(
        default="https://eskimos.ninjabot.pl/api/v2",
        description="Legacy PHP API URL",
    )

    # ==================== API Authentication ====================
    eskimos_api_key: str = Field(
        default="",
        description="API key for protecting SMS/modem endpoints (X-API-Key header). Empty = auth disabled.",
    )

    # ==================== Central API (Daemon) ====================
    central_api_url: str = Field(
        default="https://eskimos.ninjabot.pl/api/eskimos",
        description="Central management API URL",
    )
    central_api_key: str = Field(
        default="eskimos-daemon-2026",
        description="API key for central server authentication",
    )

    # ==================== Daemon ====================
    daemon_enabled: bool = Field(
        default=True,
        description="Enable phone-home daemon",
    )
    daemon_heartbeat_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Heartbeat interval in seconds",
    )
    daemon_command_poll_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Command polling interval in seconds",
    )
    daemon_update_check_interval: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="Update check interval in seconds",
    )
    daemon_auto_update: bool = Field(
        default=True,
        description="Enable automatic updates",
    )

    # ==================== Client Identity ====================
    client_name: str = Field(
        default="",
        description="Human-readable client name (auto-generated if empty)",
    )

    # ==================== Validators ====================

    @field_validator("time_window_start", "time_window_end", mode="before")
    @classmethod
    def parse_time(cls, v):
        """Parse time from string HH:MM format."""
        if isinstance(v, str):
            parts = v.split(":")
            return time(int(parts[0]), int(parts[1]))
        return v

    @field_validator("allowed_days", mode="before")
    @classmethod
    def parse_days(cls, v):
        """Parse days from comma-separated string."""
        if isinstance(v, str):
            return [int(d.strip()) for d in v.split(",")]
        return v

    # ==================== Properties ====================

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return not self.api_debug

    @property
    def has_claude_key(self) -> bool:
        """Check if Claude API key is configured."""
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Settings are loaded once and cached for performance.
    """
    return Settings()


def get_project_root() -> Path:
    """Get project root directory."""
    current = Path(__file__).resolve()
    # Go up until we find pyproject.toml
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parent.parent.parent
