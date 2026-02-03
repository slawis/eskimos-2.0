#!/usr/bin/env python3
"""
PyInstaller Build Script for Eskimos 2.0 Gateway.

This script creates a self-contained Windows executable that includes:
- Python runtime
- All dependencies (FastAPI, pyppeteer, etc.)
- Chromium browser (for Puppeteer modem adapter)
- Static files and templates

Usage:
    python scripts/build_exe.py

Output:
    dist/EskimosGateway.exe (~250-300MB)

Requirements:
    pip install pyinstaller pyppeteer
    pyppeteer-install  # Downloads Chromium
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = PROJECT_ROOT / "EskimosGateway.spec"

# PyInstaller options
APP_NAME = "EskimosGateway"
ENTRY_POINT = SRC_DIR / "eskimos" / "__main__.py"
ICON_PATH = PROJECT_ROOT / "assets" / "icon.ico"  # Optional


def get_chromium_path() -> Path | None:
    """Find pyppeteer's Chromium installation."""
    try:
        import pyppeteer
        from pyppeteer import chromium_downloader

        chromium_path = Path(chromium_downloader.chromiumExecutable.get("win64", ""))
        if chromium_path.exists():
            return chromium_path.parent
    except ImportError:
        pass

    # Check common locations
    appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    pyppeteer_home = appdata / "pyppeteer" / "local-chromium"

    if pyppeteer_home.exists():
        # Find the latest revision
        revisions = sorted(pyppeteer_home.iterdir(), reverse=True)
        if revisions:
            chrome_dir = revisions[0] / "chrome-win"
            if chrome_dir.exists():
                return chrome_dir

    return None


def get_templates_path() -> Path | None:
    """Find templates directory."""
    templates = SRC_DIR / "eskimos" / "api" / "templates"
    if templates.exists():
        return templates
    return None


def get_static_path() -> Path | None:
    """Find static files directory."""
    static = SRC_DIR / "eskimos" / "api" / "static"
    if static.exists():
        return static
    return None


def ensure_chromium() -> Path:
    """Ensure Chromium is downloaded, return path."""
    chromium_path = get_chromium_path()
    if chromium_path:
        print(f"Found Chromium at: {chromium_path}")
        return chromium_path

    print("Chromium not found. Downloading...")
    try:
        import pyppeteer
        from pyppeteer import chromium_downloader

        chromium_downloader.download_chromium()
        chromium_path = get_chromium_path()
        if chromium_path:
            return chromium_path
    except Exception as e:
        print(f"Error downloading Chromium: {e}")

    raise RuntimeError(
        "Could not find or download Chromium. "
        "Please run: pyppeteer-install"
    )


def build_data_args() -> list[str]:
    """Build --add-data arguments for PyInstaller."""
    args = []

    # Add Chromium
    try:
        chromium_path = ensure_chromium()
        # On Windows, use semicolon as separator
        args.extend(["--add-data", f"{chromium_path};chromium"])
        print(f"Including Chromium from: {chromium_path}")
    except RuntimeError as e:
        print(f"Warning: {e}")
        print("Building without bundled Chromium")

    # Add templates
    templates = get_templates_path()
    if templates:
        args.extend(["--add-data", f"{templates};templates"])
        print(f"Including templates from: {templates}")

    # Add static files
    static = get_static_path()
    if static:
        args.extend(["--add-data", f"{static};static"])
        print(f"Including static files from: {static}")

    return args


def build_hidden_imports() -> list[str]:
    """Build --hidden-import arguments for PyInstaller."""
    # These modules are dynamically imported and PyInstaller might miss them
    hidden_imports = [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "httptools",
        "websockets",
        "win32timezone",
        "pyppeteer",
        "apscheduler.triggers.cron",
        "apscheduler.triggers.interval",
        "apscheduler.triggers.date",
        "apscheduler.schedulers.asyncio",
        "sqlalchemy.dialects.postgresql",
        "asyncpg",
        "jinja2",
        "email.mime.text",
        "email.mime.multipart",
    ]

    args = []
    for imp in hidden_imports:
        args.extend(["--hidden-import", imp])

    return args


def clean_build_dirs() -> None:
    """Clean previous build artifacts."""
    for dir_path in [DIST_DIR, BUILD_DIR]:
        if dir_path.exists():
            print(f"Cleaning {dir_path}...")
            shutil.rmtree(dir_path)

    if SPEC_FILE.exists():
        SPEC_FILE.unlink()


def run_pyinstaller() -> None:
    """Run PyInstaller to create the executable."""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name", APP_NAME,
        "--onefile",
        "--noconsole",  # No console window (GUI only)
        "--clean",
        "--noconfirm",
    ]

    # Add icon if exists
    if ICON_PATH.exists():
        cmd.extend(["--icon", str(ICON_PATH)])

    # Add data files
    cmd.extend(build_data_args())

    # Add hidden imports
    cmd.extend(build_hidden_imports())

    # Add paths
    cmd.extend(["--paths", str(SRC_DIR)])

    # Entry point
    cmd.append(str(ENTRY_POINT))

    print("\nRunning PyInstaller...")
    print(f"Command: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)

    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with code {result.returncode}")


def verify_build() -> None:
    """Verify the build was successful."""
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if not exe_path.exists():
        raise RuntimeError(f"Build failed: {exe_path} not found")

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\nBuild successful!")
    print(f"Executable: {exe_path}")
    print(f"Size: {size_mb:.1f} MB")


def main() -> None:
    """Main build process."""
    print("=" * 60)
    print("Eskimos 2.0 Gateway - PyInstaller Build")
    print("=" * 60)
    print()

    # Verify we're in the right directory
    if not (PROJECT_ROOT / "pyproject.toml").exists():
        raise RuntimeError(
            "Please run this script from the project root: "
            "python scripts/build_exe.py"
        )

    # Clean previous builds
    clean_build_dirs()

    # Run PyInstaller
    run_pyinstaller()

    # Verify build
    verify_build()

    print("\nNext steps:")
    print("1. Test the executable: dist/EskimosGateway.exe serve")
    print("2. Copy to target machine and run")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
