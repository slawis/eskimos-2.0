"""Modem adapters for Eskimos 2.0.

This module implements the Adapter pattern for different GSM modems:
- PuppeteerAdapter: Legacy IK41VE1 modem via browser automation
- MockAdapter: Testing adapter with in-memory queue

Note: Direct JSON-RPC calls for IK41 are implemented inline in
infrastructure/daemon.py (_modem_send_sms_direct, _modem_receive_sms_direct).
"""

from eskimos.adapters.modem.base import ModemAdapter, ModemError
from eskimos.adapters.modem.mock import MockModemAdapter

# Puppeteer adapter - legacy, requires Chrome
try:
    from eskimos.adapters.modem.puppeteer import PuppeteerModemAdapter
except ImportError:
    PuppeteerModemAdapter = None  # type: ignore

__all__ = [
    "ModemAdapter",
    "ModemError",
    "MockModemAdapter",
    "PuppeteerModemAdapter",
]
