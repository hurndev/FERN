from __future__ import annotations

import logging
from typing import ClassVar


class ColorFormatter(logging.Formatter):
    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    DIM = "\033[2m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        timestamp = self.formatTime(record, "%H:%M:%S")
        return (
            f"{self.DIM}{timestamp}{self.RESET} "
            f"{color}{record.levelname:<7}{self.RESET} "
            f"{self.DIM}{record.name}{self.RESET}: {record.getMessage()}"
        )


def configure_logging(*, level: str, no_color: bool = False) -> None:
    """Configure concise application logs while suppressing WebSocket frame noise."""

    handler = logging.StreamHandler()
    if no_color:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
            )
        )
    else:
        handler.setFormatter(ColorFormatter())
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)
    logging.getLogger("websockets").setLevel(logging.WARNING)


__all__ = ["ColorFormatter", "configure_logging"]
