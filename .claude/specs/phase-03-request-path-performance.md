# Phase 03 — Request-path performance

Status: DONE (2026-11-02) — report `docs/phase-reports/phase-03-report.md` · ADR-011, ADR-012 (approved: Q1 yes, Q2 no, Q3 question+answer, Q4 6 / no .env.example)  ·  Owner: Vishal-AI-ML-Dev  ·  Created: 2026-11-02
Related risks: `docs/PROJECT_STATE.md` open risks #2 (blocking work in `async def`), #5 (checkpoint history growth —
prompt side only) · audit sections D, U#2

## 1. Goal
No route or middleware runs blocking work (sync DB, LLM, Qdrant, HTTP, SMTP, pypdf) on the event loop, so one slow
request cannot stall every other request on a single Uvicorn worker. The AI Mentor sends a bounded slice of the
checkpointed conversation to the model on each turn instead of the whole thread.

## 2. Non-goals
- No async rewrite of agents/LLM calls (`ainvoke`, async SQLAlchemy, async Qdrant). Handlers move to FastAPI's
  threadpool instead.
- No durable jobs, no job-queue changes (Phase 04). Upload memory (~2 × `MAX_UPLOAD_MB` per request, double pypdf
  parse, bytes in job closures) stays in the backlog for Phase 04.
- No pruning/compaction of the stored LangGraph checkpoint — only what is *sent to the model* is bounded. Checkpoint
  storage growth stays open (risk #5, partial).
- No change to public API paths, request/response shapes, status codes or error texts → no frontend change.
- No change to streaming handlers that only build a generator (their work already runs in the threadpool via
  `guard_stream`, `src/api/stream_guard.py:126`).
- No threadpool resizing unless Q2 is answered "yes".
- No fix for backlog items (account enumeration, `book_info`/`topic_info` bounds, `_bank_key` "anon" fallback, …).

## 3. Current behaviour (verified from source, 2026-11-02)
FastAPI runs `def` handlers/dependencies in a threadpool and `async def` ones directly on the event loop. Uvicorn runs
one worker in production (audit D; Render start command UNVERIFIED this session).

### 3a. `async def` handlers that block the loop
| # | Handler | File:line | Blocking work |
|---|---|---|---|
| B1 | `evaluate_sync` | `src/api/routes/evaluator.py:15` | Full LLM generation (`evaluate_answer` loop) |
| B2 | `mains_eval_sync` | `src/api/routes/evaluator.py:27` | Full LLM generation (`evaluate_mains`) |
| B3 | `model_answer_sync` | `src/api/routes/evaluator.py:45` | Full LLM generation (`get_model_answer`) |
| B4 | `generate_sync` | `src/api/routes/planner.py:34` | Full LLM generation + `get_plan_meta` → `_fetch_upsc_prelims_date` live web search (`src/agents/planner/graph.py:95`, 24 h cache) |
| B5 | `parse` | `src/api/routes/pyq.py:57` | LLM call (`parse_questions`, `src/agents/pyq/graph.py:346`) |
| B6 | `bank_upload` (`sync=true`) | `src/api/routes/pyq.py:94` | pypdf + embeddings + Qdrant upsert (`build_question_bank`). The `await read_pdf_upload` part is fine (pypdf check already offloaded, `src/api/upload_limit.py:170`) |
| B7 | `bank_status` | `src/api/routes/pyq.py:127` | Qdrant `collection_exists` (`get_bank_status`, `src/agents/pyq/graph.py:498`) |
| B8 | `bank_clear` | `src/api/routes/pyq.py:133` | Qdrant drop / `shutil.rmtree` (`clear_bank`, `src/agents/pyq/graph.py:505`) |
| B9 | `upload_pdf` (`sync=true`) | `src/api/routes/upload.py:50` | pypdf + LLM notes + embeddings (`process_upload`) |
| B10 | `process` (`sync=true`) | `src/api/routes/lecture.py:75` | YouTube fetch + LLM (`process_lecture`) |
| B11 | `process_text` (`sync=true`) | `src/api/routes/lecture.py:115` | LLM (`process_lecture_from_text`) |
| B12 | `list_conversations`, `get_messages`, `save_message` | `src/api/routes/history.py:42,53,66` | Sync SQLAlchemy |
| B13 | `forgot_password`, `resend_verification` | `src/api/routes/auth.py:259,339` | Sync SQLAlchemy + SMTP send |
| B14 | `submit_feedback` | `src/api/routes/feedback.py:28` | Sync SQLAlchemy insert/commit |
| B15 | `progress_overview` | `src/api/routes/progress.py:106` | 5 sync SQLAlchemy queries |
| B16 | `experiments_overview` | `src/api/routes/experiments.py:38` | Sync SQLAlchemy full scan of `feedback` |
| B17 | `job_status` | `src/api/routes/jobs.py:12` | Sync DB read (`get_job`) — polled every few seconds by `pollJob` |

The stub listed `upload`, `lecture`, `history`, `auth`, and "verify feedback/progress/experiments". B1–B5, B7, B8 and
B17 were **not** in the stub and are the worst offenders (B1–B5 hold the loop for a whole LLM call).

### 3b. `async def` handlers verified as non-blocking (keep as is)
`auth.me`; `cost.cost_access`, `cost.cost_overview` (in-memory `snapshot()` under a lock); `experiments.experiments_access`;
`monitoring.*` (in-memory); `current_affairs.topics/dates/months` (pure, constants/date math);
`pyq.topics` (constant lists); every streaming handler (`current_affairs.daily/editorial/monthly`, `ncert.chat_ncert`,
`planner.generate`, `pyq.generate`, `pyq.bank_generate`, `upload.chat_upload`, `lecture.chat`) — they only build a
sync generator, and `guard_stream` runs each `next()` and the session check in the threadpool;
`main.health_check`, `main.root`; `admin_access.require_admin` (pure allow-list check after the sync `get_current_user`).
Already `def`: `chat.agent_chat_stream`, `ncert.list_*`/`create_study_session`, most of `auth.py`, `get_current_user`, `get_db`.

### 3c. Middleware
| Aspect | File | Observed |
|---|---|---|
| Rate limiter | `src/api/rate_limit.py:85` `dispatch` → `RateLimiter.check` (`src/api/rate_limit_core.py:54`) → `UpstashRest.command` → sync `httpx.post` (`src/core/upstash.py:62`, timeout `redis_rest_timeout_seconds`, default 2 s) | When Upstash is configured, **every request** does 1–2 blocking HTTP round-trips on the loop (up to 2 × timeout when Upstash is slow; circuit breaker opens after repeated failures). In-memory fallback is non-blocking. Whether Upstash is configured in production: UNVERIFIED |
| Timeout | `src/api/security_headers.py:60` `asyncio.wait_for(call_next, 90)` | Cannot fire while the loop itself is blocked; works once work is in the threadpool (the thread keeps running after the 504) |
| Others | `request_id.py`, `http_metrics_mw.py`, `SecurityHeadersMiddleware`, `MaxUploadSizeMiddleware` | In-memory only — non-blocking |

### 3d. Mentor prompt history
| Aspect | File / function | Observed |
|---|---|---|
| State | `src/graph/state.py:21` `messages: Annotated[list, add_messages]` | Append-only; checkpointed per `scoped_thread_id` (`src/api/routes/chat.py:51`). Default thread is one stable thread per user, so it grows forever |
| Prompt | `src/graph/tools.py:564,587` `agent_node` | `history = list(state["messages"])` then `active.invoke([SystemMessage, *history])` — **every** earlier human/AI/tool-call/ToolMessage (incl. full web-search results) is sent on every turn. No cap anywhere |
| Turn helpers | `current_turn` (`tools.py:385`), `tool_rounds` (`:401`), `_should_seed_question` (`:356`) | Turns are delimited by the latest human message; tool budget and citations already use only the current turn |
| Other subgraphs | `src/graph/agent_subgraphs.py`, `rag_graph.py`, `plan_execute.py` | Only *append* to `messages`; they do not send the history to a model |

## 4. Requirements
- **R1 — sync handlers to `def`.** Change B1–B5, B7, B8, B10–B17 from `async def` to `def` (no `await` inside any of
  them; verified). Bodies, signatures, dependencies and responses unchanged.
- **R2 — offload sync branches of upload handlers.** B6 and B9 must stay `async def` (they `await read_pdf_upload`);
  their `sync=true` branch runs `_work` via `await run_in_threadpool(_work)` (`starlette.concurrency`, already used in
  `upload_limit.py`). Error mapping (`UserFacingError` → 400 fixed text, else 500 `GENERIC_ERROR`) unchanged.
- **R3 — non-blocking rate limiter.** `RateLimitMiddleware.dispatch` must not perform Upstash HTTP on the loop: when the
  limiter would use Upstash, run `check` via `run_in_threadpool`; the in-memory path stays inline. Limits, keys,
  exempt paths, 429 bodies and `Retry-After` unchanged. (Subject to Q1.)
- **R4 — guard test.** A static test enumerates every route in `src/api/routes/*.py` (+ `main.py`) and fails if an
  `async def` handler is not on an explicit allow-list of verified non-blocking handlers (3b) — so new blocking
  `async def` routes are caught in CI.
- **R5 — bounded mentor history.** Add a pure helper `bound_history(messages, max_turns)` in `src/graph/tools.py`:
  - the current turn (latest human message and everything after it) is always kept intact;
  - at most `max_turns` earlier turns are kept, newest first; older turns are dropped whole;
  - earlier turns are reduced to their human message + final AI answer (AI tool-call messages and `ToolMessage`s of
    *previous* turns are dropped), so no orphan tool call/result can reach the provider (subject to Q3);
  - the result never starts with a non-human message when a human message exists.
  `agent_node` sends `[SystemMessage, *bound_history(history, settings.mentor_history_max_turns)]`. `tool_rounds`,
  `_should_seed_question`, `current_turn`, citations and the checkpoint itself keep using the full history.
- **R6 — setting.** `mentor_history_max_turns: int = 6` in `src/core/config.py` (`0` = current turn only). Safe default,
  no secret. `.env.example` placeholder only if Q4 = yes.
- **R7 — docs.** ADR-011 in `docs/DECISIONS.md` (sync-work-in-`def` rule + allow-list test; mentor prompt window).
  `docs/PROJECT_STATE.md` risk #2 closed, #5 updated, at phase close.

## 5. Affected workflows
| Workflow | Touched by | How it stays working |
|---|---|---|
| AI Mentor | R5/R6 (`tools.py`), R3 | Same graph, same streaming; model sees the last N turns. Existing `tests/test_tool_agent_turns.py`, `test_mentor_routes.py`, `test_stream_citations.py` must stay green |
| NCERT Study | R3 only | No handler change |
| PYQ + Personal PYQ Bank | R1 (B5, B7, B8), R2 (B6) | Same responses; `test_pyq_ownership.py`, `test_smoke_pyq_parser.py` green |
| Answer Evaluator | R1 (B1–B3) | Same responses; `test_planner_evaluator.py`, `test_evaluator_rubric.py` green |
| Current Affairs | R3 only | No handler change |
| Study Planner | R1 (B4) | Same response; `test_planner_dates.py` green |
| Lecture Learning | R1 (B10, B11) | `BackgroundTasks` work identically in `def` handlers; `test_lecture_ownership.py`, `test_lecture_text.py` green |
| PDF / Document Chat | R2 (B9) | Same responses; `test_upload_*` green |
Cross-cutting: history (B12), auth emails (B13), feedback (B14), dashboard (B15–B17) — same contracts.

## 6. Acceptance criteria (testable)
- [ ] AC1 — No `async def` route performs sync DB/LLM/Qdrant/file/SMTP/HTTP work: the R4 guard test passes, and the
      allow-list + per-handler evidence (table 3a/3b) is in the phase report.
- [ ] AC2 — Loop responsiveness: with `evaluate_answer` patched to block 1 s, a concurrent `GET /health` completes in
      < 0.5 s (fails before R1). Same check for one `sync=true` upload branch (R2).
- [ ] AC3 — With an Upstash fake whose `command` blocks, a concurrent request is not delayed by the loop (fails
      before R3). (Only if Q1 = yes.)
- [ ] AC4 — Mentor prompt bound: a thread with 20 completed turns (incl. tool calls) sends the model exactly the system
      message + `max_turns` earlier human/AI pairs + the current turn; no `ToolMessage`/tool-call from earlier turns;
      the current turn's tool messages are intact; `max_turns=0` sends only the current turn. Checkpoint still holds
      all messages.
- [ ] AC5 — Response shapes/status codes unchanged for every touched endpoint (existing tests unchanged and green).
- [ ] All 8 workflows still pass their offline smoke checks (`tests/test_workflow_smoke.py`).
- [ ] `uv run pytest -q` green; `uv run ruff check src tests` clean; compileall exit 0. Frontend not touched.
- [ ] `security-reviewer` pass (R3 touches the rate limiter) and `code-quality-reviewer` pass: 0 BLOCKING.

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Moving long LLM calls into the threadpool can exhaust AnyIO's default 40 tokens under load; then *all* `def` handlers (incl. auth, `get_current_user`) and stream `next()` calls queue | Same total work as today, but the loop (health, streaming I/O, async handlers) stays responsive. Sizing is Q2; recorded as a risk in the report either way |
| `TimeoutMiddleware` 504s while the worker thread keeps running (provider cost continues) | Already true for `def` handlers today; documented, not changed |
| Mentor loses context older than N turns | `max_turns` configurable; long-term profile memory (`load_student_profile`) unaffected; default 6 chosen to keep follow-ups working |
| Dropping earlier tool results changes answers to "what did you find earlier" questions | Earlier final answers (which carry the "Sources used" block) are kept; Q3 lets you keep tool messages instead |
| Rate-limit offload changes security-relevant middleware | No change to limits/keys; existing `tests/test_rate_limiter.py`, `test_auth_rate_limit.py` must stay green; security review |
| Allow-list test is brittle when handlers are added | Failure message tells the developer to make the handler `def` or justify adding it to the allow-list |

## 8. Test plan
| Test (new unless noted) | Covers | Pattern copied |
|---|---|---|
| `tests/test_async_routes.py::test_async_handlers_are_allow_listed` | R4/AC1 — AST walk of route modules | pure-helper style (`tests/test_rrf_fusion.py`) |
| `tests/test_async_routes.py::test_blocking_sync_endpoint_does_not_stall_loop` (evaluator) + upload `sync=true` variant | AC2 — `httpx.AsyncClient(ASGITransport)` + `asyncio.gather` of a slow call and `/health`, with `get_current_user` overridden | `tests/test_rate_limiter.py:104` (ASGITransport), `monkeypatch.setattr` |
| `tests/test_async_routes.py::test_rate_limiter_upstash_check_off_loop` | AC3 (if Q1 = yes) — blocking fake `redis` injected into `RateLimiter` | `tests/test_rate_limiter.py` |
| `tests/test_mentor_history_bound.py` | AC4 — `bound_history` unit tests (cap, current turn intact, no orphan tool msgs, `0`, empty/no-human) + `agent_node` integration with a recording fake LLM over a 20-turn state | `RunnableLambda` fake LLM (`tests/test_tool_agent_turns.py`) |
| Existing suite | AC5, workflows | — |
Exact fixture wiring for the ASGITransport tests (lifespan/DB state, auth override) is UNVERIFIED until implementation.

## 9. Open questions — ANSWERED 2026-11-02
Answers: Q1 **yes** (R3 in scope); review decision (2026-11-02): unanswered earlier mentor questions are kept and
each counts as one of the six earlier turns, never with orphaned tool calls/results; Q2 **no** (risk recorded); Q3 **drop earlier tool messages**; Q4 **6, no `.env.example`**.

- **Q1** — Include R3 (offload the Upstash rate-limit check)? It blocks every request when Upstash is configured, but
  it is security middleware. Recommendation: **yes**. Is Upstash configured in production? (UNVERIFIED)
- **Q2** — Raise the AnyIO threadpool limit (e.g. setting `threadpool_tokens`, default 40, applied in `lifespan`)?
  Recommendation: **no** this phase — record as risk, revisit with Phase 04 jobs.
- **Q3** — For earlier turns, drop tool-call/tool-result messages (recommended: smaller prompts, no orphan-pair risk) or
  keep whole earlier turns?
- **Q4** — `mentor_history_max_turns` default 6 OK? Add a commented placeholder to `.env.example`? (Recommendation: 6; no
  `.env.example` change — it is not a secret.)
