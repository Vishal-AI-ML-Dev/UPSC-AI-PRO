"""Background-job startup hook.

The thread-backed queue in ``job_queue`` executes closures directly, so there
is no per-type handler registry to populate. What startup DOES need is to fail
jobs left queued/running by a process that died (crashed or spun down), so
clients polling those ids get a definitive ``error`` rather than a stuck
spinner. Recovery is lease-based (Phase 04, ADR-013): only rows whose heartbeat
expired are failed, so a sibling worker's live jobs are never touched. Jobs are
not resumed (inputs are not persisted). ``register_all`` is the hook the app
lifespan already calls.
"""

import logging

logger = logging.getLogger(__name__)


def register_all() -> None:
    """Prepare the background-job subsystem on app startup."""
    from src.core.job_queue import reap_stale_jobs

    reaped = reap_stale_jobs()
    if reaped:
        logger.info("Recovered %d stale background job(s) (lease expired)", reaped)
