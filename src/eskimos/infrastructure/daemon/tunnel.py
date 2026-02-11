"""WebSocket tunnel - persistent connection to central server for real-time communication."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Callable, Coroutine

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log

# Lazy websockets import
websockets = None
HAS_WEBSOCKETS = False
try:
    import websockets as _ws
    websockets = _ws
    HAS_WEBSOCKETS = True
except ImportError:
    pass


class WebSocketTunnel:
    """Persistent WebSocket connection to central server with auto-reconnect.

    Acts as a bidirectional tunnel: receives commands/requests from server,
    sends logs/metrics/responses back. Auto-reconnects on disconnect.
    """

    def __init__(self, config: DaemonConfig, client_key: str) -> None:
        self.config = config
        self.client_key = client_key
        self._ws = None
        self._connected = False
        self._handlers: dict[str, Callable] = {}
        self._should_run = True

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    def register_handler(
        self, msg_type: str, callback: Callable[..., Coroutine]
    ) -> None:
        """Register async handler for incoming message type."""
        self._handlers[msg_type] = callback

    def _build_ws_url(self) -> str:
        """Build WebSocket URL from config."""
        if self.config.ws_url:
            url = self.config.ws_url
        else:
            # Derive from central_api: https://app.ninjabot.pl/api/eskimos → wss://app.ninjabot.pl/ws/eskimos
            api = self.config.central_api
            base = api.split("/api/eskimos")[0]
            url = base.replace("https://", "wss://").replace("http://", "ws://")
            url += "/ws/eskimos"

        # Add auth params
        url += f"?role=daemon&client_key={self.client_key}&api_key={self.config.api_key}"
        return url

    async def send(
        self,
        msg_type: str,
        payload: dict[str, Any],
        msg_id: str | None = None,
    ) -> bool:
        """Send message through tunnel. Returns True if sent."""
        if not self.connected:
            return False

        envelope = {
            "type": msg_type,
            "id": msg_id or str(uuid.uuid4()),
            "client_key": self.client_key,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": payload,
        }

        try:
            await self._ws.send(json.dumps(envelope))
            return True
        except Exception:
            self._connected = False
            return False

    async def _handle_message(self, raw: str) -> None:
        """Parse and dispatch incoming message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")
        if not msg_type:
            return

        handler = self._handlers.get(msg_type)
        if handler:
            try:
                await handler(msg)
            except Exception as e:
                log(f"WS handler error ({msg_type}): {e}", self.config.log_file)

    async def run(self) -> None:
        """Main loop: connect, listen, auto-reconnect."""
        if not HAS_WEBSOCKETS:
            log("WS tunnel disabled: websockets not installed",
                self.config.log_file)
            return

        if not self.config.ws_enabled:
            return

        url = self._build_ws_url()
        log_url = url.split("?")[0]  # Don't log api_key
        log(f"WS tunnel starting: {log_url}", self.config.log_file)

        while self._should_run:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=self.config.ws_ping_interval,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    log("WS tunnel connected", self.config.log_file)

                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    except websockets.ConnectionClosed:
                        pass

                    self._connected = False
                    self._ws = None
                    log("WS tunnel disconnected", self.config.log_file)

            except Exception as e:
                self._connected = False
                self._ws = None
                log(f"WS tunnel error: {e}", self.config.log_file)

            if self._should_run:
                await asyncio.sleep(self.config.ws_reconnect_interval)

    def stop(self) -> None:
        """Request tunnel shutdown."""
        self._should_run = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())
