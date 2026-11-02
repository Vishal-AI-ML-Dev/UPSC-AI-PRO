# Architecture decision records

Format: `ADR-NNN — Title` · Date · Status (Accepted / Superseded by ADR-x) · Context · Decision · Consequences.
Append new ADRs; never rewrite accepted ones — supersede them.

---

## ADR-001 — Keep the root `src/` backend layout
2026-11-02 · Accepted
- **Context:** The backend lives in root `src/` (`api/`, `agents/`, `graph/`, `core/`); Dockerfile, Render,
  Alembic (`prepend_sys_path = .`), CI and tests import `src.*`.
- **Decision:** Do not move the backend into a `backend/` directory. New code goes into the existing layers.
- **Consequences:** No import churn; deployment configs stay valid. Layering is enforced by
  `.claude/rules/backend.md` rather than by directory names.

## ADR-002 — Spec-driven, phase-by-phase rebuild
2026-11-02 · Accepted
- **Context:** The audit found the docs overstate the implementation; changes must be incremental and verified.
- **Decision:** Every phase has an approved spec (`.claude/specs/`) and plan (`.claude/plans/`) before code.
  Only plan-listed files change. Each phase closes with a report in `docs/phase-reports/`.
- **Consequences:** Slower start, but every change is traceable, reviewable and preserves the 8 workflows.

## ADR-003 — `src/eval/` stays until the evals phase
2026-11-02 · Accepted
- **Context:** CI runs `python -m src.eval.llm_eval` and `src.eval.retrieval_eval`; moving them now would change CI.
- **Decision:** Create an empty `evals/` skeleton in Phase 00; migrate in Phase 08 together with CI updates.
- **Consequences:** Two eval locations temporarily; `evals/README.md` points to the live one.

## ADR-004 — Version the `.claude/` workspace
2026-11-02 · Accepted
- **Context:** `.gitignore` ignored all of `.claude/`, so rules, specs, skills, agents and hooks would be lost on clone.
- **Decision:** Ignore only `.claude/settings.local.json` and `.claude/**/*.local.*`; commit the rest.
- **Consequences:** Shared, reviewable Claude configuration; personal overrides stay local.

## ADR-005 — Safety hooks are stdlib Python, formatting is best-effort
2026-11-02 · Accepted
- **Context:** Windows dev machine (Git Bash + PowerShell); `uv` and `python` available; no new dependencies allowed.
- **Decision:** `protect_paths.py` blocks sensitive paths (PreToolUse). `format_changed.py` auto-formats only the
  edited file (ruff / prettier if already installed). `lint_changed.py` reports ruff findings without blocking.
- **Consequences:** Legacy files may get reformatted the first time they are edited; keep such diffs in the same
  phase and mention them in the phase report.

## ADR-006 — Keep known dead code until the operations phase
2026-11-02 · Accepted
- **Context:** Phase 01 confirmed three dead/stray files: `src/api/routes/mentor.py` (router not registered in
  `src/api/main.py`, but still imported by `tests/test_stream_citations.py`), `src/agents/upload/upload.py`
  (unmounted duplicate upload router without auth), and `src/index.css` (stray CSS in the backend package).
  Deleting files needs explicit user approval (`.claude/rules/scope-control.md`); the user chose to keep them.
- **Decision:** Preserve all three unchanged in Phase 01 — no deletion or relocation. `ARCHITECTURE.md` labels
  them as dead code. Removal is reconsidered in Phase 09 (operations), together with re-pointing or retiring the
  `test_stream_citations.py` cases that import `mentor.py`.
- **Consequences:** The files stay importable and must not be registered by accident; reviewers treat them as
  out of scope. No runtime change.

## ADR-007 — Ownership encoded in user-scoped vector keys; no legacy fallback
2026-11-02 · Accepted (Phase 02)
- **Context:** Uploads (`upload_<md5>`) and pasted lectures (`lecture_paste_<md5>`) were global collections readable
  by any user who knew the id; the mentor model could pick any `persist_key`.
- **Decision:** Owner id is part of the key (`upload_<uid>_<md5>`, `lecture_<uid>_paste_<hex11>`), built server-side
  from the JWT; ids are format-validated; the mentor tool and MCP server cannot choose a collection. Legacy
  collections are kept but never read (no fallback, no migration). A rollback build must keep these protections or
  disable the affected endpoints (see the Phase 02 plan, *Rollback*).
- **Consequences:** No schema change. Users re-upload PDFs / re-paste transcripts processed before Phase 02.
  Legacy collections need a later cleanup (backlog).

## ADR-008 — `UserFacingError` is the only displayable error
2026-11-02 · Accepted (Phase 02)
- **Context:** Routes returned `str(e)` for any `ValueError` and `f"... failed: {e}"` on 500; jobs stored `str(e)`.
- **Decision:** `src/core/errors.py:UserFacingError` (fixed strings only) → HTTP 400 / job error text. Every other
  exception, including plain `ValueError`, → 500 / `GENERIC_ERROR`, logged with `describe_exception` only.
- **Consequences:** Unexpected failures show a generic message; intended user messages are raised as
  `UserFacingError` at their source.

## ADR-009 — Exact upload allow-list and a 1 MiB body limit for everything else
2026-11-02 · Accepted (Phase 02)
- **Context:** The upload-size middleware matched any path containing "upload" and trusted `Content-Length`.
- **Decision:** Only `POST /api/v1/upload/process` and `POST /api/v1/pyq/bank/upload` get `max_upload_mb`; every
  other write body gets `max_request_body_kb` (1 MiB). Limits count received bytes. Chat history is trimmed to 20
  messages server-side (before validation) and client-side.
- **Consequences:** Long chats keep working; oversized JSON bodies get 413. A new upload endpoint must be added to
  `UPLOAD_ENDPOINTS` explicitly.

## ADR-010 — `/auth/refresh` is outside the dedicated auth rate limit
2026-11-02 · Accepted (Phase 02 review amendment, approved by the user)
- **Context:** Phase 02 R5 originally put `/auth/refresh` in the 10-per-5-minute per-IP auth budget. Review found
  that every open tab refreshes about every 30 minutes, so students behind one shared IP (coaching-centre Wi-Fi,
  colleges, mobile CGNAT) would exhaust the budget for each other; the frontend treats a 429 on refresh as transient.
- **Decision:** The dedicated budget covers only login, register, forgot-password, reset-password, verify-email and
  resend-verification. `/auth/refresh` stays under the global per-IP limiter only. Refresh tokens are random,
  rotating and revocable, so they cannot be guessed.
- **Consequences:** Refresh abuse is bounded by the global limiter (100/60 s per IP by default). Credential guessing
  and email-sending endpoints keep the strict budget.

## ADR-011 — No blocking work on the event loop; the mentor prompt is a bounded window
2026-11-02 · Accepted (Phase 03; Q1 yes, Q2 no, Q3/Q4 and the S1 review decision approved by the user)
- **Context:** One uvicorn worker served every request from one event loop. 18 `async def` handlers ran sync work on
  it: LLM calls, SQLAlchemy, Qdrant, SMTP, live web search. So did the two upload `sync=true` branches and the Upstash
  rate-limit check, which runs on every request. One slow call stalled every user, `/health` included. The AI Mentor
  also sent its whole checkpointed thread, including old tool output, to the model on every turn.
- **Decision:**
  - Route handlers that do sync work are plain `def` and run in FastAPI's threadpool. `async def` is allowed only for
    handlers on the allow-list in `tests/test_async_routes.py`: pure handlers, builders of streaming generators, or
    handlers that `await` and offload sync work with `run_in_threadpool`. CI fails on any other `async def` route.
  - Upstash rate-limit checks run on one dedicated, process-wide `anyio.CapacityLimiter(8)`, never on the default
    pool. `anyio` is imported directly; it is a starlette/httpx transitive dependency pinned in `uv.lock`, and
    `pyproject.toml` is unchanged. Declare it if starlette ever drops it.
  - The in-memory rate-limit window is lock-protected. `RateLimiter.uses_redis()` is public.
  - The mentor sends the whole current turn plus up to `mentor_history_max_turns` (default 6) earlier turns. Each
    earlier turn is reduced to its question and its last answer. Unanswered earlier questions are kept and count as
    turns. Earlier tool calls and results are never sent. The stored checkpoint is not pruned.
- **Consequences:**
  - The event loop stays responsive under blocking work (synthetic evidence in `docs/ENGINEERING_METRICS.md`).
  - More work shares AnyIO's default 40-thread pool. That risk was accepted (Q2) and is to be revisited in Phase 04.
  - A slow Upstash caps limiter throughput at about 8 ÷ RTT instead of stalling the process.
  - Mentor context older than 6 turns is not visible to the model.
  - Checkpoint storage growth remains open.

## ADR-012 — Synthetic request-path benchmark harness; one canonical metrics page
2026-11-02 · Accepted (Phase 03 plan Amendment 2, requested by the user)
- **Context:** Phase 03 claims about responsiveness needed reproducible evidence. No metrics document existed. The
  live dependencies (Render, Gemini/Groq, Upstash) cannot be measured from a dev machine without credentials.
- **Decision:**
  - `scripts/bench_request_path.py` and `scripts/bench_server.py` benchmark the real app on one local uvicorn worker.
    They use a synthetic LLM and a synthetic Upstash, run before/after via `git archive`, and print JSON plus a
    summary table.
  - The server runs offline. It never reads `.env`, drops every non-allow-listed environment variable, blanks all
    service settings, and binds to `127.0.0.1` only.
  - `docs/ENGINEERING_METRICS.md` is the canonical page for metric definitions, how to run them, recorded results and
    the list of UNVERIFIED metrics. Phase reports keep the raw measurements.
- **Consequences:**
  - Numbers are labelled SYNTHETIC, local and single-worker, and must not be cited as production performance.
  - Render latency, real LLM latency and production throughput stay UNVERIFIED until measured.
  - Absolute values vary with machine state; only before/after direction is robust.

## ADR-013 — Thread-backed jobs with a heartbeat lease: durable job state, not resumable execution
2026-11-02 · Accepted (Phase 04; user decisions Q1–Q5, safeguards S1–S8, plan Amendments 1–2)
- **Context:**
  - Every process boot failed **all** `queued`/`running` jobs, including those a sibling worker was running.
  - Status writes were unconditional, so a late result could overwrite a reaped job.
  - `job_max_retries` / `job_timeout_seconds` were never read, and the executor was never shut down.
  - Inputs (PDF bytes up to `MAX_UPLOAD_MB`, URLs, transcripts) live only in closures. Render free has no disk.
- **Decision:**
  - **Backend:** stay thread-backed (`src/core/job_queue.py`). No broker, no new dependency.
  - **No schema change:** the lease uses the existing `jobs.updated_at` column.
  - **Lease:**
    - A daemon heartbeat refreshes `updated_at` every `JOB_HEARTBEAT_SECONDS` (30), only for the job ids this process
      holds.
    - A queued/running row with no refresh for `JOB_LEASE_SECONDS` (120, clamped to > 2× heartbeat), or a NULL
      `updated_at`, is stale.
    - Recovery fails only stale rows, at boot, on each tick, and on read in `get_job` (owner only).
    - The heartbeat thread exits when the process holds no jobs.
  - **Writes:** every status transition is a conditional UPDATE (`WHERE status IN …`). Late outcomes are discarded.
  - **Retries:**
    - Only an explicit allow-list counts as transient: `TimeoutError`/`ConnectionError`, the httpx
      timeout/network/remote-protocol errors, SQLAlchemy `OperationalError`/`DisconnectionError`, and HTTP-like status
      408/429/5xx through explicit `__cause__` only.
    - Never retried: a `UserFacingError`, an `HTTPException`, or anything raised from either.
    - Retries run sequentially in the same thread, capped at `MAX_RETRIES = 3`.
    - A retry is refused if it could not start before the deadline, and re-checked after the backoff.
    - Non-idempotent work opts out with `retries=0`: PYQ bank (S6), and PDF upload (Amendment 1, because a
      partial vector upsert would be accepted).
  - **Deadline:** `JOB_TIMEOUT_SECONDS` fails the job with a fixed message and is never retried. Threads cannot be
    killed, so the outcome of the still-running thread is discarded.
  - **Shutdown (Amendment 2, user-approved):**
    - Jobs that never started are cancelled and failed.
    - Retry backoffs are interrupted.
    - Running jobs keep their heartbeat until they complete. If the process is killed, they expire by lease.
  - **No resume (Q1):** a job whose process dies ends as `error` ("Server restarted … Please retry."). Inputs are
    never persisted.
- **Consequences:**
  - Each process heartbeats only its own jobs, so a live job is never failed while its process is alive.
  - A dead worker's jobs are reported failed about lease to lease+heartbeat after the last beat (120–150 s at
    defaults; measured 0.88–1.15 leases in `scripts/bench_jobs.py`).
  - Single-process restarts no longer fail jobs instantly. Pollers see the error after the lease.
  - Lease correctness assumes the clocks of the processes involved agree within `lease − 2×heartbeat`.
  - Known limits (backlog):
    - no deadline for queued jobs;
    - a timed-out thread keeps its worker slot;
    - no per-user job cap;
    - PYQ append and partial `create_vector_store` are not idempotent;
    - Postgres comparison UNVERIFIED.
  - New settings `JOB_WORKERS`, `JOB_HEARTBEAT_SECONDS`, `JOB_LEASE_SECONDS`, `JOB_RETRY_BACKOFF_SECONDS` (safe
    defaults in `src/core/config.py`). The user documents them as commented placeholders in `.env.example`, which
    Claude is not permitted to read or edit.

## ADR-014 — Mentor chat routes every message afresh; the supervisor has 4 reachable routes
2026-11-02 · Accepted (Phase 05; user decisions Q1–Q4)
- **Context:**
  - `supervisor_node` treated `state['route']` as a forced route. The checkpointer saves each message's route, so
    from the second message of a conversation the previous route was reused and the router never ran again.
    Offline benchmark: 80 % of messages answered by the wrong specialist; the endpoint test reproduced a Question/
    Answer message getting the planner's "I need your daily study hours" reply.
  - The `rag` route needed `state['persist_key']`, which no API sets, so it silently became `mentor`.
  - `state['task_inputs']` was never set by any caller; the specialists always parsed the message text.
  - Docs and docstrings claimed forced routes, a `task_inputs` contract, a reachable `rag` route and a
    "full multi-agent system".
- **Decision:**
  - **Per-message routing:** the router runs for every message. `route` is an output of the current message and is
    never read as an input. No forced-route input exists (no caller used it).
  - **Q1 = A:** remove `rag` from the supervisor (route literal, router prompt, node). Keep
    `src/graph/rag_graph.py` for the eval harness (ADR-003). Document-aware Mentor chat is a future product
    feature (backlog, candidate Phase 07); any HTTP caller of CRAG must build the key server-side (ADR-007).
  - **Q2:** remove `task_inputs` from `AgentState` and the specialists; the text parsers are the input path.
  - **Q3:** Mentor chat does not support HITL (`HITL_ENABLED`, off by default): `/agent/chat/stream` has no resume
    path. Documented, not implemented.
  - **Q4:** `src/api/routes/mentor.py` stays unchanged and unregistered (ADR-006, Phase 09).
  - NCERT / PYQ / Lecture / Upload and the Planner / Evaluator / Current Affairs pages stay direct function calls.
- **Consequences:**
  - One fast-model router call per message instead of per conversation (~229 estimated input tokens per call;
    live latency and price UNVERIFIED) — `docs/ENGINEERING_METRICS.md` → Mentor routing benchmark.
  - Conversation history and the student profile are still carried in the checkpointed state.
  - Old checkpoints may still hold `route` / `task_inputs`; neither is read any more.
  - Re-adding a "UI picks the specialist" feature needs a non-persisted input, not `state['route']`.
- **Addendum (review round 1, user-approved):**
  - Planner / evaluator / current-affairs replies store the student's question (once; same rule as the mentor's
    `_should_seed_question`) before the answer, so a mixed conversation is a sequence of question → answer turns.
  - `bound_history` keeps each earlier turn's FIRST final answer. This supersedes the Phase 03 "last answer" rule
    (ADR-011): for pre-Phase-05 checkpoints, orphan other-route answers are dropped instead of being paired with
    the wrong question. The bound is unchanged.
  - The Mentor evaluator node no longer has a Mains/HITL branch (unreachable without `task_inputs`); the
    standalone Answer Evaluator keeps Mains evaluation.
  - **Specialist response isolation (round 2 B1, AC17):** because specialists now store the student's question in
    `messages`, `/agent/chat/stream` forwards only model output (`_is_input_message` filter). The client response,
    the response cache and the saved assistant message contain only the specialist's answer.
  - **Streaming is not token-by-token (recorded at close, pre-existing):** with `subgraphs=False` the nested
    specialists' tokens are not emitted, so answers arrive as one completed response. Documented (README, `chat.py`
    docstring) and kept in the backlog (MED); not changed in Phase 05.
