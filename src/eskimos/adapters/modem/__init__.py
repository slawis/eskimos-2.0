"""Modem adapters for Eskimos 2.0.

This module implements the Adapter pattern for different GSM modems:
- JsonRpcAdapter: TCL/Alcatel IK41 via JSON-RPC API (RECOMMENDED - 95% RAM savings)
- PuppeteerAdapter: Legacy IK41VE1 modem via browser automation
- DinstarAdapter: Dinstar UC2000 via HTTP API (future)
- MockAdapter: Testing adapter with in-memory queue
"""

from eskimos.adapters.modem.base import ModemAdapter, ModemError
from eskimos.adapters.modem.mock import MockModemAdapter

# JSON-RPC adapter - recommended for IK41 (no Chrome needed!)
try:
    from eskimos.adapters.modem.jsonrpc import JsonRpcModemAdapter, JsonRpcConfig
except ImportError:
    JsonRpcModemAdapter = None  # type: ignore
    JsonRpcConfig = None  # type: ignore

# Puppeteer adapter - legacy, requires Chrome
try:
    from eskimos.adapters.modem.puppeteer import PuppeteerModemAdapter
except ImportError:
    PuppeteerModemAdapter = None  # type: ignore

__all__ = [
    "ModemAdapter",
    "ModemError",
    "MockModemAdapter",
    "JsonRpcModemAdapter",
    "JsonRpcConfig",
    "PuppeteerModemAdapter",
]
