"""
Entry point for running eskimos as a module.

Usage:
    python -m eskimos --help
    python -m eskimos send 123456789 "Hello"
"""

from eskimos.cli.main import app

if __name__ == "__main__":
    app()
