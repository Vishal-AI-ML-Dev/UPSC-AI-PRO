"""Thread-backed, DB-persisted background job queue (no external broker).

Why this shape (free-tier friendly, zero extra infra):
  * Job *status + result* live in the app database, so a client polling a job
    id keeps working across web-process restarts / spin-down, and a finished
    result is never silently lost.
  * Execution runs in an in-process thread pool - enough on a single free-tier
    process to keep long PDF / lecture work off the request path so no HTTP
    request blocks for more than a moment.
  * If the process dies mid-job, ``reap_stale_jobs`` (called on boot) flips any
    leftover queued/running row to ``error`` so clients get a definitive answer
    instead of a stuck spinner.

settings.jobs_backend: "thread"/"auto" use the pool; "inline" runs work
synchronously (handy for tests / debugging).
"""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional

from src.core.config import settings
from src.core.db import SessionLocal
from src.core.models import Job

logger = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jobworker")
    return _executor


def _inline() -> bool:
    return (settings.jobs_backend or "auto").lower() == "inline"


def enqueue(
    job_type: str,
    work: Callable[[], dict],
    *,
    user_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> str:
    """Create a job row and run ``work`` in the background; return the job id.

    ``work`` is a zero-arg callable returning a JSON-serialisable dict (the
    inputs it needs - e.g. uploaded bytes - are captured in the closure).
    """
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
        _run(job_id, work)
    else:
        _pool().submit(_run, job_id, work)
    return job_id


def _set(job_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = _now()
        db.commit()
    finally:
        db.close()


def _run(job_id: str, work: Callable[[], dict]) -> None:
    _set(job_id, status="running")
    try:
        result = work()
        _set(job_id, status="done", result=json.dumps(result), error=None)
    except Exception as e:  # noqa: BLE001 - captured into the job record
        logger.exception("Background job %s failed", job_id)
        _set(job_id, status="error", error=str(e)[:2000])


def get_job(job_id: str, *, user_id: Optional[str] = None) -> Optional[dict]:
    """Return a job as a plain dict, or None if missing / not owned by user."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return None
        if user_id is not None and job.user_id is not None and job.user_id != user_id:
            return None
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


def reap_stale_jobs() -> int:
    """Flip leftover queued/running jobs (from a crashed run) to error."""
    db = SessionLocal()
    try:
        rows = db.query(Job).filter(Job.status.in_(["queued", "running"])).all()
        for job in rows:
            job.status = "error"
            job.error = "Server restarted before this job finished. Please retry."
            job.updated_at = _now()
        if rows:
            db.commit()
        return len(rows)
    finally:
        db.close()
