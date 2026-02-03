"""Modem adapters for Eskimos 2.0.

This module implements the Adapter pattern for different GSM modems:
- PuppeteerAdapter: Legacy IK41VE1 modem via browser automation
- DinstarAdapter: Dinstar UC2000 via HTTP API
- MockAdapter: Testing adapter with in-memory queue
"""

from eskimos.adapters.modem.base import ModemAdapter, ModemError
from eskimos.adapters.modem.mock import MockModemAdapter

# Conditional imports (may not be available in all environments)
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
