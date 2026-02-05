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


# ==================== Configuration ====================

# Sciezki relatywne do katalogu portable
PORTABLE_ROOT = Path(__file__).parent.parent.parent  # EskimosGateway/
CLIENT_KEY_FILE = PORTABLE_ROOT / ".client_key"
LOG_FILE = PORTABLE_ROOT / "daemon.log"
PID_FILE = PORTABLE_ROOT / ".daemon.pid"
CONFIG_FILE = PORTABLE_ROOT / "config" / ".env"
BACKUP_DIR = PORTABLE_ROOT / "_backups"
UPDATE_DIR = PORTABLE_ROOT / "_updates"

# API
CENTRAL_API = os.getenv("ESKIMOS_CENTRAL_API", "https://app.ninjabot.pl/api/eskimos")
HEARTBEAT_API_KEY = os.getenv("ESKIMOS_API_KEY", "eskimos-daemon-2026")

# Interwaly (sekundy)
HEARTBEAT_INTERVAL = int(os.getenv("ESKIMOS_HEARTBEAT_INTERVAL", "60"))
COMMAND_POLL_INTERVAL = int(os.getenv("ESKIMOS_COMMAND_POLL_INTERVAL", "60"))
UPDATE_CHECK_INTERVAL = int(os.getenv("ESKIMOS_UPDATE_CHECK_INTERVAL", "3600"))

# Auto-update
AUTO_UPDATE_ENABLED = os.getenv("ESKIMOS_AUTO_UPDATE", "true").lower() == "true"


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
MODEM_PHONE = os.getenv("MODEM_PHONE_NUMBER", "886480453")


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


async def get_modem_status() -> dict:
    """Get modem status - first from local API, fallback to direct probe."""
    # Try local Gateway API first
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8000/api/health",
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                modem = data.get("modem", {})
                is_connected = modem.get("connected", data.get("modem_connected", False))
                return {
                    "status": "connected" if is_connected else "disconnected",
                    "phone_number": modem.get("phone_number", MODEM_PHONE) if is_connected else "",
                    "model": modem.get("model", ""),
                    "manufacturer": modem.get("manufacturer", ""),
                    "connection_type": modem.get("connection_type", ""),
                }
    except Exception:
        pass

    # Fallback: direct TCP probe to modem IP
    reachable = await probe_modem_direct()
    return {
        "status": "connected" if reachable else "disconnected",
        "phone_number": MODEM_PHONE if reachable else "",
        "model": "",
        "manufacturer": "",
        "connection_type": "",
    }


async def get_sms_metrics() -> dict:
    """Get SMS metrics from local API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8000/api/sms/stats",
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "sms_sent_today": data.get("sent_today", 0),
                    "sms_sent_total": data.get("sent_total", 0),
                    "sms_pending": data.get("pending", 0),
                }
    except Exception:
        pass

    return {"sms_sent_today": 0, "sms_sent_total": 0, "sms_pending": 0}


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
            from eskimos.infrastructure.updater import perform_update
            success = await perform_update(payload.get("version"))
            await acknowledge_command(client_key, cmd_id, success, None if success else "Update failed")
            if success:
                log("Update complete, restarting...")
                await asyncio.sleep(2)
                graceful_shutdown()
            else:
                log("Update failed, continuing with current version")

        elif cmd_type == "restart":
            await acknowledge_command(client_key, cmd_id, True)
            log("Restart requested, shutting down...")
            await asyncio.sleep(1)
            graceful_shutdown()

        elif cmd_type == "config":
            # Apply new config values
            new_config = payload.get("config", {})
            apply_config(new_config)
            await acknowledge_command(client_key, cmd_id, True)
            log(f"Config updated: {list(new_config.keys())}")

        elif cmd_type == "diagnostic":
            # Run diagnostic and report results back to server
            diag = await run_diagnostic()
            await acknowledge_command(client_key, cmd_id, True, result=diag)
            log(f"Diagnostic complete")

        else:
            log(f"Unknown command type: {cmd_type}")
            await acknowledge_command(client_key, cmd_id, False, f"Unknown command: {cmd_type}")

    except Exception as e:
        log(f"Command execution error: {e}")
        await acknowledge_command(client_key, cmd_id, False, str(e))


def apply_config(new_config: dict) -> None:
    """Apply new configuration values to .env file."""
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
        for key, value in new_config.items():
            env_key = f"ESKIMOS_{key.upper()}"
            env_lines[env_key] = str(value)

        # Write back
        new_content = "\n".join(f"{k}={v}" for k, v in env_lines.items())
        CONFIG_FILE.write_text(new_content)

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


async def run_diagnostic() -> dict:
    """Run diagnostic checks including direct modem HTTP probing."""
    modem = await get_modem_status()
    metrics = await get_sms_metrics()
    system = get_system_info()

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

    return {
        "modem": modem,
        "modem_debug": modem_debug,
        "metrics": metrics,
        "system": system,
        "timestamp": datetime.utcnow().isoformat(),
    }


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

    # Save PID
    PID_FILE.write_text(str(os.getpid()))

    last_heartbeat = 0
    last_command_poll = 0
    last_update_check = 0

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
        # Cleanup
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
