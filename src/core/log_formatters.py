"""Logging helpers: request-ID injection + JSON formatter (pure stdlib).

Dependency-free (no app settings) so it is safe to import from the logging
bootstrap and easy to unit-test offline.
"""

from __future__ import annotations

import json
import logging

from src.core.request_context import get_request_id


class RequestIdFilter(logging.Filter):
    """Attach the current request ID to every record as ``record.request_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


# Standard LogRecord attributes we should not duplicate into the JSON "extra".
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "asctime",
    "request_id",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON object for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # Include any user-supplied structured "extra" fields.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False)
