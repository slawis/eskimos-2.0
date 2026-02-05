"""Health check endpoints."""

from __future__ import annotations

import asyncio
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

# Modem configuration from environment
MODEM_HOST = os.environ.get("MODEM_HOST", "192.168.1.1")
MODEM_PORT = int(os.environ.get("MODEM_PORT", "80"))
MODEM_PHONE = os.environ.get("MODEM_PHONE_NUMBER", "886480453")
MODEM_TYPE = os.environ.get("MODEM_TYPE", "puppeteer")
MODEM_PROBE_TIMEOUT = float(os.environ.get("MODEM_PROBE_TIMEOUT", "3.0"))

# Cache for hardware detection
_modem_hw_cache: Optional[dict] = None


class ModemHealthInfo(BaseModel):
    """Modem connectivity details."""

    connected: bool = False
    phone_number: str = ""
    host: str = ""
    adapter_type: str = ""
    model: str = ""
    manufacturer: str = ""
    connection_type: str = ""


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
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )

        # Send HTTP GET for main page
        request = f"GET / HTTP/1.0\r\nHost: {host}\r\nAccept: */*\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()

        # Read response (8KB enough for headers + start of HTML)
        data = await asyncio.wait_for(reader.read(8192), timeout=timeout)
        writer.close()
        await writer.wait_closed()

        html = data.decode("utf-8", errors="ignore")

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
                        content_type: str = "application/json") -> str:
    """Send raw HTTP request and return response body."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )

    headers = f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nAccept: */*\r\n"
    if body:
        headers += f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
    headers += "\r\n"

    writer.write(headers.encode() + body.encode())
    await writer.drain()

    data = await asyncio.wait_for(reader.read(8192), timeout=timeout)
    writer.close()
    await writer.wait_closed()

    return data.decode("utf-8", errors="ignore")


async def _try_modem_apis(host: str, port: int, timeout: float, result: dict) -> dict:
    """Try known REST API endpoints for common modem brands."""

    # TCL / Alcatel - uses POST with JSON-RPC
    tcl_methods = [
        ("GetSystemInfo", ["DeviceName", "Manufacturer", "FwVersion"]),
        ("GetDeviceInfo", ["DeviceName", "Manufacturer"]),
        ("GetCurrentLanguage", []),  # fallback to confirm it's TCL
    ]

    for method_name, _ in tcl_methods:
        try:
            json_body = f'{{"jsonrpc":"2.0","method":"{method_name}","params":{{}},"id":"1"}}'
            resp = await _http_request(host, port, "POST", "/jrd/webapi",
                                       body=json_body, timeout=timeout)
            if "404" in resp[:30] or "error" in resp[:100].lower():
                continue

            # Parse TCL response
            m = re.search(r'"DeviceName"\s*:\s*"([^"]+)"', resp)
            if m:
                result["model"] = m.group(1)
            m = re.search(r'"Manufacturer"\s*:\s*"([^"]+)"', resp)
            if m:
                result["manufacturer"] = m.group(1)
            if not result["model"]:
                m = re.search(r'"[Ff]w[Vv]ersion"\s*:\s*"([^"]+)"', resp)
                if m:
                    result["model"] = f"TCL ({m.group(1)})"
                    result["manufacturer"] = "TCL"
            if result["model"]:
                return result
        except Exception:
            continue

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

    # Generic - GET /api/device/information
    try:
        resp = await _http_request(host, port, "GET",
                                   "/api/device/information", timeout=timeout)
        m = re.search(r'"[Mm]odel[^"]*"\s*:\s*"([^"]+)"', resp)
        if m:
            result["model"] = m.group(1)
        m = re.search(r'"[Mm]anufacturer[^"]*"\s*:\s*"([^"]+)"', resp)
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


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check API health, modem connectivity and hardware info."""
    global _modem_hw_cache

    modem_reachable = await probe_modem(MODEM_HOST, MODEM_PORT, MODEM_PROBE_TIMEOUT)

    hw = {"model": "", "manufacturer": "", "connection_type": ""}

    if modem_reachable:
        # Use cache if available
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
            host=MODEM_HOST,
            adapter_type=MODEM_TYPE,
            model=hw.get("model", ""),
            manufacturer=hw.get("manufacturer", ""),
            connection_type=hw.get("connection_type", ""),
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
