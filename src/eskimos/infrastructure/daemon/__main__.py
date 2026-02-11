"""CLI entry point: python -m eskimos.infrastructure.daemon [start|stop|status]"""

from eskimos.infrastructure.daemon.process import main

if __name__ == "__main__":
    main()
