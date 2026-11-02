"""Phase 04 — durable background jobs (spec `.claude/specs/phase-04-durable-jobs.md`).

Covers lease-based stale-job recovery that never touches a live job of another
process (S1/S2), guarded status transitions so late results never overwrite a
failed job (S3), transient-only bounded retries vs the job-level deadline
(S4/S5), the PYQ opt-out (S6) and graceful shutdown (S7), while keeping the
`GET /jobs/{id}` contract (S8).

Threads are real (``jobs_backend="thread"``) with a tiny heartbeat / lease so
the timing is exercised; every wait is an Event or a poll with a deadline.
"""

import io
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core import job_queue
from src.core.config import settings
from src.core.db import SessionLocal
from src.core.errors import GENERIC_ERROR, UserFacingError
from src.core.llm import LLMTimeoutError
from src.core.models import Job

API = "/api/v1"


def _pdf() -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


HEARTBEAT = 0.05
LEASE = 0.6


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def fast_jobs(client, monkeypatch):
    """Thread backend with a tiny heartbeat/lease; clean module state after."""
    monkeypatch.setattr(settings, "jobs_backend", "thread")
    monkeypatch.setattr(settings, "job_heartbeat_seconds", HEARTBEAT)
    monkeypatch.setattr(settings, "job_lease_seconds", LEASE)
    monkeypatch.setattr(settings, "job_retry_backoff_seconds", 0)
    monkeypatch.setattr(settings, "job_timeout_seconds", 60)
    monkeypatch.setattr(settings, "job_max_retries", 1)
    monkeypatch.setattr(settings, "job_workers", 2)
    assert wait_for(lambda: not job_queue.heartbeat_alive()), "heartbeat leaked into this test"
    job_queue.shutdown()
    yield
    job_queue.shutdown()
    # Drain BEFORE monkeypatch restores the 30 s production heartbeat: a thread
    # still alive at that point would sleep 30 s and starve the next test.
    assert wait_for(
        lambda: not job_queue.held_job_ids() and not job_queue.heartbeat_alive(), timeout=15
    ), "a job or the heartbeat thread outlived the test"


def wait_for(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def status_of(job_id):
    return job_queue.get_job(job_id)["status"]


def raw_row(job_id):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        return {"status": job.status, "error": job.error, "result": job.result}
    finally:
        db.close()


def insert_row(status, age_seconds, *, user_id="u-other", null_updated=False):
    """A job row owned by ANOTHER process (not tracked here)."""
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    stamp = None if null_updated else now - timedelta(seconds=age_seconds)
    db = SessionLocal()
    try:
        db.add(
            Job(
                id=job_id,
                user_id=user_id,
                job_type="foreign",
                status=status,
                payload="{}",
                created_at=now - timedelta(seconds=age_seconds),
                updated_at=stamp,
            )
        )
        db.commit()
    finally:
        db.close()
    if null_updated:  # the ORM default would fill updated_at; force NULL
        db = SessionLocal()
        try:
            db.query(Job).filter(Job.id == job_id).update(
                {Job.updated_at: None}, synchronize_session=False
            )
            db.commit()
        finally:
            db.close()
    return job_id


def raw_updated_at(job_id):
    db = SessionLocal()
    try:
        return db.get(Job, job_id).updated_at
    finally:
        db.close()


def sibling_reap():
    """What ANOTHER process's recovery does: it knows nothing about our held jobs,
    so only the heartbeat (a fresh ``updated_at``) can protect them."""
    from sqlalchemy import update

    db = SessionLocal()
    try:
        res = db.execute(
            update(Job)
            .where(Job.status.in_(["queued", "running"]), job_queue._stale_clause())
            .values(status="error", error=job_queue.RESTART_MESSAGE)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return res.rowcount
    finally:
        db.close()


def force_status(job_id, status, error):
    """Simulate a sibling process flipping the row (e.g. its reaper)."""
    db = SessionLocal()
    try:
        db.query(Job).filter(Job.id == job_id).update(
            {Job.status: status, Job.error: error}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


class Blocker:
    """Work that blocks until released, then returns or raises."""

    def __init__(self, result=None, exc=None):
        self.started = threading.Event()
        self.release = threading.Event()
        self.result = result if result is not None else {"ok": True}
        self.exc = exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.started.set()
        assert self.release.wait(10), "test never released the job"
        if self.exc is not None:
            raise self.exc
        return self.result


class Flaky:
    """Fails with ``excs[i]`` on call i, then returns a result."""

    def __init__(self, *excs):
        self.excs = list(excs)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.excs:
            raise self.excs.pop(0)
        return {"attempt": self.calls}


# --------------------------------------------------------------------------- #
# S1/S2 — live jobs survive recovery; stale jobs are recovered (AC1, AC2, AC7, AC11)
# --------------------------------------------------------------------------- #
def test_boot_recovery_does_not_fail_a_live_job_of_this_process():
    work = Blocker()
    job_id = job_queue.enqueue("live", work, user_id="u1")
    assert work.started.wait(5)

    from src.core.job_handlers import register_all

    register_all()  # a sibling worker booting
    assert job_queue.reap_stale_jobs() == 0
    assert status_of(job_id) == "running"

    work.release.set()
    assert wait_for(lambda: status_of(job_id) == "done")
    assert job_queue.get_job(job_id)["result"] == {"ok": True}


def test_recovery_never_touches_a_fresh_job_of_another_process():
    foreign_running = insert_row("running", age_seconds=0)
    foreign_queued = insert_row("queued", age_seconds=0)
    job_queue.reap_stale_jobs()
    assert raw_row(foreign_running)["status"] == "running"
    assert raw_row(foreign_queued)["status"] == "queued"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stale_jobs_are_recovered_by_reaper(status):
    stale = insert_row(status, age_seconds=LEASE * 10)
    fresh = insert_row(status, age_seconds=0)
    assert job_queue.reap_stale_jobs() >= 1
    row = raw_row(stale)
    assert row["status"] == "error"
    assert row["error"] == job_queue.RESTART_MESSAGE
    assert raw_row(fresh)["status"] == status


def test_null_updated_at_counts_as_stale():
    job_id = insert_row("running", age_seconds=0, null_updated=True)
    job_queue.reap_stale_jobs()
    assert raw_row(job_id)["status"] == "error"


def test_restart_message_unchanged():
    assert job_queue.RESTART_MESSAGE == "Server restarted before this job finished. Please retry."


@pytest.mark.parametrize("status", ["queued", "running"])
def test_get_job_reports_stale_job_as_error_without_reaper(client, make_user, status):
    user = make_user()
    stale = insert_row(status, age_seconds=LEASE * 10, user_id=user.id)
    r = client.get(f"{API}/jobs/{stale}", headers=user.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["error"] == job_queue.RESTART_MESSAGE
    assert raw_row(stale)["status"] == "error"  # persisted, not just reported


def test_get_job_leaves_fresh_foreign_job_alone(client, make_user):
    user = make_user()
    fresh = insert_row("running", age_seconds=0, user_id=user.id)
    r = client.get(f"{API}/jobs/{fresh}", headers=user.headers)
    assert r.json()["status"] == "running"


def test_periodic_recovery_reaps_dead_rows_but_not_live_ones():
    dead_queued = insert_row("queued", age_seconds=LEASE * 10)
    dead_running = insert_row("running", age_seconds=LEASE * 10)
    work = Blocker()
    live = job_queue.enqueue("live", work, user_id="u1")  # starts heartbeat thread
    assert work.started.wait(5)
    assert wait_for(lambda: raw_row(dead_queued)["status"] == "error")
    assert wait_for(lambda: raw_row(dead_running)["status"] == "error")
    assert status_of(live) == "running"
    work.release.set()
    assert wait_for(lambda: status_of(live) == "done")


def test_heartbeat_keeps_running_and_queued_jobs_alive_past_the_lease(monkeypatch):
    monkeypatch.setattr(settings, "job_workers", 1)
    first, second = Blocker(), Blocker()
    running_id = job_queue.enqueue("first", first, user_id="u1")
    assert first.started.wait(5)
    queued_id = job_queue.enqueue("second", second, user_id="u1")  # waits for the slot

    time.sleep(LEASE * 2.5)
    job_queue.reap_stale_jobs()  # what a sibling would run
    assert status_of(running_id) == "running"
    assert status_of(queued_id) == "queued"

    first.release.set()
    assert second.started.wait(5)
    second.release.set()
    assert wait_for(lambda: status_of(queued_id) == "done")
    assert status_of(running_id) == "done"


def test_sibling_recovery_spares_heartbeated_jobs_past_the_lease(monkeypatch):
    """S1/S2 across processes: no own-id exclusion, only the heartbeat protects."""
    monkeypatch.setattr(settings, "job_workers", 1)
    first, second = Blocker(), Blocker()
    running_id = job_queue.enqueue("first", first, user_id="u1")
    assert first.started.wait(5)
    queued_id = job_queue.enqueue("second", second, user_id="u1")
    dead = insert_row("running", age_seconds=LEASE * 10)

    time.sleep(LEASE * 2.5)
    for _ in range(5):  # repeated sibling sweeps over > 2 leases
        sibling_reap()
        time.sleep(LEASE / 5)
    assert raw_row(running_id)["status"] == "running"
    assert raw_row(queued_id)["status"] == "queued"
    assert raw_row(dead)["status"] == "error"

    first.release.set()
    assert second.started.wait(5)
    second.release.set()
    assert wait_for(lambda: status_of(queued_id) == "done")
    assert status_of(running_id) == "done"


def test_without_heartbeat_a_sibling_would_reap_the_job(monkeypatch):
    """Control for the test above: stop the heartbeat and the same sweep fails the
    job, proving the survival is due to the heartbeat, not test timing."""
    work = Blocker()
    job_id = job_queue.enqueue("orphan", work, user_id="u1")
    assert work.started.wait(5)
    monkeypatch.setattr(job_queue, "_tick", lambda: None)  # heartbeat stops refreshing
    time.sleep(LEASE * 1.5)
    assert sibling_reap() >= 1
    assert raw_row(job_id)["status"] == "error"
    work.release.set()
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    assert raw_row(job_id) == {
        "status": "error",
        "error": job_queue.RESTART_MESSAGE,
        "result": None,
    }  # the late result is discarded


def test_heartbeat_only_touches_tracked_jobs():
    """S1: a foreign row (another process's job) is never refreshed by our heartbeat."""
    foreign = insert_row("running", age_seconds=0)
    before = raw_updated_at(foreign)
    work = Blocker()
    own = job_queue.enqueue("live", work, user_id="u1")
    assert work.started.wait(5)
    own_before = raw_updated_at(own)
    time.sleep(HEARTBEAT * 6)  # several ticks
    assert foreign not in job_queue.held_job_ids()
    assert raw_updated_at(own) > own_before  # we DO refresh our own job ...
    assert raw_updated_at(foreign) == before  # ... and never someone else's
    assert raw_row(foreign)["status"] == "running"
    work.release.set()
    assert wait_for(lambda: status_of(own) == "done")


def test_stale_foreign_row_is_recovered_by_our_periodic_sweep():
    stale_foreign = insert_row("running", age_seconds=LEASE * 10)
    work = Blocker()
    job_queue.enqueue("live", work, user_id="u1")
    assert work.started.wait(5)
    assert wait_for(lambda: raw_row(stale_foreign)["status"] == "error")
    work.release.set()


def test_idle_process_stops_heartbeating():
    """No held jobs -> the heartbeat thread exits: no DB writes while idle."""
    job_id = job_queue.enqueue("short", lambda: {"ok": 1}, user_id="u1")
    assert wait_for(lambda: status_of(job_id) == "done")
    assert wait_for(lambda: not job_queue.heartbeat_alive())


def test_lease_is_clamped_above_two_heartbeats(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(settings, "job_heartbeat_seconds", 31)
    monkeypatch.setattr(settings, "job_lease_seconds", 41)
    with caplog.at_level(logging.WARNING, logger="src.core.job_queue"):
        assert job_queue._lease_seconds() == 93
    assert "JOB_LEASE_SECONDS" in caplog.text


def test_deadline_mark_survives_a_failed_db_write(monkeypatch):
    """If the deadline write fails once, the next tick retries it: the job ends
    with TIMEOUT_MESSAGE, not a misleading RESTART_MESSAGE from our own sweep."""
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.2)
    real = job_queue._transition
    failed = []

    def flaky_transition(job_id, from_statuses, *extra, **fields):
        if fields.get("error") == job_queue.TIMEOUT_MESSAGE and not failed:
            failed.append(job_id)
            raise RuntimeError("db blip")
        return real(job_id, from_statuses, *extra, **fields)

    monkeypatch.setattr(job_queue, "_transition", flaky_transition)
    work = Blocker()
    job_id = job_queue.enqueue("slow", work, user_id="u1")
    assert work.started.wait(5)
    assert wait_for(lambda: status_of(job_id) == "error")
    assert failed == [job_id]
    assert job_queue.get_job(job_id)["error"] == job_queue.TIMEOUT_MESSAGE
    work.release.set()


# --------------------------------------------------------------------------- #
# S3 — guarded transitions; late results (AC6, AC12)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("late", ["result", "transient", "permanent"])
def test_late_outcome_never_overwrites_a_failed_job(late):
    exc = {"result": None, "transient": LLMTimeoutError("t"), "permanent": RuntimeError("x")}[late]
    work = Blocker(exc=exc)
    job_id = job_queue.enqueue("late", work, user_id="u1")
    assert work.started.wait(5)
    force_status(job_id, "error", job_queue.RESTART_MESSAGE)  # reaped by a sibling

    work.release.set()
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    row = raw_row(job_id)
    assert row == {"status": "error", "error": job_queue.RESTART_MESSAGE, "result": None}
    assert work.calls == 1  # no retry for a job that is already failed


def test_queued_job_failed_elsewhere_is_never_started(monkeypatch):
    monkeypatch.setattr(settings, "job_workers", 1)
    first, second = Blocker(), Blocker()
    job_queue.enqueue("first", first, user_id="u1")
    assert first.started.wait(5)
    queued_id = job_queue.enqueue("second", second, user_id="u1")
    force_status(queued_id, "error", job_queue.RESTART_MESSAGE)
    first.release.set()
    assert wait_for(lambda: queued_id not in job_queue.held_job_ids())
    assert second.calls == 0
    assert raw_row(queued_id)["status"] == "error"


# --------------------------------------------------------------------------- #
# S4/S5 — transient-only retries vs the job deadline (AC3, AC4, AC6, AC13)
# --------------------------------------------------------------------------- #
@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(settings, "jobs_backend", "inline")


def test_transient_failure_is_retried_once_then_succeeds(inline):
    work = Flaky(LLMTimeoutError("slow model"))
    job_id = job_queue.enqueue("retry", work, user_id="u1")
    job = job_queue.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"attempt": 2}
    assert work.calls == 2


def test_persistent_transient_failure_stops_at_the_limit(inline):
    work = Flaky(*(ConnectionError("down") for _ in range(5)))
    job_id = job_queue.enqueue("retry", work, user_id="u1")
    job = job_queue.get_job(job_id)
    assert work.calls == 2  # 1 + job_max_retries
    assert job["status"] == "error"
    assert job["error"] == GENERIC_ERROR


def test_max_retries_setting_is_honoured(inline, monkeypatch):
    monkeypatch.setattr(settings, "job_max_retries", 2)
    work = Flaky(*(TimeoutError("t") for _ in range(5)))
    job_queue.enqueue("retry", work, user_id="u1")
    assert work.calls == 3


@pytest.mark.parametrize(
    "exc, detail",
    [
        (UserFacingError("Shown message"), "Shown message"),
        (ValueError("secret-path /srv"), GENERIC_ERROR),
        (RuntimeError("secret-path /srv"), GENERIC_ERROR),
        (KeyError("k"), GENERIC_ERROR),
    ],
)
def test_permanent_errors_are_never_retried(inline, exc, detail):
    work = Flaky(exc)
    job_id = job_queue.enqueue("permanent", work, user_id="u1")
    job = job_queue.get_job(job_id)
    assert work.calls == 1
    assert job["status"] == "error"
    assert job["error"] == detail


def test_retries_zero_disables_retry(inline):
    work = Flaky(LLMTimeoutError("slow"))
    job_id = job_queue.enqueue("pyq_bank_upload", work, user_id="u1", retries=0)
    assert work.calls == 1
    assert job_queue.get_job(job_id)["status"] == "error"


def test_retry_runs_in_the_thread_backend_too():
    work = Flaky(ConnectionError("blip"))
    job_id = job_queue.enqueue("retry", work, user_id="u1")
    assert wait_for(lambda: status_of(job_id) == "done")
    assert work.calls == 2


def test_job_deadline_fails_the_job_and_late_result_is_discarded(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.2)
    work = Blocker()
    job_id = job_queue.enqueue("slow", work, user_id="u1")
    assert work.started.wait(5)
    assert wait_for(lambda: status_of(job_id) == "error")
    assert job_queue.get_job(job_id)["error"] == job_queue.TIMEOUT_MESSAGE
    assert job_id not in job_queue.heartbeat_job_ids()  # no longer kept alive

    work.release.set()  # the thread finishes late with a result
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    row = raw_row(job_id)
    assert row["status"] == "error" and row["result"] is None
    assert row["error"] == job_queue.TIMEOUT_MESSAGE


def test_job_past_deadline_is_not_retried_even_on_transient_error(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.2)
    work = Blocker(exc=LLMTimeoutError("dependency timeout after the deadline"))
    job_id = job_queue.enqueue("slow", work, user_id="u1")
    assert work.started.wait(5)
    assert wait_for(lambda: status_of(job_id) == "error")
    work.release.set()
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    assert work.calls == 1
    assert job_queue.get_job(job_id)["error"] == job_queue.TIMEOUT_MESSAGE


def test_no_retry_when_backoff_would_cross_the_deadline(monkeypatch):
    """SF1/S4: a retry that could only start after the job deadline never starts."""
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.3)
    monkeypatch.setattr(settings, "job_retry_backoff_seconds", 1.0)
    work = Flaky(LLMTimeoutError("slow"))
    job_id = job_queue.enqueue("retry", work, user_id="u1")
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    assert work.calls == 1
    job = job_queue.get_job(job_id)
    assert job["status"] == "error"
    assert job["error"] == GENERIC_ERROR  # refused up front, before any backoff


def test_no_retry_if_job_failed_elsewhere_during_backoff(monkeypatch):
    """SF1/S3: status is re-checked AFTER the backoff, right before the retry."""
    monkeypatch.setattr(settings, "job_retry_backoff_seconds", 0.5)
    first_failure = threading.Event()
    calls = []

    def work():
        calls.append(1)
        if len(calls) == 1:
            first_failure.set()
            raise ConnectionError("blip")
        return {"retried": True}

    job_id = job_queue.enqueue("retry", work, user_id="u1")
    assert first_failure.wait(5)
    force_status(job_id, "error", job_queue.RESTART_MESSAGE)  # sibling reaped it
    assert wait_for(lambda: job_id not in job_queue.held_job_ids())
    assert len(calls) == 1
    assert raw_row(job_id) == {
        "status": "error",
        "error": job_queue.RESTART_MESSAGE,
        "result": None,
    }


def test_shutdown_interrupts_a_retry_backoff(monkeypatch):
    monkeypatch.setattr(settings, "job_retry_backoff_seconds", 30)
    first_failure = threading.Event()
    calls = []

    def work():
        calls.append(1)
        first_failure.set()
        raise ConnectionError("blip")

    job_id = job_queue.enqueue("retry", work, user_id="u1")
    assert first_failure.wait(5)
    time.sleep(0.05)  # inside the backoff
    t0 = time.monotonic()
    job_queue.shutdown()
    assert wait_for(lambda: job_id not in job_queue.held_job_ids(), timeout=3)
    assert time.monotonic() - t0 < 3  # did not sleep out the 30 s backoff
    assert len(calls) == 1
    assert job_queue.get_job(job_id)["error"] == job_queue.RESTART_MESSAGE


def test_max_retries_is_capped(inline, monkeypatch):
    """Review L2: an operator typo cannot multiply LLM cost unboundedly."""
    monkeypatch.setattr(settings, "job_max_retries", 50)
    work = Flaky(*(ConnectionError("down") for _ in range(100)))
    job_queue.enqueue("retry", work, user_id="u1")
    assert job_queue.MAX_RETRIES == 3
    assert work.calls == 1 + job_queue.MAX_RETRIES


def test_timeout_message_is_fixed():
    assert job_queue.TIMEOUT_MESSAGE == "This job took too long and was stopped. Please retry."


class _HttpStatusError(Exception):
    def __init__(self, code):
        super().__init__(f"status {code}")
        self.status_code = code


class _GoogleStyleError(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


@pytest.mark.parametrize(
    "exc, expected",
    [
        (LLMTimeoutError("t"), True),
        (TimeoutError("t"), True),
        (ConnectionError("c"), True),
        (_HttpStatusError(429), True),
        (_HttpStatusError(503), True),
        (_GoogleStyleError(504), True),
        (_HttpStatusError(400), False),
        (_HttpStatusError(404), False),
        (UserFacingError("bad pdf"), False),
        (ValueError("bad"), False),
        (RuntimeError("boom"), False),
        (KeyError("k"), False),
    ],
)
def test_is_transient_classification(exc, expected):
    assert job_queue._is_transient(exc) is expected


def test_is_transient_follows_the_cause_chain():
    try:
        try:
            raise ConnectionError("socket")
        except ConnectionError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert job_queue._is_transient(outer) is True


def test_permanent_error_raised_while_handling_a_timeout_is_not_transient():
    """Implicit context (no ``from``) does not make a permanent error transient."""
    try:
        try:
            raise TimeoutError("t")
        except TimeoutError:
            raise ValueError("bad data")  # noqa: B904 - deliberately implicit
    except ValueError as exc:
        assert exc.__context__ is not None
        assert job_queue._is_transient(exc) is False


def test_user_facing_error_is_never_transient_even_with_transient_cause():
    err = UserFacingError("Could not read the PDF")
    err.__cause__ = ConnectionError("x")
    assert job_queue._is_transient(err) is False


@pytest.mark.parametrize("code", [500, 503])
def test_http_exception_is_never_transient(code):
    """An HTTPException is our own error mapping, not a dependency failure (S5)."""
    from fastapi import HTTPException

    assert job_queue._is_transient(HTTPException(status_code=code, detail="x")) is False
    try:
        try:
            raise ConnectionError("x")
        except ConnectionError as inner:
            raise HTTPException(status_code=code, detail="x") from inner
    except HTTPException as outer:
        assert job_queue._is_transient(outer) is False


def test_is_transient_sqlalchemy_and_httpx():
    import httpx
    from sqlalchemy.exc import OperationalError

    assert job_queue._is_transient(OperationalError("select 1", {}, Exception("gone")))
    assert job_queue._is_transient(httpx.ConnectError("refused"))
    assert job_queue._is_transient(httpx.ReadTimeout("slow"))


# --------------------------------------------------------------------------- #
# S6 — PYQ bank is never auto-retried (AC5)
# --------------------------------------------------------------------------- #
def test_pyq_bank_upload_enqueues_without_retries(client, make_user, monkeypatch):
    import src.api.routes.pyq as pyq_routes

    captured = {}

    def fake_enqueue(job_type, work, **kw):
        captured["type"] = job_type
        captured.update(kw)
        return "job-test"

    monkeypatch.setattr(pyq_routes, "enqueue", fake_enqueue)
    user = make_user()
    r = client.post(
        f"{API}/pyq/bank/upload",
        files={"file": ("paper.pdf", _pdf(), "application/pdf")},
        headers=user.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": "job-test", "status": "queued"}
    assert captured["type"] == "pyq_bank_upload"
    assert captured["retries"] == 0
    assert captured["user_id"] == user.id


def _capture_enqueue(monkeypatch, module):
    import importlib

    mod = importlib.import_module(module)
    captured = {}

    def fake_enqueue(jt, work, **kw):
        captured["type"] = jt
        captured.update(kw)
        return "job-test"

    monkeypatch.setattr(mod, "enqueue", fake_enqueue)
    return captured


def test_upload_job_is_not_retried(client, make_user, monkeypatch):
    """Amendment 1: a retry could accept a partly-written vector collection."""
    captured = _capture_enqueue(monkeypatch, "src.api.routes.upload")
    user = make_user()
    r = client.post(
        f"{API}/upload/process",
        files={"file": ("notes.pdf", _pdf(), "application/pdf")},
        headers=user.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": "job-test", "status": "queued"}
    assert captured["type"] == "upload_process"
    assert captured["retries"] == 0


@pytest.mark.parametrize(
    "path, body, job_type",
    [
        (
            "/lecture/process",
            {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            "lecture_process",
        ),
        ("/lecture/process-text", {"transcript": "Federalism. " * 20}, "lecture_process_text"),
    ],
)
def test_lecture_jobs_use_default_retry_policy(
    client, make_user, monkeypatch, path, body, job_type
):
    captured = _capture_enqueue(monkeypatch, "src.api.routes.lecture")
    user = make_user()
    r = client.post(f"{API}{path}", json=body, headers=user.headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": "job-test", "status": "queued"}
    assert captured["type"] == job_type
    assert captured.get("retries") is None  # -> settings.job_max_retries
    assert captured["user_id"] == user.id


# --------------------------------------------------------------------------- #
# S7 — graceful shutdown (AC8)
# --------------------------------------------------------------------------- #
def test_shutdown_fails_unstarted_jobs_and_keeps_running_ones_alive(monkeypatch):
    """S7 + review SF2: after shutdown the in-flight job keeps heartbeating, so a
    sibling does not fail it while it is still finishing; then the heartbeat stops."""
    monkeypatch.setattr(settings, "job_workers", 1)
    first, second = Blocker(), Blocker()
    running_id = job_queue.enqueue("first", first, user_id="u1")
    assert first.started.wait(5)
    queued_id = job_queue.enqueue("second", second, user_id="u1")
    assert job_queue.heartbeat_alive()

    job_queue.shutdown()
    row = raw_row(queued_id)
    assert row["status"] == "error"
    assert row["error"] == job_queue.RESTART_MESSAGE

    time.sleep(LEASE * 2)
    sibling_reap()
    assert raw_row(running_id)["status"] == "running"  # still protected

    first.release.set()  # the in-flight job still finishes cleanly
    assert wait_for(lambda: status_of(running_id) == "done")
    assert second.calls == 0
    assert wait_for(lambda: not job_queue.heartbeat_alive())


def test_shutdown_keeps_tracking_a_job_that_just_started(monkeypatch):
    """Review SF2 race: a job that moved to running but has not recorded its
    start yet must stay tracked (heartbeat + deadline) through shutdown."""
    real = job_queue._transition
    reached, proceed = threading.Event(), threading.Event()

    def gated(job_id, from_statuses, *extra, **fields):
        ok = real(job_id, from_statuses, *extra, **fields)
        if fields.get("status") == "running":
            reached.set()
            assert proceed.wait(5)
        return ok

    monkeypatch.setattr(job_queue, "_transition", gated)
    work = Blocker()
    job_id = job_queue.enqueue("racy", work, user_id="u1")
    assert reached.wait(5)
    job_queue.shutdown()
    proceed.set()
    assert work.started.wait(5)
    assert job_id in job_queue.held_job_ids()
    assert job_id in job_queue.heartbeat_job_ids()
    work.release.set()
    assert wait_for(lambda: status_of(job_id) == "done")


def test_failed_submit_fails_the_row_and_stops_tracking(monkeypatch):
    """Review N1: e.g. 'cannot schedule new futures after interpreter shutdown'."""

    class BrokenPool:
        def submit(self, *a, **k):
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    monkeypatch.setattr(job_queue, "_executor", BrokenPool())
    before = job_queue.held_job_ids()
    with pytest.raises(RuntimeError):
        job_queue.enqueue("doomed", lambda: {}, user_id="u-submit")
    assert job_queue.held_job_ids() == before
    db = SessionLocal()
    try:
        row = db.query(Job).filter(Job.user_id == "u-submit").one()
        assert row.status == "error"
        assert row.error == job_queue.RESTART_MESSAGE
    finally:
        db.close()
    monkeypatch.setattr(job_queue, "_executor", None)


def test_shutdown_is_idempotent_and_enqueue_works_afterwards():
    job_queue.shutdown()
    job_queue.shutdown()
    work = Blocker()
    job_id = job_queue.enqueue("after", work, user_id="u1")
    assert work.started.wait(5)
    assert job_queue.heartbeat_alive()
    work.release.set()
    assert wait_for(lambda: status_of(job_id) == "done")


def test_shutdown_after_inline_only_use_is_safe(inline):
    job_queue.enqueue("inline", lambda: {"ok": 1}, user_id="u1")
    job_queue.shutdown()
    assert wait_for(lambda: not job_queue.heartbeat_alive())


def test_lifespan_shutdown_calls_job_queue_shutdown(monkeypatch):
    """Run the real lifespan with startup side effects stubbed; shutdown must call
    job_queue.shutdown() once, and a failure there must not abort shutdown."""
    import asyncio
    import types

    import src.api.main as main

    calls = []
    for name in (
        "check_email_config",
        "init_db",
        "purge_expired_tokens",
        "register_all",
        "ensure_vector_storage",
    ):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(main, "build_app", lambda: object())
    monkeypatch.setattr(main, "close_memory", lambda: calls.append("close_memory"))
    monkeypatch.setattr(main.observability, "flush", lambda: calls.append("flush"))

    def boom():
        calls.append("jobs")
        raise RuntimeError("db down")

    async def run():
        async with main.lifespan(types.SimpleNamespace(state=types.SimpleNamespace())):
            assert calls == []

    with monkeypatch.context() as m:  # undo before the autouse fixture's teardown
        m.setattr(main.job_queue, "shutdown", boom)
        asyncio.run(run())
    assert calls == ["jobs", "flush", "close_memory"]


# --------------------------------------------------------------------------- #
# S8 — API contract and ownership (AC9, AC10)
# --------------------------------------------------------------------------- #
def test_job_status_contract_and_ownership(client, make_user):
    owner, other = make_user(), make_user()
    job_id = job_queue.enqueue("contract", lambda: {"x": 1}, user_id=owner.id)
    assert wait_for(lambda: status_of(job_id) == "done")

    r = client.get(f"{API}/jobs/{job_id}", headers=owner.headers)
    assert r.status_code == 200
    assert set(r.json()) == {
        "job_id",
        "type",
        "status",
        "result",
        "error",
        "created_at",
        "updated_at",
    }
    assert r.json()["result"] == {"x": 1}

    assert client.get(f"{API}/jobs/{job_id}", headers=other.headers).status_code == 404
    assert client.get(f"{API}/jobs/{job_id}").status_code == 401


def test_users_poll_never_writes_an_ownerless_job(client, make_user):
    """Security review L1: recovery-on-read only for the caller's own job."""
    user = make_user()
    stale = insert_row("running", age_seconds=LEASE * 10, user_id=None)
    client.get(f"{API}/jobs/{stale}", headers=user.headers)
    assert raw_row(stale)["status"] == "running"


def test_other_users_stale_job_is_404_and_not_reaped_by_their_poll(client, make_user):
    owner, other = make_user(), make_user()
    stale = insert_row("running", age_seconds=LEASE * 10, user_id=owner.id)
    assert client.get(f"{API}/jobs/{stale}", headers=other.headers).status_code == 404
    assert raw_row(stale)["status"] == "running"  # ownership check comes first
