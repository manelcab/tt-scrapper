from __future__ import annotations

import logging
import os

from rich.logging import RichHandler

# Module loggers are created at import time, before load_dotenv() has run, so we
# start from the shell environment (or INFO) and let set_level() re-apply the
# value from .env once Config has read it.
_level = os.getenv("LOG_LEVEL", "INFO").upper()
_loggers: list[logging.Logger] = []


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%H:%M:%S]"))
        logger.addHandler(handler)
        logger.propagate = False
        _loggers.append(logger)

    logger.setLevel(_level)
    return logger


def set_level(level: str) -> None:
    """Apply `level` to every logger already created, and to future ones."""
    global _level
    _level = level.upper()
    for logger in _loggers:
        logger.setLevel(_level)
