"""
Entry point for running eskimos as a module or PyInstaller bundle.

Usage:
    python -m eskimos --help
    python -m eskimos send 123456789 "Hello"
    EskimosGateway.exe serve  # When bundled

PyInstaller Bundle:
    This module detects if running as a PyInstaller bundle and sets up
    paths for bundled resources (Chromium, templates, etc.).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_bundled() -> bool:
    """Check if running as PyInstaller bundle."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_bundle_dir() -> Path:
    """Get the bundle directory (where resources are extracted)."""
    if is_bundled():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


def setup_bundled_environment() -> None:
    """Configure environment for PyInstaller bundle.

    Sets up paths for:
    - Chromium browser (for Puppeteer)
    - Templates directory
    - Static files
    """
    if not is_bundled():
        return

    bundle_dir = get_bundle_dir()

    # Set templates path
    templates_path = bundle_dir / "templates"
    if templates_path.exists():
        os.environ["ESKIMOS_TEMPLATES_DIR"] = str(templates_path)

    # Set static files path
    static_path = bundle_dir / "static"
    if static_path.exists():
        os.environ["ESKIMOS_STATIC_DIR"] = str(static_path)


def main() -> None:
    """Main entry point."""
    # Setup bundled environment first
    setup_bundled_environment()

    # Import and run CLI
    from eskimos.cli.main import app
    app()


if __name__ == "__main__":
    main()
