"""Command handler registry - dispatch and execute remote commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from eskimos.infrastructure.daemon.at_commands import AtCommandHelper, HAS_SERIAL
from eskimos.infrastructure.daemon.commands import CommandPoller
from eskimos.infrastructure.daemon.config import DaemonConfig, PORTABLE_ROOT
from eskimos.infrastructure.daemon.diagnostics import DiagnosticsService
from eskimos.infrastructure.daemon.log import log
from eskimos.infrastructure.daemon.modem_control import ModemControlService
from eskimos.infrastructure.daemon.sms_incoming import SmsDedup
from eskimos.infrastructure.daemon.sms_metrics import SmsMetrics
from eskimos.infrastructure.daemon.sms_outgoing import SmsOutgoingService
from eskimos.infrastructure.daemon.sms_storage import SmsStorageMonitor

# Lazy httpx
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


class CommandHandlerRegistry:
    """Registry of command handlers, dispatches by command_type."""

    def __init__(
        self,
        config: DaemonConfig,
        poller: CommandPoller,
        metrics: SmsMetrics,
        at_helper: AtCommandHelper,
        modem_control: ModemControlService,
        diagnostics: DiagnosticsService,
        sms_outgoing: SmsOutgoingService,
        sms_storage: SmsStorageMonitor,
        dedup: SmsDedup,
        shutdown_fn=None,
    ) -> None:
        self.config = config
        self.poller = poller
        self.metrics = metrics
        self.at_helper = at_helper
        self.modem_control = modem_control
        self.diagnostics = diagnostics
        self.sms_outgoing = sms_outgoing
        self.sms_storage = sms_storage
        self.dedup = dedup
        self._shutdown_fn = shutdown_fn

    async def _ack(self, client_key, cmd_id, success, error=None, result=None):
        await self.poller.acknowledge(client_key, cmd_id, success, error, result)

    async def execute(self, client_key: str, command: dict) -> None:
        """Execute a command from central server."""
        cmd_type = command.get("command_type")
        cmd_id = command.get("id")
        payload = command.get("payload", {})

        log(f"Executing command: {cmd_type} (id={cmd_id})",
            self.config.log_file)

        try:
            handler = getattr(self, f"_handle_{cmd_type}", None)
            if handler:
                await handler(client_key, cmd_id, payload)
            else:
                log(f"Unknown command type: {cmd_type}", self.config.log_file)
                await self._ack(
                    client_key, cmd_id, False,
                    f"Unknown command: {cmd_type}")
        except Exception as e:
            log(f"Command execution error: {e}", self.config.log_file)
            await self._ack(client_key, cmd_id, False, str(e))

    # ---- Individual command handlers ----

    async def _handle_update(self, client_key, cmd_id, payload):
        import subprocess
        from eskimos.infrastructure.updater import download_update
        try:
            zip_file = await download_update(payload.get("version"))
            if not zip_file:
                await self._ack(client_key, cmd_id, False, "Download failed")
                log("Update download failed", self.config.log_file)
            else:
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
                subprocess.Popen(
                    ["cmd", "/c", str(bat_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                await self._ack(client_key, cmd_id, True)
                log("Update downloaded, helper script launched, shutting down...",
                    self.config.log_file)
                await asyncio.sleep(1)
                if self._shutdown_fn:
                    self._shutdown_fn()
        except Exception as e:
            await self._ack(client_key, cmd_id, False, str(e))
            log(f"Update error: {e}", self.config.log_file)

    async def _handle_restart(self, client_key, cmd_id, payload):
        await self._ack(client_key, cmd_id, True)
        log("Restart requested, shutting down...", self.config.log_file)
        await asyncio.sleep(1)
        if self._shutdown_fn:
            self._shutdown_fn()

    async def _handle_restart_gateway(self, client_key, cmd_id, payload):
        import subprocess
        svc_name = payload.get("service_name", "EskimosGateway")
        try:
            subprocess.run(
                ["net", "stop", svc_name],
                timeout=30, capture_output=True)
            await asyncio.sleep(2)
            subprocess.run(
                ["net", "start", svc_name],
                timeout=30, capture_output=True)
            log(f"Service {svc_name} restarted", self.config.log_file)
            await self._ack(client_key, cmd_id, True)
        except Exception as e:
            log(f"Service restart failed: {e}", self.config.log_file)
            await self._ack(client_key, cmd_id, False, str(e))

    async def _handle_config(self, client_key, cmd_id, payload):
        new_config = payload.get("config", None)
        if new_config is None:
            new_config = {k: v for k, v in payload.items() if k != "type"}
        apply_config(new_config, self.config)
        await self._ack(client_key, cmd_id, True)
        log(f"Config updated: {list(new_config.keys())}",
            self.config.log_file)

    async def _handle_diagnostic(self, client_key, cmd_id, payload):
        diag = await self.diagnostics.run_diagnostic()
        await self._ack(client_key, cmd_id, True, result=diag)
        log("Diagnostic complete", self.config.log_file)

    async def _handle_sms_discover(self, client_key, cmd_id, payload):
        result = await self.diagnostics.discover_api_methods()
        await self._ack(client_key, cmd_id, True, result=result)
        log(
            f"SMS discover complete: {len(result.get('all_methods', []))} methods",
            self.config.log_file,
        )

    async def _handle_sms_cleanup(self, client_key, cmd_id, payload):
        result = await self.diagnostics.try_delete_sms()
        await self._ack(client_key, cmd_id, True, result=result)
        log("SMS cleanup complete", self.config.log_file)

    async def _handle_usb_diag(self, client_key, cmd_id, payload):
        import subprocess
        result = {"success": False}
        try:
            r1 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_1BBB*' } "
                 "| Select-Object Status, Class, FriendlyName, InstanceId | Format-List"],
                capture_output=True, text=True, timeout=30)
            result["alcatel_devices"] = r1.stdout.strip()[-3000:]

            r2 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice | Where-Object { $_.InstanceId -like '*VID_1BBB*MI*' } "
                 "| Select-Object Status, Class, FriendlyName, InstanceId | Format-List"],
                capture_output=True, text=True, timeout=30)
            result["usb_interfaces"] = r2.stdout.strip()[-3000:]

            r3 = subprocess.run(
                ["powershell", "-Command",
                 r"Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_1BBB*\*' "
                 "-ErrorAction SilentlyContinue | Select-Object PSChildName, DeviceDesc, "
                 "Service, Driver, CompatibleIDs, HardwareID | Format-List"],
                capture_output=True, text=True, timeout=30)
            result["registry_usb"] = r3.stdout.strip()[-3000:]

            r4 = subprocess.run(
                ["powershell", "-Command",
                 r"Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' -Recurse "
                 "-ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*1BBB*' } "
                 "| ForEach-Object { $_.Name } | Select-Object -First 20"],
                capture_output=True, text=True, timeout=30)
            result["registry_children"] = r4.stdout.strip()[-2000:]

            r5 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class Modem -ErrorAction SilentlyContinue "
                 "| Select-Object Status, FriendlyName, InstanceId | Format-List"],
                capture_output=True, text=True, timeout=30)
            result["modem_class"] = r5.stdout.strip()[-1000:]

            r6 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue "
                 "| Select-Object Status, FriendlyName, InstanceId | Format-List"],
                capture_output=True, text=True, timeout=30)
            result["ports_class"] = r6.stdout.strip()[-1000:]

            result["success"] = True
        except Exception as e:
            result["error"] = str(e)
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log("USB diag complete", self.config.log_file)

    async def _handle_install_modem_driver(self, client_key, cmd_id, payload):
        import subprocess
        import tempfile
        result = {"success": False, "steps": []}
        try:
            drv_dir = Path(tempfile.gettempdir()) / "alcatel_driver"
            drv_dir.mkdir(exist_ok=True)
            inf_path = drv_dir / "alcatel_serial.inf"

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

            r1 = subprocess.run(
                ["pnputil", "/add-driver", str(inf_path), "/install"],
                capture_output=True, text=True, timeout=30)
            result["pnputil_stdout"] = r1.stdout.strip()[-2000:]
            result["pnputil_stderr"] = r1.stderr.strip()[-1000:]
            result["pnputil_rc"] = r1.returncode
            result["steps"].append(f"pnputil: rc={r1.returncode}")

            r2 = subprocess.run(
                ["pnputil", "/scan-devices"],
                capture_output=True, text=True, timeout=30)
            result["steps"].append(
                f"scan-devices: {r2.stdout.strip()[:200]}")

            if HAS_SERIAL:
                import serial.tools.list_ports as list_ports
                ports = list(list_ports.comports())
                result["com_ports_after"] = [
                    {"port": p.device, "desc": p.description,
                     "hwid": p.hwid}
                    for p in ports
                ]

            r3 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue "
                 "| Select-Object Status, FriendlyName, InstanceId | Format-List"],
                capture_output=True, text=True, timeout=15)
            result["ports_after"] = r3.stdout.strip()[-1000:]

            result["success"] = True
        except Exception as e:
            result["error"] = str(e)
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"Driver install: {'OK' if result.get('success') else 'FAIL'}",
            self.config.log_file,
        )

    async def _handle_usb_modeswitch(self, client_key, cmd_id, payload):
        import subprocess
        result = {"success": False, "steps": []}
        try:
            action = payload.get("action", "eject")

            if action == "eject":
                r1 = subprocess.run(
                    ["powershell", "-Command",
                     "Get-WmiObject Win32_DiskDrive | Where-Object "
                     "{ $_.PNPDeviceID -like '*VID_1BBB*' } | ForEach-Object {"
                     " $disk = $_; "
                     "$part = Get-WmiObject -Query \"ASSOCIATORS OF "
                     "{Win32_DiskDrive.DeviceID='$($disk.DeviceID)'} "
                     "WHERE AssocClass=Win32_DiskDriveToDiskPartition\"; "
                     "$vol = Get-WmiObject -Query \"ASSOCIATORS OF "
                     "{Win32_DiskPartition.DeviceID='$($part.DeviceID)'} "
                     "WHERE AssocClass=Win32_LogicalDiskToPartition\"; "
                     "[PSCustomObject]@{DiskID=$disk.DeviceID; PNP=$disk.PNPDeviceID; "
                     "Drive=$vol.DeviceID; Model=$disk.Model} } | Format-List"],
                    capture_output=True, text=True, timeout=30)
                result["modem_storage"] = r1.stdout.strip()[:1000]
                result["steps"].append("Found storage device")

                r2 = subprocess.run(
                    ["powershell", "-Command",
                     "$instanceId = (Get-PnpDevice | Where-Object "
                     "{ $_.InstanceId -like 'USB\\VID_1BBB&PID_0195\\*' "
                     "-and $_.Class -eq 'USB' }).InstanceId; "
                     "if ($instanceId) { "
                     "Disable-PnpDevice -InstanceId $instanceId -Confirm:$false "
                     "-ErrorAction SilentlyContinue; Start-Sleep -Seconds 3; "
                     "Enable-PnpDevice -InstanceId $instanceId -Confirm:$false "
                     "-ErrorAction SilentlyContinue; 'Device toggled' "
                     "} else { 'No USB composite device found' }"],
                    capture_output=True, text=True, timeout=30)
                result["toggle_result"] = r2.stdout.strip()[:500]
                result["toggle_stderr"] = r2.stderr.strip()[:500]
                result["steps"].append("Toggle attempted")

            elif action == "disable_enable":
                r1 = subprocess.run(
                    ["powershell", "-Command",
                     "$dev = Get-PnpDevice | Where-Object "
                     "{ $_.InstanceId -like 'USB\\VID_1BBB&PID_0195\\*' "
                     "-and $_.Class -eq 'USB' }; "
                     "if ($dev) { "
                     "Disable-PnpDevice -InstanceId $dev.InstanceId "
                     "-Confirm:$false; Start-Sleep -Seconds 5; "
                     "Enable-PnpDevice -InstanceId $dev.InstanceId "
                     "-Confirm:$false; 'Re-enabled: ' + $dev.InstanceId "
                     "} else { 'Device not found' }"],
                    capture_output=True, text=True, timeout=30)
                result["reenable"] = r1.stdout.strip()[:500]
                result["reenable_stderr"] = r1.stderr.strip()[:500]
                result["steps"].append("Disable/Enable done")

            await asyncio.sleep(5)
            r3 = subprocess.run(
                ["powershell", "-Command",
                 "Get-PnpDevice | Where-Object "
                 "{ $_.InstanceId -like '*VID_1BBB*' } | "
                 "Select-Object Status, Class, FriendlyName, InstanceId "
                 "| Format-List"],
                capture_output=True, text=True, timeout=15)
            result["devices_after"] = r3.stdout.strip()[:2000]

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
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(f"USB modeswitch: {result.get('steps')}", self.config.log_file)

    async def _handle_modem_backup(self, client_key, cmd_id, payload):
        result = await self.modem_control.backup_settings()
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"Modem backup: {len(result.get('backup', {}))} settings saved",
            self.config.log_file,
        )

    async def _handle_modem_reboot(self, client_key, cmd_id, payload):
        result = await self.modem_control.reboot()
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"Modem reboot: {'OK' if result.get('success') else 'FAIL'}",
            self.config.log_file,
        )

    async def _handle_modem_factory_reset(self, client_key, cmd_id, payload):
        result = await self.modem_control.factory_reset()
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"Factory reset: sms_before={result.get('sms_before')}, "
            f"sms_after={result.get('sms_after')}",
            self.config.log_file,
        )

    async def _handle_send_sms(self, client_key, cmd_id, payload):
        to = payload.get("to", "").strip()
        message = payload.get("message", "").strip()
        if not to or not message:
            await self._ack(
                client_key, cmd_id, False,
                result={"error": "Missing 'to' or 'message' in payload"})
            return
        if len(message) > 1600:
            await self._ack(
                client_key, cmd_id, False,
                result={"error": f"Message too long: {len(message)}/1600 chars"})
            return

        allowed, reason = self.metrics.check_rate_limit(
            self.config.sms_daily_limit, self.config.sms_hourly_limit)
        if not allowed:
            await self._ack(
                client_key, cmd_id, False,
                result={"error": f"Rate limited: {reason}"})
            return

        modem_override = payload.get("modem_type", self.config.modem_type)
        if modem_override == "serial":
            success, error = await self.sms_outgoing._send_serial(to, message)
        else:
            success, error = await self.sms_outgoing._send_direct(to, message)

        if success:
            self.metrics.record_sent()
            if self.metrics.sent_today % 10 == 0:
                asyncio.ensure_future(self.sms_storage.check_storage())

        await self._ack(
            client_key, cmd_id, success,
            result={
                "sent": success, "to": to, "error": error,
                "modem": modem_override,
                "msg_preview": message[:50],
            })
        log(
            f"SMS send command: to={to[:6]}***, modem={modem_override}, "
            f"success={success}",
            self.config.log_file,
        )

    async def _handle_clear_processed_sms(self, client_key, cmd_id, payload):
        old_count = len(self.dedup._ids)
        self.dedup.clear()
        await self._ack(
            client_key, cmd_id, True,
            result={"cleared": old_count,
                     "message": "Processed SMS IDs cleared"})
        log(f"Cleared {old_count} processed SMS IDs", self.config.log_file)

    async def _handle_modem_api_call(self, client_key, cmd_id, payload):
        import re as _re
        from eskimos.infrastructure.daemon.modem_control import _modem_login
        method_name = payload.get("method", "GetSystemInfo")
        params = payload.get("params", {})
        need_login = payload.get("login", True)
        base_url = (
            f"http://{self.config.modem_host}:{self.config.modem_port}")
        result = {"method": method_name, "success": False}
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
            ) as api_client:
                resp = await api_client.get(base_url)
                m = _re.search(
                    r'name="header-meta"\s+content="([^"]+)"', resp.text)
                if not m:
                    result["error"] = "Cannot extract token"
                else:
                    tok = m.group(1)
                    hdrs = {
                        "_TclRequestVerificationKey": tok,
                        "Referer": (
                            f"http://{self.config.modem_host}/index.html"),
                    }
                    if need_login:
                        lr = await api_client.post(
                            f"{base_url}/jrd/webapi",
                            json={
                                "jsonrpc": "2.0", "method": "Login",
                                "params": {"UserName": "admin",
                                           "Password": "admin"},
                                "id": "1",
                            },
                            headers=hdrs)
                        result["login"] = lr.text[:300]
                    resp = await api_client.post(
                        f"{base_url}/jrd/webapi",
                        json={
                            "jsonrpc": "2.0", "method": method_name,
                            "params": params, "id": "2",
                        },
                        headers=hdrs)
                    result["response"] = resp.text[:3000]
                    result["success"] = True
                    if need_login:
                        await api_client.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0", "method": "Logout",
                                  "params": {}, "id": "3"},
                            headers=hdrs)
        except Exception as e:
            result["error"] = str(e)
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"Modem API call {method_name}: "
            f"{'OK' if result.get('success') else 'FAIL'}",
            self.config.log_file,
        )

    async def _handle_sms_at_probe(self, client_key, cmd_id, payload):
        result = await self.at_helper.probe_at_ports()
        await self._ack(client_key, cmd_id, True, result=result)
        log(f"AT probe complete: port={result.get('at_port')}",
            self.config.log_file)

    async def _handle_sms_at_delete(self, client_key, cmd_id, payload):
        com_port = payload.get("com_port")
        result = await self.at_helper.delete_sms_via_at(com_port)
        await self._ack(
            client_key, cmd_id, result.get("success", False), result=result)
        log(
            f"AT delete: success={result.get('success')}, "
            f"deleted={result.get('deleted', 0)}",
            self.config.log_file,
        )

    async def _handle_pip_install(self, client_key, cmd_id, payload):
        import subprocess
        import sys
        packages = payload.get("packages", [])
        if isinstance(packages, str):
            packages = [packages]
        ALLOWED_PACKAGES = {"pyserial", "psutil", "httpx", "python-dotenv"}
        rejected = [p for p in packages if p not in ALLOWED_PACKAGES]
        if rejected:
            await self._ack(
                client_key, cmd_id, False,
                f"Packages not in whitelist: {rejected}. "
                f"Allowed: {sorted(ALLOWED_PACKAGES)}")
        else:
            try:
                result_out = subprocess.run(
                    [sys.executable, "-m", "pip", "install"] + packages,
                    capture_output=True, text=True, timeout=120)
                success = result_out.returncode == 0
                result = {
                    "packages": packages,
                    "success": success,
                    "python": sys.executable,
                    "stdout": (result_out.stdout[-2000:]
                               if result_out.stdout else ""),
                    "stderr": (result_out.stderr[-1000:]
                               if result_out.stderr else ""),
                }
                await self._ack(
                    client_key, cmd_id, success, result=result)
                log(
                    f"pip install {' '.join(packages)} "
                    f"(via {sys.executable}): "
                    f"{'OK' if success else 'FAIL'}",
                    self.config.log_file,
                )
            except Exception as e:
                await self._ack(client_key, cmd_id, False, str(e))
                log(f"pip install failed: {e}", self.config.log_file)


def apply_config(new_config: dict, config: DaemonConfig) -> None:
    """Apply new configuration values to .env file."""
    try:
        env_content = ""
        if config.config_file.exists():
            env_content = config.config_file.read_text()

        env_lines = {}
        for line in env_content.strip().split("\n"):
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                env_lines[key.strip()] = value.strip()

        for key, value in new_config.items():
            if key == key.upper():
                env_key = key
            else:
                env_key = f"ESKIMOS_{key.upper()}"
            env_lines[env_key] = str(value)

        new_content = "\n".join(f"{k}={v}" for k, v in env_lines.items())
        config.config_file.write_text(new_content)

        # Update live config values
        if "sms_daily_limit" in new_config:
            config.sms_daily_limit = int(new_config["sms_daily_limit"])
            log(f"Daily SMS limit updated: {config.sms_daily_limit}",
                config.log_file)
        if "sms_hourly_limit" in new_config:
            config.sms_hourly_limit = int(new_config["sms_hourly_limit"])
            log(f"Hourly SMS limit updated: {config.sms_hourly_limit}",
                config.log_file)
        if "MODEM_TYPE" in new_config:
            config.modem_type = new_config["MODEM_TYPE"]
            log(f"MODEM_TYPE changed to: {config.modem_type}",
                config.log_file)
        if "SERIAL_PORT" in new_config:
            config.serial_port = new_config["SERIAL_PORT"]
        if "SERIAL_BAUDRATE" in new_config:
            config.serial_baudrate = int(new_config["SERIAL_BAUDRATE"])

    except Exception as e:
        log(f"Config apply error: {e}", config.log_file)
