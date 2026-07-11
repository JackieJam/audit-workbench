"""Uvicorn access-log filters for local dev noise."""

from __future__ import annotations

import logging


class SkipForeignStreamAccessLog(logging.Filter):
    """Drop uvicorn access lines for mistaken /stream SSE probes (other local tools)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/stream" in msg:
            return False
        return True


def configure_access_logging() -> None:
    logging.getLogger("uvicorn.access").addFilter(SkipForeignStreamAccessLog())
