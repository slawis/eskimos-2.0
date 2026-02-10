"""Health check endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from eskimos import __version__

router = APIRouter()
logger = logging.getLogger(__name__)

# Load config/.env into os.environ (Gateway process doesn't load it automatically)
from pathlib import Path as _Path
_env_file = _Path(__file__).parent.parent.parent.parent / "config" / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _k, _v = _k.strip(), _v.strip()
            if _k and _k not in os.environ:
                os.environ[_k] = _v

# Modem configuration from environment
MODEM_HOST = os.environ.get("MODEM_HOST", "192.168.1.1")
MODEM_PORT = int(os.environ.get("MODEM_PORT", "80"))
MODEM_PHONE = os.environ.get("MODEM_PHONE_NUMBER", "886480453")
MODEM_TYPE = os.environ.get("MODEM_TYPE", "mock")
MODEM_PROBE_TIMEOUT = float(os.environ.get("MODEM_PROBE_TIMEOUT", "5.0"))

# Cache for hardware detection
_modem_hw_cache: Optional[dict] = None


def _resolve_serial_port() -> str:
    """Resolve serial port - auto-detect or use explicit config."""
    port = os.environ.get("SERIAL_PORT", "auto")
    if port != "auto":
        return port
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if ("SIMCOM" in desc or "SIM7600" in desc) and "AT" in desc:
                return p.device
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if "SIMCOM" in desc or "SIM7600" in desc:
                return p.device
    except ImportError:
        pass
    return "COM6"  # fallback


SERIAL_PORT = _resolve_serial_port()


class ModemHealthInfo(BaseModel):
    """Modem connectivity details."""

    connected: bool = False
    phone_number: str = ""
    host: str = ""
    adapter_type: str = ""
    model: str = ""
    manufacturer: str = ""
    connection_type: str = ""
    signal_strength: int | None = None
    network: str = ""


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime
    modem_connected: bool = False
    modem: ModemHealthInfo = ModemHealthInfo()
    api_available: bool = True


async def detect_modem_via_http(host: str, port: int, timeout: float) -> dict:
    """Detect modem model by querying its web panel via HTTP.

    Connects to the modem's web interface, reads the HTML response,
    and extracts model/manufacturer from page content and headers.
    Works in any context (service, terminal, etc.) - no WMI needed.
    """
    result = {"model": "", "manufacturer": "", "connection_type": "RNDIS/USB"}

    try:
        html = await _http_request(host, port, "GET", "/", timeout=timeout)

        # Extract from HTTP headers (Server header often has model)
        server_match = re.search(r"Server:\s*(.+)", html, re.IGNORECASE)
        if server_match:
            server = server_match.group(1).strip()
            if server and server.lower() not in ("", "nginx", "apache", "lighttpd"):
                result["model"] = server

        # Extract from HTML title
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            if title and title.lower() not in ("", "home", "index", "login"):
                if not result["model"]:
                    result["model"] = title

        # Search for device/model info in JavaScript variables
        for pattern in [
            r'device[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
            r'[Mm]odel[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
            r'product[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
            r'"model"\s*:\s*"([^"]+)"',
            r'"deviceName"\s*:\s*"([^"]+)"',
            r'"DeviceName"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, html)
            if m:
                result["model"] = m.group(1).strip()
                break

        # Search for manufacturer
        for pattern in [
            r'[Mm]anufacturer["\s:=]+["\']([^"\']+)',
            r'"manufacturer"\s*:\s*"([^"]+)"',
            r'"vendor"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, html)
            if m:
                result["manufacturer"] = m.group(1).strip()
                break

        # Try known modem API endpoints if main page didn't yield results
        if not result["model"]:
            result = await _try_modem_apis(host, port, timeout, result)

    except Exception as e:
        logger.debug(f"HTTP modem detection failed: {e}")

    return result


async def _http_request(host: str, port: int, method: str, path: str,
                        body: str = "", timeout: float = 3.0,
                        content_type: str = "application/json",
                        extra_headers: str = "") -> str:
    """Send raw HTTP request and return full response."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )

    headers = f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nAccept: */*\r\n"
    if body:
        headers += f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
    if extra_headers:
        headers += extra_headers
    headers += "\r\n"

    writer.write(headers.encode() + body.encode())
    await writer.drain()

    # Read full response (loop until EOF or timeout)
    chunks = []
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(8192), timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk)
    except (asyncio.TimeoutError, ConnectionError):
        pass
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    return b"".join(chunks).decode("utf-8", errors="ignore")


async def _tcl_api_call(host: str, port: int, timeout: float,
                        token: str, method: str, params: dict = None) -> str:
    """Make a TCL JRD webapi call with proper auth headers."""
    params = params or {}
    json_body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": "1"})
    extra = (
        f"_TclRequestVerificationKey: {token}\r\n"
        f"Referer: http://{host}/index.html\r\n"
    )
    return await _http_request(host, port, "POST", "/jrd/webapi",
                               body=json_body, timeout=timeout, extra_headers=extra)


async def _try_modem_apis(host: str, port: int, timeout: float, result: dict) -> dict:
    """Try known REST API endpoints for common modem brands."""

    # TCL / Alcatel - get token from HTML, login, then GetSystemInfo
    try:
        resp = await _http_request(host, port, "GET", "/index.html", timeout=timeout)
        token_match = re.search(r'name="header-meta"\s+content="([^"]+)"', resp)

        if not token_match:
            # Try main page (might redirect)
            resp = await _http_request(host, port, "GET", "/", timeout=timeout)
            token_match = re.search(r'name="header-meta"\s+content="([^"]+)"', resp)

        if token_match:
            token = token_match.group(1)
            result["manufacturer"] = "Alcatel/TCL"

            # Login with default password
            login_resp = await _tcl_api_call(
                host, port, timeout, token, "Login",
                {"UserName": "admin", "Password": "admin"}
            )

            if '"result"' in login_resp and "-32697" not in login_resp:
                # Login successful - query system info
                sys_resp = await _tcl_api_call(
                    host, port, timeout, token, "GetSystemInfo"
                )

                m = re.search(r'"DeviceName"\s*:\s*"([^"]+)"', sys_resp)
                if m:
                    result["model"] = m.group(1).strip()

                hw = re.search(r'"HwVersion"\s*:\s*"([^"]+)"', sys_resp)
                if hw and result["model"]:
                    result["model"] = f"{result['model']} ({hw.group(1).strip()})"

                # Logout
                try:
                    await _tcl_api_call(host, port, timeout, token, "Logout")
                except Exception:
                    pass

                if result["model"]:
                    return result
    except Exception as e:
        logger.debug(f"TCL detection error: {e}")

    # Huawei HiLink - GET
    try:
        resp = await _http_request(host, port, "GET",
                                   "/api/device/basic_information", timeout=timeout)
        m = re.search(r'"DeviceName"\s*:\s*"([^"]+)"', resp, re.IGNORECASE)
        if m:
            result["model"] = m.group(1)
            result["manufacturer"] = "Huawei"
            return result
    except Exception:
        pass

    # ZTE - GET
    try:
        resp = await _http_request(host, port, "GET",
                                   "/goform/goform_get_cmd_process?cmd=manufacturer_name,model_name",
                                   timeout=timeout)
        m = re.search(r'"model_name"\s*:\s*"([^"]+)"', resp)
        if m:
            result["model"] = m.group(1)
        m = re.search(r'"manufacturer_name"\s*:\s*"([^"]+)"', resp)
        if m:
            result["manufacturer"] = m.group(1)
        if result["model"]:
            return result
    except Exception:
        pass

    return result


def clear_modem_cache() -> None:
    """Clear hardware cache (call when modem might have changed)."""
    global _modem_hw_cache
    _modem_hw_cache = None


async def probe_modem(host: str, port: int, timeout: float) -> bool:
    """Check if modem is reachable via TCP connection."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False


async def probe_serial_modem(port: str) -> dict:
    """Probe serial modem via AT commands. Returns modem info dict."""
    try:
        from eskimos.adapters.modem.serial_at import SerialModemAdapter, SerialModemConfig

        config = SerialModemConfig(phone_number=MODEM_PHONE, port=port)
        adapter = SerialModemAdapter(config)
        await adapter.connect()

        info = await adapter.get_modem_info()
        network = await adapter.get_network_info()
        signal = await adapter.get_signal_strength()

        await adapter.disconnect()

        return {
            "connected": True,
            "model": info.get("model", ""),
            "manufacturer": info.get("manufacturer", ""),
            "connection_type": "Serial/AT",
            "signal_strength": signal,
            "network": network.get("operator", ""),
            "technology": network.get("technology", ""),
        }
    except Exception as e:
        logger.warning("Serial modem probe failed on %s: %s", port, e)
        return {"connected": False}


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check API health, modem connectivity and hardware info."""
    global _modem_hw_cache

    hw = {"model": "", "manufacturer": "", "connection_type": ""}
    modem_reachable = False
    signal_strength = None
    network = ""

    if MODEM_TYPE == "serial":
        # Serial modem: probe via AT commands
        serial_info = await probe_serial_modem(SERIAL_PORT)
        modem_reachable = serial_info.get("connected", False)
        if modem_reachable:
            hw = serial_info
            signal_strength = serial_info.get("signal_strength")
            network = serial_info.get("network", "")
    else:
        # HTTP-based modem (IK41, Dinstar)
        modem_reachable = await probe_modem(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)
        if modem_reachable:
            if _modem_hw_cache is not None:
                hw = _modem_hw_cache
            else:
                try:
                    hw = await detect_modem_via_http(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)
                    _modem_hw_cache = hw
                except Exception:
                    pass
        else:
            clear_modem_cache()

    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.utcnow(),
        modem_connected=modem_reachable,
        modem=ModemHealthInfo(
            connected=modem_reachable,
            phone_number=MODEM_PHONE if modem_reachable else "",
            host=SERIAL_PORT if MODEM_TYPE == "serial" else MODEM_HOST,
            adapter_type=MODEM_TYPE,
            model=hw.get("model", ""),
            manufacturer=hw.get("manufacturer", ""),
            connection_type=hw.get("connection_type", ""),
            signal_strength=signal_strength,
            network=network,
        ),
        api_available=True,
    )


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint."""
    return {"pong": True}


@router.get("/modem/debug")
async def modem_debug() -> dict:
    """Debug endpoint: show raw modem HTTP responses."""
    results = {}

    # 1. Main page
    try:
        resp = await _http_request(MODEM_HOST, MODEM_PORT, "GET", "/",
                                   timeout=MODEM_PROBE_TIMEOUT)
        results["main_page"] = resp[:2000]
    except Exception as e:
        results["main_page"] = f"ERROR: {e}"

    # 2. TCL API - GetSystemInfo
    try:
        json_body = '{"jsonrpc":"2.0","method":"GetSystemInfo","params":{},"id":"1"}'
        resp = await _http_request(MODEM_HOST, MODEM_PORT, "POST", "/jrd/webapi",
                                   body=json_body, timeout=MODEM_PROBE_TIMEOUT)
        results["tcl_system_info"] = resp[:2000]
    except Exception as e:
        results["tcl_system_info"] = f"ERROR: {e}"

    # 3. TCL API - GetDeviceInfo
    try:
        json_body = '{"jsonrpc":"2.0","method":"GetDeviceInfo","params":{},"id":"1"}'
        resp = await _http_request(MODEM_HOST, MODEM_PORT, "POST", "/jrd/webapi",
                                   body=json_body, timeout=MODEM_PROBE_TIMEOUT)
        results["tcl_device_info"] = resp[:2000]
    except Exception as e:
        results["tcl_device_info"] = f"ERROR: {e}"

    # 4. Detection result
    try:
        clear_modem_cache()
        hw = await detect_modem_via_http(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)
        results["detected"] = hw
    except Exception as e:
        results["detected"] = f"ERROR: {e}"

    return results
