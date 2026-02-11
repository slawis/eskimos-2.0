"""Daemon logging - simple file + stdout logger."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def log(message: str, log_file: Path | None = None) -> None:
    """Log message to file and stdout."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)

    if log_file is not None:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
