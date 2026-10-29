"""Per-request correlation ID stored in a contextvar.

A single request-scoped ID that flows through logs (and Sentry) so one request's
whole lifecycle can be grepped by ID. Pure stdlib -- safe to import anywhere,
including the logging bootstrap, with no app-config dependency.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    """Return a fresh correlation ID (32-char hex)."""
    return uuid.uuid4().hex


def set_request_id(value: str | None) -> Token:
    """Set the current request ID; returns a token for reset_request_id()."""
    return _request_id.set(value)


def get_request_id() -> str | None:
    """Return the current request ID, or None outside a request."""
    return _request_id.get()


def reset_request_id(token: Token) -> None:
    """Restore the previous request-ID context (best-effort)."""
    try:
        _request_id.reset(token)
    except (ValueError, LookupError):
        pass
