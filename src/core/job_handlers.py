"""Background-job startup hook.

The thread-backed queue in ``job_queue`` executes closures directly, so there
is no per-type handler registry to populate. What startup DOES need is to clear
out jobs left queued/running by a previous (crashed or spun-down) process, so
clients polling those ids get a definitive ``error`` rather than a stuck
spinner. ``register_all`` is the hook the app lifespan already calls.
"""

import logging

logger = logging.getLogger(__name__)


def register_all() -> None:
    """Prepare the background-job subsystem on app startup."""
    from src.core.job_queue import reap_stale_jobs

    reaped = reap_stale_jobs()
    if reaped:
        logger.info("Reaped %d stale background job(s) from a previous run", reaped)
