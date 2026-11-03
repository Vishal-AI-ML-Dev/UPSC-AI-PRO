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

## ADR-015 — Retrieval is measured before it is changed; Phase 06 keeps production retrieval as-is
2026-11-02 · Accepted (Phase 06; owner decisions after the Stage 1 baseline)
- **Context:**
  - The only earlier retrieval evidence (`src/eval/retrieval_eval_report.md`) had n = 3 on one NCERT chapter. Its
    keyword-coverage labels measure the same signal as the lexical arm, so it favoured the RRF re-order by
    construction.
  - Verified from source: with default settings the lexical/RRF step re-orders the **same** k dense candidates, so
    it cannot change Recall@k. The mentor KB search (`mentor_kb.search_kb`) is dense only (k=4, threshold 0.25). The
    other vector workflows use `similarity_search*` (k=5, or 8 for PYQ; threshold 0.3).
- **Decision (measurement method):**
  - Retrieval quality is judged with evidence-span labels: a chunk is relevant if it contains a gold quote, not if
    keywords overlap. Metrics are Recall@k, MRR, NDCG@10 and threshold rates, with paired query-level 95% bootstrap
    CIs.
  - Variants are compared on one dense candidate pool per query, so re-ordering the same candidates and fetching a
    larger candidate set stay distinct.
  - Harness: `scripts/bench_retrieval.py` (isolated embedded Qdrant, embedding cache with a cumulative budget ledger,
    pacing, dry-run estimate before any request, no LLM calls). Dataset: `src/eval/retrieval_gold.json`. Metrics:
    `src/eval/retrieval_eval.py`.
  - Acceptance criteria for any ranking or threshold change are fixed **before** measuring (spec R5).
- **Baseline (2026-11-02):**
  - Dataset: 84 answerable + 13 unanswerable queries over four corpora (mentor KB and three synthetic corpora).
    Questions and 101 evidence quotes were written by Claude and checked by script for verbatim containment in a
    production chunk only. All entries keep `human_verified: false`.
  - Embeddings `gemini-embedding-001` @ 768. Production pooled Recall@k 0.988, MRR@k 0.935, NDCG@k 0.938
    (corrected definition, see the addendum). The Stage 1 historical value was 0.939 under the legacy definition
    (one quote per rank, capped at 1.0).
  - Thresholds 0.3 / 0.25 passed every tested result. Top-1 scores are 0.75–0.90, so the no-answer false-positive
    retrieval rate is 13/13. This is a retrieval finding, not an answer-grounding measurement.
  - Details and limitations: `docs/ENGINEERING_METRICS.md` → Retrieval quality benchmark.
- **Decision (production):** no change to thresholds, ranking, the lexical/RRF re-order, k, chunking or indexing.
  - Over-fetch 2k→k is rejected: Recall@k Δ +0.012, CI [0.000, +0.030].
  - Routing the mentor KB through RRF is not adopted: MRR Δ +0.079, CI includes 0.
  - Reranker and BM25 are deferred (headroom 0.006, below the 0.10 trigger).
  - RRF's ordering signal is noted as promising but did not meet the criteria.
  - The exploratory 0.80 threshold from a post-hoc sweep is **not** approved. Calibration needs an independent
    held-out set and explicit owner approval (Backlog).
- **Consequences:**
  - Off-topic or unanswerable questions still send k chunks to the model in NCERT / PDF / Lecture / PYQ / Mentor KB.
    Only the prompt's own instructions guard against answering from them.
  - Results hold only for these small, partly synthetic corpora with Claude-written queries and exact local search.
    NCERT, real uploads and production Qdrant remain unmeasured.
  - The application-side Chroma fallback is unchanged (risk #4 open). Only the benchmark got a preflight error.
  - The 24 h answer-cache issue (risk #9) is deferred to the Backlog.
- **Addendum (review remediation, owner-approved S1, S2, Q1–Q6; 2026-11-02):**
  - Harness corrections:
    - Each workflow is checked against its real production function: `similarity_search` for PDF / Lecture / PYQ,
      `similarity_search_with_sources` for NCERT, `search_kb` for the mentor KB.
    - All rankings come from one verified dense pool; the k and 2k searches are checked equal to the pool prefixes.
    - Over-fetch is compared with same-k RRF, so its effect is isolated from re-ordering.
    - The mentor KB and the shared pipeline are reported separately.
    - Pooled "@k" is labelled as a mean over k = 4 / 5 / 8, and headroom is computed at each corpus's production k.
    - NDCG uses the achievable ideal (real chunks). The Stage 1 definition stays available as `ndcg_legacy`.
    - Exit codes 3–7 are tested, and a test shows the consistency check fails on a tampered production function.
  - Runner safety:
    - Corpus paths are limited to `src/eval/corpora/` and `data/mentor_kb/` (resolved with symlinks and junctions
      followed, then checked for containment; secret/config files
      and `data/ncert/` refused) and are checked before any read.
    - Cleanup of the temp Qdrant dir is attempted after the client closes, on normal completion and on handled
      exceptions. A hard process kill can leave temporary files behind.
    - The new `--cache-only` mode never calls the API.
  - Corrected cache-only re-run (0 API requests): Recall / MRR and threshold findings unchanged.
    - Isolated over-fetch: no significant gain (Recall@k +0.012, CI [0.000, +0.030]; NDCG@k +0.007, CI
      [0.000, +0.017]).
    - The Stage 1 pooled "over-fetch NDCG +0.031 (marginal)" came from the mentor KB's RRF re-order.
    - Pooled RRF re-order NDCG@k +0.030, CI [+0.0004, +0.062], still marginal; not significant within the mentor KB
      or the shared pipeline alone.
    - Headroom at production k: pooled 0.012, mentor KB 0.028.
  - **No decision in this ADR changes.** The Stage 1 numbers stay recorded as prior evidence in
    `docs/ENGINEERING_METRICS.md`.
  - **Final remediation (owner-approved SF-1, S-1, S-2, doc accuracy, LF protection; 2026-11-02):**
    - Every mentor-KB file the topper-ingest script opens, and every file hashed for the report, is now validated
      individually (resolved, contained in `data/mentor_kb/`, policy-checked) before it is read.
    - The offline network guard patches `socket.socket.connect` only. It is not a complete network sandbox; the
      guarantee that cache-only sends nothing rests on never creating the provider client and blanking the key.
    - Hard links cannot be detected by path resolution.
    - `.gitattributes` keeps LF for `src/eval/retrieval_gold.json` and `src/eval/corpora/**` only, so the recorded
      hashes and chunking reproduce on Windows checkouts.
    - No measurement or decision changed.

## ADR-016 — Workflow evals in `evals/`, three evidence tiers, fenced metadata, live evals gated on validated bounds
2026-11-03 · Accepted (Phase 07; owner decisions in `.claude/specs/phase-07-llm-eval-safety.md`, Revision 3; amends
ADR-003)
- **Context:**
  - The only LLM eval (`src/eval/llm_eval.py`) scored the CRAG graph, which production never runs, on 3 NCERT rows.
  - Several untrusted values reached prompts unfenced:
    - upload `book_info` and lecture `topic_info` / `medium`;
    - model-derived notes metadata;
    - the PYQ bank and parser input;
    - the student profile in the mentor system prompt;
    - the evaluator's student answer;
    - study-aids material.
  - The sanitiser removed only exact fence markers, so look-alike variants survived.
  - Evaluator scores were unbounded: a "12/10" was accepted, and a model-written maximum replaced the requested
    marks.
- **Decision (directory ownership):**
  - `evals/` holds the workflow-evaluation harness: datasets, runners, evaluators, git-ignored reports.
  - `src/eval/` keeps the shared eval helpers (`JudgeScore`, judge prompts, `gates.py`), the CRAG `llm_eval` and the
    Phase 06 retrieval eval. It is unchanged and still run by CI.
  - `evals/` may import from `src/eval/`, never the reverse.
  - The full migration that ADR-003 planned for "the evals phase" is deferred to a separately approved change.
- **Decision (evidence tiers):** every report keeps three tiers separate.
  - **T1:** offline prompt-construction checks.
  - **T2:** offline scripted behaviour, with fake models.
  - **T3:** owner-run live models.
  - T1/T2 are never cited as real-model safety or quality.
- **Decision (offline isolation):** offline runs fail closed.
  - Keys and service URLs are blanked.
  - `connect`, `connect_ex`, `create_connection` and `getaddrinfo` are blocked and counted.
  - Real-client constructors are tripwired.
  - A temp embedded Qdrant is used, with fake embeddings and eval-scoped owned keys.
  - Every change is undone on success and on error, and the model caches are reset afterwards.
- **Decision (judge):**
  - One pinned model: `gemini-2.5-flash`, temperature 0, no fallback.
  - The system message is fixed text. The question, the evidence and the answer each go in their own fence in the
    human message.
  - A judge error is excluded from means, never scored 0.
  - The judge is disclosed as the same model family as the app.
  - Judge scores are report-only.
- **Decision (metadata rule):**
  - Every client-sent or model-derived metadata value that is rendered into a prompt goes through
    `fence_label(value, default, label)`, which is `harden_untrusted(clean_label(...))`.
  - `clean_label` only normalises: one line, markers stripped, at most 120 characters. It is not a security control.
  - Control-only fields (`book_info.confidence`) are never rendered.
  - The sanitiser also neutralises marker look-alikes: variants are replaced by `[marker removed]`, including
    Unicode-disguised, nested and stuck-character forms (see the remediation addenda).
- **Decision (scores):** evaluator scores use the requested maximum. A score written against another maximum is
  rescaled. Anything outside `[0, max]` becomes `None` ("unscored").
- **Decision (live budget):** live mode budgets every role (workflow, judge, embedding).
  - A shared ledger is enforced before each send. This happens in two independent places: an LLM callback with
    `raise_error=True` that reserves (1 + SDK `max_retries`) sends per attempt, and an embedding guard under the
    cache.
  - The first stop latches.
  - Five caps are required, including a total cost cap across all roles.
  - The `utf8_bytes` token bound is an **experimental estimate**.
  - Live mode refuses, before any client is built, unless every model it can reach has owner-recorded prices and a
    `validated` token bound (with date and evidence).
  - Live mode also refuses if usage can't be checked, if a client's retries or output limit don't match the budget,
    or if one worst-case request alone exceeds a cap.
  - `evals/pricing.json` ships every model `unvalidated`, so **T3 is disabled** until the owner approves a validation
    procedure.
- **Consequences:**
  - Offline evals run in pytest and in the CLI. The "before" run had 21 failing T1 checks; the "after" run has 0.
  - One documented scope-gate gap is reported as `known_gap`, not hidden (Backlog).
  - Real-model safety, quality and cost stay UNVERIFIED.
  - All eval cases are Claude-written (`human_verified: false`).
  - Prompt templates are unchanged; fenced values appear on their own lines.
  - One existing test assertion was updated for that format (plan Amendment 1).
- **Addendum (review remediation, owner-approved B1 / S1–S10 / NITs; 2026-11-03):**
  - **Fence markers (B1, S5):**
    - Detection runs on a per-character NFKC skeleton: format characters (Cf) dropped, bracket look-alikes mapped,
      separators ignored.
    - Only the matched spans of the original text are replaced. The replacement is the non-empty
      `[marker removed]`, repeated until nothing matches (bounded, linear time). Head and tail fragments are
      replaced too.
    - Nested markers can no longer rebuild an exact marker.
    - The eval-side detector is independent: it collapses the text to `[a-z0-9]` and counts marker words.
    - Not covered: homoglyph letters from other scripts.
  - **Lecture output language (S6):** allow-listed (English / Hindi / Hinglish, default English) and rendered as
    trusted text, not fenced. Mains keywords and the model-answer question / keywords are fenced (S10).
  - **Live budget (S1–S4, NITs):**
    - Enforced at three points on one ledger:
      - the LLM callback, re-attached to tool-bound and structured-output models and to the judge;
      - a send guard patched into every production client's send methods (`_TimeoutGuard`), which refuses any send
        without a reservation;
      - the embedding guard.
    - The judge client passes the same retry and output-limit preflight as the workflow clients.
    - `--sdk-max-retries` below 1 is refused, because 0 means the Gemini default of 5 retries.
    - Only the repository's `evals/pricing.json` is accepted; the owner may edit its contents.
    - A non-interactive live start needs `UPSC_EVALS_LIVE_CONFIRM=RUN` in addition to `--yes` and every cap.
    - An undeclared model at send time stops the run with exit 2.
    - Judge inputs are cut to fixed byte budgets, so the judge input has an exact byte bound.
    - A NaN judge score is a `judge_error`.
  - **Runner (S8, NITs):**
    - Exit 5 = execution error, never a safety result.
    - `known_gap` downgrades only its declared check.
    - Report excerpts are matched after case and whitespace folding, and omitted when the sources are not fully
      captured.
    - Live reports carry their own disclaimer.
    - Offline isolation also blocks UDP sends, resets LangSmith's env cache and restores the response-cache / Upstash
      / NCERT-storage / usage-tracker globals.
  - **Final review (B-1, SF-1, SF-2, S-1, S-2):**
    - Marker fragments anchored by brackets are always replaced. Only the bracket-less core needs a word boundary,
      so study text like "untrusted sources" is kept. A rejected core match re-searches from the next character.
    - Invisible Default_Ignorable letters (Hangul fillers, combining grapheme joiner, variation selectors) are
      dropped like Cf.
    - The eval detector now keeps brackets and has the same boundary rule, implemented independently.
    - Judge inputs are sanitised before they are cut to their byte budget, which makes the judge byte bound exact.
    - `BaseChatModel.stream` calls `_stream` without a run manager. The send guard therefore lets an unmanaged send
      claim the latest unclaimed reservation of its thread, once.
  - **Round-3 review (SF-1, SF-2, N-1, N-2):**
    - A linear bracket-segment pass replaces marker words glued to a bracket run by ANY number of stuck characters
      (`<<<00000END_UNTRUSTED_SOURCE00000>>>`, `<<<END0UNTRUSTED0SOURCE>>>`). The eval detector does the same with
      its own independent, linear segment scan; its earlier quadratic regex is gone.
    - The budget callback runs inline (`run_inline = True`), so async reservations are made on the event-loop
      thread.
    - Unclaimed reservations live in one lock-protected run-id -> thread map, so a reservation claimed from any
      thread can never be claimed again.
    - Tests cover invoke, stream, a reserved `astream`, an unreserved `ainvoke` and the cross-thread case.
  - **Round-4 review (SF-A, SF-B, SF-1, N-B, N-C):**
    - The bracket-segment pass is word-bounded. Its round-3 form could delete long stretches of study text between a
      "<<" and a distant "untrusted".
      - Head: BEGIN / END sits in the first or second word after >= 2 "<", with UNTRUSTED in the same or the next
        word.
      - Tail: SOURCE sits in the last or second-to-last word before >= 2 ">", with UNTRUSTED in the same or the
        previous word.
      - Any number of stuck characters inside those words is still tolerated, and decoy UNTRUSTED words no longer
        hide a marker.
    - Canadian syllabics U+1438 / U+1433 map to "<" / ">".
    - The eval detector applies the same word rules in its own code, for ASCII brackets only: it drops non-ASCII
      bracket look-alikes instead of mapping them. It is linear on long space / dot runs.
    - A run claimed by an unmanaged send can no longer also pass a later run-id check, so one reservation covers
      one send.
  - **Round-5 review:**
    - Combining marks (Mn / Me) are skipped in the detection skeleton without a word break, so an accent inside
      "UNTRUSTED" no longer splits the word.
    - The managed reservation check is one atomic check-and-claim.
    - Tests pin non-ASCII bracket look-alikes with stuck characters in production.
    - Accepted residuals (Backlog):
      - three or more padding words between the bracket run and a glued marker word;
      - a noise word between the marker words;
      - the detector over-counts around non-ASCII bracket look-alikes;
      - the detector counts one hit per segment.
  - **Final budget review (2026-11-03):**
    - `BudgetLedger` holds one re-entrant lock (`_lock`). Every reservation (LLM and embedding) does the stop check,
      every cap check (including the shared total-cost cap) and the counter update as one transaction under it.
      `record_llm_usage` detects invalid usage, updates the actual-usage counters, compares against the bound and
      latches the stop in the same transaction. A concurrent reservation cannot pass a cap that only one fits, and
      cannot slip in between detecting invalid usage and the stop latch.
    - Lock order is always callback -> ledger; the ledger never takes another lock. `LLMBudgetCallback._lock` guards
      only the callback's own maps, and `on_chat_model_start` checks for a duplicate run, reserves and registers in one
      locked step (a run seen twice, even concurrently, is charged once).
    - **One managed reservation = one send.** `require_reservation` returns a scope that `install_send_guard` holds
      open for the whole send. Nested checks of the same run while the scope is open (`_agenerate` -> `_generate` ->
      `_stream`, including from an executor thread) pass. Once the outermost scope exits, another send with that run
      id is refused and latched. This supersedes the round-3 N-3 residual ("checked any number of times while its run
      is open").
    - Accepted residual: two sends that overlap in time and share one run manager look like one send with a nested
      check. LangChain gives every invoke its own run manager.
    - Deterministic tests (barrier/event interleaving) fail with the locks replaced by no-op locks and pass with them.
  - **No decision above changes.** T3 stays disabled; real-model results stay UNVERIFIED.
