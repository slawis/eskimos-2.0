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

# Public API - backward compatible with old monolithic daemon.py
from eskimos.infrastructure.daemon.config import DaemonConfig  # noqa: F401
from eskimos.infrastructure.daemon.log import log  # noqa: F401
from eskimos.infrastructure.daemon.identity import (  # noqa: F401
    get_or_create_client_key,
    get_system_info,
    UptimeTracker,
)
from eskimos.infrastructure.daemon.process import (  # noqa: F401
    main,
    start_daemon,
    stop_daemon,
    daemon_status,
    setup_signal_handlers,
)
from eskimos.infrastructure.daemon.loop import daemon_loop  # noqa: F401
from eskimos.infrastructure.daemon.tunnel import WebSocketTunnel  # noqa: F401
