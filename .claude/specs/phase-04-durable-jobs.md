# Phase 04 — Durable background jobs

Status: DONE (2026-11-02; approved with user safeguards S1–S8, Q1–Q5, Amendments 1–2) — report `docs/phase-reports/phase-04-report.md`
Owner: user  ·  Created: 2026-11-02  ·  Related risks: #3 (non-durable jobs; boot reaper fails sibling workers' jobs),
#8 (upload memory, partly), backlog "AnyIO threadpool" (context only)  ·  Audit: §N

## 1. Goal
Background jobs (PDF upload, YouTube/pasted lecture, PYQ bank upload) behave correctly across several workers and
restarts. A booting worker never fails another live worker's jobs. A job whose owner died reaches a definitive
`error` within a bounded time. Transient failures are retried a bounded number of times. Long jobs time out.
In-flight work is shut down cleanly. All of this without a broker, new dependency or schema change.

## 2. Non-goals
- **Resuming a job after its process dies.** Inputs are not persisted. PDF bytes (up to `MAX_UPLOAD_MB`) have
  no durable home: Render free has no disk, and putting them in Postgres/Supabase is costly. A job whose process
  dies still ends in `error` with "Server restarted … Please retry." The user retries manually, as today
  (ADR-013, open question Q1).
- No broker (RQ/Redis/Celery), no new dependency. `jobs_backend="rq"`, `redis_url` and `rq_queue_name` remain
  unused (backlog).
- No DB schema change or migration. The `jobs` table (migration `0003_jobs`) is used as is.
- No change to `GET /api/v1/jobs/{id}` response shape, the enqueue responses, or `frontend/src/lib/api.ts`.
- No job cancel API, no per-user cap on active jobs, no queue-depth limit (backlog; Q4).
- No fix for the non-idempotent PYQ bank append or the partially-built vector store that a failed
  `create_vector_store` can leave behind. Both already happen on a manual retry today (backlog).
- No change to the `?sync=true` paths, the AnyIO threadpool size, or upload memory use (risk #8 stays open).

## 3. Current behaviour (verified from source, 2026-11-02)
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Executor | `src/core/job_queue.py:_pool` | Lazily created module-global `ThreadPoolExecutor(max_workers=2)`. Never shut down. |
| Enqueue | `job_queue.enqueue(job_type, work, user_id, payload)` | Inserts a `Job` row (`queued`, `payload="{}"` from every caller), then `submit(_run, job_id, work)`. Inputs live only in the closure (PDF bytes, URL, transcript). |
| Run | `job_queue._run` | `running` → `work()` → `done` + JSON result, or `error`: the text of a `UserFacingError`, otherwise `GENERIC_ERROR`. No retry, no timeout. Unconditional writes, so they would overwrite a row someone else had already reaped. |
| Inline mode | `job_queue._inline` | `jobs_backend == "inline"` runs `_run` synchronously. Tests use it (`tests/test_error_leaks.py:114`). |
| Status read | `job_queue.get_job`, `src/api/routes/jobs.py:job_status` (`def`, JWT) | Owner-filtered (`job.user_id != user_id` → None → 404). Returns `job_id,type,status,result,error,created_at,updated_at`. |
| Reaper | `job_queue.reap_stale_jobs` ← `src/core/job_handlers.py:register_all` ← `src/api/main.py:lifespan` | On **every** process boot, flips **all** `queued`/`running` rows to `error` ("Server restarted before this job finished. Please retry."). That includes jobs a sibling worker is running right now (risk #3). |
| Shutdown | `src/api/main.py:lifespan` after `yield` | Flushes Langfuse and closes LangGraph memory. Does nothing with jobs: queued futures and rows are left for the next boot's reaper. |
| Heartbeat / lease | — | None. `Job.updated_at` exists (`src/core/models.py:150`, nullable per migration 0003) and is set on every `_set`. |
| Settings | `src/core/config.py:283-287` | `jobs_backend="auto"`, `job_max_retries=1` and `job_timeout_seconds=900` exist but **are never read** (backlog "Config fields never read"). `redis_url` and `rq_queue_name` are unused. `src/worker.py` does not exist. |
| Callers | `src/api/routes/upload.py:60` (`upload_process`), `lecture.py:88` (`lecture_process`), `lecture.py:128` (`lecture_process_text`), `pyq.py:102` (`pyq_bank_upload`) | Each passes a zero-arg closure and `user_id`. Lecture closures swallow chat-index failures. |
| Idempotency | `src/core/vector_store.py:upsert_documents` (`store.add_documents(docs)`, no ids), used by `pyq/graph.py:build_question_bank` | Running a PYQ bank job twice **duplicates** chunks. `create_vector_store` (upload, lecture) reuses an existing collection, so re-running is idempotent. If the collection is partial, it stays partial. |
| Transient detection | `src/core/llm.py:is_llm_timeout` | Detects LLM timeouts through the cause chain. No general "transient" classifier exists. LLM 429s are already absorbed by the Gemini→Groq fallback. |
| Deployment | `render.yaml:33` (single uvicorn process); `Dockerfile:76` (`--workers ${WEB_CONCURRENCY:-1}`) | One process by default. The sibling-reaper bug appears with `WEB_CONCURRENCY>1`, or when an old and a new instance overlap during a deploy (overlap on Render is UNVERIFIED). |
| Frontend | `frontend/src/lib/api.ts:598 pollJob` | Polls every 2 s and gives up after 10 min (client-side 504 message). States `queued/running/done/error`. |
| Tests | `tests/test_error_leaks.py:107-119` (job error contract, inline); `tests/test_upload_limits.py` (fake `enqueue`) | No test covers the reaper, multiple workers, retry, timeout or shutdown. |

## 4. Requirements
- **R1 Heartbeat lease.** While a process holds a job (queued in its executor or running), it refreshes
  `jobs.updated_at` for that job every `job_heartbeat_seconds` (new, default 30). One background daemon thread per
  process does this with one batched `UPDATE`. A job is *stale* when it is `queued`/`running` and its `updated_at`
  is older than `job_lease_seconds` (new, default 120) or is NULL.
- **R2 Lease-aware reaping.** `reap_stale_jobs()` flips only stale jobs to `error`, using the existing message,
  and returns the count. It runs at boot (`register_all`, unchanged call site) and on each heartbeat tick.
- **R3 Lazy reaping on read.** `get_job` reports a stale non-terminal job as `error` (and persists that), so a
  poller gets a definitive answer even if no process boots.
- **R4 Guarded writes.** The transitions `running`, `done`, `error` and retry are conditional updates
  (`WHERE id=? AND status IN (expected)`). A result that arrives after the job was reaped or timed out is
  discarded and logged. It never overwrites a terminal status.
- **R5 Bounded retries.** A job that fails with a *transient* error is re-run in the same worker thread up to
  `retries` extra times (`enqueue(..., retries=None)` → `settings.job_max_retries`, default 1). Between attempts it
  waits `job_retry_backoff_seconds × attempt` (new, default 5). It stays non-terminal while retrying. Transient
  means: builtin `TimeoutError` (incl. `LLMTimeoutError`) / `ConnectionError`; `sqlalchemy.exc.OperationalError` /
  `DisconnectionError`; `httpx.TimeoutException` / `NetworkError` / `RemoteProtocolError` (narrower than
  `TransportError`, by review); or an exception carrying an HTTP status (`code`/`status_code`) of
  408, 429, 500, 502, 503 or 504. A `UserFacingError` or any other exception is never retried. After the last
  attempt the existing error contract applies unchanged (ADR-008).
- **R6 Non-idempotent jobs opt out.** `pyq_bank_upload` is enqueued with `retries=0` because it appends without ids.
  `upload_process` is also enqueued with `retries=0` (Amendment 1, user decision after review): its only transient
  failure point is a partial vector upsert, which a retry would silently accept. Lecture jobs keep 1 retry. Their chat
  index is built only after notes succeed, and failures there are swallowed, so a retry never sees its own partial
  index.
- **R7 Timeout.** A job still running after `job_timeout_seconds` (existing, 900) is marked `error` with a fixed
  message ("This job took too long and was stopped. Please retry."), and the heartbeat stops covering it. Python
  threads cannot be killed, so the worker thread runs on and its late result is discarded (R4). This is documented.
- **R8 Shutdown.** `lifespan` shutdown calls `job_queue.shutdown()`. It cancels this process's not-yet-started
  futures, marks their rows `error` with the restart message, and interrupts retry backoffs. It does not wait for
  running jobs. *Amendment 2 (review round 1 SF2; APPROVED by the user 2026-11-02):* running jobs **keep their heartbeat**
  until they finish, and the heartbeat thread exits once the process holds no jobs. A process that is still
  finishing work therefore cannot be reaped by a sibling (S1). If it is killed, its rows expire by lease.
  Binding conditions (user, 2026-11-02):
  - only this process's held jobs are heartbeated;
  - jobs that never started are failed on shutdown;
  - running jobs stay protected until they complete;
  - the heartbeat stops when no held jobs remain;
  - abrupt termination still allows stale-lease recovery. Calling it twice, or after inline-only use, is safe, and a
  later `enqueue` builds a fresh executor (the tests boot the lifespan repeatedly).
- **R9 Config.** New settings with safe defaults in `src/core/config.py`: `job_workers=2`,
  `job_heartbeat_seconds=30`, `job_lease_seconds=120`, `job_retry_backoff_seconds=5`. Comments on
  `job_max_retries` / `job_timeout_seconds` are updated (now read). Settings are validated at use: the lease
  must be greater than 2× the heartbeat, otherwise we log a warning and clamp.
- **R10 Contracts preserved.** Ownership filtering in `get_job`, the job error contract (only `UserFacingError`
  text reaches the client), `inline` mode, API paths and response shapes stay as they are. Logs carry job id,
  type, attempt and `describe_exception()`, never user content.

- **R11 Documented limitation (Q1).** This phase delivers *durable job-state tracking and stale-worker recovery*,
  **not resumable execution**. A job whose process dies is marked `error` with the safe restart message and the
  user retries. This is stated in the `job_queue` module docstring, ADR-013 and the phase report.

### User safeguards (approval 2026-11-02) — binding
| # | Safeguard | Where satisfied |
|---|---|---|
| S1 | A process heartbeats only the job ids it tracks; startup/periodic recovery never fails a live job of another process | R1 (in-process `_held` registry), R2 (lease-only reaping); AC1, AC7, AC11 |
| S2 | Tests prove live jobs survive stale checks and genuinely stale jobs are recovered | AC1, AC2, AC7, AC11 |
| S3 | Conditional status transitions; a late result never overwrites a failed job | R4; AC6, AC12 |
| S4 | Transient dependency timeouts (exceptions, e.g. LLM/httpx timeout → retryable) are distinct from the job-level execution deadline (`job_timeout_seconds`, watchdog → terminal, never retried). Retries run sequentially in the same worker thread, so no retry starts while a previous attempt's thread is still running; a job that hit the deadline is never retried | R5, R7; AC6, AC13 |
| S5 | Retry only explicitly classified transient errors; validation/processing errors never retry | R5 (closed allow-list); AC4 |
| S6 | PYQ bank jobs stay non-retryable until their processing is demonstrably idempotent | R6; AC5; backlog item |
| S7 | Tests for graceful shutdown, worker recovery, stale queued, stale running and late results | AC2 (queued + running), AC8, AC11, AC12 |
| S8 | All 8 workflows and the API contract preserved | R10; AC9, AC10, workflow smoke |

## 5. Affected workflows
| Workflow | Impact | How it stays working |
|---|---|---|
| PDF / Document Chat | Upload job gains heartbeat and timeout, **no retry** (Amendment 1) | Same enqueue/poll contract. `create_vector_store` returns early for ANY populated collection, so a retry after a partial upsert would report `done` over a truncated index. Upload uses `retries=0` until that is fixed (backlog). |
| Lecture Learning | Both lecture jobs gain heartbeat, retry (1), timeout | Same as above. The swallowed chat-index error is unchanged. |
| PYQ + Personal PYQ Bank | Bank upload job gains heartbeat and timeout, **no retry** (R6) | One-line `retries=0` in `pyq.py`. Test asserts it is never retried. |
| AI Mentor, NCERT, Evaluator, Current Affairs, Planner | Not touched | Workflow smoke tests rerun at close. |

## 6. Acceptance criteria (testable)
- [x] AC1 Worker A has a running job with a fresh heartbeat. Simulating worker B's boot (`register_all()` /
  `reap_stale_jobs()`) leaves A's job `running`, and it later completes `done`.
- [x] AC2 A `queued`/`running` row with `updated_at` older than the lease (or NULL) is flipped to `error` with the
  restart message by `reap_stale_jobs()`, and also by `GET /jobs/{id}` without any reaper run.
- [x] AC3 A job whose work fails once with a transient error and then succeeds ends `done`, ran twice, and
  `GET /jobs/{id}` returns the result. With `job_max_retries=1`, a job that keeps failing transiently runs exactly
  twice and ends `error` with `GENERIC_ERROR`.
- [x] AC4 `UserFacingError` and a plain `ValueError` are never retried (1 attempt). The existing
  `tests/test_error_leaks.py` job contract still passes.
- [x] AC5 `POST /pyq/bank/upload` and `POST /upload/process` enqueue with `retries=0`; the lecture endpoints use the default. A transient failure in that job is not retried.
- [x] AC6 A job running past `job_timeout_seconds` ends `error` with the timeout message. When its work later
  returns, the row stays `error` (late result discarded).
- [x] AC7 While a job is queued or running longer than the lease, its heartbeat keeps it non-stale (no reap).
- [x] AC8 `shutdown()` marks this process's not-started jobs `error`, keeps heartbeating running jobs until they
  finish (the heartbeat then exits; Amendment 2), and is
  idempotent. `enqueue` afterwards works (new executor).
- [x] AC9 Ownership: another user's job id still returns 404 (existing behaviour, re-asserted after the get_job
  change).
- [x] AC10 `GET /jobs/{id}` key set is unchanged. `frontend/` is not modified.
- [x] AC11 Worker recovery: rows from a "dead" process (stale `queued` and stale `running`) are recovered while a
  live process's fresh job in the same table is untouched, by both boot and periodic (heartbeat-tick) recovery.
- [x] AC12 Late result: a job flipped to `error` (by reaper or deadline) whose work then returns or raises keeps its
  `error` row (result/error text unchanged); no retry is scheduled for it.
- [x] AC13 Deadline vs transient: a work function that raises `LLMTimeoutError` is retried; a job that exceeds
  `job_timeout_seconds` is not retried, even if its work later raises a transient error.
- [x] AC14 Job reliability benchmark (`scripts/bench_jobs.py`) runs offline and reports: false reaps of live jobs
  (must be 0), stale jobs recovered (must be all), time to definitive error for a dead job, heartbeat tick cost.
- [x] All 8 workflows still pass their offline smoke checks (`tests/test_workflow_smoke.py`).
- [x] `uv run pytest -q` green, `uv run ruff check src tests` clean, `uv run python -m compileall -q src tests` exit 0.

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Tz-aware vs naive `updated_at` comparison on SQLite (string-stored) vs Postgres (timestamptz) | Compare with a cutoff written by the same SQLAlchemy type. Tests on SQLite. Postgres behaviour is **UNVERIFIED** offline. Noted in the report. |
| Clock skew between hosts (lease uses each process's own clock) | Lease 120 s ≫ heartbeat 30 s. Single host today. Documented in ADR-013. |
| Heartbeat thread starved or DB blip longer than the lease → a sibling reaps a live job | 3 missed beats are tolerated. The late result is discarded, not corrupted (R4). The user sees "please retry". |
| Timed-out thread still occupies 1 of `job_workers` slots | Documented. `job_workers` is configurable. Timeout is a definitive answer, not reclaimed capacity. |
| Retry repeats LLM cost (notes generation) | Max 1 retry, transient errors only, backoff. |
| Retry after partial vector indexing (upload/lecture) reuses a partial collection | Same as today's manual retry. Backlog item (not new). |
| Background threads leak between tests | `shutdown()` in a fixture. Tests use short heartbeat/lease via `monkeypatch.setattr(settings, …)`. |
| Single-process restart: dead jobs are reported after ≤ lease (120 s) instead of immediately at boot | Accepted trade-off (Q2). `get_job` reports them lazily. The frontend keeps polling up to 10 min. |

## 8. Test plan
New `tests/test_job_queue_durability.py`. It copies `monkeypatch.setattr(settings, …)` and the `client`/`make_user`
fixtures (`tests/conftest.py`), the inline-contract style of `tests/test_error_leaks.py`, and the
threading.Event-based waiting used in `tests/test_upload_ownership.py` (concurrent cache test). Coverage:
- AC1 two-"worker" simulation: a fresh heartbeat survives `register_all()`.
- AC2 stale rows (old / NULL `updated_at`) reaped by `reap_stale_jobs()` and by `GET /jobs/{id}`.
- AC3/AC4 retry matrix (parametrised exception types → attempts, final status/error).
- AC5 PYQ route passes `retries=0` (fake `enqueue` captures kwargs, as in `tests/test_upload_limits.py`), and an
  enqueue with `retries=0` is not retried.
- AC6 timeout with a tiny `job_timeout_seconds` and an Event-blocked work function; late result discarded.
- AC7 heartbeat keeps a blocked job alive past a tiny lease.
- AC8 shutdown semantics and idempotency; re-enqueue afterwards.
- AC9/AC10 cross-user 404 and the unchanged key set via the HTTP client.
- `_is_transient` unit cases (pure helper, like `tests/test_grounding.py`).
Then the full suite, ruff and compileall.

## 9. Decisions (user, 2026-11-02)
- Q1 Accepted: no automatic resume; documented limitation (R11).
- Q2 Approved: heartbeat 30 s, lease (stale threshold) 120 s.
- Q3 Approved: 1 retry for transient upload/lecture failures; PYQ bank never auto-retries.
- Q4 Per-user active-job cap stays in the backlog.
- Q5 (final): the USER adds the placeholders to `.env.example` by hand; Claude's permission rules deny reading or
  editing that file. Placeholders: four commented optional lines, `JOB_WORKERS=2`, `JOB_HEARTBEAT_SECONDS=30`,
  `JOB_LEASE_SECONDS=120`, `JOB_RETRY_BACKOFF_SECONDS=5`.

## 10. Original open questions (answered above)
- **Q1 (scope)** Accept "no resume after restart" (inputs not persisted)? Recommended: yes. Alternative: persist the
  small inputs (YouTube URL, pasted transcript ≤ its `max_length`) in `payload` and re-run lecture jobs on another
  worker. That needs a type→handler registry, route refactors and touches ownership, and PDFs still couldn't
  resume.
- **Q2** Lease 120 s / heartbeat 30 s defaults OK? A shorter lease reports dead jobs faster but risks false reaps.
- **Q3** Retry policy: `job_max_retries=1` for upload/lecture and 0 for PYQ bank. OK?
- **Q4** Per-user active-job cap (abuse/memory, risk #8) stays in the backlog for now?
- **Q5** Add the four new settings as commented placeholders to `.env.example`? (Needs your OK. Defaults work
  without it.)
