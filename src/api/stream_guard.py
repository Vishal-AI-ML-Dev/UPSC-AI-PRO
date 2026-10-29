"""Session guard for streaming responses (single active session).

``get_current_user`` validates the session once, when a request starts. A stream
can then run for a long time, so ``guard_stream`` keeps re-checking that the
token's session is still the account's current one:

* before the first chunk is delivered;
* before delivering a chunk if the last successful check is older than the
  interval (``settings.stream_session_check_seconds``);
* while waiting for the next upstream chunk, every interval, so a silent or
  blocked upstream does not delay detection.

On a superseded/ended session, or if the check itself fails (fail closed), it
emits one plain-text notice in the existing ``text/plain`` stream format and
stops. Nothing from upstream is delivered after a failed check.

Upstream sources are the existing synchronous generators. Each ``next()`` runs in
a worker thread; a thread blocked inside an LLM/provider call cannot be
interrupted, so the guard stops delivering immediately but closes the generator
only once that in-flight step returns. Provider work (and billing) for that step
may therefore continue after the stream has been stopped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable, Iterator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from src.api.deps import SESSION_MESSAGES
from src.core.config import settings
from src.core.db import SessionLocal
from src.core.models import User

logger = logging.getLogger(__name__)

STOP_NOTICES = {
    "session_superseded": "\n\n⚠️ " + SESSION_MESSAGES["session_superseded"],
    "session_ended": "\n\n⚠️ " + SESSION_MESSAGES["session_ended"],
    "unavailable": "\n\n⚠️ This answer was stopped because your session could "
    "not be verified. Please try again.",
}

_DONE = object()
_cleanups: set[asyncio.Future] = set()  # keep background closers referenced


def check_session(user_id: str, sid: str | None) -> str:
    """Return "ok", "session_superseded", "session_ended" or "unavailable".

    Uses its own short-lived database session (the request's session may be
    closed or busy while a response streams).
    """
    if not sid:
        return "session_ended"
    try:
        with SessionLocal() as db:
            active = db.scalar(select(User.active_session_id).where(User.id == user_id))
    except SQLAlchemyError as exc:
        logger.warning("Stream session check failed (%s); stopping output", type(exc).__name__)
        return "unavailable"
    if active == sid:
        return "ok"
    return "session_ended" if active is None else "session_superseded"


def _next(it: Iterator[str]):
    try:
        return next(it)
    except StopIteration:
        return _DONE


def _close_later(it: Iterator[str], pending: asyncio.Future | None) -> None:
    """Close the upstream generator without blocking the response.

    If a ``next()`` is still running in a worker thread, the generator cannot be
    closed until it returns, so closing is chained onto that call.
    """
    close = getattr(it, "close", None)
    if close is None:
        return
    loop = asyncio.get_running_loop()

    def _schedule(prev: asyncio.Future | None = None):
        if prev is not None and not prev.cancelled():
            prev.exception()  # retrieve: an abandoned step's error must not go unhandled
        fut = loop.run_in_executor(None, close)
        _cleanups.add(fut)
        fut.add_done_callback(_cleanups.discard)

    if pending is not None and not pending.done():
        _cleanups.add(pending)
        pending.add_done_callback(_cleanups.discard)
        pending.add_done_callback(_schedule)
    else:
        _schedule()


async def guard_stream(
    source: Iterable[str], user: dict, *, interval: float | None = None
) -> AsyncIterator[str]:
    """Wrap a streaming body so it stops when the user's session is revoked."""
    every = settings.stream_session_check_seconds if interval is None else interval
    user_id, sid = user["id"], user.get("sid")
    loop = asyncio.get_running_loop()
    it = iter(source)
    pending: asyncio.Future | None = None
    finished = False

    async def verify() -> str:
        return await run_in_threadpool(check_session, user_id, sid)

    try:
        status = await verify()
        if status != "ok":
            yield STOP_NOTICES[status]
            return
        last_ok = loop.time()
        while True:
            if pending is None:
                pending = asyncio.ensure_future(run_in_threadpool(_next, it))
            timeout = max(0.0, last_ok + every - loop.time())
            done, _ = await asyncio.wait({pending}, timeout=timeout)
            if not done:  # upstream silent: re-check while it keeps working
                status = await verify()
                if status != "ok":
                    yield STOP_NOTICES[status]
                    return
                last_ok = loop.time()
                continue
            chunk, pending = pending.result(), None
            if chunk is _DONE:
                finished = True
                return
            if loop.time() - last_ok >= every:  # check is due before delivering
                status = await verify()
                if status != "ok":
                    yield STOP_NOTICES[status]
                    return
                last_ok = loop.time()
            yield chunk
    finally:
        if not finished:
            _close_later(it, pending)
