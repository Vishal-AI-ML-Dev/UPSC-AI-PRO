"""Thread-backed, DB-persisted background job queue (no external broker).

Why this shape (free-tier friendly, zero extra infra):
  * Job *status + result* live in the app database, so a client polling a job
    id keeps working across web-process restarts / spin-down, and a finished
    result is never silently lost.
  * Execution runs in an in-process thread pool (``settings.job_workers``) to
    keep long PDF / lecture work off the request path.

Durability model (Phase 04, ADR-013) - durable job STATE and stale-worker
recovery, NOT resumable execution:
  * Inputs (PDF bytes, URL, transcript) live only in the closure and are never
    persisted. If the process that holds a job dies, the job is NOT resumed: it
    ends in ``error`` with ``RESTART_MESSAGE`` and the user retries.
  * Lease: while a process holds jobs (queued in its executor or running), a
    daemon heartbeat thread refreshes ``jobs.updated_at`` for exactly THOSE ids
    every ``job_heartbeat_seconds``. A queued/running row with no refresh for
    ``job_lease_seconds`` (or a NULL ``updated_at``) is stale. Recovery (at boot,
    on every heartbeat tick, and lazily in ``get_job`` for the owner) only fails
    stale rows, so a live job of a sibling worker is never touched. The
    heartbeat thread exits when the process holds no jobs (no idle DB writes).
    The lease compares the wall clocks of the processes involved: workers on
    different hosts must keep their clocks within ``lease - 2*heartbeat``.
  * Every status change is a conditional UPDATE (``WHERE status IN ...``): a
    late result from a job that was already reaped or timed out is discarded.
  * Retries: only explicitly transient errors (``_is_transient``), at most
    ``retries`` (<= ``MAX_RETRIES``) extra attempts, sequentially in the SAME
    worker thread - so no retry can start while an earlier attempt is still
    running. A retry is only started if it can begin before the job deadline and
    the row is still ``running`` after the backoff. Work that is not idempotent
    opts out with ``retries=0`` (PYQ bank append, PDF upload indexing).
  * Deadline: a job still running after ``job_timeout_seconds`` is failed with
    ``TIMEOUT_MESSAGE`` and never retried. Python threads cannot be killed, so
    the thread runs on (holding a worker slot) and its outcome is discarded.
  * ``shutdown()`` (app lifespan) cancels jobs that have not started and fails
    their rows, and interrupts retry backoffs. Jobs already running keep their
    heartbeat until they finish, or expire by lease if the process is killed.
    Python's exit hook joins worker threads, so a hung job delays process exit
    until the platform kills it (then its row expires by lease).
  * Known limit (backlog): a queued job has no deadline of its own. If every
    worker slot is held by a hung job, queued jobs stay ``queued`` (and
    heartbeated) until a slot frees or the process exits.

settings.jobs_backend: "thread"/"auto" use the pool; "inline" runs work
synchronously (handy for tests / debugging; same retry rules, no heartbeat).
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx
from sqlalchemy import or_, update
from sqlalchemy.exc import DisconnectionError, OperationalError
from starlette.exceptions import HTTPException

from src.core.config import settings
from src.core.db import SessionLocal
from src.core.errors import GENERIC_ERROR, UserFacingError
from src.core.llm import describe_exception
from src.core.models import Job

logger = logging.getLogger(__name__)

RESTART_MESSAGE = "Server restarted before this job finished. Please retry."
TIMEOUT_MESSAGE = "This job took too long and was stopped. Please retry."
MAX_RETRIES = 3  # hard cap on extra attempts, whatever JOB_MAX_RETRIES says

_ACTIVE = ("queued", "running")
_HEARTBEAT_THREAD_NAME = "jobheartbeat"

# Explicit allow-list of transient failures (S5). Anything else - including a
# UserFacingError (bad input) or a processing bug - is permanent and never retried.
_TRANSIENT_TYPES = (
    TimeoutError,  # incl. src.core.llm.LLMTimeoutError (dependency timeout)
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    OperationalError,
    DisconnectionError,
)
_TRANSIENT_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass
class _Held:
    """A job this process holds (queued in its executor or running)."""

    job_type: str
    future: Optional[Future] = None
    started_at: Optional[float] = None  # time.monotonic() of the first attempt
    expired: bool = False  # hit the job deadline: no heartbeat, no retry
    closing: threading.Event = field(default_factory=threading.Event)


_lock = threading.RLock()
_held: dict[str, _Held] = {}
_executor: Optional[ThreadPoolExecutor] = None
_closing = threading.Event()  # set by shutdown(); replaced for the next generation
_hb_thread: Optional[threading.Thread] = None
_clamp_warned: set = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _inline() -> bool:
    return (settings.jobs_backend or "auto").lower() == "inline"


def _heartbeat_seconds() -> float:
    return max(float(settings.job_heartbeat_seconds), 0.01)


def _lease_seconds() -> float:
    """The stale threshold; always > 2 heartbeats so one missed beat is harmless."""
    heartbeat = _heartbeat_seconds()
    lease = float(settings.job_lease_seconds)
    if lease <= 2 * heartbeat:
        key = (lease, heartbeat)
        if key not in _clamp_warned:
            _clamp_warned.add(key)
            logger.warning(
                "JOB_LEASE_SECONDS (%s) must exceed 2x JOB_HEARTBEAT_SECONDS (%s); using %s",
                lease,
                heartbeat,
                3 * heartbeat,
            )
        lease = 3 * heartbeat
    return lease


def _stale_clause():
    cutoff = _now() - timedelta(seconds=_lease_seconds())
    return or_(Job.updated_at.is_(None), Job.updated_at < cutoff)


# --------------------------------------------------------------------------- #
# Introspection (tests, benchmark)
# --------------------------------------------------------------------------- #
def held_job_ids() -> set:
    """Ids of every job this process holds (queued or running here)."""
    with _lock:
        return set(_held)


def heartbeat_job_ids() -> set:
    """Ids this process keeps alive (held and not past the job deadline)."""
    with _lock:
        return {job_id for job_id, h in _held.items() if not h.expired}


def heartbeat_alive() -> bool:
    return any(t.name == _HEARTBEAT_THREAD_NAME and t.is_alive() for t in threading.enumerate())


# --------------------------------------------------------------------------- #
# Status transitions (always conditional - S3)
# --------------------------------------------------------------------------- #
def _transition(job_id: str, from_statuses: tuple, *extra_where, **fields) -> bool:
    """Set ``fields`` only if the job is still in ``from_statuses``; True if it was."""
    fields["updated_at"] = _now()
    db = SessionLocal()
    try:
        res = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(from_statuses), *extra_where)
            .values(**fields)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return res.rowcount == 1
    finally:
        db.close()


def _status(job_id: str) -> Optional[str]:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        return job.status if job is not None else None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Retry classification (S4/S5)
# --------------------------------------------------------------------------- #
def _is_transient(exc: BaseException) -> bool:
    """True only for explicitly classified transient dependency failures.

    Follows EXPLICIT chaining only (``raise X from Y`` -> ``__cause__``): a
    permanent error raised while handling a timeout (implicit ``__context__``)
    stays permanent. A ``UserFacingError`` (bad input) or an ``HTTPException``
    (our own error mapping, whatever its status code) is never transient, nor is
    anything raised from it. The job-level deadline is not an exception and is
    handled separately (never retried).
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (UserFacingError, HTTPException)):
            return False
        if isinstance(exc, _TRANSIENT_TYPES):
            return True
        code = getattr(exc, "status_code", None)
        if code is None:
            code = getattr(exc, "code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            if code in _TRANSIENT_HTTP_STATUS:
                return True
        exc = exc.__cause__
    return False


# --------------------------------------------------------------------------- #
# Executor + heartbeat
# --------------------------------------------------------------------------- #
def _ensure_started() -> ThreadPoolExecutor:
    """Create the pool and heartbeat thread if needed. Caller holds ``_lock``."""
    global _executor, _hb_thread
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, int(settings.job_workers)), thread_name_prefix="jobworker"
        )
    if _hb_thread is None or not _hb_thread.is_alive():
        _hb_thread = threading.Thread(
            target=_heartbeat_loop, name=_HEARTBEAT_THREAD_NAME, daemon=True
        )
        _hb_thread.start()
    return _executor


def _heartbeat_loop() -> None:
    """Tick while this process holds jobs; exit when it holds none.

    The emptiness check and ``_hb_thread = None`` happen under ``_lock``, and
    ``enqueue`` registers a job and (re)starts the thread under the same lock,
    so a newly held job always has a live heartbeat.
    """
    global _hb_thread
    while True:
        time.sleep(_heartbeat_seconds())
        with _lock:
            if not _held:
                if _hb_thread is threading.current_thread():
                    _hb_thread = None
                return
        try:
            _tick()
        except Exception as e:  # noqa: BLE001 - fail-open: never kill the heartbeat
            logger.warning("Job heartbeat tick failed: %s", describe_exception(e))


def _tick() -> None:
    """One heartbeat: enforce deadlines, refresh held jobs, recover stale rows."""
    timeout = float(settings.job_timeout_seconds)
    now = time.monotonic()
    due = []
    with _lock:
        for job_id, h in _held.items():
            if h.started_at is not None and not h.expired and now - h.started_at > timeout:
                due.append((job_id, h))
    for job_id, held in due:
        try:
            if _transition(job_id, ("running",), status="error", error=TIMEOUT_MESSAGE):
                logger.warning(
                    "Job %s (%s) exceeded its deadline; marked failed", job_id, held.job_type
                )
            held.expired = True  # only once the outcome is known (written or terminal)
        except Exception as e:  # noqa: BLE001 - retried on the next tick
            logger.warning("Job %s deadline write failed: %s", job_id, describe_exception(e))

    ids = list(heartbeat_job_ids())
    if ids:
        db = SessionLocal()
        try:
            db.execute(
                update(Job)
                .where(Job.id.in_(ids), Job.status.in_(_ACTIVE))
                .values(updated_at=_now())
                .execution_options(synchronize_session=False)
            )
            db.commit()
        finally:
            db.close()
    reap_stale_jobs()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def enqueue(
    job_type: str,
    work: Callable[[], dict],
    *,
    user_id: Optional[str] = None,
    payload: Optional[dict] = None,
    retries: Optional[int] = None,
) -> str:
    """Create a job row and run ``work`` in the background; return the job id.

    ``work`` is a zero-arg callable returning a JSON-serialisable dict (its
    inputs are captured in the closure and are NOT persisted - no resume after a
    restart). ``retries`` = extra attempts after a transient failure; ``None``
    uses ``settings.job_max_retries``; pass 0 for non-idempotent work. Capped at
    ``MAX_RETRIES``.
    """
    retries = settings.job_max_retries if retries is None else retries
    retries = min(MAX_RETRIES, max(0, int(retries)))
    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.add(
            Job(
                id=job_id,
                user_id=user_id,
                job_type=job_type,
                status="queued",
                payload=json.dumps(payload or {}),
                created_at=_now(),
                updated_at=_now(),
            )
        )
        db.commit()
    finally:
        db.close()

    if _inline():
        _run(job_id, job_type, work, retries, threading.Event())
        return job_id

    with _lock:
        held = _Held(job_type=job_type, closing=_closing)
        _held[job_id] = held
        try:
            pool = _ensure_started()
            held.future = pool.submit(_run, job_id, job_type, work, retries, _closing)
        except Exception:
            # e.g. "cannot schedule new futures after interpreter shutdown": never
            # leave a held (heartbeated) row that nothing will run.
            _held.pop(job_id, None)
            _transition(job_id, ("queued",), status="error", error=RESTART_MESSAGE)
            raise
    return job_id


def _may_retry(job_id: str, started: float, delay: float) -> bool:
    """A retry must start before the deadline, on a job that is still ours (S3/S4)."""
    with _lock:
        held = _held.get(job_id)
        if held is not None and held.expired:
            return False
    if time.monotonic() - started + delay >= float(settings.job_timeout_seconds):
        return False
    return _status(job_id) == "running"


def _fail(job_id: str, job_type: str, message: str) -> None:
    if not _transition(job_id, ("running",), status="error", error=message):
        logger.warning("Job %s (%s): late failure discarded", job_id, job_type)


def _run(
    job_id: str,
    job_type: str,
    work: Callable[[], dict],
    retries: int,
    closing: threading.Event,
) -> None:
    try:
        if not _transition(job_id, ("queued",), status="running"):
            logger.warning("Job %s (%s) was no longer queued; not started", job_id, job_type)
            return
        started = time.monotonic()
        with _lock:
            held = _held.get(job_id)
            if held is not None:
                held.started_at = started

        attempt = 0
        while True:
            attempt += 1
            try:
                result = json.dumps(work())
            except Exception as e:  # noqa: BLE001 - captured into the job record
                delay = float(settings.job_retry_backoff_seconds) * attempt
                if attempt <= retries and _is_transient(e) and _may_retry(job_id, started, delay):
                    logger.warning(
                        "Job %s (%s) attempt %d failed transiently; retrying: %s",
                        job_id,
                        job_type,
                        attempt,
                        describe_exception(e),
                    )
                    if closing.wait(delay):  # shutdown during the backoff
                        _fail(job_id, job_type, RESTART_MESSAGE)
                        return
                    if _may_retry(job_id, started, 0):  # re-check right before retrying
                        continue
                    logger.warning(
                        "Job %s (%s): retry abandoned (deadline or failed elsewhere)",
                        job_id,
                        job_type,
                    )
                # Only a UserFacingError's fixed message may reach the client;
                # anything else (incl. a plain ValueError) could carry paths or
                # provider text.
                logger.error(
                    "Background job %s (%s) failed on attempt %d: %s",
                    job_id,
                    job_type,
                    attempt,
                    describe_exception(e),
                )
                message = str(e)[:2000] if isinstance(e, UserFacingError) else GENERIC_ERROR
                _fail(job_id, job_type, message)
                return
            if not _transition(job_id, ("running",), status="done", result=result, error=None):
                logger.warning("Job %s (%s): late result discarded", job_id, job_type)
            return
    finally:
        with _lock:
            _held.pop(job_id, None)


def get_job(job_id: str, *, user_id: Optional[str] = None) -> Optional[dict]:
    """Return a job as a plain dict, or None if missing / not owned by user.

    A queued/running job whose lease expired (its process died) is reported -
    and persisted - as ``error`` with ``RESTART_MESSAGE``. Only the job's owner
    (or an internal caller passing no ``user_id``) triggers that write.
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return None
        if user_id is not None and job.user_id is not None and job.user_id != user_id:
            return None
        may_recover = user_id is None or job.user_id == user_id
        if (
            may_recover
            and job.status in _ACTIVE
            and job_id not in heartbeat_job_ids()
            and _looks_stale(job)
        ):
            res = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(_ACTIVE), _stale_clause())
                .values(status="error", error=RESTART_MESSAGE, updated_at=_now())
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if res.rowcount:
                logger.info("Recovered stale job %s (%s) on read", job_id, job.job_type)
            db.refresh(job)
        return {
            "job_id": job.id,
            "type": job.job_type,
            "status": job.status,
            "result": json.loads(job.result) if job.result else None,
            "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
    finally:
        db.close()


def _looks_stale(job: Job) -> bool:
    """Cheap pre-check so a fresh job costs no UPDATE (SQLite returns naive UTC)."""
    stamp = job.updated_at
    if stamp is None:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp < _now() - timedelta(seconds=_lease_seconds())


def reap_stale_jobs() -> int:
    """Fail queued/running jobs whose lease expired (their process died).

    Never touches a job with a fresh heartbeat (a live sibling worker) or one
    this process keeps alive. Returns the number of jobs recovered.
    """
    own = list(heartbeat_job_ids())
    where = [Job.status.in_(_ACTIVE), _stale_clause()]
    if own:
        where.append(Job.id.not_in(own))
    db = SessionLocal()
    try:
        res = db.execute(
            update(Job)
            .where(*where)
            .values(status="error", error=RESTART_MESSAGE, updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return res.rowcount or 0
    finally:
        db.close()


def shutdown() -> None:
    """Cancel jobs that never started (and fail their rows); interrupt retry
    backoffs. Idempotent.

    Running jobs are not waited for and keep their heartbeat until they finish,
    so a sibling worker does not fail them; if the process is killed they expire
    by lease. A later ``enqueue`` builds a fresh pool.
    """
    global _executor, _closing
    with _lock:
        pool, closing = _executor, _closing
        _executor = None
        _closing = threading.Event()
    closing.set()
    if pool is None:
        return
    pool.shutdown(wait=False, cancel_futures=True)
    with _lock:
        cancelled = [
            job_id
            for job_id, h in _held.items()
            if h.closing is closing and h.future is not None and h.future.cancelled()
        ]
        for job_id in cancelled:
            _held.pop(job_id, None)  # will never run: stop tracking it
    failed = 0
    for job_id in cancelled:
        if _transition(job_id, ("queued",), status="error", error=RESTART_MESSAGE):
            failed += 1
    if failed:
        logger.info("Job queue shutdown: %d unstarted job(s) marked failed", failed)
