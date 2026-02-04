#!/usr/bin/env python3
"""
Build Portable Eskimos Gateway Distribution

Creates a self-contained .zip file that includes:
- Embedded Python 3.11
- Chrome for Testing
- All dependencies pre-installed
- Launcher scripts

Usage:
    python scripts/build_portable.py

Output:
    dist/EskimosGateway.zip (~200MB)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
BUILD_DIR = PROJECT_ROOT / "portable" / "build"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_NAME = "EskimosGateway"

# Download URLs
PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.11.7/python-3.11.7-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# Chrome for Testing - stable version
CHROME_URL = "https://storage.googleapis.com/chrome-for-testing-public/131.0.6778.85/win64/chrome-win64.zip"

# Required packages
PACKAGES = [
    "fastapi>=0.108",
    "uvicorn[standard]>=0.25",
    "jinja2>=3.1",
    "python-multipart>=0.0.6",
    "pyppeteer>=2.0.0",
    "httpx>=0.25",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "typer[all]>=0.9",
    "rich>=13.7",
    "aiofiles>=23.2",
    "structlog>=24.1",
    "phonenumbers>=8.13",
    "python-dotenv>=1.0",
]


def download_file(url: str, dest: Path, desc: str = "") -> None:
    """Download a file with progress."""
    print(f"Downloading {desc or url}...")

    def progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            percent = min(100, downloaded * 100 / total_size)
            mb = downloaded / (1024 * 1024)
            print(f"\r  {percent:.1f}% ({mb:.1f} MB)", end="", flush=True)

    urllib.request.urlretrieve(url, dest, progress)
    print()


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract a zip file."""
    print(f"Extracting {zip_path.name}...")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest_dir)


def setup_python(build_dir: Path) -> Path:
    """Setup embedded Python with pip."""
    python_dir = build_dir / "python"
    python_dir.mkdir(exist_ok=True)

    # Download embedded Python
    python_zip = build_dir / "python-embed.zip"
    if not python_zip.exists():
        download_file(PYTHON_EMBED_URL, python_zip, "Embedded Python 3.11")

    # Extract
    extract_zip(python_zip, python_dir)

    # Fix python311._pth to allow importing from site-packages
    pth_file = python_dir / "python311._pth"
    if pth_file.exists():
        content = pth_file.read_text()
        # Uncomment import site
        content = content.replace("#import site", "import site")
        # Add paths
        if "Lib\\site-packages" not in content:
            content += "\nLib\\site-packages\n"
        content += "..\\eskimos\n"
        pth_file.write_text(content)

    # Download get-pip.py
    get_pip = build_dir / "get-pip.py"
    if not get_pip.exists():
        download_file(GET_PIP_URL, get_pip, "get-pip.py")

    # Install pip
    python_exe = python_dir / "python.exe"
    print("Installing pip...")
    subprocess.run(
        [str(python_exe), str(get_pip), "--no-warn-script-location"],
        cwd=python_dir,
        check=True
    )

    return python_dir


def install_packages(python_dir: Path) -> None:
    """Install required packages."""
    python_exe = python_dir / "python.exe"

    print("\nInstalling packages...")
    for pkg in PACKAGES:
        print(f"  Installing {pkg}...")
        subprocess.run(
            [str(python_exe), "-m", "pip", "install", pkg,
             "--no-warn-script-location", "-q"],
            check=True
        )


def setup_chrome(build_dir: Path) -> Path:
    """Download and setup Chrome for Testing."""
    chrome_dir = build_dir / "chromium"
    chrome_dir.mkdir(exist_ok=True)

    # Download Chrome
    chrome_zip = build_dir / "chrome.zip"
    if not chrome_zip.exists():
        download_file(CHROME_URL, chrome_zip, "Chrome for Testing")

    # Extract
    extract_zip(chrome_zip, build_dir)

    # Move to chromium folder (Chrome extracts to chrome-win64/)
    chrome_extracted = build_dir / "chrome-win64"
    if chrome_extracted.exists():
        for item in chrome_extracted.iterdir():
            dest = chrome_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(chrome_dir))
        chrome_extracted.rmdir()

    return chrome_dir


def copy_eskimos(build_dir: Path) -> None:
    """Copy eskimos source code."""
    src_dir = PROJECT_ROOT / "src" / "eskimos"
    dest_dir = build_dir / "eskimos"

    print("Copying eskimos source...")
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(src_dir, dest_dir)

    # Copy templates
    templates_src = src_dir / "api" / "templates"
    if templates_src.exists():
        templates_dest = dest_dir / "api" / "templates"
        if not templates_dest.exists():
            shutil.copytree(templates_src, templates_dest)


def create_batch_files(build_dir: Path) -> None:
    """Create launcher batch files."""
    print("Creating launcher scripts...")

    # START.bat
    start_bat = build_dir / "START.bat"
    start_bat.write_text(r'''@echo off
title Eskimos Gateway
cd /d "%~dp0"

:: Set paths
set PYTHON=%~dp0python\python.exe
set CHROMIUM=%~dp0chromium\chrome.exe
set ESKIMOS=%~dp0eskimos

:: Set environment
set PYPPETEER_EXECUTABLE_PATH=%CHROMIUM%
set PYTHONPATH=%ESKIMOS%

echo.
echo ========================================
echo    ESKIMOS SMS GATEWAY
echo ========================================
echo.
echo Starting server...

:: Start server
"%PYTHON%" -m eskimos.cli.main serve --host 0.0.0.0 --port 8000

pause
''', encoding='utf-8')

    # START_DASHBOARD.bat (opens browser)
    start_dash = build_dir / "START_DASHBOARD.bat"
    start_dash.write_text(r'''@echo off
cd /d "%~dp0"

:: Set paths
set PYTHON=%~dp0python\pythonw.exe
set CHROMIUM=%~dp0chromium\chrome.exe
set ESKIMOS=%~dp0eskimos

:: Set environment
set PYPPETEER_EXECUTABLE_PATH=%CHROMIUM%
set PYTHONPATH=%ESKIMOS%

:: Start server in background
start "Eskimos Server" /min cmd /c ""%~dp0python\python.exe" -m eskimos.cli.main serve --host 0.0.0.0 --port 8000"

:: Wait for server to start
echo Starting Eskimos Gateway...
timeout /t 3 /nobreak >nul

:: Open dashboard
start http://localhost:8000/dashboard

echo.
echo Dashboard opened in browser!
echo Server is running in background.
echo.
echo To stop: Close the "Eskimos Server" window or run STOP.bat
pause
''', encoding='utf-8')

    # STOP.bat
    stop_bat = build_dir / "STOP.bat"
    stop_bat.write_text(r'''@echo off
echo Stopping Eskimos Gateway...
taskkill /f /im python.exe /fi "WINDOWTITLE eq Eskimos*" 2>nul
taskkill /f /im pythonw.exe 2>nul
echo Done.
pause
''', encoding='utf-8')

    # UPDATE.bat
    update_bat = build_dir / "UPDATE.bat"
    update_bat.write_text(r'''@echo off
cd /d "%~dp0"
echo.
echo Updating Eskimos Gateway...
echo.

:: Download latest code
curl -L -o eskimos-latest.zip https://github.com/slawis/eskimos-2.0/archive/refs/heads/master.zip
if errorlevel 1 (
    echo ERROR: Failed to download update!
    pause
    exit /b 1
)

:: Backup current eskimos folder
if exist eskimos.bak rmdir /s /q eskimos.bak
if exist eskimos rename eskimos eskimos.bak

:: Extract
tar -xf eskimos-latest.zip
if errorlevel 1 (
    echo ERROR: Failed to extract update!
    if exist eskimos.bak rename eskimos.bak eskimos
    pause
    exit /b 1
)

:: Move new code
move eskimos-2.0-master\src\eskimos eskimos

:: Cleanup
del eskimos-latest.zip
rmdir /s /q eskimos-2.0-master
if exist eskimos.bak rmdir /s /q eskimos.bak

echo.
echo Update complete!
echo Restart the gateway to apply changes.
pause
''', encoding='utf-8')

    # CONFIG.bat
    config_bat = build_dir / "CONFIG.bat"
    config_bat.write_text(r'''@echo off
cd /d "%~dp0"
if not exist config mkdir config
if not exist config\.env (
    echo # Eskimos Gateway Configuration > config\.env
    echo ESKIMOS_MODEM_HOST=192.168.1.1 >> config\.env
    echo ESKIMOS_MODEM_PHONE=886480453 >> config\.env
)
notepad config\.env
''', encoding='utf-8')


def create_config(build_dir: Path) -> None:
    """Create default configuration."""
    config_dir = build_dir / "config"
    config_dir.mkdir(exist_ok=True)

    env_file = config_dir / ".env"
    env_file.write_text('''# Eskimos Gateway Configuration
ESKIMOS_MODEM_HOST=192.168.1.1
ESKIMOS_MODEM_PHONE=886480453
ESKIMOS_DEBUG=false
''')


def create_zip(build_dir: Path, output_path: Path) -> None:
    """Create final zip package."""
    print(f"\nCreating {output_path.name}...")

    # Remove old zip if exists
    if output_path.exists():
        output_path.unlink()

    # Create zip
    shutil.make_archive(
        str(output_path.with_suffix('')),
        'zip',
        build_dir.parent,
        build_dir.name
    )

    # Get size
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Created: {output_path}")
    print(f"Size: {size_mb:.1f} MB")


def main() -> None:
    """Main build process."""
    print("=" * 60)
    print("Eskimos Gateway - Portable Distribution Builder")
    print("=" * 60)
    print()

    # Create directories
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(exist_ok=True)

    # Setup components
    python_dir = setup_python(BUILD_DIR)
    install_packages(python_dir)
    setup_chrome(BUILD_DIR)
    copy_eskimos(BUILD_DIR)
    create_batch_files(BUILD_DIR)
    create_config(BUILD_DIR)

    # Rename build dir to output name
    output_dir = BUILD_DIR.parent / OUTPUT_NAME
    if output_dir.exists():
        shutil.rmtree(output_dir)
    BUILD_DIR.rename(output_dir)

    # Create zip
    output_zip = DIST_DIR / f"{OUTPUT_NAME}.zip"
    create_zip(output_dir, output_zip)

    print("\n" + "=" * 60)
    print("BUILD COMPLETE!")
    print("=" * 60)
    print(f"\nOutput: {output_zip}")
    print("\nNext steps:")
    print("1. Upload to GitHub Releases or share via Google Drive")
    print("2. User downloads and extracts to C:\\EskimosGateway\\")
    print("3. Double-click START_DASHBOARD.bat")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
