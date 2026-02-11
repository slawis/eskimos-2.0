"""Daemon logging - simple file + stdout logger with callback support."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

_log_callbacks: list[Callable[[str], None]] = []


def add_log_callback(fn: Callable[[str], None]) -> None:
    """Register a callback to receive log messages (e.g. WS streaming)."""
    _log_callbacks.append(fn)


def log(message: str, log_file: Path | None = None) -> None:
    """Log message to file, stdout, and callbacks."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)

    if log_file is not None:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    for cb in _log_callbacks:
        try:
            cb(message)
        except Exception:
            pass
