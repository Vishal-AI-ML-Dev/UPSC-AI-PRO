"""
Structured logging configuration.

Single place to configure log levels + format. Level priority:
  1. settings.log_level (env LOG_LEVEL) if valid
  2. DEBUG when settings.debug else INFO

Noisy third-party loggers (httpx, urllib3, chromadb, ...) are turned down so
our own app logs stay readable.
"""

import logging
import sys

from src.core.config import settings
from src.core.log_formatters import JsonFormatter, RequestIdFilter

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_NOISY = (
    "httpx",
    "httpcore",
    "urllib3",
    "chromadb",
    "langchain",
    "google",
    "google_genai",
    "asyncio",
    "langfuse",
    "opentelemetry",
)


def resolve_level() -> str:
    name = (settings.log_level or "").strip().upper()
    if name in _LEVELS:
        return name
    return "DEBUG" if settings.debug else "INFO"


def resolve_format() -> str:
    """Return 'json' or 'text'. Defaults to JSON in prod, text in debug."""
    fmt = (settings.log_format or "").strip().lower()
    if fmt in ("json", "text"):
        return fmt
    return "text" if settings.debug else "json"


def setup_logging() -> str:
    """Configure root logger. Returns the level name applied."""
    level_name = resolve_level()
    level = getattr(logging, level_name)

    root = logging.getLogger()
    root.setLevel(level)
    # Remove pre-existing handlers (basicConfig / uvicorn) to avoid dup lines
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = resolve_format()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | [%(request_id)s] | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)

    # Tone down noisy libraries
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging configured at level %s (format=%s)", level_name, fmt)
    return level_name
