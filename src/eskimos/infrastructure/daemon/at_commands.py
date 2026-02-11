"""AT command helper - serial modem communication.

Provides low-level AT command interface for daemon operations.
Works without pydantic or full Eskimos package installed.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Optional

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log

# Lazy imports
serial_mod = None
HAS_SERIAL = False
try:
    import serial as _serial
    import serial.tools.list_ports as _list_ports
    serial_mod = _serial
    HAS_SERIAL = True
except ImportError:
    pass


class AtCommandHelper:
    """Low-level AT command interface for daemon.

    Provides synchronous AT command execution over serial port.
    Used by modem status, SMS send/receive, and diagnostics modules.
    """

    def __init__(self, config: DaemonConfig) -> None:
        self.config = config

    @staticmethod
    def at_send_sync(ser, cmd: str, timeout: float = 5.0) -> str:
        """Send AT command and read response (synchronous, blocking).

        This is the canonical AT command implementation for the daemon.
        Kept inline (not imported from serial_at.py) to avoid pydantic
        dependency chain in portable bundle.
        """
        ser.reset_input_buffer()
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

    async def probe_at_ports(self) -> dict:
        """Scan COM ports for AT-capable modem and check SMS storage."""
        result = {
            "ports_found": [],
            "at_port": None,
            "sms_storage": None,
            "has_serial": HAS_SERIAL,
        }

        # Windows device diagnostics (even without pyserial)
        try:
            wmic_out = subprocess.run(
                ["wmic", "path", "Win32_PnPEntity", "where",
                 "Caption like '%COM%' or Caption like '%modem%' or Caption like '%Alcatel%' "
                 "or Caption like '%TCL%' or Caption like '%Mobile%' or Caption like '%USB%Serial%'",
                 "get", "Caption,DeviceID,Status"],
                capture_output=True, text=True, timeout=15,
            )
            result["wmic_devices"] = wmic_out.stdout.strip()[-2000:] if wmic_out.stdout else ""

            wmic_net = subprocess.run(
                ["wmic", "path", "Win32_NetworkAdapter", "where",
                 "Name like '%RNDIS%' or Name like '%Alcatel%' or Name like '%Mobile%' "
                 "or Name like '%modem%'",
                 "get", "Name,DeviceID,NetEnabled"],
                capture_output=True, text=True, timeout=15,
            )
            result["wmic_network"] = wmic_net.stdout.strip()[-1000:] if wmic_net.stdout else ""

            # Brute-force COM1-COM20
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
                        parity="N", stopbits=1,
                    )
                    resp = self.at_send_sync(ser, "AT", timeout=3)
                    if "OK" not in resp:
                        ser.close()
                        continue

                    result["at_port"] = port
                    at_results = {"AT": resp}

                    resp = self.at_send_sync(ser, "AT+CMGF=1")
                    at_results["AT+CMGF=1"] = resp

                    resp = self.at_send_sync(ser, "AT+CPMS?")
                    at_results["AT+CPMS?"] = resp
                    m = re.search(r'\+CPMS:\s*"(\w+)",(\d+),(\d+)', resp)
                    if m:
                        result["sms_storage"] = {
                            "memory": m.group(1),
                            "used": int(m.group(2)),
                            "total": int(m.group(3)),
                        }

                    resp = self.at_send_sync(ser, "ATI")
                    at_results["ATI"] = resp

                    result["at_responses"] = at_results
                    ser.close()
                    break
                except Exception as e:
                    result.setdefault("port_errors", {})[port] = str(e)
                    try:
                        ser.close()
                    except Exception:
                        pass

        except Exception as e:
            result["error"] = str(e)

        return result

    async def delete_sms_via_at(self, com_port: Optional[str] = None) -> dict:
        """Delete all SMS from modem via AT commands on serial port."""
        result = {"success": False, "sms_before": 0, "sms_after": 0}

        if not HAS_SERIAL:
            result["error"] = "pyserial not installed. Run: pip install pyserial"
            return result

        try:
            if not com_port:
                probe = await self.probe_at_ports()
                com_port = probe.get("at_port")
                if not com_port:
                    result["error"] = "No AT-capable port found"
                    result["probe"] = probe
                    return result

            result["port"] = com_port

            ser = serial_mod.Serial(
                com_port, baudrate=115200, timeout=5,
                write_timeout=5, bytesize=8,
                parity="N", stopbits=1,
            )

            resp = self.at_send_sync(ser, "AT")
            if "OK" not in resp:
                ser.close()
                result["error"] = f"AT failed on {com_port}: {resp}"
                return result

            self.at_send_sync(ser, "AT+CMGF=1")

            resp = self.at_send_sync(ser, "AT+CPMS?")
            m = re.search(r'\+CPMS:\s*"(\w+)",(\d+),(\d+)', resp)
            if m:
                result["sms_before"] = int(m.group(2))
                result["storage_total"] = int(m.group(3))

            resp = self.at_send_sync(ser, "AT+CMGD=1,4", timeout=10)
            result["delete_response"] = resp
            delete_ok = "OK" in resp

            if not delete_ok:
                resp = self.at_send_sync(ser, "AT+CMGD=0,4", timeout=10)
                result["delete_alt_response"] = resp
                delete_ok = "OK" in resp

            resp = self.at_send_sync(ser, "AT+CPMS?")
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
