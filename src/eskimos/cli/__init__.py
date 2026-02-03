"""Eskimos CLI - Command Line Interface.

Usage:
    eskimos --help
    eskimos send <phone> <message>
    eskimos modem status
    eskimos modem test
"""

from eskimos.cli.main import app

__all__ = ["app"]
