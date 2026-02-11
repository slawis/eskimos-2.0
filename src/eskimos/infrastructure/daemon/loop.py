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
from eskimos.infrastructure.daemon.log import log
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


class DaemonOrchestrator:
    """Wire all services and run the main daemon loop."""

    def __init__(self, config: DaemonConfig) -> None:
        self.config = config

        # Core
        self.metrics = SmsMetrics()
        self.uptime = UptimeTracker()
        self.at_helper = AtCommandHelper()

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

        last_heartbeat = 0
        last_command_poll = 0
        last_update_check = 0
        last_sms_poll = 0
        last_incoming_poll = 0
        last_storage_check = 0

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

                # Command polling
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

                await asyncio.sleep(5)

        finally:
            if self.config.pid_file.exists():
                self.config.pid_file.unlink()
            log("Daemon stopped", self.config.log_file)


async def daemon_loop() -> None:
    """Entry point for the daemon loop. Creates orchestrator and runs."""
    config = DaemonConfig.from_env()
    orchestrator = DaemonOrchestrator(config)
    await orchestrator.run()
