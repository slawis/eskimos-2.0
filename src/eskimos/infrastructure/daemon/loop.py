"""Daemon orchestrator - main loop with timer scheduling and DI wiring."""

from __future__ import annotations

import asyncio
import os
import time

from eskimos.infrastructure.daemon.at_commands import AtCommandHelper
from eskimos.infrastructure.daemon.command_handlers import CommandHandlerRegistry
from eskimos.infrastructure.daemon.commands import CommandPoller
from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.diagnostics import DiagnosticsService
from eskimos.infrastructure.daemon.heartbeat import HeartbeatService
from eskimos.infrastructure.daemon.identity import UptimeTracker, get_or_create_client_key
from eskimos.infrastructure.daemon.log import log, add_log_callback
from eskimos.infrastructure.daemon.modem_control import ModemControlService
from eskimos.infrastructure.daemon.modem_status import ModemStatusProvider
from eskimos.infrastructure.daemon.process import (
    is_shutdown_requested,
    request_shutdown,
)
from eskimos.infrastructure.daemon.sms_incoming import SmsDedup, SmsIncomingService
from eskimos.infrastructure.daemon.sms_metrics import SmsMetrics
from eskimos.infrastructure.daemon.sms_outgoing import SmsOutgoingService
from eskimos.infrastructure.daemon.sms_storage import SmsStorageMonitor
from eskimos.infrastructure.daemon.tunnel import WebSocketTunnel


class DaemonOrchestrator:
    """Wire all services and run the main daemon loop."""

    def __init__(self, config: DaemonConfig) -> None:
        self.config = config

        # Core
        self.metrics = SmsMetrics()
        self.uptime = UptimeTracker()
        self.at_helper = AtCommandHelper(config)

        # Modem
        self.modem_status = ModemStatusProvider(config, self.at_helper)
        self.dedup = SmsDedup(config.processed_sms_file, config.log_file)

        # Services
        self.heartbeat = HeartbeatService(
            config, self.modem_status, self.metrics, self.uptime)
        self.sms_outgoing = SmsOutgoingService(
            config, self.metrics, self.at_helper, self.modem_status)
        self.sms_incoming = SmsIncomingService(
            config, self.metrics, self.at_helper, self.modem_status,
            self.dedup)
        self.modem_control = ModemControlService(config, self.dedup)
        self.sms_storage = SmsStorageMonitor(
            config, self.metrics, self.dedup, self.modem_control)
        self.diagnostics = DiagnosticsService(
            config, self.modem_status, self.metrics,
            dedup_count_fn=lambda: len(self.dedup._ids))

        # Commands
        self.poller = CommandPoller(config)
        self.handlers = CommandHandlerRegistry(
            config=config,
            poller=self.poller,
            metrics=self.metrics,
            at_helper=self.at_helper,
            modem_control=self.modem_control,
            diagnostics=self.diagnostics,
            sms_outgoing=self.sms_outgoing,
            sms_storage=self.sms_storage,
            dedup=self.dedup,
            shutdown_fn=request_shutdown,
        )

        # WebSocket tunnel (initialized in run() after client_key is known)
        self.tunnel: WebSocketTunnel | None = None
        self._tunnel_task: asyncio.Task | None = None

    def _setup_tunnel(self, client_key: str) -> None:
        """Initialize WebSocket tunnel with handlers."""
        self.tunnel = WebSocketTunnel(self.config, client_key)

        # Register handlers for incoming WS messages
        self.tunnel.register_handler("command", self._on_ws_command)
        self.tunnel.register_handler("at_command", self._on_ws_at_command)

        # Stream logs through tunnel (rate-limited, re-entrancy safe)
        _sending_log = False
        _log_budget = 10
        _log_budget_reset = time.time()

        def _log_to_ws(msg: str) -> None:
            nonlocal _sending_log, _log_budget, _log_budget_reset
            if _sending_log:
                return
            now = time.time()
            if now - _log_budget_reset >= 1.0:
                _log_budget = 10
                _log_budget_reset = now
            if _log_budget <= 0:
                return
            if self.tunnel and self.tunnel.connected:
                _log_budget -= 1
                _sending_log = True
                asyncio.ensure_future(
                    self.tunnel.send("log", {"message": msg, "level": "info"})
                )
                _sending_log = False

        add_log_callback(_log_to_ws)

    async def _on_ws_command(self, msg: dict) -> None:
        """Handle command received via WebSocket."""
        payload = msg.get("payload", {})
        cmd_id = payload.get("id")
        cmd_type = payload.get("command_type")

        if not cmd_type:
            return

        log(f"WS command received: {cmd_type} (id={cmd_id})",
            self.config.log_file)

        # Delegate to existing command handler registry
        # handlers.execute() does its own HTTP ack internally
        # We wrap in try/except to send the real result back via WS
        try:
            await self.handlers.execute(self.tunnel.client_key, payload)
            success, error = True, None
        except Exception as e:
            success, error = False, str(e)

        if self.tunnel and self.tunnel.connected:
            await self.tunnel.send("command_result", {
                "id": cmd_id,
                "command_type": cmd_type,
                "success": success,
                "error": error,
            }, msg_id=msg.get("id"))

    async def _on_ws_at_command(self, msg: dict) -> None:
        """Handle AT command received via WebSocket.

        Opens serial port, sends AT command, returns response.
        Requires pyserial and a configured serial port.
        """
        payload = msg.get("payload", {})
        command = payload.get("command", "")
        timeout = payload.get("timeout", 5)
        com_port = payload.get("com_port", self.config.serial_port)

        if not command:
            return

        log(f"WS AT command: {command} (port={com_port})",
            self.config.log_file)

        def _run_at() -> tuple[bool, str]:
            try:
                import serial as serial_mod
            except ImportError:
                return False, "pyserial not installed"

            port = com_port
            if port == "auto":
                # Try to find modem port
                import serial.tools.list_ports as list_ports
                for p in list_ports.comports():
                    if "1BBB" in (p.hwid or "").upper() or "modem" in (p.description or "").lower():
                        port = p.device
                        break
                else:
                    return False, "No modem port found (auto-detect failed)"

            try:
                ser = serial_mod.Serial(
                    port, baudrate=self.config.serial_baudrate, timeout=timeout)
                try:
                    resp = AtCommandHelper.at_send_sync(ser, command, timeout)
                    return True, resp
                finally:
                    ser.close()
            except Exception as e:
                return False, str(e)

        success, response = await asyncio.get_running_loop().run_in_executor(
            None, _run_at)

        if self.tunnel and self.tunnel.connected:
            await self.tunnel.send("at_response", {
                "command": command,
                "response": response,
                "success": success,
            }, msg_id=msg.get("id"))

    async def _push_metrics(self) -> None:
        """Push current metrics through WS tunnel."""
        if not self.tunnel or not self.tunnel.connected:
            return

        modem = await self.modem_status.get_status()
        await self.tunnel.send("metrics", {
            "sms_sent_today": self.metrics.sent_today,
            "sms_sent_total": self.metrics.sent_total,
            "sms_received_today": self.metrics.received_today,
            "sms_received_total": self.metrics.received_total,
            "storage_used": self.metrics.storage_used,
            "storage_max": self.metrics.storage_max,
            "modem_status": modem,
            "uptime_seconds": self.uptime.get_uptime(),
        })

    async def run(self) -> None:
        """Main daemon loop."""
        client_key = get_or_create_client_key(self.config)
        log(f"Daemon started: {client_key[:12]}...", self.config.log_file)
        log(f"Central API: {self.config.central_api}", self.config.log_file)
        log(
            f"Heartbeat: {self.config.heartbeat_interval}s, "
            f"Auto-update: {self.config.auto_update_enabled}",
            self.config.log_file,
        )

        # Save PID
        self.config.pid_file.write_text(str(os.getpid()))

        # Start WebSocket tunnel if enabled
        if self.config.ws_enabled:
            self._setup_tunnel(client_key)
            self._tunnel_task = asyncio.create_task(self.tunnel.run())
            log("WS tunnel enabled", self.config.log_file)
        else:
            log("WS tunnel disabled (ESKIMOS_WS_ENABLED=false)",
                self.config.log_file)

        last_heartbeat = 0
        last_command_poll = 0
        last_update_check = 0
        last_sms_poll = 0
        last_incoming_poll = 0
        last_storage_check = 0
        last_metrics_push = 0

        log(
            f"SMS polling: {self.config.sms_poll_interval}s, "
            f"Incoming SMS: {self.config.incoming_sms_interval}s",
            self.config.log_file,
        )
        log(
            f"Rate limits: {self.config.sms_daily_limit}/day, "
            f"{self.config.sms_hourly_limit}/hour",
            self.config.log_file,
        )
        log(f"PHP API: {self.config.php_api}", self.config.log_file)
        log(
            f"Modem type: {self.config.modem_type}, "
            f"phone: {self.config.modem_phone}",
            self.config.log_file,
        )
        if self.config.modem_type == "serial":
            log(
                f"Serial: port={self.config.serial_port}, "
                f"baud={self.config.serial_baudrate}",
                self.config.log_file,
            )
        else:
            log(
                f"Modem: {self.config.modem_host}:{self.config.modem_port}",
                self.config.log_file,
            )

        try:
            while not is_shutdown_requested():
                now = time.time()

                # Heartbeat
                if now - last_heartbeat >= self.config.heartbeat_interval:
                    response = await self.heartbeat.send_heartbeat(client_key)
                    last_heartbeat = now

                    if (response.get("update_available")
                            and self.config.auto_update_enabled):
                        log("Update available via heartbeat response",
                            self.config.log_file)

                # Command polling (HTTP fallback - always active)
                if now - last_command_poll >= self.config.command_poll_interval:
                    commands = await self.poller.poll(client_key)
                    for cmd in commands:
                        await self.handlers.execute(client_key, cmd)
                    last_command_poll = now

                # SMS queue polling (send outgoing)
                if now - last_sms_poll >= self.config.sms_poll_interval:
                    try:
                        await self.sms_outgoing.poll_and_send()
                    except Exception as e:
                        log(f"SMS poll loop error: {e}", self.config.log_file)
                    last_sms_poll = now

                # Incoming SMS polling (receive)
                if now - last_incoming_poll >= self.config.incoming_sms_interval:
                    try:
                        await self.sms_incoming.poll_incoming()
                    except Exception as e:
                        log(f"Incoming SMS loop error: {e}",
                            self.config.log_file)
                    last_incoming_poll = now

                # SMS storage monitoring
                if now - last_storage_check >= self.config.sms_storage_check_interval:
                    try:
                        await self.sms_storage.check_storage()
                    except Exception as e:
                        log(f"SMS storage check error: {e}",
                            self.config.log_file)
                    last_storage_check = now

                # Periodic update check
                if (self.config.auto_update_enabled
                        and now - last_update_check
                        >= self.config.update_check_interval):
                    try:
                        from eskimos.infrastructure.updater import check_for_update
                        has_update, latest_version = await check_for_update()
                        if has_update:
                            log(
                                f"Auto-update available: {latest_version}",
                                self.config.log_file,
                            )
                    except Exception as e:
                        log(f"Update check error: {e}", self.config.log_file)
                    last_update_check = now

                # WS metrics push (every 60s when connected)
                if (self.tunnel and self.tunnel.connected
                        and now - last_metrics_push >= 60):
                    try:
                        await self._push_metrics()
                    except Exception as e:
                        log(f"Metrics push error: {e}", self.config.log_file)
                    last_metrics_push = now

                await asyncio.sleep(5)

        finally:
            # Stop tunnel
            if self.tunnel:
                self.tunnel.stop()
            if self._tunnel_task:
                self._tunnel_task.cancel()
                try:
                    await self._tunnel_task
                except asyncio.CancelledError:
                    pass

            if self.config.pid_file.exists():
                self.config.pid_file.unlink()
            log("Daemon stopped", self.config.log_file)


async def daemon_loop() -> None:
    """Entry point for the daemon loop. Creates orchestrator and runs."""
    config = DaemonConfig.from_env()
    orchestrator = DaemonOrchestrator(config)
    await orchestrator.run()
