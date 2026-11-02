# Phase 04 — Durable background jobs

Status: STUB  ·  Related audit sections: N, U#3

## Goal
Background processing (PDF upload, lecture, PYQ bank) survives restarts and multiple workers.

## Candidate scope
- Persist job inputs (or a reference to them) so work can be retried; bounded retries
  (`job_max_retries` exists in `src/core/config.py`, unused).
- Safe stale-job reaping (lease/heartbeat or owning process) instead of reaping everything on every boot
  (`src/core/job_handlers.py:register_all`, `src/core/job_queue.py:reap_stale_jobs`).
- Job timeout (`job_timeout_seconds`, unused) and executor shutdown in `lifespan` (`src/api/main.py`).
- Decide (ADR): stay thread-backed or introduce a broker. No new dependency without approval.

## Candidate acceptance criteria
- [ ] A second worker booting does not fail jobs running in the first.
- [ ] A transient failure is retried up to the limit; status visible via `GET /jobs/{id}`.
