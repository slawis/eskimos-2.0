"""Daemon process lifecycle - start, stop, status, signal handling."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log


_shutdown_requested = False


def graceful_shutdown(signum=None, frame=None) -> None:
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    _shutdown_requested = True
    log("Shutdown requested")


def is_shutdown_requested() -> bool:
    """Check if shutdown was requested."""
    return _shutdown_requested


def request_shutdown() -> None:
    """Programmatically request shutdown."""
    global _shutdown_requested
    _shutdown_requested = True


def setup_signal_handlers() -> None:
    """Setup signal handlers for graceful shutdown."""
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, graceful_shutdown)
        signal.signal(signal.SIGINT, graceful_shutdown)
    else:
        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGBREAK, graceful_shutdown)


def is_daemon_running(config: DaemonConfig) -> bool:
    """Check if daemon is already running."""
    if not config.pid_file.exists():
        return False

    try:
        pid = int(config.pid_file.read_text().strip())
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
        config.pid_file.unlink()

    return False


def save_pid(config: DaemonConfig) -> None:
    """Save current PID to file."""
    config.pid_file.write_text(str(os.getpid()))


def cleanup_pid(config: DaemonConfig) -> None:
    """Remove PID file."""
    if config.pid_file.exists():
        config.pid_file.unlink()


def start_daemon() -> None:
    """Start the daemon."""
    config = DaemonConfig.from_env()

    if is_daemon_running(config):
        log("Daemon already running", config.log_file)
        return

    setup_signal_handlers()

    try:
        from eskimos.infrastructure.daemon.loop import daemon_loop
        asyncio.run(daemon_loop())
    except KeyboardInterrupt:
        log("Interrupted by user", config.log_file)
    except Exception as e:
        log(f"Daemon error: {e}", config.log_file)
        import traceback
        traceback.print_exc()

    sys.exit(0)


def stop_daemon() -> None:
    """Stop the daemon."""
    config = DaemonConfig.from_env()

    if not config.pid_file.exists():
        log("Daemon not running", config.log_file)
        return

    try:
        pid = int(config.pid_file.read_text().strip())
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGTERM)
        log(f"Sent stop signal to PID {pid}", config.log_file)
    except Exception as e:
        log(f"Stop error: {e}", config.log_file)

    config.pid_file.unlink(missing_ok=True)


def daemon_status() -> None:
    """Print daemon status."""
    config = DaemonConfig.from_env()

    if is_daemon_running(config):
        pid = config.pid_file.read_text().strip()
        print(f"Daemon running (PID: {pid})")
    else:
        print("Daemon not running")


def main() -> None:
    """CLI entry point."""
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
