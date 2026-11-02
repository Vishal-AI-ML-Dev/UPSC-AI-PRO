# Plan — Phase 04 Durable background jobs

Spec: `.claude/specs/phase-04-durable-jobs.md`  ·  Status: DONE (2026-11-02; Amendments 1–2 approved)

## Files to touch (exhaustive — anything else needs a plan amendment)
| File | Change |
|---|---|
| `src/core/job_queue.py` | Main change: heartbeat thread + in-process registry of held job ids; lease-aware `reap_stale_jobs()`; lazy reap in `get_job`; conditional (status-guarded) transitions; `_is_transient()`; retry loop with backoff; timeout watchdog (in heartbeat tick); `enqueue(..., retries=None)`; `shutdown()`; pool size from `job_workers`; module docstring updated. |
| `src/core/job_handlers.py` | Docstring/log wording: boot reap is now lease-based (call unchanged). |
| `src/api/main.py` | `lifespan` shutdown: call `job_queue.shutdown()` (guarded with try/except + log, before `close_memory()`). |
| `src/core/config.py` | Add `job_workers=2`, `job_heartbeat_seconds=30`, `job_lease_seconds=120`, `job_retry_backoff_seconds=5`; update comments on `job_max_retries` / `job_timeout_seconds` (now read; thread backend). |
| `src/api/routes/upload.py` | **Amendment 1 (user, 2026-11-02, after review SHOULD-FIX 3):** `enqueue("upload_process", …, retries=0)`. The only transient error that reaches the upload job comes from `create_vector_store` (notes failures are already swallowed), and a retry would load a partly written collection and report `done`. Non-retryable until the index build is retry-safe (backlog). |
| `src/api/routes/pyq.py` | `enqueue("pyq_bank_upload", _work, user_id=user_id, retries=0)` + comment (non-idempotent append). |
| `tests/test_job_queue_durability.py` | New: AC1–AC10 + `_is_transient` unit cases (spec §8). |
| `.env.example` | **Edited by the user, not Claude** (Claude is denied access). Q5 = yes: four commented optional placeholders (`JOB_WORKERS=2`, `JOB_HEARTBEAT_SECONDS=30`, `JOB_LEASE_SECONDS=120`, `JOB_RETRY_BACKOFF_SECONDS=5`). |
| `scripts/bench_jobs.py` | New offline job-reliability benchmark (AC14): temp SQLite, simulated live + dead workers; reports false reaps, recovered stale jobs, time-to-definitive-error, heartbeat tick cost. Same style as `scripts/bench_request_path.py`. |
| `docs/ENGINEERING_METRICS.md` | Add a "Background jobs" section with the benchmark command and measured numbers (canonical metrics page, ADR-012). |
| `docs/DECISIONS.md` | ADR-013: stay thread-backed; heartbeat lease on `updated_at`; no input persistence/resume; retry only transient + idempotent jobs; timeout = definitive answer, not thread kill. |
| `docs/PROJECT_STATE.md` | Phase table, risk #3, workflow rows (upload/lecture/PYQ), backlog additions (PYQ append idempotency, partial vector store on failed create, per-user job cap, `rq` settings still unused), handoff. |
| `.claude/specs/phase-04-durable-jobs.md`, this plan, `.claude/specs/README.md` | Status updates. |
| `docs/phase-reports/phase-04-report.md` | New, at close. |

Not touched: `src/core/models.py`, `migrations/`, `src/api/routes/{jobs,lecture}.py`, `frontend/`, Docker/Render/CI.

## Steps
1. **Config** — add the 4 settings with comments (`src/core/config.py`).
2. **Guarded transitions** — replace `_set` uses with `_transition(job_id, from_statuses, **fields) -> bool` doing
   `UPDATE jobs SET … , updated_at=now WHERE id=:id AND status IN :from` (SQLAlchemy `update()`; returns
   `rowcount == 1`). `running` from `{queued, running}`; `done`/`error` from `{running}`.
3. **Registry + heartbeat** — module state guarded by a lock: `_held: dict[job_id, _Held(started_at|None,
   timed_out)]`. `enqueue` registers the id before `submit`; `_run` sets `started_at`; `finally` unregisters.
   A daemon `threading.Thread` (started lazily with the pool; `threading.Event` stop flag) every
   `job_heartbeat_seconds`: (a) batched `UPDATE … SET updated_at=now WHERE id IN held AND status IN (queued,running)`;
   (b) timeout check — held jobs with `started_at` older than `job_timeout_seconds` → `_transition(running→error,
   TIMEOUT_MESSAGE)`, mark `timed_out`, drop from heartbeat set; (c) `reap_stale_jobs()`. Each tick wrapped in
   try/except + `describe_exception` log (fail-open, never kills the thread). Clamp lease to > 2× heartbeat with a
   warning.
4. **Lease reaper** — `reap_stale_jobs()`: one `UPDATE … SET status='error', error=RESTART_MESSAGE, updated_at=now
   WHERE status IN (queued,running) AND (updated_at IS NULL OR updated_at < now - lease)`; return rowcount.
5. **Lazy reap** — in `get_job`, after the ownership check, if status non-terminal and stale →
   `_transition({queued,running}→error, RESTART_MESSAGE)` then re-read; response keys unchanged.
6. **Retry** — `_is_transient(exc)` per spec R5 (walk `__cause__`/`__context__` like `is_llm_timeout`; never for
   `UserFacingError`). `_run(job_id, work, retries)`: loop attempts `0..retries`; on transient failure with attempts
   left and job not timed out → log (job id, type, attempt, `describe_exception`), sleep
   `backoff × attempt` (interruptible via the stop Event), retry; else existing error mapping. Inline mode uses the
   same `_run`.
7. **Shutdown** — `shutdown()`: set the generation's `closing` Event (interrupts retry backoffs), swap in a new one,
   `executor.shutdown(wait=False, cancel_futures=True)`; for futures actually cancelled →
   `_transition(queued→error, RESTART_MESSAGE)` and stop tracking them. **Amendment 2 (review SF2; APPROVED by the user 2026-11-02):** the heartbeat is
   NOT stopped. It keeps running jobs alive and exits by itself when nothing is held. Idempotent; a later `enqueue`
   builds a fresh pool. Wire into `lifespan` (`src/api/main.py`).
8. **PYQ opt-out** — `retries=0` in `src/api/routes/pyq.py`.
9. **Tests (written FIRST)** — before steps 1–8, write the new test file and run it; record the expected
   failures (names + reason) in the phase report. Then implement steps 1–8 and re-run. `tests/test_job_queue_durability.py`; autouse fixture calls `job_queue.shutdown()` after each test
   and sets tiny heartbeat/lease/backoff via `monkeypatch.setattr(settings, …)`; waits use Events/poll-with-deadline
   (no bare sleeps > 0.05 s).
9b. **Benchmark** — `scripts/bench_jobs.py`; record numbers in `docs/ENGINEERING_METRICS.md`.
10. **Verify** (below), then reviews: `code-quality-reviewer`; `security-reviewer` (touches job ownership read path
    and error contract).
11. **Close** with `/complete-phase` (ADR-013, report, state).

## Reused code
- `describe_exception`, `is_llm_timeout` — `src/core/llm.py`
- `GENERIC_ERROR`, `UserFacingError` — `src/core/errors.py`
- `SessionLocal` — `src/core/db.py`; `Job` — `src/core/models.py`
- `settings` — `src/core/config.py`
- Test fixtures `client`, `make_user` — `tests/conftest.py`; fake-`enqueue` pattern — `tests/test_upload_limits.py`;
  job contract — `tests/test_error_leaks.py`

## Verification
```bash
uv run pytest -q tests/test_job_queue_durability.py
uv run pytest -q tests/test_error_leaks.py tests/test_upload_limits.py tests/test_upload_ownership.py tests/test_workflow_smoke.py
for i in 1 2 3 4 5; do uv run pytest -q tests/test_job_queue_durability.py; done   # flakiness check
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
git diff --check
uv run python scripts/bench_jobs.py
```
Manual / workflow checks: none required offline. Live upload/lecture/PYQ job on a running server with
`WEB_CONCURRENCY=2` — UNVERIFIED unless the user runs it (optional; `docs/SMOKE_CHECKLIST.md`). Postgres
timestamp comparison — UNVERIFIED offline.

## Rollback
Revert the listed `src/` files and delete the new test file. No migration, no schema or data change: existing rows
remain valid (`updated_at` only). New settings are ignored by the old code.
