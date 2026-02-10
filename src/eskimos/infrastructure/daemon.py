"""
Eskimos Daemon - Phone Home System

Daemon dziala w tle i:
1. Wysyla heartbeat do centrali co 60s
2. Polluje komendy (update, restart, config) co 60s
3. Wykonuje auto-update gdy jest nowa wersja
4. Restartuje serwis po update (graceful)

Uruchomienie:
    python -m eskimos.infrastructure.daemon

Lub przez DAEMON.bat w paczce portable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import secrets
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lazy imports dla szybszego startu
httpx = None
HAS_HTTPX = False

try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass

serial_mod = None
HAS_SERIAL = False
try:
    import serial as _serial
    import serial.tools.list_ports as _list_ports
    serial_mod = _serial
    HAS_SERIAL = True
except ImportError:
    pass


# ==================== Configuration ====================

# Sciezki relatywne do katalogu portable
PORTABLE_ROOT = Path(__file__).parent.parent.parent  # EskimosGateway/
CLIENT_KEY_FILE = PORTABLE_ROOT / ".client_key"
LOG_FILE = PORTABLE_ROOT / "daemon.log"
PID_FILE = PORTABLE_ROOT / ".daemon.pid"
CONFIG_FILE = PORTABLE_ROOT / "config" / ".env"
BACKUP_DIR = PORTABLE_ROOT / "_backups"
UPDATE_DIR = PORTABLE_ROOT / "_updates"
PROCESSED_SMS_FILE = PORTABLE_ROOT / ".processed_sms.json"

# Load config/.env into os.environ (no external dependency needed)
if CONFIG_FILE.exists():
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:  # don't override system env
                os.environ[key] = value

# API
CENTRAL_API = os.getenv("ESKIMOS_CENTRAL_API", "https://app.ninjabot.pl/api/eskimos")
ESKIMOS_PHP_API = os.getenv("ESKIMOS_PHP_API", "https://eskimos.ninjabot.pl/api/v2")
HEARTBEAT_API_KEY = os.getenv("ESKIMOS_API_KEY", "eskimos-daemon-2026")

# Interwaly (sekundy)
HEARTBEAT_INTERVAL = int(os.getenv("ESKIMOS_HEARTBEAT_INTERVAL", "60"))
COMMAND_POLL_INTERVAL = int(os.getenv("ESKIMOS_COMMAND_POLL_INTERVAL", "60"))
UPDATE_CHECK_INTERVAL = int(os.getenv("ESKIMOS_UPDATE_CHECK_INTERVAL", "3600"))
SMS_POLL_INTERVAL = int(os.getenv("ESKIMOS_SMS_POLL_INTERVAL", "15"))
INCOMING_SMS_INTERVAL = int(os.getenv("ESKIMOS_INCOMING_SMS_INTERVAL", "15"))

# Auto-update
AUTO_UPDATE_ENABLED = os.getenv("ESKIMOS_AUTO_UPDATE", "true").lower() == "true"

# Rate limiting
SMS_DAILY_LIMIT = int(os.getenv("ESKIMOS_SMS_DAILY_LIMIT", "100"))
SMS_HOURLY_LIMIT = int(os.getenv("ESKIMOS_SMS_HOURLY_LIMIT", "20"))

# SMS counters (runtime, reset on daemon restart)
_sms_sent_today = 0
_sms_sent_total = 0
_sms_received_today = 0
_sms_received_total = 0
_last_sms_error = ""
_sms_modem = None
_processed_sms_ids = set()  # Track processed SMSIds to prevent duplicates
_sms_hourly_count = 0
_sms_hourly_reset_time = 0
_sms_rate_limited = False

# SMS storage monitoring
SMS_STORAGE_CHECK_INTERVAL = 1 * 3600  # 1 hour (auto-reset needs frequent checks)
SMS_STORAGE_WARN_PERCENT = 80  # Auto-reset when storage > 80% full
SMS_STORAGE_AUTO_RESET = True  # Enable automatic factory reset on full storage
_sms_storage_used = 0
_sms_storage_max = 100
_auto_reset_in_progress = False


# ==================== Logging ====================

def log(message: str) -> None:
    """Log message to file and stdout."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ==================== Client Key ====================

def get_or_create_client_key() -> str:
    """Get existing client key or generate new one."""
    if CLIENT_KEY_FILE.exists():
        return CLIENT_KEY_FILE.read_text().strip()

    # Generate new key
    key = f"esk_{secrets.token_hex(32)}"
    CLIENT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_KEY_FILE.write_text(key)
    log(f"Generated new client key: {key[:12]}...")
    return key


# ==================== Processed SMS Persistence ====================

def load_processed_sms_ids() -> set:
    """Load processed SMS IDs from disk to survive daemon restarts."""
    if PROCESSED_SMS_FILE.exists():
        try:
            data = json.loads(PROCESSED_SMS_FILE.read_text(encoding="utf-8"))
            ids = set(data.get("ids", []))
            log(f"Loaded {len(ids)} processed SMS IDs from disk")
            return ids
        except Exception as e:
            log(f"Error loading processed SMS IDs: {e}")
    return set()


def save_processed_sms_ids() -> None:
    """Save processed SMS IDs to disk."""
    global _processed_sms_ids
    try:
        # Cap at 10000 entries - keep newest (SMSId is monotonically increasing on IK41)
        if len(_processed_sms_ids) > 10000:
            sorted_ids = sorted(_processed_sms_ids)
            _processed_sms_ids = set(sorted_ids[-5000:])

        data = {
            "ids": list(_processed_sms_ids),
            "count": len(_processed_sms_ids),
            "updated_at": datetime.now().isoformat(),
        }
        PROCESSED_SMS_FILE.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log(f"Error saving processed SMS IDs: {e}")


# ==================== System Info ====================

def get_system_info() -> dict:
    """Get system information."""
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


def get_uptime() -> int:
    """Get daemon uptime in seconds."""
    if not hasattr(get_uptime, "_start_time"):
        get_uptime._start_time = time.time()
    return int(time.time() - get_uptime._start_time)


# ==================== Modem Status ====================

MODEM_HOST = os.getenv("MODEM_HOST", "192.168.1.1")
MODEM_PORT = int(os.getenv("MODEM_PORT", "80"))
MODEM_PHONE = os.getenv("ESKIMOS_MODEM_PHONE", os.getenv("MODEM_PHONE_NUMBER", ""))

# Modem type: "serial" (SIM7600G-H AT commands) or "ik41" (Alcatel JSON-RPC)
MODEM_TYPE = os.getenv("MODEM_TYPE", "ik41")
SERIAL_PORT = os.getenv("SERIAL_PORT", "auto")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))

# Cached serial port (resolved once on first use)
_cached_serial_port = None


async def _resolve_serial_port() -> str | None:
    """Resolve serial port - auto-detect or use explicit config."""
    global _cached_serial_port
    if _cached_serial_port:
        return _cached_serial_port

    if SERIAL_PORT != "auto":
        _cached_serial_port = SERIAL_PORT
        return SERIAL_PORT

    if not HAS_SERIAL:
        log("Serial port auto-detect: pyserial not installed")
        return None

    def _detect():
        import serial.tools.list_ports as list_ports
        # First: scan by device description/hwid
        for port_info in list_ports.comports():
            desc = (port_info.description or "").upper()
            hwid = (port_info.hwid or "").upper()
            if "SIMCOM" in desc or "SIM7600" in desc or "1E0E" in hwid:
                try:
                    ser = serial_mod.Serial(port_info.device, SERIAL_BAUDRATE, timeout=2)
                    resp = _at_send(ser, "AT", timeout=2)
                    ser.close()
                    if "OK" in resp:
                        return port_info.device
                except Exception:
                    pass
        # Fallback: brute-force COM1-COM20 with ATI
        for i in range(1, 21):
            port = f"COM{i}"
            try:
                ser = serial_mod.Serial(port, SERIAL_BAUDRATE, timeout=2)
                resp = _at_send(ser, "ATI", timeout=3)
                ser.close()
                if "SIMCOM" in resp or "SIM7600" in resp:
                    return port
            except Exception:
                pass
        return None

    port = await asyncio.get_event_loop().run_in_executor(None, _detect)
    if port:
        _cached_serial_port = port
        log(f"Serial port auto-detected: {port}")
    else:
        log("Serial port auto-detect FAILED - no SIMCOM modem found")
    return port


async def probe_modem_direct() -> bool:
    """Direct TCP probe to modem IP - fallback when Gateway API is down."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(MODEM_HOST, MODEM_PORT), timeout=3.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False


_modem_model_cache = None


async def detect_modem_model_tcl() -> dict:
    """Detect TCL/Alcatel modem model via JRD webapi login."""
    global _modem_model_cache
    if _modem_model_cache:
        return _modem_model_cache

    if not HAS_HTTPX:
        return {}

    import re
    result = {"model": "", "manufacturer": "", "connection_type": "RNDIS/USB"}
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            # Get main page and extract TCL verification token
            resp = await client.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if not m:
                return result

            token = m.group(1)
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{MODEM_HOST}/index.html",
            }
            result["manufacturer"] = "Alcatel/TCL"

            # Login
            login_body = {
                "jsonrpc": "2.0", "method": "Login",
                "params": {"UserName": "admin", "Password": "admin"}, "id": "1"
            }
            resp = await client.post(f"{base_url}/jrd/webapi",
                                     json=login_body, headers=headers)
            if "result" not in resp.text or "error" in resp.text.lower():
                return result

            # GetSystemInfo
            body = {"jsonrpc": "2.0", "method": "GetSystemInfo",
                    "params": {}, "id": "1"}
            resp = await client.post(f"{base_url}/jrd/webapi",
                                     json=body, headers=headers)
            m = re.search(r'"DeviceName"\s*:\s*"([^"]+)"', resp.text)
            if m:
                result["model"] = m.group(1).strip()
                hw = re.search(r'"HwVersion"\s*:\s*"([^"]+)"', resp.text)
                if hw:
                    result["model"] = f"{result['model']} ({hw.group(1).strip()})"

            # Logout
            try:
                await client.post(f"{base_url}/jrd/webapi",
                                  json={"jsonrpc": "2.0", "method": "Logout",
                                        "params": {}, "id": "1"},
                                  headers=headers)
            except Exception:
                pass

            if result["model"]:
                _modem_model_cache = result

    except Exception as e:
        log(f"TCL detection error: {e}")

    return result


GATEWAY_PORT = int(os.getenv("ESKIMOS_GATEWAY_PORT", "8000"))


async def _get_modem_status_via_gateway() -> dict | None:
    """Get modem status by querying local Gateway API (localhost:8000/api/health).

    The Gateway process can see USB COM ports that the daemon process sometimes cannot
    on Windows (different process context). This provides a reliable fallback.
    """
    try:
        if not HAS_HTTPX:
            return None
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://127.0.0.1:{GATEWAY_PORT}/api/health")
            if resp.status_code != 200:
                return None
            data = resp.json()
            modem = data.get("modem", {})
            if not modem.get("connected"):
                return None
            return {
                "status": "connected",
                "phone_number": modem.get("phone_number", MODEM_PHONE),
                "model": modem.get("model", ""),
                "manufacturer": modem.get("manufacturer", ""),
                "connection_type": modem.get("connection_type", "Serial/USB"),
                "signal_strength": modem.get("signal_strength"),
                "network": modem.get("network", ""),
            }
    except Exception as e:
        log(f"Gateway API modem status failed: {e}")
        return None


async def get_modem_status() -> dict:
    """Get modem status - branches on MODEM_TYPE."""
    if MODEM_TYPE == "serial":
        # Try Gateway API first (Gateway process can see COM ports reliably)
        gw_status = await _get_modem_status_via_gateway()
        if gw_status:
            return gw_status
        # Fallback: direct serial probe
        return await get_modem_status_serial()

    # IK41/TCL: direct TCP probe + JSON-RPC model detection
    reachable = await probe_modem_direct()

    if not reachable:
        global _modem_model_cache
        _modem_model_cache = None
        return {
            "status": "disconnected",
            "phone_number": "",
            "model": "",
            "manufacturer": "",
            "connection_type": "",
        }

    # Direct model detection (cached)
    hw = await detect_modem_model_tcl()

    return {
        "status": "connected",
        "phone_number": MODEM_PHONE,
        "model": hw.get("model", ""),
        "manufacturer": hw.get("manufacturer", ""),
        "connection_type": hw.get("connection_type", "RNDIS/USB"),
    }


async def get_modem_status_serial() -> dict:
    """Get modem status via serial AT commands (SIM7600G-H)."""
    import re

    port = await _resolve_serial_port()
    if not port:
        return {
            "status": "disconnected",
            "phone_number": "",
            "model": "",
            "manufacturer": "",
            "connection_type": "",
        }

    def _probe():
        try:
            ser = serial_mod.Serial(port, SERIAL_BAUDRATE, timeout=3)
            resp = _at_send(ser, "AT")
            if "OK" not in resp:
                ser.close()
                return None
            ati = _at_send(ser, "ATI")
            csq = _at_send(ser, "AT+CSQ")
            cops = _at_send(ser, "AT+COPS?")
            ser.close()
            return {"ati": ati, "csq": csq, "cops": cops}
        except Exception as e:
            log(f"Serial probe error: {e}")
            return None

    info = await asyncio.get_event_loop().run_in_executor(None, _probe)
    if not info:
        return {
            "status": "disconnected",
            "phone_number": MODEM_PHONE,
            "model": "",
            "manufacturer": "",
            "connection_type": "Serial/USB",
        }

    # Parse ATI → model
    model = ""
    manufacturer = "SIMCOM"
    ati = info["ati"]
    if "SIM7600" in ati:
        m = re.search(r"(SIM\d+\S*)", ati)
        model = m.group(1) if m else "SIM7600G-H"
    elif "Manufacturer" in ati:
        m = re.search(r"Model:\s*(.+)", ati)
        model = m.group(1).strip() if m else ati.split("\n")[0]

    # Parse CSQ → signal 0-100
    signal_pct = None
    m = re.search(r"\+CSQ:\s*(\d+)", info["csq"])
    if m:
        rssi = int(m.group(1))
        if rssi <= 31:
            signal_pct = round(rssi / 31 * 100)

    # Parse COPS → operator
    operator_name = ""
    m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', info["cops"])
    if m:
        operator_name = m.group(1)

    return {
        "status": "connected",
        "phone_number": MODEM_PHONE,
        "model": model,
        "manufacturer": manufacturer,
        "connection_type": "Serial/USB",
        "signal_strength": signal_pct,
        "network": operator_name,
    }


def check_rate_limit() -> tuple:
    """Check if SMS sending is within rate limits.

    Returns (allowed: bool, reason: str).
    """
    global _sms_hourly_count, _sms_hourly_reset_time, _sms_rate_limited

    now = time.time()

    # Reset hourly counter every hour
    if now - _sms_hourly_reset_time >= 3600:
        _sms_hourly_count = 0
        _sms_hourly_reset_time = now

    # Check daily limit
    if _sms_sent_today >= SMS_DAILY_LIMIT:
        _sms_rate_limited = True
        return False, f"Daily limit reached: {_sms_sent_today}/{SMS_DAILY_LIMIT}"

    # Check hourly limit
    if _sms_hourly_count >= SMS_HOURLY_LIMIT:
        _sms_rate_limited = True
        return False, f"Hourly limit reached: {_sms_hourly_count}/{SMS_HOURLY_LIMIT}"

    _sms_rate_limited = False
    return True, ""


async def get_sms_metrics() -> dict:
    """Get SMS metrics - local counters + pending from PHP API."""
    pending = 0
    try:
        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ESKIMOS_PHP_API}/health.php")
                if resp.status_code == 200:
                    data = resp.json()
                    queue = data.get("queue", {})
                    pending = queue.get("sms_pending", 0) or 0
    except Exception:
        pass

    return {
        "sms_sent_today": _sms_sent_today,
        "sms_sent_total": _sms_sent_total,
        "sms_received_today": _sms_received_today,
        "sms_received_total": _sms_received_total,
        "sms_pending": pending,
        "last_sms_error": _last_sms_error,
        "rate_limited": _sms_rate_limited,
        "daily_limit": SMS_DAILY_LIMIT,
        "hourly_limit": SMS_HOURLY_LIMIT,
        "hourly_count": _sms_hourly_count,
        "storage_used": _sms_storage_used,
        "storage_max": _sms_storage_max,
    }


# ==================== Heartbeat ====================

async def send_heartbeat(client_key: str) -> dict:
    """Send heartbeat to central server."""
    if not HAS_HTTPX:
        log("Heartbeat skipped: httpx not installed")
        return {}

    try:
        from eskimos import __version__
    except ImportError:
        __version__ = "0.0.0"

    modem = await get_modem_status()
    metrics = await get_sms_metrics()
    system = get_system_info()

    payload = {
        "client_key": client_key,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "version": __version__,
        "uptime_seconds": get_uptime(),
        "modem": modem,
        "metrics": metrics,
        "system": system,
        "auto_reset_in_progress": _auto_reset_in_progress,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CENTRAL_API}/heartbeat",
                json=payload,
                headers={"X-Client-Key": client_key, "X-API-Key": HEARTBEAT_API_KEY},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                log(f"Heartbeat OK: v{__version__}, modem={modem.get('status')}")
                return data
            else:
                log(f"Heartbeat failed: {response.status_code}")

    except Exception as e:
        log(f"Heartbeat error: {e}")

    return {}


# ==================== Commands ====================

async def poll_commands(client_key: str) -> list:
    """Poll pending commands from central server."""
    if not HAS_HTTPX:
        return []

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CENTRAL_API}/commands/{client_key}",
                headers={"X-Client-Key": client_key, "X-API-Key": HEARTBEAT_API_KEY},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                commands = data.get("commands", [])
                if commands:
                    log(f"Received {len(commands)} command(s)")
                return commands

    except Exception as e:
        log(f"Command poll error: {e}")

    return []


async def acknowledge_command(client_key: str, command_id: str, success: bool,
                              error: str = None, result: dict = None) -> None:
    """Acknowledge command execution with optional result data."""
    if not HAS_HTTPX:
        return

    try:
        payload = {"success": success, "error": error}
        if result is not None:
            payload["result"] = result

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{CENTRAL_API}/commands/{command_id}/ack",
                json=payload,
                headers={"X-Client-Key": client_key, "X-API-Key": HEARTBEAT_API_KEY},
                timeout=10.0
            )
    except Exception as e:
        log(f"Command ack error: {e}")


async def execute_command(client_key: str, command: dict) -> None:
    """Execute a command from central server."""
    cmd_type = command.get("command_type")
    cmd_id = command.get("id")
    payload = command.get("payload", {})

    log(f"Executing command: {cmd_type} (id={cmd_id})")

    try:
        if cmd_type == "update":
            # Windows-safe update: download zip, then launch helper batch that
            # waits for daemon to exit, replaces files, and restarts everything.
            import subprocess
            from eskimos.infrastructure.updater import download_update
            try:
                zip_file = await download_update(payload.get("version"))
                if not zip_file:
                    await acknowledge_command(client_key, cmd_id, False, "Download failed")
                    log("Update download failed")
                else:
                    # Write helper batch script
                    bat_path = PORTABLE_ROOT / "_update_helper.bat"
                    bat_content = f"""@echo off
cd /d "{PORTABLE_ROOT}"
echo Waiting for daemon to exit...
timeout /t 5 /nobreak >nul
echo Applying update from {zip_file.name}...
if exist eskimos.bak rmdir /s /q eskimos.bak
rename eskimos eskimos.bak
tar -xf "{zip_file}" -C _updates\\extract 2>nul
if exist _updates\\extract\\EskimosGateway\\eskimos (
    move _updates\\extract\\EskimosGateway\\eskimos eskimos
) else if exist _updates\\extract\\eskimos (
    move _updates\\extract\\eskimos eskimos
) else (
    echo ERROR: eskimos folder not found in zip, restoring backup
    rename eskimos.bak eskimos
    goto :cleanup
)
if exist eskimos.bak rmdir /s /q eskimos.bak
:cleanup
if exist _updates rmdir /s /q _updates
del "{zip_file}" 2>nul
echo Update applied, starting services...
call START_ALL.bat
del "%~f0"
"""
                    bat_path.write_text(bat_content, encoding="utf-8")
                    # Launch helper (runs after daemon exits)
                    subprocess.Popen(
                        ["cmd", "/c", str(bat_path)],
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    await acknowledge_command(client_key, cmd_id, True)
                    log("Update downloaded, helper script launched, shutting down...")
                    await asyncio.sleep(1)
                    graceful_shutdown()
            except Exception as e:
                await acknowledge_command(client_key, cmd_id, False, str(e))
                log(f"Update error: {e}")

        elif cmd_type == "restart":
            await acknowledge_command(client_key, cmd_id, True)
            log("Restart requested, shutting down...")
            await asyncio.sleep(1)
            graceful_shutdown()

        elif cmd_type == "restart_gateway":
            # Restart EskimosGateway Windows service (picks up new code)
            import subprocess
            svc_name = payload.get("service_name", "EskimosGateway")
            try:
                subprocess.run(
                    ["net", "stop", svc_name],
                    timeout=30, capture_output=True
                )
                await asyncio.sleep(2)
                subprocess.run(
                    ["net", "start", svc_name],
                    timeout=30, capture_output=True
                )
                log(f"Service {svc_name} restarted")
                await acknowledge_command(client_key, cmd_id, True)
            except Exception as e:
                log(f"Service restart failed: {e}")
                await acknowledge_command(client_key, cmd_id, False, str(e))

        elif cmd_type == "config":
            # Apply new config values - support both {"config": {...}} and flat payload
            new_config = payload.get("config", None)
            if new_config is None:
                # Flat payload: keys directly in payload (e.g. {"MODEM_TYPE": "serial"})
                new_config = {k: v for k, v in payload.items() if k != "type"}
            apply_config(new_config)
            # Reload globals that depend on config
            global MODEM_TYPE, SERIAL_PORT, SERIAL_BAUDRATE, _cached_serial_port
            if "MODEM_TYPE" in new_config:
                MODEM_TYPE = new_config["MODEM_TYPE"]
                _cached_serial_port = None  # reset port cache
                log(f"MODEM_TYPE changed to: {MODEM_TYPE}")
            if "SERIAL_PORT" in new_config:
                SERIAL_PORT = new_config["SERIAL_PORT"]
                _cached_serial_port = None
            if "SERIAL_BAUDRATE" in new_config:
                SERIAL_BAUDRATE = int(new_config["SERIAL_BAUDRATE"])
            await acknowledge_command(client_key, cmd_id, True)
            log(f"Config updated: {list(new_config.keys())}")

        elif cmd_type == "diagnostic":
            # Run diagnostic and report results back to server
            diag = await run_diagnostic()
            await acknowledge_command(client_key, cmd_id, True, result=diag)
            log(f"Diagnostic complete")

        elif cmd_type == "sms_discover":
            # Discover all API methods from modem's web panel JS
            result = await discover_modem_api_methods()
            await acknowledge_command(client_key, cmd_id, True, result=result)
            log(f"SMS discover complete: {len(result.get('all_methods', []))} methods found")

        elif cmd_type == "sms_cleanup":
            # Try to delete SMS from modem using discovered methods
            result = await try_delete_sms_from_modem()
            await acknowledge_command(client_key, cmd_id, True, result=result)
            log(f"SMS cleanup complete")

        elif cmd_type == "usb_diag":
            # Deep USB device diagnostics - find all interfaces of modem
            import subprocess
            result = {"success": False}
            try:
                # 1. All PnP devices from VID_1BBB (Alcatel)
                r1 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_1BBB*' } | Select-Object Status, Class, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=30
                )
                result["alcatel_devices"] = r1.stdout.strip()[-3000:]

                # 2. All USB composite children (MI_xx interfaces)
                r2 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_1BBB*MI*' } | Select-Object Status, Class, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=30
                )
                result["usb_interfaces"] = r2.stdout.strip()[-3000:]

                # 3. USB device descriptor via devcon or registry
                r3 = subprocess.run(
                    ["powershell", "-Command",
                     """Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_1BBB*\\*' -ErrorAction SilentlyContinue | Select-Object PSChildName, DeviceDesc, Service, Driver, CompatibleIDs, HardwareID | Format-List"""],
                    capture_output=True, text=True, timeout=30
                )
                result["registry_usb"] = r3.stdout.strip()[-3000:]

                # 4. Check children in registry (composite device interfaces)
                r4 = subprocess.run(
                    ["powershell", "-Command",
                     """Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\USB' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*1BBB*' } | ForEach-Object { $_.Name } | Select-Object -First 20"""],
                    capture_output=True, text=True, timeout=30
                )
                result["registry_children"] = r4.stdout.strip()[-2000:]

                # 5. Device manager - modem class devices
                r5 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice -Class Modem -ErrorAction SilentlyContinue | Select-Object Status, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=30
                )
                result["modem_class"] = r5.stdout.strip()[-1000:]

                # 6. Ports (COM & LPT) class devices
                r6 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | Select-Object Status, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=30
                )
                result["ports_class"] = r6.stdout.strip()[-1000:]

                result["success"] = True
            except Exception as e:
                result["error"] = str(e)
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"USB diag complete")

        elif cmd_type == "install_modem_driver":
            # Create and install INF for Alcatel IK41 serial port
            import subprocess
            import tempfile
            result = {"success": False, "steps": []}
            try:
                drv_dir = Path(tempfile.gettempdir()) / "alcatel_driver"
                drv_dir.mkdir(exist_ok=True)
                inf_path = drv_dir / "alcatel_serial.inf"

                # Create INF that maps MI_00 and MI_01 and MI_02 to usbser.sys
                inf_content = """; Alcatel IK41 Serial Port Driver
; Maps USB interfaces to Windows serial port driver (usbser.sys)

[Version]
Signature="$Windows NT$"
Class=Ports
ClassGuid={4D36E978-E325-11CE-BFC1-08002BE10318}
Provider=%ManufacturerName%
DriverVer=02/06/2026,1.0.0.0
CatalogFile=alcatel_serial.cat

[Manufacturer]
%ManufacturerName%=DeviceList,NTamd64

[DeviceList.NTamd64]
%DeviceName_MI01%=AlcatelSerial_Install, USB\\VID_1BBB&PID_0195&MI_01
%DeviceName_MI03%=AlcatelSerial_Install, USB\\VID_1BBB&PID_0195&MI_03
%DeviceName_MI04%=AlcatelSerial_Install, USB\\VID_1BBB&PID_0195&MI_04
; Also try MBIM mode PIDs
%DeviceName_MBIM%=AlcatelSerial_Install, USB\\VID_1BBB&PID_00B6&MI_01
%DeviceName_RNDIS%=AlcatelSerial_Install, USB\\VID_1BBB&PID_01AA&MI_01

[AlcatelSerial_Install]
Include=mdmcpq.inf,usb.inf
CopyFiles=FakeModemCopyFileSection
AddReg=AlcatelSerial_Install.AddReg

[AlcatelSerial_Install.AddReg]
HKR,,DevLoader,,*ntkern
HKR,,NTMPDriver,,usbser.sys
HKR,,EnumPropPages32,,"MsPorts.dll,SerialPortPropPageProvider"

[AlcatelSerial_Install.Services]
Include=mdmcpq.inf
AddService=usbser, 0x00000002, usbser_Service_Inst

[usbser_Service_Inst]
DisplayName=%ServiceName%
ServiceType=1
StartType=3
ErrorControl=1
ServiceBinary=%12%\\usbser.sys
LoadOrderGroup=Base

[Strings]
ManufacturerName="Alcatel/TCL"
DeviceName_MI01="Alcatel IK41 AT Port (MI_01)"
DeviceName_MI03="Alcatel IK41 Diagnostic Port (MI_03)"
DeviceName_MI04="Alcatel IK41 AT Port 2 (MI_04)"
DeviceName_MBIM="Alcatel IK41 MBIM AT Port"
DeviceName_RNDIS="Alcatel IK41 RNDIS AT Port"
ServiceName="Alcatel Serial Driver"
"""
                inf_path.write_text(inf_content, encoding="utf-8")
                result["steps"].append(f"INF written to {inf_path}")

                # Install INF to driver store
                r1 = subprocess.run(
                    ["pnputil", "/add-driver", str(inf_path), "/install"],
                    capture_output=True, text=True, timeout=30
                )
                result["pnputil_stdout"] = r1.stdout.strip()[-2000:]
                result["pnputil_stderr"] = r1.stderr.strip()[-1000:]
                result["pnputil_rc"] = r1.returncode
                result["steps"].append(f"pnputil: rc={r1.returncode}")

                # Rescan devices to trigger driver matching
                r2 = subprocess.run(
                    ["pnputil", "/scan-devices"],
                    capture_output=True, text=True, timeout=30
                )
                result["steps"].append(f"scan-devices: {r2.stdout.strip()[:200]}")

                # Check if new COM ports appeared
                if HAS_SERIAL:
                    import serial.tools.list_ports as list_ports
                    ports = list(list_ports.comports())
                    result["com_ports_after"] = [
                        {"port": p.device, "desc": p.description, "hwid": p.hwid}
                        for p in ports
                    ]

                # Also check PnP for new Ports class devices
                r3 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | Select-Object Status, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=15
                )
                result["ports_after"] = r3.stdout.strip()[-1000:]

                result["success"] = True
            except Exception as e:
                result["error"] = str(e)
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"Driver install: {'OK' if result.get('success') else 'FAIL'}")

        elif cmd_type == "usb_modeswitch":
            # Try to switch modem USB mode via SCSI eject or WMI
            import subprocess
            result = {"success": False, "steps": []}
            try:
                action = payload.get("action", "eject")

                if action == "eject":
                    # Standard eject on mass storage - may trigger mode switch
                    # Find the mass storage drive letter
                    r1 = subprocess.run(
                        ["powershell", "-Command",
                         """Get-WmiObject Win32_DiskDrive | Where-Object { $_.PNPDeviceID -like '*VID_1BBB*' } | ForEach-Object {
                            $disk = $_
                            $part = Get-WmiObject -Query "ASSOCIATORS OF {Win32_DiskDrive.DeviceID='$($disk.DeviceID)'} WHERE AssocClass=Win32_DiskDriveToDiskPartition"
                            $vol = Get-WmiObject -Query "ASSOCIATORS OF {Win32_DiskPartition.DeviceID='$($part.DeviceID)'} WHERE AssocClass=Win32_LogicalDiskToPartition"
                            [PSCustomObject]@{DiskID=$disk.DeviceID; PNP=$disk.PNPDeviceID; Drive=$vol.DeviceID; Model=$disk.Model}
                         } | Format-List"""],
                        capture_output=True, text=True, timeout=30
                    )
                    result["modem_storage"] = r1.stdout.strip()[:1000]
                    result["steps"].append("Found storage device")

                    # Use PowerShell to eject the USB device
                    r2 = subprocess.run(
                        ["powershell", "-Command",
                         """$vol = (Get-WmiObject Win32_DiskDrive | Where-Object { $_.PNPDeviceID -like '*VID_1BBB*' })
                         if ($vol) {
                            $eject = New-Object -comObject Shell.Application
                            # Try to use devcon-like approach to restart device
                            $instanceId = (Get-PnpDevice | Where-Object { $_.InstanceId -like 'USB\\VID_1BBB&PID_0195\\*' -and $_.Class -eq 'USB' }).InstanceId
                            if ($instanceId) {
                                Disable-PnpDevice -InstanceId $instanceId -Confirm:$false -ErrorAction SilentlyContinue
                                Start-Sleep -Seconds 3
                                Enable-PnpDevice -InstanceId $instanceId -Confirm:$false -ErrorAction SilentlyContinue
                                'Device toggled (disable/enable)'
                            } else { 'No USB composite device found' }
                         } else { 'No Alcatel storage found' }"""],
                        capture_output=True, text=True, timeout=30
                    )
                    result["toggle_result"] = r2.stdout.strip()[:500]
                    result["toggle_stderr"] = r2.stderr.strip()[:500]
                    result["steps"].append("Toggle attempted")

                elif action == "disable_enable":
                    # Disable and re-enable to force re-enumeration
                    r1 = subprocess.run(
                        ["powershell", "-Command",
                         """$dev = Get-PnpDevice | Where-Object { $_.InstanceId -like 'USB\\VID_1BBB&PID_0195\\*' -and $_.Class -eq 'USB' }
                         if ($dev) {
                            Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false
                            Start-Sleep -Seconds 5
                            Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false
                            'Re-enabled: ' + $dev.InstanceId
                         } else { 'Device not found' }"""],
                        capture_output=True, text=True, timeout=30
                    )
                    result["reenable"] = r1.stdout.strip()[:500]
                    result["reenable_stderr"] = r1.stderr.strip()[:500]
                    result["steps"].append("Disable/Enable done")

                # After any action, check new device state
                await asyncio.sleep(5)
                r3 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_1BBB*' } | Select-Object Status, Class, FriendlyName, InstanceId | Format-List"],
                    capture_output=True, text=True, timeout=15
                )
                result["devices_after"] = r3.stdout.strip()[:2000]

                # Check for new COM ports
                if HAS_SERIAL:
                    import serial.tools.list_ports as list_ports
                    ports = list(list_ports.comports())
                    result["com_ports"] = [
                        {"port": p.device, "desc": p.description}
                        for p in ports
                    ]

                result["success"] = True
            except Exception as e:
                result["error"] = str(e)
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"USB modeswitch: {result.get('steps')}")

        elif cmd_type == "modem_backup":
            # Backup all modem settings via Get* methods
            result = await modem_backup_settings()
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"Modem backup: {len(result.get('backup', {}))} settings saved")

        elif cmd_type == "modem_reboot":
            # Safe reboot - no data loss
            result = await modem_reboot()
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"Modem reboot: {'OK' if result.get('success') else 'FAIL'}")

        elif cmd_type == "modem_factory_reset":
            # Factory reset with backup/restore
            result = await modem_factory_reset()
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"Factory reset: sms_before={result.get('sms_before')}, sms_after={result.get('sms_after')}")

        elif cmd_type == "send_sms":
            # Send SMS via modem JSON-RPC (triggered from dashboard)
            global _sms_sent_today, _sms_sent_total, _sms_hourly_count
            to = payload.get("to", "").strip()
            message = payload.get("message", "").strip()
            if not to or not message:
                await acknowledge_command(client_key, cmd_id, False,
                    result={"error": "Missing 'to' or 'message' in payload"})
            elif len(message) > 1600:
                await acknowledge_command(client_key, cmd_id, False,
                    result={"error": f"Message too long: {len(message)}/1600 chars"})
            else:
                allowed, reason = check_rate_limit()
                if not allowed:
                    await acknowledge_command(client_key, cmd_id, False,
                        result={"error": f"Rate limited: {reason}"})
                else:
                    modem_override = payload.get("modem_type", MODEM_TYPE)
                    if modem_override == "serial":
                        success, error = await _modem_send_sms_serial(to, message)
                    else:
                        success, error = await _modem_send_sms_direct(to, message)
                    if success:
                        _sms_sent_today += 1
                        _sms_sent_total += 1
                        _sms_hourly_count += 1
                        # Check storage every 10 SMS sent
                        if _sms_sent_today % 10 == 0:
                            asyncio.ensure_future(check_sms_storage())
                    await acknowledge_command(client_key, cmd_id, success,
                        result={"sent": success, "to": to, "error": error,
                                 "modem": modem_override,
                                 "msg_preview": message[:50]})
                    log(f"SMS send command: to={to[:6]}***, modem={modem_override}, success={success}")

        elif cmd_type == "clear_processed_sms":
            # Clear processed SMS IDs (needed after factory reset if IDs overlap)
            old_count = len(_processed_sms_ids)
            _processed_sms_ids.clear()
            save_processed_sms_ids()
            await acknowledge_command(client_key, cmd_id, True,
                result={"cleared": old_count, "message": "Processed SMS IDs cleared"})
            log(f"Cleared {old_count} processed SMS IDs")

        elif cmd_type == "modem_api_call":
            # Call arbitrary JSON-RPC method on modem (for diagnostics)
            import re as _re
            method_name = payload.get("method", "GetSystemInfo")
            params = payload.get("params", {})
            need_login = payload.get("login", True)
            base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
            result = {"method": method_name, "success": False}
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as api_client:
                    resp = await api_client.get(base_url)
                    m = _re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
                    if not m:
                        result["error"] = "Cannot extract token"
                    else:
                        tok = m.group(1)
                        hdrs = {"_TclRequestVerificationKey": tok,
                                "Referer": f"http://{MODEM_HOST}/index.html"}
                        if need_login:
                            lr = await api_client.post(f"{base_url}/jrd/webapi",
                                json={"jsonrpc": "2.0", "method": "Login",
                                      "params": {"UserName": "admin", "Password": "admin"},
                                      "id": "1"}, headers=hdrs)
                            result["login"] = lr.text[:300]
                        resp = await api_client.post(f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0", "method": method_name,
                                  "params": params, "id": "2"}, headers=hdrs)
                        result["response"] = resp.text[:3000]
                        result["success"] = True
                        if need_login:
                            await api_client.post(f"{base_url}/jrd/webapi",
                                json={"jsonrpc": "2.0", "method": "Logout",
                                      "params": {}, "id": "3"}, headers=hdrs)
            except Exception as e:
                result["error"] = str(e)
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"Modem API call {method_name}: {'OK' if result.get('success') else 'FAIL'}")

        elif cmd_type == "sms_at_probe":
            # Probe COM ports for AT-capable modem
            result = await probe_at_ports()
            await acknowledge_command(client_key, cmd_id, True, result=result)
            log(f"AT probe complete: port={result.get('at_port')}")

        elif cmd_type == "sms_at_delete":
            # Delete all SMS via AT commands
            com_port = payload.get("com_port")
            result = await delete_sms_via_at(com_port)
            await acknowledge_command(client_key, cmd_id, result.get("success", False), result=result)
            log(f"AT delete: success={result.get('success')}, deleted={result.get('deleted', 0)}")

        elif cmd_type == "pip_install":
            # Install Python packages remotely using SAME Python as daemon
            import subprocess
            import sys
            packages = payload.get("packages", [])
            if isinstance(packages, str):
                packages = [packages]
            # Whitelist allowed packages
            ALLOWED_PACKAGES = {"pyserial", "psutil", "httpx", "python-dotenv"}
            rejected = [p for p in packages if p not in ALLOWED_PACKAGES]
            if rejected:
                await acknowledge_command(client_key, cmd_id, False,
                    f"Packages not in whitelist: {rejected}. Allowed: {sorted(ALLOWED_PACKAGES)}")
            else:
                try:
                    # Use sys.executable to ensure we install for the SAME Python
                    result_out = subprocess.run(
                        [sys.executable, "-m", "pip", "install"] + packages,
                        capture_output=True, text=True, timeout=120
                    )
                    success = result_out.returncode == 0
                    result = {
                        "packages": packages,
                        "success": success,
                        "python": sys.executable,
                        "stdout": result_out.stdout[-2000:] if result_out.stdout else "",
                        "stderr": result_out.stderr[-1000:] if result_out.stderr else "",
                    }
                    await acknowledge_command(client_key, cmd_id, success, result=result)
                    log(f"pip install {' '.join(packages)} (via {sys.executable}): {'OK' if success else 'FAIL'}")
                except Exception as e:
                    await acknowledge_command(client_key, cmd_id, False, str(e))
                    log(f"pip install failed: {e}")

        else:
            log(f"Unknown command type: {cmd_type}")
            await acknowledge_command(client_key, cmd_id, False, f"Unknown command: {cmd_type}")

    except Exception as e:
        log(f"Command execution error: {e}")
        await acknowledge_command(client_key, cmd_id, False, str(e))


def apply_config(new_config: dict) -> None:
    """Apply new configuration values to .env file."""
    global SMS_DAILY_LIMIT, SMS_HOURLY_LIMIT
    try:
        env_content = ""
        if CONFIG_FILE.exists():
            env_content = CONFIG_FILE.read_text()

        # Parse existing
        env_lines = {}
        for line in env_content.strip().split("\n"):
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                env_lines[key.strip()] = value.strip()

        # Update with new values
        # Keys already in UPPER_CASE format are written as-is (e.g. MODEM_TYPE, SERIAL_PORT)
        # Keys in lowercase get ESKIMOS_ prefix (legacy behavior for sms_daily_limit etc.)
        for key, value in new_config.items():
            if key == key.upper():
                env_key = key
            else:
                env_key = f"ESKIMOS_{key.upper()}"
            env_lines[env_key] = str(value)

        # Write back
        new_content = "\n".join(f"{k}={v}" for k, v in env_lines.items())
        CONFIG_FILE.write_text(new_content)

        # Reload rate limits from new config
        if "sms_daily_limit" in new_config:
            SMS_DAILY_LIMIT = int(new_config["sms_daily_limit"])
            log(f"Daily SMS limit updated: {SMS_DAILY_LIMIT}")
        if "sms_hourly_limit" in new_config:
            SMS_HOURLY_LIMIT = int(new_config["sms_hourly_limit"])
            log(f"Hourly SMS limit updated: {SMS_HOURLY_LIMIT}")

    except Exception as e:
        log(f"Config apply error: {e}")


async def probe_modem_debug() -> dict:
    """Probe modem for model info via HTML/JS files and hashed login."""
    if not HAS_HTTPX:
        return {"error": "httpx not available"}

    import re
    import hashlib
    import base64
    results = {}
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"

    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        # Get main page (full HTML)
        token = ""
        try:
            resp = await client.get(base_url)
            html = resp.text
            results["html_length"] = len(html)

            # Extract token
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', html)
            if m:
                token = m.group(1)
                results["tcl_token"] = token

            # Extract all script src URLs
            scripts = re.findall(r'src="([^"]+\.js[^"]*)"', html)
            results["js_files"] = scripts[:10]

            # Look for model/device info in HTML
            for pattern in [
                r'device[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
                r'[Mm]odel[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
                r'product[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
            ]:
                m2 = re.search(pattern, html)
                if m2:
                    results["html_model"] = m2.group(1)
                    break
        except Exception as e:
            results["main_page_error"] = str(e)

        # Fetch JS files that might contain model info
        js_paths = [
            "/js/home.js", "/js/app.js", "/js/main.js",
            "/js/config.js", "/js/device.js", "/js/status.js",
        ]
        for path in js_paths:
            try:
                resp = await client.get(f"{base_url}{path}")
                if resp.status_code == 200 and len(resp.text) > 10:
                    # Search for model info in JS
                    for pat in [r'"DeviceName"\s*:\s*"([^"]+)"',
                                r'"model"\s*:\s*"([^"]+)"',
                                r'IK\d+\w+', r'MW\d+\w+', r'MR\d+\w+']:
                        m = re.search(pat, resp.text)
                        if m:
                            results[f"js_{path}_match"] = m.group(0)[:200]
                            break
                    if f"js_{path}_match" not in results:
                        results[f"js_{path}_size"] = len(resp.text)
            except Exception:
                pass

        # Try login with hashed passwords
        if token:
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{MODEM_HOST}/index.html",
            }
            # TCL modems often require base64 or SHA256 hashed password
            pwd_variants = [
                ("admin_plain", "admin"),
                ("admin_b64", base64.b64encode(b"admin").decode()),
                ("admin_sha256", hashlib.sha256(b"admin").hexdigest()),
                ("empty_plain", ""),
            ]
            for name, pwd in pwd_variants:
                try:
                    login_body = {
                        "jsonrpc": "2.0",
                        "method": "Login",
                        "params": {"UserName": "admin", "Password": pwd},
                        "id": "1"
                    }
                    resp = await client.post(f"{base_url}/jrd/webapi",
                                             json=login_body, headers=headers)
                    resp_text = resp.text[:300]
                    results[f"login_{name}"] = resp_text
                    if "result" in resp_text and "error" not in resp_text.lower():
                        # Login success - try GetSystemInfo
                        body = {"jsonrpc": "2.0", "method": "GetSystemInfo",
                                "params": {}, "id": "1"}
                        resp2 = await client.post(f"{base_url}/jrd/webapi",
                                                  json=body, headers=headers)
                        results["system_info_after_login"] = resp2.text[:2000]
                        # Logout
                        await client.post(f"{base_url}/jrd/webapi",
                                          json={"jsonrpc": "2.0", "method": "Logout",
                                                "params": {}, "id": "1"},
                                          headers=headers)
                        break
                except Exception as e:
                    results[f"login_{name}_error"] = str(e)

    return results


async def discover_modem_api_methods() -> dict:
    """Fetch modem's web panel JS files and extract all JSON-RPC method names."""
    import re
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
    result = {"all_methods": [], "sms_methods": [], "delete_methods": [],
              "set_methods": [], "js_files_checked": []}

    if not HAS_HTTPX:
        result["error"] = "httpx not available"
        return result

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Get main page to find JS files
            resp = await client.get(base_url)
            scripts = re.findall(r'src="([^"]+\.js[^"]*)"', resp.text)

            all_methods = set()
            for script_path in scripts:
                url = f"{base_url}/{script_path.lstrip('/')}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        js_text = resp.text
                        result["js_files_checked"].append(
                            f"{script_path} ({len(js_text)} bytes)"
                        )
                        # Pattern 1: Quoted strings with known API verb prefixes
                        # Minified JS has method names as function args, not next to "method" key
                        m1 = re.findall(
                            r'''["']((?:Get|Set|Delete|Send|Save|Clear|Remove|Check|Login|Logout|Connect|Disconnect|Start|Stop|Enable|Disable|Add|Update|Create|Reset|Change)[A-Z][a-zA-Z0-9]*?)["']''',
                            js_text
                        )
                        all_methods.update(m1)
                        # Pattern 2: lowercase get/set variants (TCL firmware anomaly)
                        m2 = re.findall(r'''["']((?:get|set)[A-Z][a-zA-Z0-9]+)["']''', js_text)
                        all_methods.update(m2)
                        # Pattern 3: URL ?api=Method or ?name=Method
                        m3 = re.findall(r'''[?&](?:api|name)=["']?([A-Za-z][a-zA-Z]+)["']?''', js_text)
                        all_methods.update(m3)
                        # Pattern 4: "method":"MethodName" (flexible)
                        m4 = re.findall(r'''["']?method["']?\s*[,:]\s*["']([A-Za-z][a-zA-Z]+)["']''', js_text)
                        all_methods.update(m4)
                        # Pattern 5: Property style: GetSMS: or GetSMS=
                        m5 = re.findall(r'''((?:Get|Set|Delete|Send|Login|Logout|get|set)[A-Z][a-zA-Z]+)\s*[:=]''', js_text)
                        all_methods.update(m5)
                except Exception:
                    pass

            result["all_methods"] = sorted(all_methods)
            result["sms_methods"] = sorted(
                m for m in all_methods if "sms" in m.lower()
            )
            result["delete_methods"] = sorted(
                m for m in all_methods if "delete" in m.lower() or "clear" in m.lower() or "remove" in m.lower()
            )
            result["set_methods"] = sorted(
                m for m in all_methods if m.startswith("Set") or m.startswith("set")
            )
            result["reboot_methods"] = sorted(
                m for m in all_methods if "reboot" in m.lower() or "reset" in m.lower() or "factory" in m.lower()
            )
            result["storage_methods"] = sorted(
                m for m in all_methods if "storage" in m.lower() or "memory" in m.lower()
            )
            result["total_methods"] = len(all_methods)

    except Exception as e:
        result["error"] = str(e)

    return result


async def try_delete_sms_from_modem() -> dict:
    """Try multiple methods to delete SMS from modem."""
    import re
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
    results = {"methods_tried": [], "success": False, "sms_before": 0, "sms_after": 0}

    if not HAS_HTTPX:
        results["error"] = "httpx not available"
        return results

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Get token
            resp = await client.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if not m:
                results["error"] = "Cannot extract token"
                return results

            token = m.group(1)
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{MODEM_HOST}/index.html",
            }

            # Login
            resp = await client.post(f"{base_url}/jrd/webapi",
                json={"jsonrpc": "2.0", "method": "Login",
                      "params": {"UserName": "admin", "Password": "admin"},
                      "id": "1"}, headers=headers)
            if "error" in resp.json():
                results["error"] = f"Login failed: {resp.text[:200]}"
                return results

            # Count SMS before
            resp = await client.post(f"{base_url}/jrd/webapi",
                json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                      "params": {"Page": 0, "ContactNum": 100},
                      "id": "2"}, headers=headers)
            contacts = (resp.json().get("result") or {}).get("SMSContactList") or []
            total_before = sum(c.get("TSMSCount", 0) for c in contacts)
            results["sms_before"] = total_before
            results["contacts_before"] = len(contacts)

            # Check storage state
            try:
                resp = await client.post(f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "GetSMSStorageState",
                          "params": {}, "id": "3"}, headers=headers)
                results["storage_state"] = resp.json()
            except Exception:
                pass

            # Try delete methods
            contact_ids = [c.get("ContactId") for c in contacts if c.get("ContactId")]
            sms_ids = [c.get("SMSId") for c in contacts if c.get("SMSId")]

            # Get individual SMS IDs from first contact's content list
            first_sms_id = None
            if contact_ids:
                try:
                    resp = await client.post(f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "GetSMSContentList",
                              "params": {"Page": 0, "ContactId": contact_ids[0]},
                              "id": "4"}, headers=headers)
                    sms_list = (resp.json().get("result") or {}).get("SMSContentList") or []
                    if sms_list:
                        first_sms_id = sms_list[0].get("SMSId")
                        results["first_sms_detail"] = sms_list[0]
                except Exception:
                    pass

            delete_attempts = [
                # Discovered methods from modem's JS
                ("DeleteALLsingle", {}, "DeleteALLsingle (no params)"),
                ("DeleteALLsingle", {"ContactId": contact_ids[0] if contact_ids else 0}, "DeleteALLsingle by ContactId"),
                ("DeleteALLsingle", {"SMSId": first_sms_id or (sms_ids[0] if sms_ids else 0)}, "DeleteALLsingle by SMSId"),
                # Standard DeleteSMS with actual SMS content ID
                ("DeleteSMS", {"SMSId": first_sms_id or 0}, "DeleteSMS by content SMSId"),
                ("DeleteSMS", {"SMSId": first_sms_id or 0, "Flag": 0}, "DeleteSMS SMSId+Flag0"),
                ("DeleteSMS", {"ContactId": contact_ids[0] if contact_ids else 0, "Flag": 0}, "DeleteSMS ContactId+Flag0"),
                ("DeleteSMS", {"ContactId": contact_ids[0] if contact_ids else 0, "Flag": 1}, "DeleteSMS ContactId+Flag1"),
                ("DeleteSMS", {"Flag": 2}, "DeleteSMS Flag2 (delete all)"),
                # Other approaches
                ("SetSMSSettings", {"SaveSMS": 0}, "Disable SMS saving"),
            ]

            req_id = 10
            for method, params, desc in delete_attempts:
                try:
                    resp = await client.post(f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": method,
                              "params": params, "id": str(req_id)},
                        headers=headers)
                    resp_data = resp.json()
                    success = "result" in resp_data and "error" not in resp_data
                    attempt = {
                        "method": method,
                        "params": params,
                        "desc": desc,
                        "success": success,
                        "response": str(resp_data)[:300],
                    }
                    # Check if SMS count changed after successful call
                    if success:
                        try:
                            resp2 = await client.post(f"{base_url}/jrd/webapi",
                                json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                                      "params": {"Page": 0, "ContactNum": 100},
                                      "id": str(req_id + 100)},
                                headers=headers)
                            c_after = (resp2.json().get("result") or {}).get("SMSContactList") or []
                            count_after = sum(c.get("TSMSCount", 0) for c in c_after)
                            attempt["sms_count_after"] = count_after
                            if count_after < total_before:
                                attempt["sms_deleted"] = total_before - count_after
                                results["working_method"] = desc
                        except Exception:
                            pass
                    results["methods_tried"].append(attempt)
                    req_id += 1
                except Exception as e:
                    results["methods_tried"].append({
                        "method": method, "desc": desc,
                        "success": False, "error": str(e),
                    })

            # Count SMS after (if modem didn't reboot)
            if not results.get("modem_rebooted"):
                try:
                    resp = await client.post(f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                              "params": {"Page": 0, "ContactNum": 100},
                              "id": "99"}, headers=headers)
                    contacts_after = (resp.json().get("result") or {}).get("SMSContactList") or []
                    results["sms_after"] = sum(c.get("TSMSCount", 0) for c in contacts_after)
                    results["success"] = results["sms_after"] < total_before
                except Exception:
                    pass

                # Logout
                try:
                    await client.post(f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "Logout",
                              "params": {}, "id": "100"}, headers=headers)
                except Exception:
                    pass

    except Exception as e:
        results["error"] = str(e)

    return results


async def run_diagnostic() -> dict:
    """Run diagnostic checks including direct modem HTTP probing."""
    modem = await get_modem_status()
    metrics = await get_sms_metrics()
    system = get_system_info()
    # Debug: include daemon config state
    system["modem_type"] = MODEM_TYPE
    system["modem_phone"] = MODEM_PHONE
    system["config_file"] = str(CONFIG_FILE)
    system["config_exists"] = CONFIG_FILE.exists()
    try:
        system["config_content"] = CONFIG_FILE.read_text()[:500] if CONFIG_FILE.exists() else "NOT FOUND"
    except Exception:
        system["config_content"] = "READ ERROR"

    # Direct HTTP probe to modem (bypasses Gateway API)
    modem_debug = {}
    try:
        reachable = await probe_modem_direct()
        if reachable:
            modem_debug = await probe_modem_debug()
        else:
            modem_debug = {"error": "Modem not reachable via TCP"}
    except Exception as e:
        modem_debug = {"error": str(e)}

    # Test incoming SMS read from modem
    incoming_test = {}
    try:
        import re
        base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as hc:
            resp = await hc.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if m:
                token = m.group(1)
                hdrs = {"_TclRequestVerificationKey": token,
                        "Referer": f"http://{MODEM_HOST}/index.html"}
                resp = await hc.post(f"{base_url}/jrd/webapi",
                                      json={"jsonrpc": "2.0", "method": "Login",
                                            "params": {"UserName": "admin", "Password": "admin"},
                                            "id": "1"}, headers=hdrs)
                login = resp.json()
                incoming_test["login"] = str(login)
                if "error" not in login:
                    resp = await hc.post(f"{base_url}/jrd/webapi",
                                          json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                                                "params": {"Page": 0, "ContactNum": 100},
                                                "id": "2"}, headers=hdrs)
                    contacts = resp.json()
                    incoming_test["contacts_raw"] = str(contacts)
                    # Get SMS count info
                    clist = (contacts.get("result") or {}).get("SMSContactList") or []
                    incoming_test["conversations"] = len(clist)
                    incoming_test["processed_ids"] = len(_processed_sms_ids)
                    # Logout
                    try:
                        await hc.post(f"{base_url}/jrd/webapi",
                                       json={"jsonrpc": "2.0", "method": "Logout",
                                             "params": {}, "id": "99"}, headers=hdrs)
                    except Exception:
                        pass
            else:
                incoming_test["error"] = "no token"
    except Exception as e:
        incoming_test["error"] = str(e)

    return {
        "modem": modem,
        "modem_debug": modem_debug,
        "incoming_test": incoming_test,
        "metrics": metrics,
        "system": system,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ==================== SMS Queue Polling ====================


async def _get_modem_adapter():
    """Stub - adapter disabled in favor of direct JSON-RPC calls."""
    return None


async def _disconnect_modem():
    """Stub - adapter disabled."""
    pass


async def _modem_send_sms_direct(recipient: str, message: str) -> tuple:
    """Send SMS via direct JSON-RPC calls (same method as detect_modem_model_tcl).

    Returns (success: bool, error: str or None)
    """
    import re
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # 1. Get verification token from main page
        resp = await client.get(base_url)
        m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
        if not m:
            return False, "Cannot extract modem token"

        token = m.group(1)
        headers = {
            "_TclRequestVerificationKey": token,
            "Referer": f"http://{MODEM_HOST}/index.html",
        }

        # 2. Login
        login_body = {
            "jsonrpc": "2.0", "method": "Login",
            "params": {"UserName": "admin", "Password": "admin"}, "id": "1"
        }
        resp = await client.post(f"{base_url}/jrd/webapi",
                                  json=login_body, headers=headers)
        login_data = resp.json()
        if "error" in login_data:
            return False, f"Login failed: {login_data}"

        log(f"Modem login OK, sending SMS to {recipient}")

        # 3. Send SMS
        from datetime import datetime as dt
        now = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        sms_body = {
            "jsonrpc": "2.0", "method": "SendSMS",
            "params": {
                "SMSId": -1,
                "SMSContent": message,
                "PhoneNumber": [recipient],
                "SMSTime": now,
            }, "id": "2"
        }
        resp = await client.post(f"{base_url}/jrd/webapi",
                                  json=sms_body, headers=headers)
        sms_result = resp.json()

        # 4. Logout
        try:
            await client.post(f"{base_url}/jrd/webapi",
                               json={"jsonrpc": "2.0", "method": "Logout",
                                     "params": {}, "id": "3"},
                               headers=headers)
        except Exception:
            pass

        if "error" in sms_result:
            return False, f"SendSMS error: {sms_result.get('error')}"

        return True, None


async def _modem_send_sms_serial(recipient: str, message: str) -> tuple:
    """Send SMS via serial AT commands (SIM7600G-H).

    Returns (success: bool, error: str or None)
    """
    port = await _resolve_serial_port()
    if not port:
        return False, "Serial port not found"

    def _send():
        try:
            ser = serial_mod.Serial(port, SERIAL_BAUDRATE, timeout=3)
            _at_send(ser, "AT")
            _at_send(ser, "AT+CMGF=1")  # text mode

            # Send AT+CMGS="recipient"
            ser.reset_input_buffer()
            ser.write(f'AT+CMGS="{recipient}"\r\n'.encode())
            time.sleep(1)
            # Send message body + Ctrl+Z
            ser.write(message.encode("utf-8"))
            ser.write(b"\x1a")

            # Wait for +CMGS: or ERROR (up to 15s)
            end_time = time.time() + 15
            response = b""
            while time.time() < end_time:
                if ser.in_waiting:
                    response += ser.read(ser.in_waiting)
                    if b"+CMGS:" in response or b"ERROR" in response:
                        break
                time.sleep(0.2)
            ser.close()

            text = response.decode("utf-8", errors="replace")
            if "+CMGS:" in text:
                return True, None
            return False, f"AT error: {text[:200]}"
        except Exception as e:
            return False, f"Serial error: {e}"

    return await asyncio.get_event_loop().run_in_executor(None, _send)


async def poll_and_send_sms() -> bool:
    """Poll SMS queue from PHP API and send via modem.

    Returns True if an SMS was sent, False otherwise.
    """
    global _sms_sent_today, _sms_sent_total, _last_sms_error, _sms_hourly_count
    if not HAS_HTTPX:
        return False

    # Rate limit check
    allowed, reason = check_rate_limit()
    if not allowed:
        log(f"SMS rate limited: {reason}")
        return False

    sms_key = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Check for pending SMS
            resp = await client.get(
                f"{ESKIMOS_PHP_API}/get-sms.php",
                params={"from": MODEM_PHONE},
            )

            if resp.status_code != 200:
                _last_sms_error = f"API {resp.status_code}"
                log(f"SMS poll: API returned {resp.status_code}")
                return False

            data = resp.json()
            if not data or not isinstance(data, list) or not data[0].get("isset"):
                return False  # No pending SMS - silent

            sms = data[0]
            sms_key = sms.get("sms_key")
            sms_to = sms.get("sms_to")
            sms_message = sms.get("sms_message")

            if not sms_key or not sms_to or not sms_message:
                _last_sms_error = f"incomplete data key={sms_key}"
                log(f"SMS poll: incomplete data - key={sms_key}")
                return False

            log(f"SMS queued: to={sms_to}, key={sms_key[:12]}..., len={len(sms_message)}")

            # Send SMS via modem (serial AT or IK41 JSON-RPC)
            if MODEM_TYPE == "serial":
                success, error = await _modem_send_sms_serial(sms_to, sms_message)
            else:
                success, error = await _modem_send_sms_direct(sms_to, sms_message)

            if success:
                # Report success to PHP API
                await client.post(
                    f"{ESKIMOS_PHP_API}/update-sms.php",
                    json={
                        "SMS_KEY": sms_key,
                        "SMS_FROM": MODEM_PHONE,
                        "SMS_IS_REPLY": sms.get("sms_is_reply", 0),
                    },
                )
                _sms_sent_today += 1
                _sms_sent_total += 1
                _sms_hourly_count += 1
                _last_sms_error = ""
                log(f"SMS SENT: to={sms_to}, key={sms_key[:12]}... (today: {_sms_sent_today}, hour: {_sms_hourly_count})")
                return True
            else:
                _last_sms_error = f"send failed: {error}"
                log(f"SMS send FAILED: {error}")
                return False

    except Exception as e:
        _last_sms_error = f"exception: {e}"
        log(f"SMS poll error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def _modem_receive_sms_direct() -> list:
    """Read incoming SMS from modem via direct JSON-RPC.

    Same proven method as _modem_send_sms_direct():
    - URL: /jrd/webapi (no ?api= param)
    - Headers: _TclRequestVerificationKey (CamelCase)
    - Content: json= (not content=)

    Returns list of dicts with keys: sender, content
    """
    import re
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # 1. Get token
        resp = await client.get(base_url)
        m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
        if not m:
            log("Incoming SMS: cannot extract modem token")
            return []

        token = m.group(1)
        headers = {
            "_TclRequestVerificationKey": token,
            "Referer": f"http://{MODEM_HOST}/index.html",
        }

        # 2. Login
        resp = await client.post(f"{base_url}/jrd/webapi",
                                  json={"jsonrpc": "2.0", "method": "Login",
                                        "params": {"UserName": "admin", "Password": "admin"},
                                        "id": "1"},
                                  headers=headers)
        login_data = resp.json()
        if "error" in login_data:
            log(f"Incoming SMS: login failed: {login_data}")
            return []

        messages = []
        try:
            # 3. GetSMSContactList
            resp = await client.post(f"{base_url}/jrd/webapi",
                                      json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                                            "params": {"Page": 0, "ContactNum": 100},
                                            "id": "2"},
                                      headers=headers)
            contacts_data = resp.json()
            result = contacts_data.get("result") or {}
            contact_list = result.get("SMSContactList") or []

            if not contact_list:
                return []

            # 4. For each contact, get messages
            req_id = 3
            for contact in contact_list:
                contact_id = contact.get("ContactId")
                # PhoneNumber can be a list ['797053850'] or string '797053850'
                phone_raw = contact.get("PhoneNumber", "")
                if isinstance(phone_raw, list):
                    phone_number = phone_raw[0] if phone_raw else ""
                else:
                    phone_number = str(phone_raw)
                if not contact_id:
                    continue

                resp = await client.post(f"{base_url}/jrd/webapi",
                                          json={"jsonrpc": "2.0", "method": "GetSMSContentList",
                                                "params": {"ContactId": contact_id, "Page": 0},
                                                "id": str(req_id)},
                                          headers=headers)
                req_id += 1
                sms_list = (resp.json().get("result") or {}).get("SMSContentList") or []

                for sms in sms_list:
                    sms_type = sms.get("SMSType", 0)
                    sms_id = sms.get("SMSId")
                    # TCL/Alcatel IK41: SMSType=0 means INCOMING (received), SMSType=2 means OUTGOING (sent)
                    # Skip already processed messages (DeleteSMS doesn't work on IK41 firmware)
                    if sms_type == 0 and sms_id not in _processed_sms_ids:
                        messages.append({
                            "sender": phone_number,
                            "content": sms.get("SMSContent", ""),
                        })
                        _processed_sms_ids.add(sms_id)
                        save_processed_sms_ids()

        finally:
            # 6. Logout
            try:
                await client.post(f"{base_url}/jrd/webapi",
                                   json={"jsonrpc": "2.0", "method": "Logout",
                                         "params": {}, "id": "99"},
                                   headers=headers)
            except Exception:
                pass

        return messages


async def _modem_receive_sms_serial() -> list:
    """Read incoming SMS via serial AT commands (SIM7600G-H).

    Returns list of dicts with keys: sender, content
    """
    import re

    port = await _resolve_serial_port()
    if not port:
        return []

    def _receive():
        try:
            ser = serial_mod.Serial(port, SERIAL_BAUDRATE, timeout=3)
            _at_send(ser, "AT+CMGF=1")
            resp = _at_send(ser, 'AT+CMGL="REC UNREAD"', timeout=10)

            messages = []
            # +CMGL: idx,"REC UNREAD","+48797053850","","26/02/10,12:30:00+04"\r\ncontent
            pattern = r'\+CMGL:\s*\d+,"[^"]*","([^"]+)".*?\r\n(.+?)(?=\r\n\+CMGL:|\r\nOK|\r\n$)'
            for match in re.finditer(pattern, resp, re.DOTALL):
                sender = match.group(1).strip()
                # Strip +48 prefix if present
                if sender.startswith("+48"):
                    sender = sender[3:]
                messages.append({
                    "sender": sender,
                    "content": match.group(2).strip(),
                })

            # Delete read messages to free storage
            if messages:
                _at_send(ser, "AT+CMGD=1,3")  # delete all read messages

            ser.close()
            return messages
        except Exception as e:
            log(f"Serial receive error: {e}")
            return []

    return await asyncio.get_event_loop().run_in_executor(None, _receive)


async def poll_incoming_sms() -> int:
    """Check modem for incoming SMS and forward to PHP API.

    Returns number of messages received.
    """
    global _sms_received_today, _sms_received_total
    if not HAS_HTTPX:
        return 0

    try:
        if MODEM_TYPE == "serial":
            messages = await _modem_receive_sms_serial()
        else:
            messages = await _modem_receive_sms_direct()
        if not messages:
            return 0

        count = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for msg in messages:
                try:
                    await client.post(
                        f"{ESKIMOS_PHP_API}/receive-sms.php",
                        json={
                            "sms_message": msg["content"],
                            "sms_from": msg["sender"],
                            "sms_to": MODEM_PHONE,
                        },
                    )
                    count += 1
                    _sms_received_today += 1
                    _sms_received_total += 1
                    log(f"SMS RECEIVED: from={msg['sender']}, len={len(msg['content'])}")
                except Exception as e:
                    log(f"Incoming SMS forward error: {e}")

        if count > 0:
            log(f"Total incoming SMS processed: {count}")
        return count

    except Exception as e:
        log(f"Incoming SMS poll error: {e}")
        import traceback
        traceback.print_exc()
        return 0


# ==================== AT Commands (Serial) ====================

def _at_send(ser, cmd: str, timeout: float = 5.0) -> str:
    """Send AT command and read response."""
    ser.reset_input_buffer()
    # IK41 uses \n instead of standard \r\n
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.5)
    end_time = time.time() + timeout
    response = b""
    while time.time() < end_time:
        if ser.in_waiting:
            response += ser.read(ser.in_waiting)
            if b"OK" in response or b"ERROR" in response:
                break
        time.sleep(0.1)
    return response.decode("utf-8", errors="replace").strip()


async def probe_at_ports() -> dict:
    """Scan COM ports for AT-capable modem and check SMS storage."""
    result = {"ports_found": [], "at_port": None, "sms_storage": None,
              "has_serial": HAS_SERIAL}

    # Always add USB/device diagnostics (even without pyserial)
    import subprocess
    try:
        # Check Windows Device Manager for modems and COM ports
        wmic_out = subprocess.run(
            ["wmic", "path", "Win32_PnPEntity", "where",
             "Caption like '%COM%' or Caption like '%modem%' or Caption like '%Alcatel%' or Caption like '%TCL%' or Caption like '%Mobile%' or Caption like '%USB%Serial%'",
             "get", "Caption,DeviceID,Status"],
            capture_output=True, text=True, timeout=15
        )
        result["wmic_devices"] = wmic_out.stdout.strip()[-2000:] if wmic_out.stdout else ""

        # Also check network adapters (RNDIS modems show as network)
        wmic_net = subprocess.run(
            ["wmic", "path", "Win32_NetworkAdapter", "where",
             "Name like '%RNDIS%' or Name like '%Alcatel%' or Name like '%Mobile%' or Name like '%modem%'",
             "get", "Name,DeviceID,NetEnabled"],
            capture_output=True, text=True, timeout=15
        )
        result["wmic_network"] = wmic_net.stdout.strip()[-1000:] if wmic_net.stdout else ""

        # Direct check: try opening COM1-COM20 brute force
        result["brute_force_ports"] = []
        if HAS_SERIAL:
            for i in range(1, 21):
                port = f"COM{i}"
                try:
                    ser = serial_mod.Serial(port, baudrate=115200, timeout=1)
                    result["brute_force_ports"].append({"port": port, "open": True})
                    ser.close()
                except Exception as e:
                    err = str(e)
                    if "PermissionError" in err or "Access is denied" in err:
                        result["brute_force_ports"].append(
                            {"port": port, "open": False, "reason": "in use/permission denied"})
                    # Skip "FileNotFoundError" - port doesn't exist
    except Exception as e:
        result["diag_error"] = str(e)

    if not HAS_SERIAL:
        result["error"] = "pyserial not installed. Run: pip install pyserial"
        return result

    try:
        import serial.tools.list_ports as list_ports
        ports = list(list_ports.comports())
        result["ports_found"] = [
            {"port": p.device, "desc": p.description, "hwid": p.hwid}
            for p in ports
        ]

        for port_info in ports:
            port = port_info.device
            try:
                ser = serial_mod.Serial(
                    port, baudrate=115200, timeout=3,
                    write_timeout=3, bytesize=8,
                    parity="N", stopbits=1
                )
                # Test AT
                resp = _at_send(ser, "AT", timeout=3)
                if "OK" not in resp:
                    ser.close()
                    continue

                result["at_port"] = port
                at_results = {"AT": resp}

                # Set text mode
                resp = _at_send(ser, "AT+CMGF=1")
                at_results["AT+CMGF=1"] = resp

                # Check SMS storage
                resp = _at_send(ser, "AT+CPMS?")
                at_results["AT+CPMS?"] = resp
                # Parse: +CPMS: "ME",19,100,"ME",19,100,"ME",19,100
                import re
                m = re.search(r'\+CPMS:\s*"(\w+)",(\d+),(\d+)', resp)
                if m:
                    result["sms_storage"] = {
                        "memory": m.group(1),
                        "used": int(m.group(2)),
                        "total": int(m.group(3)),
                    }

                # Get modem info
                resp = _at_send(ser, "ATI")
                at_results["ATI"] = resp

                result["at_responses"] = at_results
                ser.close()
                break  # Found working port
            except Exception as e:
                result.setdefault("port_errors", {})[port] = str(e)
                try:
                    ser.close()
                except Exception:
                    pass

    except Exception as e:
        result["error"] = str(e)

    return result


async def delete_sms_via_at(com_port: str = None) -> dict:
    """Delete all SMS from modem via AT commands on serial port."""
    result = {"success": False, "sms_before": 0, "sms_after": 0}

    if not HAS_SERIAL:
        result["error"] = "pyserial not installed. Run: pip install pyserial"
        return result

    try:
        # Auto-detect port if not specified
        if not com_port:
            probe = await probe_at_ports()
            com_port = probe.get("at_port")
            if not com_port:
                result["error"] = "No AT-capable port found"
                result["probe"] = probe
                return result

        result["port"] = com_port

        ser = serial_mod.Serial(
            com_port, baudrate=115200, timeout=5,
            write_timeout=5, bytesize=8,
            parity="N", stopbits=1
        )

        # Test AT
        resp = _at_send(ser, "AT")
        if "OK" not in resp:
            ser.close()
            result["error"] = f"AT failed on {com_port}: {resp}"
            return result

        # Set text mode
        _at_send(ser, "AT+CMGF=1")

        # Check SMS count before
        resp = _at_send(ser, "AT+CPMS?")
        import re
        m = re.search(r'\+CPMS:\s*"(\w+)",(\d+),(\d+)', resp)
        if m:
            result["sms_before"] = int(m.group(2))
            result["storage_total"] = int(m.group(3))

        # Delete ALL SMS: AT+CMGD=1,4
        resp = _at_send(ser, "AT+CMGD=1,4", timeout=10)
        result["delete_response"] = resp
        delete_ok = "OK" in resp

        if not delete_ok:
            # Try alternative: AT+CMGD=0,4
            resp = _at_send(ser, "AT+CMGD=0,4", timeout=10)
            result["delete_alt_response"] = resp
            delete_ok = "OK" in resp

        # Check SMS count after
        resp = _at_send(ser, "AT+CPMS?")
        m = re.search(r'\+CPMS:\s*"(\w+)",(\d+),(\d+)', resp)
        if m:
            result["sms_after"] = int(m.group(2))

        result["success"] = delete_ok and result["sms_after"] < result["sms_before"]
        result["deleted"] = result["sms_before"] - result["sms_after"]

        ser.close()

    except Exception as e:
        result["error"] = str(e)
        try:
            ser.close()
        except Exception:
            pass

    return result


# ==================== Modem Backup / Reset ====================

async def _modem_api(client, base_url: str, method: str, params: dict = None,
                     headers: dict = None) -> dict:
    """Call modem JSON-RPC method. Returns parsed result or error dict."""
    try:
        resp = await client.post(f"{base_url}/jrd/webapi",
            json={"jsonrpc": "2.0", "method": method,
                  "params": params or {}, "id": "1"},
            headers=headers)
        data = resp.json()
        if "error" in data:
            return {"_error": data["error"]}
        return data.get("result", {})
    except Exception as e:
        return {"_error": str(e)}


async def _modem_login(client, base_url: str) -> tuple:
    """Login to modem. Returns (headers, error_or_None)."""
    import re
    try:
        resp = await client.get(base_url)
        m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
        if not m:
            return None, "Cannot extract token"

        tok = m.group(1)
        hdrs = {"_TclRequestVerificationKey": tok,
                "Referer": f"http://{MODEM_HOST}/index.html"}

        lr = await client.post(f"{base_url}/jrd/webapi",
            json={"jsonrpc": "2.0", "method": "Login",
                  "params": {"UserName": "admin", "Password": "admin"},
                  "id": "0"}, headers=hdrs)
        lr_data = lr.json()
        if "error" in lr_data:
            return None, f"Login failed: {lr_data['error']}"
        return hdrs, None
    except Exception as e:
        return None, str(e)


async def modem_backup_settings() -> dict:
    """Backup all modem settings via Get* methods."""
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
    result = {"success": False, "backup": {}, "errors": {}}

    if not HAS_HTTPX:
        result["error"] = "httpx not available"
        return result

    backup_methods = [
        "GetProfileList",
        "GetConnectionSettings",
        "GetNetworkSettings",
        "GetLanSettings",
        "GetSMSSettings",
        "GetWlanSettings",
        "GetPowerSavingMode",
        "GetLanguage",
        "GetSMSStorageState",
        "GetSystemInfo",
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["error"] = err
                return result

            for method in backup_methods:
                data = await _modem_api(client, base_url, method, headers=hdrs)
                if "_error" in data:
                    result["errors"][method] = data["_error"]
                else:
                    result["backup"][method] = data

            # Also try built-in backup
            bk = await _modem_api(client, base_url, "SetDeviceBackup", headers=hdrs)
            result["builtin_backup"] = bk

            await _modem_api(client, base_url, "Logout", headers=hdrs)

        result["success"] = len(result["backup"]) > 0
        log(f"Modem backup: {len(result['backup'])} settings, {len(result['errors'])} errors")
    except Exception as e:
        result["error"] = str(e)

    return result


async def modem_reboot() -> dict:
    """Safe reboot - no data loss, modem comes back with same settings."""
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
    result = {"success": False}

    if not HAS_HTTPX:
        result["error"] = "httpx not available"
        return result

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["error"] = err
                return result

            # Get SMS count before
            storage = await _modem_api(client, base_url, "GetSMSStorageState", headers=hdrs)
            result["sms_before"] = storage.get("TUseCount", -1)

            # Reboot
            rb = await _modem_api(client, base_url, "SetDeviceReboot", headers=hdrs)
            result["reboot_response"] = rb
            log("Modem reboot sent, waiting for restart...")

        # Wait for modem to come back
        await asyncio.sleep(60)  # Initial wait (modem needs time)
        came_back = False
        for i in range(60):  # 60 * 5s = 300s + 60s = 360s max
            await asyncio.sleep(5)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(base_url)
                    if resp.status_code == 200:
                        came_back = True
                        result["restart_time_s"] = (i + 1) * 5 + 60
                        log(f"Modem back after {result['restart_time_s']}s")
                        break
            except Exception:
                pass

        if not came_back:
            result["error"] = "Modem did not come back after 360s"
            return result

        # Check SMS after reboot
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["error"] = f"Post-reboot login failed: {err}"
                return result
            storage = await _modem_api(client, base_url, "GetSMSStorageState", headers=hdrs)
            result["sms_after"] = storage.get("TUseCount", -1)
            await _modem_api(client, base_url, "Logout", headers=hdrs)

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


async def modem_factory_reset() -> dict:
    """Factory reset modem with automatic backup/restore of settings."""
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"
    result = {"success": False, "phases": {}, "sms_before": -1, "sms_after": -1}

    if not HAS_HTTPX:
        result["error"] = "httpx not available"
        return result

    # --- PHASE 1: BACKUP ---
    log("Factory reset PHASE 1: Backing up settings...")
    backup_result = await modem_backup_settings()
    result["phases"]["backup"] = {
        "success": backup_result.get("success"),
        "settings_count": len(backup_result.get("backup", {})),
        "errors": backup_result.get("errors", {}),
    }
    backup = backup_result.get("backup", {})

    if not backup_result.get("success"):
        result["error"] = "Backup failed, aborting reset"
        return result

    result["backup"] = backup  # Save full backup in result for manual recovery
    result["sms_before"] = backup.get("GetSMSStorageState", {}).get("TUseCount", -1)
    log(f"Backup complete: {len(backup)} settings, SMS={result['sms_before']}")

    # --- PHASE 2: RESET ---
    log("Factory reset PHASE 2: Sending SetDeviceReset...")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["error"] = f"Login before reset failed: {err}"
                return result

            reset_resp = await _modem_api(client, base_url, "SetDeviceReset", headers=hdrs)
            result["phases"]["reset"] = {"response": reset_resp}
            log(f"SetDeviceReset response: {reset_resp}")
    except Exception as e:
        result["error"] = f"Reset call failed: {e}"
        return result

    # --- PHASE 3: WAIT ---
    log("Factory reset PHASE 3: Waiting for modem to restart...")
    await asyncio.sleep(60)  # Longer initial wait for factory reset
    came_back = False
    for i in range(78):  # 78 * 5s = 390s + 60s = 450s max
        await asyncio.sleep(5)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(base_url)
                if resp.status_code == 200:
                    came_back = True
                    result["phases"]["wait"] = {"restart_time_s": (i + 1) * 5 + 60}
                    log(f"Modem back after {result['phases']['wait']['restart_time_s']}s")
                    break
        except Exception:
            pass

    if not came_back:
        result["error"] = "Modem did not come back after 450s. Backup saved in result."
        result["phases"]["wait"] = {"error": "timeout"}
        return result

    await asyncio.sleep(10)  # Extra wait for services to stabilize

    # --- PHASE 4: VERIFY SMS CLEARED ---
    log("Factory reset PHASE 4: Verifying SMS cleared...")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["phases"]["verify"] = {"error": f"Post-reset login: {err}"}
                # Try without login for some methods
            else:
                storage = await _modem_api(client, base_url, "GetSMSStorageState", headers=hdrs)
                result["sms_after"] = storage.get("TUseCount", -1)
                result["phases"]["verify"] = {
                    "sms_after": result["sms_after"],
                    "sms_cleared": result["sms_after"] == 0,
                }
                log(f"SMS after reset: {result['sms_after']}")

                sysinfo = await _modem_api(client, base_url, "GetSystemInfo", headers=hdrs)
                result["phases"]["verify"]["imei"] = sysinfo.get("IMEI", "?")
    except Exception as e:
        result["phases"]["verify"] = {"error": str(e)}

    # --- PHASE 5: RESTORE ---
    log("Factory reset PHASE 5: Restoring settings...")
    restore_results = {}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if err:
                result["phases"]["restore"] = {"error": f"Login for restore: {err}"}
                result["error"] = "Cannot login to restore settings. Backup saved in result."
                return result

            # 1. APN Profile (CRITICAL)
            profiles = backup.get("GetProfileList", {})
            profile_list = profiles.get("ProfileList", [])
            if profile_list:
                for profile in profile_list:
                    pr = await _modem_api(client, base_url, "AddNewProfile",
                        params=profile, headers=hdrs)
                    restore_results["AddNewProfile"] = pr
                    log(f"APN restore: {pr}")
                # Set first profile as default
                dp = await _modem_api(client, base_url, "SetDefaultProfile",
                    params={"ProfileID": 1}, headers=hdrs)
                restore_results["SetDefaultProfile"] = dp

            # 2. Connection Settings
            conn = backup.get("GetConnectionSettings")
            if conn:
                r = await _modem_api(client, base_url, "SetConnectionSettings",
                    params=conn, headers=hdrs)
                restore_results["SetConnectionSettings"] = r

            # 3. Network Settings
            net = backup.get("GetNetworkSettings")
            if net:
                r = await _modem_api(client, base_url, "SetNetworkSettings",
                    params=net, headers=hdrs)
                restore_results["SetNetworkSettings"] = r

            # 4. LAN Settings
            lan = backup.get("GetLanSettings")
            if lan:
                r = await _modem_api(client, base_url, "SetLanSettings",
                    params=lan, headers=hdrs)
                restore_results["SetLanSettings"] = r

            # 5. SMS Settings (may fail - known bug)
            sms = backup.get("GetSMSSettings")
            if sms:
                r = await _modem_api(client, base_url, "SetSMSSettings",
                    params=sms, headers=hdrs)
                restore_results["SetSMSSettings"] = r

            # 6. Power Saving
            ps = backup.get("GetPowerSavingMode")
            if ps:
                r = await _modem_api(client, base_url, "SetPowerSavingMode",
                    params=ps, headers=hdrs)
                restore_results["SetPowerSavingMode"] = r

            # 7. Language
            lang = backup.get("GetLanguage")
            if lang:
                r = await _modem_api(client, base_url, "SetLanguage",
                    params=lang, headers=hdrs)
                restore_results["SetLanguage"] = r

            # Try built-in restore
            br = await _modem_api(client, base_url, "SetDeviceRestore", headers=hdrs)
            restore_results["SetDeviceRestore_builtin"] = br

            await _modem_api(client, base_url, "Logout", headers=hdrs)

    except Exception as e:
        restore_results["_exception"] = str(e)

    result["phases"]["restore"] = restore_results

    # --- PHASE 6: FINAL VERIFY ---
    log("Factory reset PHASE 6: Final verification...")
    try:
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            hdrs, err = await _modem_login(client, base_url)
            if not err:
                storage = await _modem_api(client, base_url, "GetSMSStorageState", headers=hdrs)
                result["sms_after"] = storage.get("TUseCount", result["sms_after"])
                profiles = await _modem_api(client, base_url, "GetProfileList", headers=hdrs)
                conn_state = await _modem_api(client, base_url, "GetConnectionState", headers=hdrs)
                result["phases"]["final_verify"] = {
                    "sms": result["sms_after"],
                    "profiles": profiles,
                    "connection": conn_state,
                }
                await _modem_api(client, base_url, "Logout", headers=hdrs)
    except Exception as e:
        result["phases"]["final_verify"] = {"error": str(e)}

    result["success"] = result["sms_after"] == 0

    # Clear processed SMS IDs after factory reset - IK41 doesn't reset ID counter,
    # so old IDs in the set would cause new incoming SMS to be skipped
    if result["success"]:
        old_count = len(_processed_sms_ids)
        _processed_sms_ids.clear()
        save_processed_sms_ids()
        log(f"Cleared {old_count} processed SMS IDs after factory reset")

    log(f"Factory reset complete: SMS {result['sms_before']} → {result['sms_after']}")
    return result


# ==================== SMS Storage Monitoring ====================

async def check_sms_storage() -> None:
    """Check modem SMS storage. Auto-reset if > 80% full."""
    global _sms_storage_used, _sms_storage_max, _auto_reset_in_progress
    import re
    base_url = f"http://{MODEM_HOST}:{MODEM_PORT}"

    if not HAS_HTTPX:
        return

    if _auto_reset_in_progress:
        log("SMS storage check skipped: auto-reset in progress")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(base_url)
            m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
            if not m:
                return

            token = m.group(1)
            headers = {
                "_TclRequestVerificationKey": token,
                "Referer": f"http://{MODEM_HOST}/index.html",
            }

            resp = await client.post(f"{base_url}/jrd/webapi",
                json={"jsonrpc": "2.0", "method": "GetSMSStorageState",
                      "params": {}, "id": "1"}, headers=headers)

            result = (resp.json().get("result") or {})
            _sms_storage_max = result.get("MaxCount", 100)
            _sms_storage_used = result.get("TUseCount", 0)
            left = result.get("LeftCount", _sms_storage_max - _sms_storage_used)

            percent = (_sms_storage_used / _sms_storage_max * 100) if _sms_storage_max > 0 else 0
            log(f"SMS storage: {_sms_storage_used}/{_sms_storage_max} ({percent:.0f}%), {left} free")

            if percent >= SMS_STORAGE_WARN_PERCENT:
                global _last_sms_error
                _last_sms_error = f"SMS storage {percent:.0f}% full ({_sms_storage_used}/{_sms_storage_max})"

                if SMS_STORAGE_AUTO_RESET:
                    log(f"AUTO-RESET: SMS storage {percent:.0f}% full ({_sms_storage_used}/{_sms_storage_max}), triggering factory reset...")
                    _auto_reset_in_progress = True
                    try:
                        reset_result = await modem_factory_reset()
                        sms_before = reset_result.get("sms_before", "?")
                        sms_after = reset_result.get("sms_after", "?")
                        success = reset_result.get("success", False)
                        log(f"AUTO-RESET complete: SMS {sms_before} -> {sms_after}, success={success}")

                        if success:
                            _sms_storage_used = 0
                            _last_sms_error = None

                            # Also clear SMS from database
                            try:
                                async with httpx.AsyncClient(timeout=10.0) as api_client:
                                    del_resp = await api_client.delete(
                                        f"{CENTRAL_API}/sms/received/all",
                                        headers={"X-Dashboard-Key": HEARTBEAT_API_KEY},
                                        timeout=10.0
                                    )
                                    if del_resp.status_code == 200:
                                        del_data = del_resp.json()
                                        log(f"AUTO-RESET: Cleared {del_data.get('deleted', 0)} SMS from database")
                                    else:
                                        log(f"AUTO-RESET: DB cleanup failed: {del_resp.status_code}")
                            except Exception as db_err:
                                log(f"AUTO-RESET: DB cleanup error: {db_err}")
                        else:
                            log(f"AUTO-RESET FAILED: {reset_result.get('error', 'unknown')}")
                    except Exception as reset_err:
                        log(f"AUTO-RESET error: {reset_err}")
                    finally:
                        _auto_reset_in_progress = False
                else:
                    log(f"WARNING: SMS storage {percent:.0f}% full! "
                        f"Only {left} slots remaining. Auto-reset disabled.")

    except Exception as e:
        log(f"SMS storage check error: {e}")


# ==================== Shutdown ====================

_shutdown_requested = False

def graceful_shutdown(signum=None, frame=None):
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    _shutdown_requested = True
    log("Shutdown requested")


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, graceful_shutdown)
        signal.signal(signal.SIGINT, graceful_shutdown)
    else:
        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGBREAK, graceful_shutdown)


# ==================== Main Loop ====================

async def daemon_loop():
    """Main daemon loop."""
    global _shutdown_requested

    client_key = get_or_create_client_key()
    log(f"Daemon started: {client_key[:12]}...")
    log(f"Central API: {CENTRAL_API}")
    log(f"Heartbeat: {HEARTBEAT_INTERVAL}s, Auto-update: {AUTO_UPDATE_ENABLED}")

    # Load persisted processed SMS IDs to prevent duplicates after restart
    global _processed_sms_ids
    _processed_sms_ids = load_processed_sms_ids()

    # Save PID
    PID_FILE.write_text(str(os.getpid()))

    last_heartbeat = 0
    last_command_poll = 0
    last_update_check = 0
    last_sms_poll = 0
    last_incoming_poll = 0
    last_storage_check = 0

    log(f"SMS polling: {SMS_POLL_INTERVAL}s, Incoming SMS: {INCOMING_SMS_INTERVAL}s")
    log(f"Rate limits: {SMS_DAILY_LIMIT}/day, {SMS_HOURLY_LIMIT}/hour")
    log(f"PHP API: {ESKIMOS_PHP_API}")
    log(f"Modem type: {MODEM_TYPE}, phone: {MODEM_PHONE}")
    if MODEM_TYPE == "serial":
        log(f"Serial: port={SERIAL_PORT}, baud={SERIAL_BAUDRATE}")
    else:
        log(f"Modem: {MODEM_HOST}:{MODEM_PORT}")

    try:
        while not _shutdown_requested:
            now = time.time()

            # Heartbeat
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                response = await send_heartbeat(client_key)
                last_heartbeat = now

                # Check for update hint in response
                if response.get("update_available") and AUTO_UPDATE_ENABLED:
                    log("Update available via heartbeat response")
                    # Will be handled by command poll

            # Command polling
            if now - last_command_poll >= COMMAND_POLL_INTERVAL:
                commands = await poll_commands(client_key)
                for cmd in commands:
                    await execute_command(client_key, cmd)
                last_command_poll = now

            # SMS queue polling (send outgoing)
            if now - last_sms_poll >= SMS_POLL_INTERVAL:
                try:
                    await poll_and_send_sms()
                except Exception as e:
                    log(f"SMS poll loop error: {e}")
                last_sms_poll = now

            # Incoming SMS polling (receive)
            if now - last_incoming_poll >= INCOMING_SMS_INTERVAL:
                try:
                    await poll_incoming_sms()
                except Exception as e:
                    log(f"Incoming SMS loop error: {e}")
                last_incoming_poll = now

            # SMS storage monitoring
            if now - last_storage_check >= SMS_STORAGE_CHECK_INTERVAL:
                try:
                    await check_sms_storage()
                except Exception as e:
                    log(f"SMS storage check error: {e}")
                last_storage_check = now

            # Periodic update check (background)
            if AUTO_UPDATE_ENABLED and now - last_update_check >= UPDATE_CHECK_INTERVAL:
                try:
                    from eskimos.infrastructure.updater import check_for_update
                    has_update, latest_version = await check_for_update()
                    if has_update:
                        log(f"Auto-update available: {latest_version}")
                        # Auto-update will be triggered by command from server
                except Exception as e:
                    log(f"Update check error: {e}")
                last_update_check = now

            # Sleep
            await asyncio.sleep(5)

    finally:
        # Cleanup modem connection
        await _disconnect_modem()
        # Cleanup PID file
        if PID_FILE.exists():
            PID_FILE.unlink()
        log("Daemon stopped")


def is_daemon_running() -> bool:
    """Check if daemon is already running."""
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process exists
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
        else:
            os.kill(pid, 0)
            return True
    except (ValueError, OSError, ProcessLookupError):
        # Process not running, clean up stale PID
        PID_FILE.unlink()

    return False


def start_daemon():
    """Start the daemon."""
    if is_daemon_running():
        log("Daemon already running")
        return

    setup_signal_handlers()

    try:
        asyncio.run(daemon_loop())
    except KeyboardInterrupt:
        log("Interrupted by user")
    except Exception as e:
        log(f"Daemon error: {e}")
        import traceback
        traceback.print_exc()

    sys.exit(0)


def stop_daemon():
    """Stop the daemon."""
    if not PID_FILE.exists():
        log("Daemon not running")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGTERM)
        log(f"Sent stop signal to PID {pid}")
    except Exception as e:
        log(f"Stop error: {e}")

    PID_FILE.unlink(missing_ok=True)


def daemon_status():
    """Print daemon status."""
    if is_daemon_running():
        pid = PID_FILE.read_text().strip()
        print(f"Daemon running (PID: {pid})")
    else:
        print("Daemon not running")


# ==================== CLI ====================

def main():
    """CLI entry point."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m eskimos.infrastructure.daemon [start|stop|status]")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "start":
        start_daemon()
    elif command == "stop":
        stop_daemon()
    elif command == "status":
        daemon_status()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
