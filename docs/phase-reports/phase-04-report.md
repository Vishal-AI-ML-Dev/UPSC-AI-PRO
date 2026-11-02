# Phase 04 report — Durable background jobs

Date: 2026-11-02 · Spec: `.claude/specs/phase-04-durable-jobs.md` (DONE) · Plan: `.claude/plans/phase-04-durable-jobs.md`
(DONE, Amendments 1–2) · ADR-013 · Status: **DONE** (all acceptance criteria MET; open items listed under "Not verified")

## Summary
Background jobs are now safe with more than one worker and across restarts. This phase gives **durable job state and
stale-worker recovery, not resumable execution** (user decision Q1).

- **Lease:** a process heartbeats only the jobs it holds, on `jobs.updated_at`. No schema change.
- **Recovery:** boot, periodic and on-read (owner only) recovery fail only rows whose lease expired. A live job of
  another process is never touched.
- **Writes:** every status change is conditional, so late results are discarded.
- **Retries:**
  - Only an explicit allow-list of transient errors is retried, sequentially in the same thread and capped at 3.
  - No retry is started after the deadline or after the job failed elsewhere.
  - Upload and PYQ bank jobs do not retry.
- **Deadline:** a job-level deadline gives a definitive answer.
- **Shutdown:** jobs that never started are failed and backoffs are interrupted. Running jobs keep their heartbeat
  until they finish (Amendment 2). Abrupt termination still recovers by lease.

A job whose process dies is **not resumed**. It ends `error` with "Server restarted before this job finished.
Please retry."

## Decisions during the phase
| # | Decision | By |
|---|---|---|
| Q1 | No automatic resume; documented limitation | user |
| Q2 | Heartbeat 30 s, lease 120 s | user |
| Q3 | 1 retry for transient upload/lecture failures; PYQ never retries | user (upload later changed by Amendment 1) |
| Q4 | Per-user active-job cap stays in backlog | user |
| Q5 | Four commented placeholders in `.env.example` (`JOB_WORKERS=2`, `JOB_HEARTBEAT_SECONDS=30`, `JOB_LEASE_SECONDS=120`, `JOB_RETRY_BACKOFF_SECONDS=5`) | user. Added **by the user by hand**: Claude's permission rules deny reading/editing `.env.example` |
| S1–S8 | Binding safeguards (spec §4) | user |
| Amendment 1 | Upload `retries=0`. The only transient failure is a partial vector upsert, which a retry would accept as `done` | user, after review round 1 |
| Amendment 2 | Heartbeat continues for running jobs during graceful shutdown until they finish. Conditions: only own jobs, unstarted jobs failed, running jobs protected, heartbeat stops when none held, lease recovery after abrupt termination | user, after review round 1 |

## Test-first evidence (failing before each fix, recorded in this session)
1. **New test file before any implementation:** `3 warnings, 52 errors in 2.97s`. Every test errored in setup:
   `AttributeError: Settings … has no attribute 'job_heartbeat_seconds'` (and `job_queue.shutdown` was missing).
2. **Behavioural probe of the pre-Phase-04 code** (scratch script, temp SQLite, offline):
   ```
   (a) reap_stale_jobs() on a live, just-started job -> reaped 1 status error
   (b) after the live job returns -> status done (error overwritten)
   (c) transient LLMTimeoutError -> attempts 1 status error
   (d) job_heartbeat_seconds setting exists: False | job_queue.shutdown exists: False
   ```
3. After implementation: `52 passed`. Then 2 cross-process tests were added (heartbeat-only protection and its
   no-heartbeat control): `54 passed` ×5.
4. **Review round 1 regression tests**, written before the fixes: `11 failed, 55 passed, 1 error`. The failures:
   - retry after the deadline or after the job failed elsewhere;
   - backoff not interruptible by shutdown;
   - shutdown dropping the heartbeat and the pop race;
   - deadline mark lost when its DB write fails;
   - unbounded retries;
   - implicit `__context__` counted as transient;
   - ownerless job written on a poll;
   - upload `retries` not 0;
   - idle heartbeat never exiting.
   The 1 error was a teardown bug in the new lifespan test, which was then fixed.
5. **Review round 2 regression tests:** `3 failed, 60 passed, 7 errors`. The failures were `HTTPException(500/503)`
   classified as transient, and a failed `submit` leaving a held job. The 7 errors were that leaked held job's
   heartbeat cascading into the leak check of every later test.
6. **Test-isolation flake found and fixed.** 2 of 6 full-suite runs failed (4 and 6 failures, all in the new file).
   - Cause: a heartbeat thread was alive when monkeypatch restored the 30 s production interval. It then slept 30 s,
     and the next test saw it as alive, so nothing ticked.
   - Fix: the autouse fixture drains held jobs and the heartbeat before teardown, and asserts no leaked heartbeat at
     setup.
   - After the fix, 7 of 7 full runs passed.

## Files changed (`git status --porcelain`)
```
 M .claude/specs/phase-04-durable-jobs.md
 M .env.example                              (edited by the user, not Claude)
 M docs/ENGINEERING_METRICS.md
 M docs/PROJECT_STATE.md
 M src/api/main.py
 M src/api/routes/pyq.py
 M src/api/routes/upload.py
 M src/core/config.py
 M src/core/job_handlers.py
 M src/core/job_queue.py
?? .claude/plans/phase-04-durable-jobs.md
?? scripts/bench_jobs.py
?? tests/test_job_queue_durability.py
```
Also changed at close: `docs/DECISIONS.md` (ADR-013), `.claude/specs/README.md`, this report.
No migration, no schema change, no dependency change, `frontend/` untouched.

## Acceptance criteria
| AC | Criterion | Result | Evidence (`tests/test_job_queue_durability.py` unless noted) |
|---|---|---|---|
| AC1 | A sibling's boot never fails a live job | MET | `test_boot_recovery_does_not_fail_a_live_job_of_this_process`, `test_recovery_never_touches_a_fresh_job_of_another_process`, `test_sibling_recovery_spares_heartbeated_jobs_past_the_lease` (+ control `test_without_heartbeat_a_sibling_would_reap_the_job`); benchmark A: 0 false reaps (5 runs) |
| AC2 | Stale queued/running (and NULL) rows recovered by the reaper and by `GET /jobs/{id}` | MET | `test_stale_jobs_are_recovered_by_reaper[queued/running]`, `test_null_updated_at_counts_as_stale`, `test_get_job_reports_stale_job_as_error_without_reaper[queued/running]` |
| AC3 | Transient failure retried to the limit; final status visible | MET | `test_transient_failure_is_retried_once_then_succeeds`, `test_persistent_transient_failure_stops_at_the_limit`, `test_max_retries_setting_is_honoured`, `test_max_retries_is_capped`, `test_retry_runs_in_the_thread_backend_too`; benchmark D |
| AC4 | Permanent errors never retried; error contract kept | MET | `test_permanent_errors_are_never_retried[4]`, `test_is_transient_classification[12]`, `test_http_exception_is_never_transient[2]`, `test_permanent_error_raised_while_handling_a_timeout_is_not_transient`; `tests/test_error_leaks.py` job contract passes |
| AC5 | PYQ bank and upload enqueue with `retries=0`; lecture uses the default | MET | `test_pyq_bank_upload_enqueues_without_retries`, `test_upload_job_is_not_retried`, `test_lecture_jobs_use_default_retry_policy[2]`, `test_retries_zero_disables_retry` |
| AC6 | Deadline → `TIMEOUT_MESSAGE`; late result discarded | MET | `test_job_deadline_fails_the_job_and_late_result_is_discarded`, `test_deadline_mark_survives_a_failed_db_write` |
| AC7 | Heartbeat keeps queued/running jobs alive past the lease | MET | `test_heartbeat_keeps_running_and_queued_jobs_alive_past_the_lease`, `test_sibling_recovery_spares_heartbeated_jobs_past_the_lease` |
| AC8 | Shutdown fails unstarted jobs; running jobs keep their heartbeat until done, then it stops (Amendment 2); idempotent; enqueue works afterwards | MET | `test_shutdown_fails_unstarted_jobs_and_keeps_running_ones_alive`, `test_shutdown_keeps_tracking_a_job_that_just_started`, `test_shutdown_interrupts_a_retry_backoff`, `test_shutdown_is_idempotent_and_enqueue_works_afterwards`, `test_shutdown_after_inline_only_use_is_safe`, `test_idle_process_stops_heartbeating`, `test_lifespan_shutdown_calls_job_queue_shutdown`, `test_failed_submit_fails_the_row_and_stops_tracking` |
| AC9 | Another user's job → 404; no side effect | MET | `test_job_status_contract_and_ownership`, `test_other_users_stale_job_is_404_and_not_reaped_by_their_poll`, `test_users_poll_never_writes_an_ownerless_job` |
| AC10 | `GET /jobs/{id}` keys unchanged; frontend untouched | MET | `test_job_status_contract_and_ownership` (exact key set); enqueue response asserted in the route tests; `git status -- frontend` empty |
| AC11 | Worker recovery: dead rows recovered, live ones untouched (boot + periodic) | MET | `test_periodic_recovery_reaps_dead_rows_but_not_live_ones`, `test_stale_foreign_row_is_recovered_by_our_periodic_sweep`, `test_heartbeat_only_touches_tracked_jobs`; benchmark B1/B2: 8/8 recovered |
| AC12 | Late result/failure never overwrites a failed job; no retry for it | MET | `test_late_outcome_never_overwrites_a_failed_job[result/transient/permanent]`, `test_queued_job_failed_elsewhere_is_never_started`, `test_no_retry_if_job_failed_elsewhere_during_backoff` |
| AC13 | Transient dependency timeout retried; job past its deadline never retried | MET | `test_transient_failure_is_retried_once_then_succeeds` (`LLMTimeoutError`), `test_job_past_deadline_is_not_retried_even_on_transient_error`, `test_no_retry_when_backoff_would_cross_the_deadline` |
| AC14 | Job reliability benchmark runs offline and reports the metrics | MET | `scripts/bench_jobs.py`, 5 runs `ALL PASS: True`; `docs/ENGINEERING_METRICS.md` |
| — | 8 workflows pass their offline smoke checks | MET | `tests/test_workflow_smoke.py` inside the full suite and the focused run (`215 passed`) |
| — | pytest / ruff / compileall green | MET | below |

## Gate results (final run, verbatim)
```
uv run pytest -q                      -> 1182 passed, 13 warnings in 128.48s (0:02:08)
focused (job_queue_durability, error_leaks, upload_limits, upload_ownership, workflow_smoke, input_limits)
                                      -> 215 passed, 3 warnings in 41.05s
uv run ruff check src tests           -> All checks passed!
uv run ruff check scripts/bench_jobs.py -> All checks passed!
uv run python -m compileall -q src tests -> exit 0
git diff --check                      -> exit 0 (CRLF notices only; includes .env.example)
cd frontend && npm run build / lint   -> not run: frontend/ unchanged in this phase
```
Earlier full runs on the final code: `1182 passed` ×3 (125.24 s, 126.66 s, 128.48 s). Before the round-2 fixes,
`1179 passed` ×5 after the fixture fix. Focused job tests: `69 passed` ×3. Baseline before the phase: `1113 passed`.
Phase 04 adds 69 tests.

## Reviews
| Round | Reviewer | Result |
|---|---|---|
| 1 | code-quality-reviewer | 0 BLOCKING, 3 SHOULD-FIX (retry after deadline / backoff not interruptible; shutdown dropped running jobs' heartbeat + pop race; upload retry over a partial index) → all fixed (Amendments 1–2); NITs fixed |
| 1 | security-reviewer | 0 CRITICAL/HIGH/MEDIUM; L1 (ownerless job writable on poll) fixed, L2 (unbounded retries) fixed with cap 3, L3 (no per-user cap / slot starvation) → backlog; I1 (traceback in shutdown log) fixed; I2 (clock skew) documented |
| 2 | code-quality-reviewer | 0 BLOCKING; all round-1 fixes confirmed, no new races found; SHOULD-FIX spec/plan drift → Amendment 2 recorded; SHOULD-FIX queued-job deadline → backlog; NITs N1 (failed submit), N2 (test margin), N3 (docstring), N4 (loose assert) fixed |
| 2 | security-reviewer | 0 CRITICAL/HIGH/MEDIUM; L1/L2/I1 confirmed resolved; new LOW (`HTTPException` 5xx counted as transient) fixed |

## Engineering metrics
`scripts/bench_jobs.py` runs real separate OS processes sharing one SQLite file. Timings are scaled down (heartbeat
0.2 s, lease 1.0 s). The full tables are in `docs/ENGINEERING_METRICS.md`. Final code, 3 runs:

- **A (sibling boot):** 0 false reaps across 14–16 sibling boot recoveries; 8/8 jobs `done`.
- **B1 / B2 (dead worker):** 8/8 jobs recovered, at most 0.88–1.07 leases after the crash.
- **C (heartbeat tick):** 6.9–7.7 ms median for 1–50 held jobs.
- **D (retry policy):** correct in 50/50 jobs per case.

These are SQLite numbers on a local machine, not production numbers.

## 8-workflow status
| Workflow | Phase 04 impact | Status |
|---|---|---|
| AI Mentor | none | offline smoke PASS (full suite) |
| NCERT Study | none | offline smoke PASS |
| PYQ + Personal PYQ Bank | bank upload job: heartbeat, deadline, no retry | offline smoke PASS; route test asserts `retries=0` |
| Answer Evaluator | none | offline smoke PASS |
| Current Affairs | none | offline smoke PASS |
| Study Planner | none | offline smoke PASS |
| Lecture Learning | both lecture jobs: heartbeat, deadline, 1 transient retry | offline smoke PASS; route tests assert the default policy |
| PDF / Document Chat | upload job: heartbeat, deadline, no retry | offline smoke PASS; route test asserts `retries=0` |

## Not verified
- **`.env.example` content.** The user added the four placeholders. Claude's permission rules deny reading or
  diffing the file (both attempts were denied), so its exact content is UNVERIFIED by Claude. `git diff --check`
  passed for it, so it has no whitespace errors. The user should confirm with `git diff -- .env.example`.
- Postgres `timestamptz` lease comparison (tests and benchmark use SQLite).
- Render deploy overlap, `WEB_CONCURRENCY>1` under real HTTP load, live smoke (`docs/SMOKE_CHECKLIST.md`).
- Frontend build/lint: not run because `frontend/` is unchanged.

## New backlog items (added to `docs/PROJECT_STATE.md` → Backlog)
- PYQ bank append not idempotent — MED.
- `create_vector_store` keeps partial collections, which blocks upload retries — MED.
- No per-user active-job cap; unbounded executor queue holds upload bytes; a timed-out thread keeps its slot — MED.
- Queued jobs have no deadline; hung workers can leave them `queued` (round 2 S2) — MED.
- Ownerless job rows readable by any user (read side of L1) — LOW.
- `OperationalError` always counted as transient — LOW.
- Lease clock-skew assumption; Postgres comparison UNVERIFIED — LOW.
- `.env.example` placeholders: Claude is denied access, so the user edits the file — LOW (done by the user this phase).

## Follow-ups
- **Make upload retry-safe and re-enable its retry:** rebuild the collection on retry, or use deterministic chunk ids.
- **Make PYQ appends idempotent:** ids from `pdf_hash` + chunk index.
- **Queued-job deadline and per-user active-job cap:** pairs naturally with the open AnyIO threadpool capacity item.
- **Verify on staging:** Postgres lease comparison and `WEB_CONCURRENCY=2` behaviour.
