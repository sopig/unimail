"""Structured logging for UniMail.

Uses standard library logging with:
- JSON formatter for production
- Colored terminal formatter for development
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import get_config

# Avoid conflicts with stdlib logging module name by using 'log.py'

_initialized = False


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production environments."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Extra fields
        for key in ("account_id", "action", "email", "connector", "message_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        return json.dumps(log_entry, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """Colored terminal formatter for development."""

    COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        timestamp = datetime.now().strftime("%H:%M:%S")
        msg = record.getMessage()
        name = record.name.replace("unimail.", "")
        formatted = f"{color}{timestamp} [{record.levelname:>7}]{self.RESET} {name}: {msg}"
        if record.exc_info and record.exc_info[0]:
            formatted += "\n" + self.formatException(record.exc_info)
        return formatted


def setup_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """Initialize the logging system.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to config value.
        fmt: Format type ('json' or 'console'). Defaults to config value.
    """
    global _initialized
    if _initialized:
        return

    config = get_config()
    log_level = level or config.logging.level
    log_format = fmt or config.logging.format

    # Get root unimail logger
    root_logger = logging.getLogger("unimail")
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler(sys.stderr)

    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ColoredFormatter())

    root_logger.addHandler(handler)

    # Don't propagate to root logger
    root_logger.propagate = False

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name.

    Usage:
        from ..log import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened", extra={"account_id": "abc"})
    """
    # Ensure logging is set up
    if not _initialized:
        setup_logging()

    # Prefix with 'unimail.' if not already
    if not name.startswith("unimail."):
        # Convert module paths like src.engine.mail_engine to unimail.engine.mail_engine
        parts = name.split(".")
        # Remove 'src' prefix if present
        if parts and parts[0] == "src":
            parts = parts[1:]
        name = "unimail." + ".".join(parts)

    return logging.getLogger(name)
