#!/usr/bin/env python3
"""
Build Portable Eskimos Gateway Distribution

Creates a self-contained .zip file that includes:
- Embedded Python 3.11
- All dependencies pre-installed
- Launcher scripts

Usage:
    python scripts/build_portable.py

Output:
    dist/EskimosGateway.zip (~50MB)
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

# Required packages
PACKAGES = [
    "fastapi>=0.108",
    "uvicorn[standard]>=0.25",
    "jinja2>=3.1",
    "python-multipart>=0.0.6",
    "httpx>=0.25",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "typer[all]>=0.9",
    "rich>=13.7",
    "aiofiles>=23.2",
    "structlog>=24.1",
    "phonenumbers>=8.13",
    "python-dotenv>=1.0",
    "pyserial>=3.5",
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
        # Add parent directory (where eskimos folder is) so Python can find the module
        content += "..\n"
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
set ESKIMOS=%~dp0eskimos

:: Set environment
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
set ESKIMOS=%~dp0eskimos

:: Set environment
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

    # DAEMON.bat - Phone Home Daemon
    daemon_bat = build_dir / "DAEMON.bat"
    daemon_bat.write_text(r'''@echo off
title Eskimos Daemon
cd /d "%~dp0"

:: Set paths
set PYTHON=%~dp0python\pythonw.exe
set ESKIMOS=%~dp0eskimos

:: Set environment
set PYTHONPATH=%ESKIMOS%

echo.
echo ========================================
echo    ESKIMOS DAEMON (Phone Home)
echo ========================================
echo.
echo Starting daemon in background...

:: Start daemon
start "Eskimos Daemon" /min cmd /c ""%~dp0python\python.exe" -m eskimos.infrastructure.daemon start"

echo.
echo Daemon started!
echo - Heartbeat: every 60 seconds
echo - Commands: polled every 60 seconds
echo - Auto-update: enabled
echo.
echo To stop: taskkill /f /fi "WINDOWTITLE eq Eskimos Daemon*"
pause
''', encoding='utf-8')

    # START_ALL.bat - Start Gateway + Daemon
    start_all_bat = build_dir / "START_ALL.bat"
    start_all_bat.write_text(r'''@echo off
title Eskimos Gateway + Daemon
cd /d "%~dp0"

:: Set paths
set PYTHON=%~dp0python\python.exe
set PYTHONW=%~dp0python\pythonw.exe
set ESKIMOS=%~dp0eskimos

:: Set environment
set PYTHONPATH=%ESKIMOS%

echo.
echo ========================================
echo    ESKIMOS FULL STARTUP
echo ========================================
echo.

:: 1. Start Daemon (background)
echo [1/2] Starting Daemon...
start "Eskimos Daemon" /min cmd /c ""%PYTHON%" -m eskimos.infrastructure.daemon start"
timeout /t 2 /nobreak >nul

:: 2. Start Gateway (background)
echo [2/2] Starting Gateway...
start "Eskimos Server" /min cmd /c ""%PYTHON%" -m eskimos.cli.main serve --host 0.0.0.0 --port 8000"
timeout /t 3 /nobreak >nul

:: 3. Open Dashboard
start http://localhost:8000/dashboard

echo.
echo ========================================
echo    ALL SERVICES STARTED
echo ========================================
echo.
echo - Daemon: Running (heartbeat + updates)
echo - Gateway: http://localhost:8000
echo - Dashboard: Opened in browser
echo.
echo To stop all: Run STOP_ALL.bat
pause
''', encoding='utf-8')

    # STOP_ALL.bat - Stop everything
    stop_all_bat = build_dir / "STOP_ALL.bat"
    stop_all_bat.write_text(r'''@echo off
echo Stopping all Eskimos services...

:: Stop CMD windows by title
taskkill /f /fi "WINDOWTITLE eq Eskimos*" 2>nul

:: Wait for CMD windows to close
timeout /t 2 /nobreak >nul

:: Kill ALL python processes (CMD child processes don't inherit window title)
taskkill /f /im python.exe 2>nul
taskkill /f /im pythonw.exe 2>nul

:: Remove PID file
if exist .daemon.pid del .daemon.pid

echo.
echo All services stopped.
pause
''', encoding='utf-8')

    # INSTALL_SERVICE.bat - Install as Windows Service (uses bundled NSSM)
    install_svc_bat = build_dir / "INSTALL_SERVICE.bat"
    install_svc_bat.write_text(r'''@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ============================================
::  ESKIMOS GATEWAY - Windows Service Installer
::  Installs as auto-start Windows Services
:: ============================================

title Eskimos Service Installer

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  ========================================================
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    echo  ========================================================
    echo.
    echo   Kliknij prawym przyciskiem na ten plik i wybierz:
    echo   "Uruchom jako administrator"
    echo.
    pause
    exit /b 1
)

:: Set paths
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "NSSM=%ROOT%\tools\nssm.exe"
set "PYTHON=%ROOT%\python\python.exe"
set "PYTHONW=%ROOT%\python\pythonw.exe"

:: Check NSSM exists
if not exist "%NSSM%" (
    echo [ERROR] NSSM not found: %NSSM%
    echo Please ensure tools\nssm.exe exists.
    pause
    exit /b 1
)

echo.
echo  ========================================================
echo          ESKIMOS GATEWAY - SERVICE INSTALLER
echo  ========================================================
echo.
echo   Ten skrypt zainstaluje dwa serwisy Windows:
echo.
echo   1. EskimosGateway  - Glowna aplikacja (API + Dashboard)
echo   2. EskimosDaemon   - Phone-home daemon (heartbeat)
echo.
echo   Serwisy uruchomia sie automatycznie po starcie Windows.
echo.
echo  ========================================================
echo.

:: Confirm installation
set /p CONFIRM="Czy chcesz zainstalowac serwisy? (T/N): "
if /i not "%CONFIRM%"=="T" (
    echo Anulowano.
    pause
    exit /b 0
)

echo.
echo [1/4] Sprawdzanie istniejacych serwisow...

:: Stop existing services if running
"%NSSM%" status EskimosGateway >nul 2>&1
if %errorLevel% equ 0 (
    echo       Zatrzymywanie EskimosGateway...
    "%NSSM%" stop EskimosGateway >nul 2>&1
    "%NSSM%" remove EskimosGateway confirm >nul 2>&1
)

"%NSSM%" status EskimosDaemon >nul 2>&1
if %errorLevel% equ 0 (
    echo       Zatrzymywanie EskimosDaemon...
    "%NSSM%" stop EskimosDaemon >nul 2>&1
    "%NSSM%" remove EskimosDaemon confirm >nul 2>&1
)

echo       OK - Gotowe do instalacji
echo.

:: Create logs directory
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

:: ============================================
:: Install EskimosGateway Service
:: ============================================
echo [2/4] Instalacja EskimosGateway...

"%NSSM%" install EskimosGateway "%PYTHON%"
"%NSSM%" set EskimosGateway AppParameters "-m eskimos.cli.main serve"
"%NSSM%" set EskimosGateway AppDirectory "%ROOT%"
"%NSSM%" set EskimosGateway AppEnvironmentExtra "PYTHONPATH=%ROOT%\eskimos"
"%NSSM%" set EskimosGateway DisplayName "Eskimos SMS Gateway"
"%NSSM%" set EskimosGateway Description "Eskimos SMS Gateway - API and Dashboard for SMS automation"
"%NSSM%" set EskimosGateway Start SERVICE_AUTO_START
"%NSSM%" set EskimosGateway AppStdout "%ROOT%\logs\gateway_service.log"
"%NSSM%" set EskimosGateway AppStderr "%ROOT%\logs\gateway_error.log"
"%NSSM%" set EskimosGateway AppRotateFiles 1
"%NSSM%" set EskimosGateway AppRotateBytes 10485760

echo       OK - EskimosGateway zainstalowany
echo.

:: ============================================
:: Install EskimosDaemon Service
:: ============================================
echo [3/4] Instalacja EskimosDaemon...

"%NSSM%" install EskimosDaemon "%PYTHONW%"
"%NSSM%" set EskimosDaemon AppParameters "-m eskimos.infrastructure.daemon start"
"%NSSM%" set EskimosDaemon AppDirectory "%ROOT%"
"%NSSM%" set EskimosDaemon AppEnvironmentExtra "PYTHONPATH=%ROOT%\eskimos"
"%NSSM%" set EskimosDaemon DisplayName "Eskimos Phone-Home Daemon"
"%NSSM%" set EskimosDaemon Description "Eskimos Daemon - Heartbeat and remote management"
"%NSSM%" set EskimosDaemon Start SERVICE_AUTO_START
"%NSSM%" set EskimosDaemon AppStdout "%ROOT%\logs\daemon_service.log"
"%NSSM%" set EskimosDaemon AppStderr "%ROOT%\logs\daemon_error.log"
"%NSSM%" set EskimosDaemon AppRotateFiles 1
"%NSSM%" set EskimosDaemon AppRotateBytes 10485760
"%NSSM%" set EskimosDaemon DependOnService EskimosGateway

echo       OK - EskimosDaemon zainstalowany
echo.

:: ============================================
:: Start services
:: ============================================
echo [4/4] Uruchamianie serwisow...

"%NSSM%" start EskimosGateway
timeout /t 3 /nobreak >nul
"%NSSM%" start EskimosDaemon

echo.
echo  ========================================================
echo              INSTALACJA ZAKONCZONA POMYSLNIE
echo  ========================================================
echo.
echo   Serwisy zostaly zainstalowane i uruchomione.
echo   Uruchomia sie automatycznie po kazdym starcie Windows.
echo.
echo   Zarzadzanie:
echo     - SERVICE_STATUS.bat    - sprawdz status
echo     - SERVICE_STOP.bat      - zatrzymaj serwisy
echo     - SERVICE_START.bat     - uruchom serwisy
echo     - UNINSTALL_SERVICE.bat - odinstaluj
echo.
echo   Lub przez Windows Services (services.msc):
echo     - Eskimos SMS Gateway
echo     - Eskimos Phone-Home Daemon
echo.
echo  ========================================================
echo.

pause
''', encoding='utf-8')

    # SERVICE_STATUS.bat
    svc_status_bat = build_dir / "SERVICE_STATUS.bat"
    svc_status_bat.write_text(r'''@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "NSSM=%ROOT%\tools\nssm.exe"

echo.
echo  ========================================================
echo            ESKIMOS GATEWAY - STATUS SERWISOW
echo  ========================================================
echo.

echo   EskimosGateway (API + Dashboard):
"%NSSM%" status EskimosGateway 2>nul
if %errorLevel% neq 0 (
    echo     Status: NIE ZAINSTALOWANY
)
echo.

echo   EskimosDaemon (Phone-Home):
"%NSSM%" status EskimosDaemon 2>nul
if %errorLevel% neq 0 (
    echo     Status: NIE ZAINSTALOWANY
)
echo.

echo  ========================================================
echo.

if /i not "%1"=="nopause" pause
''', encoding='utf-8')

    # SERVICE_START.bat
    svc_start_bat = build_dir / "SERVICE_START.bat"
    svc_start_bat.write_text(r'''@echo off
chcp 65001 >nul 2>&1

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    pause
    exit /b 1
)

set "ROOT=%~dp0"
set "NSSM=%ROOT%tools\nssm.exe"

echo.
echo   Uruchamianie serwisow Eskimos...
echo.

"%NSSM%" start EskimosGateway
timeout /t 2 /nobreak >nul
"%NSSM%" start EskimosDaemon

echo.
echo   Serwisy uruchomione.
echo.
call "%ROOT%SERVICE_STATUS.bat" nopause
pause
''', encoding='utf-8')

    # SERVICE_STOP.bat
    svc_stop_bat = build_dir / "SERVICE_STOP.bat"
    svc_stop_bat.write_text(r'''@echo off
chcp 65001 >nul 2>&1

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    pause
    exit /b 1
)

set "ROOT=%~dp0"
set "NSSM=%ROOT%tools\nssm.exe"

echo.
echo   Zatrzymywanie serwisow Eskimos...
echo.

"%NSSM%" stop EskimosDaemon
"%NSSM%" stop EskimosGateway

echo.
echo   Serwisy zatrzymane.
echo.
call "%ROOT%SERVICE_STATUS.bat" nopause
pause
''', encoding='utf-8')

    # UNINSTALL_SERVICE.bat
    uninstall_svc_bat = build_dir / "UNINSTALL_SERVICE.bat"
    uninstall_svc_bat.write_text(r'''@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

title Eskimos Service Uninstaller

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    echo   Kliknij prawym przyciskiem i wybierz "Uruchom jako administrator"
    echo.
    pause
    exit /b 1
)

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "NSSM=%ROOT%\tools\nssm.exe"

echo.
echo  ========================================================
echo         ESKIMOS GATEWAY - SERVICE UNINSTALLER
echo  ========================================================
echo.
echo   Ten skrypt usunie serwisy Eskimos z systemu.
echo.

set /p CONFIRM="Czy na pewno chcesz odinstalowac serwisy? (T/N): "
if /i not "%CONFIRM%"=="T" (
    echo Anulowano.
    pause
    exit /b 0
)

echo.
echo [1/2] Zatrzymywanie i usuwanie EskimosDaemon...
"%NSSM%" stop EskimosDaemon >nul 2>&1
"%NSSM%" remove EskimosDaemon confirm >nul 2>&1
echo       OK

echo.
echo [2/2] Zatrzymywanie i usuwanie EskimosGateway...
"%NSSM%" stop EskimosGateway >nul 2>&1
"%NSSM%" remove EskimosGateway confirm >nul 2>&1
echo       OK

echo.
echo  ========================================================
echo              DEINSTALACJA ZAKONCZONA
echo  ========================================================
echo.
echo   Serwisy zostaly usuniete.
echo   Mozesz uruchamiac Eskimos recznie przez START_ALL.bat
echo.
echo  ========================================================
echo.

pause
''', encoding='utf-8')


def copy_tools(build_dir: Path) -> None:
    """Copy NSSM and other tools."""
    tools_src = PROJECT_ROOT / "tools"
    tools_dest = build_dir / "tools"
    tools_dest.mkdir(exist_ok=True)

    nssm_src = tools_src / "nssm.exe"
    if nssm_src.exists():
        print("Copying NSSM service manager...")
        shutil.copy2(nssm_src, tools_dest / "nssm.exe")
    else:
        print("WARNING: tools/nssm.exe not found! INSTALL_SERVICE.bat won't work.")


def copy_readme(build_dir: Path) -> None:
    """Copy README.txt to build."""
    readme_src = PROJECT_ROOT / "scripts" / "README.txt"
    if readme_src.exists():
        print("Copying README.txt...")
        shutil.copy2(readme_src, build_dir / "README.txt")
    else:
        print("WARNING: scripts/README.txt not found!")


def create_config(build_dir: Path) -> None:
    """Create default configuration."""
    config_dir = build_dir / "config"
    config_dir.mkdir(exist_ok=True)

    env_file = config_dir / ".env"
    env_file.write_text('''# Eskimos Gateway Configuration
# ================================

# Modem type: "serial" (SIM7600G-H AT commands) or "ik41" (Alcatel JSON-RPC)
MODEM_TYPE=serial

# Phone number in modem SIM card
ESKIMOS_MODEM_PHONE=WPISZ_NUMER

# Serial modem settings (for MODEM_TYPE=serial)
# auto = auto-detect COM port, or set explicitly e.g. COM6
SERIAL_PORT=auto
SERIAL_BAUDRATE=115200

# Legacy IK41 modem settings (for MODEM_TYPE=ik41)
# ESKIMOS_MODEM_HOST=192.168.1.1

# Debug mode
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
    copy_eskimos(BUILD_DIR)
    create_batch_files(BUILD_DIR)
    copy_tools(BUILD_DIR)
    copy_readme(BUILD_DIR)
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
    print("3. Run INSTALL_SERVICE.bat as Administrator")
    print("4. Done! Auto-start on boot, remote updates enabled.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
