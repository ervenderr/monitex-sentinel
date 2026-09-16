"""Entrypoint:  python -m backend.main"""

from __future__ import annotations

import logging

import uvicorn

from backend.api import create_app
from backend.config import load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",  # our own logs carry the signal
    )


if __name__ == "__main__":
    main()
