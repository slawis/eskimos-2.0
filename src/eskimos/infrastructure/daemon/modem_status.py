"""Modem status detection - probe, detect model, get status.

Supports two modem types:
- serial: SIM7600G-H via AT commands over COM port
- ik41: Alcatel IK41 via JSON-RPC over HTTP (RNDIS/USB)
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from eskimos.infrastructure.daemon.at_commands import AtCommandHelper, HAS_SERIAL, serial_mod
from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log

# Lazy httpx import
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


class ModemStatusProvider:
    """Detect and report modem status.

    Replaces 8 module-level functions and 2 cache globals from _original.py.
    """

    def __init__(self, config: DaemonConfig, at_helper: AtCommandHelper) -> None:
        self.config = config
        self.at_helper = at_helper
        self._modem_model_cache: Optional[dict] = None
        self._cached_serial_port: Optional[str] = None

    async def get_status(self) -> dict:
        """Get modem status - branches on modem_type."""
        if self.config.modem_type == "serial":
            gw_status = await self._get_status_via_gateway()
            if gw_status:
                return gw_status
            return await self._get_status_serial()

        # IK41/TCL: direct TCP probe + JSON-RPC model detection
        reachable = await self._probe_direct()

        if not reachable:
            self._modem_model_cache = None
            return {
                "status": "disconnected",
                "phone_number": "",
                "model": "",
                "manufacturer": "",
                "connection_type": "",
            }

        hw = await self._detect_model_tcl()

        return {
            "status": "connected",
            "phone_number": self.config.modem_phone,
            "model": hw.get("model", ""),
            "manufacturer": hw.get("manufacturer", ""),
            "connection_type": hw.get("connection_type", "RNDIS/USB"),
        }

    async def _resolve_serial_port(self) -> Optional[str]:
        """Resolve serial port - auto-detect or use explicit config."""
        if self._cached_serial_port:
            return self._cached_serial_port

        if self.config.serial_port != "auto":
            self._cached_serial_port = self.config.serial_port
            return self.config.serial_port

        if not HAS_SERIAL:
            log("Serial port auto-detect: pyserial not installed", self.config.log_file)
            return None

        at_send = self.at_helper.at_send_sync
        baudrate = self.config.serial_baudrate

        def _detect():
            import serial.tools.list_ports as list_ports
            for port_info in list_ports.comports():
                desc = (port_info.description or "").upper()
                hwid = (port_info.hwid or "").upper()
                if "SIMCOM" in desc or "SIM7600" in desc or "1E0E" in hwid:
                    try:
                        ser = serial_mod.Serial(port_info.device, baudrate, timeout=2)
                        resp = at_send(ser, "AT", timeout=2)
                        ser.close()
                        if "OK" in resp:
                            return port_info.device
                    except Exception:
                        pass
            for i in range(1, 21):
                port = f"COM{i}"
                try:
                    ser = serial_mod.Serial(port, baudrate, timeout=2)
                    resp = at_send(ser, "ATI", timeout=3)
                    ser.close()
                    if "SIMCOM" in resp or "SIM7600" in resp:
                        return port
                except Exception:
                    pass
            return None

        port = await asyncio.get_running_loop().run_in_executor(None, _detect)
        if port:
            self._cached_serial_port = port
            log(f"Serial port auto-detected: {port}", self.config.log_file)
        else:
            log("Serial port auto-detect FAILED - no SIMCOM modem found", self.config.log_file)
        return port

    async def _probe_direct(self) -> bool:
        """Direct TCP probe to modem IP."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.modem_host, self.config.modem_port),
                timeout=3.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
            return False

    async def _detect_model_tcl(self) -> dict:
        """Detect TCL/Alcatel modem model via JRD webapi login."""
        if self._modem_model_cache:
            return self._modem_model_cache

        if not HAS_HTTPX:
            return {}

        result = {"model": "", "manufacturer": "", "connection_type": "RNDIS/USB"}
        base_url = f"http://{self.config.modem_host}:{self.config.modem_port}"

        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(base_url)
                m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
                if not m:
                    return result

                token = m.group(1)
                headers = {
                    "_TclRequestVerificationKey": token,
                    "Referer": f"http://{self.config.modem_host}/index.html",
                }
                result["manufacturer"] = "Alcatel/TCL"

                login_body = {
                    "jsonrpc": "2.0", "method": "Login",
                    "params": {"UserName": "admin", "Password": "admin"}, "id": "1",
                }
                resp = await client.post(
                    f"{base_url}/jrd/webapi", json=login_body, headers=headers)
                if "result" not in resp.text or "error" in resp.text.lower():
                    return result

                body = {
                    "jsonrpc": "2.0", "method": "GetSystemInfo",
                    "params": {}, "id": "1",
                }
                resp = await client.post(
                    f"{base_url}/jrd/webapi", json=body, headers=headers)
                m = re.search(r'"DeviceName"\s*:\s*"([^"]+)"', resp.text)
                if m:
                    result["model"] = m.group(1).strip()
                    hw = re.search(r'"HwVersion"\s*:\s*"([^"]+)"', resp.text)
                    if hw:
                        result["model"] = f"{result['model']} ({hw.group(1).strip()})"

                try:
                    await client.post(
                        f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "Logout",
                              "params": {}, "id": "1"},
                        headers=headers)
                except Exception:
                    pass

                if result["model"]:
                    self._modem_model_cache = result

        except Exception as e:
            log(f"TCL detection error: {e}", self.config.log_file)

        return result

    async def _get_status_via_gateway(self) -> Optional[dict]:
        """Get modem status from local Gateway API (localhost:8000/api/health)."""
        try:
            if not HAS_HTTPX:
                return None
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"http://127.0.0.1:{self.config.gateway_port}/api/health")
                if resp.status_code != 200:
                    return None
                data = resp.json()
                modem = data.get("modem", {})
                if not modem.get("connected"):
                    return None
                return {
                    "status": "connected",
                    "phone_number": modem.get("phone_number", self.config.modem_phone),
                    "model": modem.get("model", ""),
                    "manufacturer": modem.get("manufacturer", ""),
                    "connection_type": modem.get("connection_type", "Serial/USB"),
                    "signal_strength": modem.get("signal_strength"),
                    "network": modem.get("network", ""),
                }
        except Exception as e:
            log(f"Gateway API modem status failed: {e}", self.config.log_file)
            return None

    async def _get_status_serial(self) -> dict:
        """Get modem status via serial AT commands (SIM7600G-H)."""
        port = await self._resolve_serial_port()
        if not port:
            return {
                "status": "disconnected",
                "phone_number": "",
                "model": "",
                "manufacturer": "",
                "connection_type": "",
            }

        at_send = self.at_helper.at_send_sync
        baudrate = self.config.serial_baudrate

        def _probe():
            try:
                ser = serial_mod.Serial(port, baudrate, timeout=3)
                resp = at_send(ser, "AT")
                if "OK" not in resp:
                    ser.close()
                    return None
                ati = at_send(ser, "ATI")
                csq = at_send(ser, "AT+CSQ")
                cops = at_send(ser, "AT+COPS?")
                ser.close()
                return {"ati": ati, "csq": csq, "cops": cops}
            except Exception as e:
                log(f"Serial probe error: {e}", self.config.log_file)
                return None

        info = await asyncio.get_running_loop().run_in_executor(None, _probe)
        if not info:
            return {
                "status": "disconnected",
                "phone_number": self.config.modem_phone,
                "model": "",
                "manufacturer": "",
                "connection_type": "Serial/USB",
            }

        # Parse ATI
        model = ""
        manufacturer = "SIMCOM"
        ati = info["ati"]
        if "SIM7600" in ati:
            m = re.search(r"(SIM\d+\S*)", ati)
            model = m.group(1) if m else "SIM7600G-H"
        elif "Manufacturer" in ati:
            m = re.search(r"Model:\s*(.+)", ati)
            model = m.group(1).strip() if m else ati.split("\n")[0]

        # Parse CSQ
        signal_pct = None
        m = re.search(r"\+CSQ:\s*(\d+)", info["csq"])
        if m:
            rssi = int(m.group(1))
            if rssi <= 31:
                signal_pct = round(rssi / 31 * 100)

        # Parse COPS
        operator_name = ""
        m = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"', info["cops"])
        if m:
            operator_name = m.group(1)

        return {
            "status": "connected",
            "phone_number": self.config.modem_phone,
            "model": model,
            "manufacturer": manufacturer,
            "connection_type": "Serial/USB",
            "signal_strength": signal_pct,
            "network": operator_name,
        }
