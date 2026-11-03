# Project state — UPSC-AI-PRO

Read this first in every new Claude Code session. Facts here are verified from source unless marked UNVERIFIED.
Baseline evidence: `docs/audit/2026-11-02-architecture-audit.md`.

## Current phase
| Phase | Title | Status |
|---|---|---|
| 00 | Engineering workspace setup | DONE (2026-11-02) — `docs/phase-reports/phase-00-report.md` |
| 01 | Baseline & safety net | DONE (2026-11-02) — `docs/phase-reports/phase-01-report.md` |
| 02 | Security & ownership | DONE (2026-11-02) — `docs/phase-reports/phase-02-report.md` |
| 03 | Request-path performance | DONE (2026-11-02; commit `2ac904e`) — `docs/phase-reports/phase-03-report.md` |
| 04 | Durable jobs | DONE (2026-11-02) — `docs/phase-reports/phase-04-report.md` |
| 05 | Agent architecture truthfulness | DONE (2026-11-02) — `docs/phase-reports/phase-05-report.md` |
| 06 | RAG quality — `.claude/specs/phase-06-rag-quality.md` | DONE (2026-11-03) — measurement + documentation; no production retrieval change (ADR-015) — `docs/phase-reports/phase-06-report.md` |
| 07–09 | See `.claude/specs/README.md` | STUB (Phase 07 not started; wait for the user) |

## The 8 workflows (must not regress)
| Workflow | Backend entry | Frontend | Status (last verified) |
|---|---|---|---|
| AI Mentor | `src/api/routes/chat.py` `POST /api/v1/agent/chat/stream` | `features/mentor/MentorChat.tsx` | Code present; runtime UNVERIFIED; KB tool limited to mentor KB (Phase 02); offline smoke PASS 2026-11-02; prompt bounded to current turn + 6 earlier Q/A turns (Phase 03) (Phase 03 close); every message routed afresh — the first message's route no longer sticks to the conversation; supervisor has 4 routes, no `rag`; specialists store the question once, mentor history = coherent Q/A pairs; replies never echo the question (Phase 05, ADR-014); answers arrive whole, not token by token (pre-existing, backlog MED); offline smoke PASS 2026-11-02 (Phase 05 close) |
| NCERT Study | `src/api/routes/ncert.py` | `features/ncert/Ncert.tsx` | Code present; needs R2 or local PDFs; runtime UNVERIFIED; input bounds (Phase 02); offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed) |
| PYQ + Personal PYQ Bank | `src/api/routes/pyq.py` | `features/pyq/Pyq.tsx` | Code present; runtime UNVERIFIED; PDF validation + cross-user regression test (Phase 02); offline smoke PASS 2026-11-02; parse/bank status/clear off the event loop, bank `sync=true` offloaded (Phase 03); bank upload job: heartbeat lease, deadline, no auto-retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close) |
| Answer Evaluator | `src/api/routes/evaluator.py` | `features/evaluator/Evaluator.tsx` | Code present; runtime UNVERIFIED; offline smoke PASS 2026-11-02; `*/sync` endpoints off the event loop (Phase 03) (Phase 03 close) |
| Current Affairs | `src/api/routes/current_affairs.py` | `features/current_affairs/CurrentAffairs.tsx` | Code present; live web; runtime UNVERIFIED; offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed) |
| Study Planner | `src/api/routes/planner.py` | `features/planner/Planner.tsx` | Generates plans; not persisted; offline smoke PASS 2026-11-02; `generate/sync` off the event loop (Phase 03) (Phase 03 close) |
| Lecture Learning | `src/api/routes/lecture.py` | `features/lecture/Lecture.tsx` | Code present; YouTube reachability UNVERIFIED; pasted transcripts per user (old pastes must be re-pasted); offline smoke PASS 2026-11-02; `process`/`process-text` off the event loop (Phase 03); lecture jobs: heartbeat lease, deadline, 1 transient retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close) |
| PDF / Document Chat | `src/api/routes/upload.py` | `features/upload/Upload.tsx` | Per-user ownership (Phase 02; old uploads must be re-uploaded); runtime UNVERIFIED; offline smoke PASS 2026-11-02; upload `sync=true` offloaded (Phase 03); upload job: heartbeat lease, deadline, no auto-retry (Phase 04 Amendment 1); offline smoke PASS 2026-11-02 (Phase 04 close) |

Test suite status (2026-11-03, Phase 06 final remediation): `1272 passed, 2 skipped, 13 warnings` (the 2 skips are
file-symlink escape tests that need Windows symlink privilege; earlier Phase 06 runs: `1267 passed, 1 skipped`
after round-1 remediation, `1231 passed` at the Stage 2 closeout; Phase 05 close: `1212 passed`; Phase 04 close: `1182 passed`;
Phase 03 close: `1113 passed`;
Phase 02 close: `1086 passed`;
Phase 01 close: `930 passed`).
Engineering metrics (synthetic request-path benchmark, how to rerun, UNVERIFIED production metrics):
`docs/ENGINEERING_METRICS.md`; harness `scripts/bench_request_path.py` + `scripts/bench_server.py`.
Ruff: `All checks passed!`. compileall exit 0. Frontend unchanged in Phase 03 (last build/lint clean at Phase 02 close).
Live workflow smoke (`docs/SMOKE_CHECKLIST.md`): not run — UNVERIFIED.

## Verified architecture facts
- Backend root is `src/` (ADR-001). API prefix `/api/v1`; all feature routers JWT-protected in `src/api/main.py`.
- "Supervisor" (`src/graph/supervisor.py`) = one structured LLM routing call **per message** → one of mentor /
  planner / evaluator / current_affairs (+ optional plan_execute, off). No parallel or multi-step orchestration.
  A route saved in the checkpoint is never read as input (Phase 05, ADR-014).
- No `rag` supervisor route (removed Phase 05). CRAG (`src/graph/rag_graph.py`) only runs in `src/eval/llm_eval.py`.
- `task_inputs` removed (Phase 05); planner / evaluator / CA specialists parse the chat message. HITL is not
  supported by Mentor chat (no resume path; `HITL_ENABLED` off).
- Mentor chat answers are delivered whole, not token by token: `/agent/chat/stream` streams with `subgraphs=False`
  and every specialist is a nested subgraph (pre-existing; backlog MED). Only model output is forwarded; stored
  student questions are never echoed (Phase 05, AC17).
- The only autonomous agent is the ReAct mentor in `src/graph/tools.py:build_tool_agent` (tools: `web_search`,
  `knowledge_base_search`; cap 3 rounds).
- `src/agents/*/graph.py` are plain functions, not LangGraph. NCERT/PYQ/Lecture/Upload are called directly by routes.
- LLM chains: Gemini 2.5 flash / flash-lite with Groq fallback (`src/core/llm.py`). Embeddings: Gemini, 768-dim.
- Vectors: one Qdrant collection per `persist_key` (`src/core/vector_store.py`). Chroma fallback broken
  (`langchain_chroma` not installed).
- Jobs: in-process thread pool (`JOB_WORKERS`, default 2), DB-persisted status; heartbeat lease on `jobs.updated_at`,
  lease-based recovery, conditional transitions, transient-only retries, deadline; inputs not persisted (no resume) —
  `src/core/job_queue.py`, ADR-013.
- Checkpointer/store: Postgres when `DATABASE_URL` is Postgres; else SQLite checkpointer + in-memory store.
- Jev / TypeSafe: not present anywhere in the repository.

## Open risks (from the audit risk register; ranked)
| # | Risk | Sev | Planned phase |
|---|---|---|---|
| 1 | ~~No ownership at vector boundary; LLM-chosen `persist_key`; global upload collections~~ CLOSED Phase 02 (ADR-007); legacy collections remain (cleanup backlog) | HIGH | 02 ✓ |
| 2 | Blocking work in `async def` handlers (single worker) → CLOSED Phase 03 (ADR-011: 18 handlers → `def`, upload sync branches + Upstash check offloaded, allow-list test). Threadpool (40) capacity risk accepted (Q2, backlog MED) | HIGH | 03 ✓ |
| 3 | ~~Boot reaper fails sibling workers' jobs; no retry/timeout/shutdown~~ CLOSED Phase 04 (ADR-013: lease + heartbeat, guarded transitions, transient retries, deadline, graceful shutdown). Jobs are still not resumable after a process dies (accepted, Q1); queued-job deadline / per-user cap in backlog | HIGH/MED | 04 ✓ |
| 4 | Vector stack fails without Qdrant (Chroma not installed) — kept OPEN by owner decision in Phase 06 (only the benchmark got a preflight error; app unchanged) | HIGH (misconfig) | backlog |
| 5 | Unbounded inputs → CLOSED Phase 02 (field + 1 MiB body limits, ADR-009); checkpoint history growth: mentor *prompt* bounded in Phase 03 (6 earlier turns); stored checkpoint still grows | MED | 03 (prompt ✓) / later (storage) |
| 6 | Auth rate limit → CLOSED Phase 02 (ADR-010, refresh excluded); account enumeration (register message, no dummy bcrypt) still open | MED | backlog |
| 7 | Tokens in `localStorage` | MED | backlog |
| 8 | ~~Upload size check relies on Content-Length; unbounded `_processed_cache`~~ CLOSED Phase 02 (byte-counted limits, bounded thread-safe per-user cache); upload memory ~2×50 MB/request open | MED | 03/04 |
| 9 | 24 h semantic cache can serve stale time-sensitive answers — deferred by owner in Phase 06 (Backlog) | MED | backlog |
| 10 | Eval gate tests CRAG path production never runs (Phase 06 added a retrieval benchmark over the production retrieval functions, ADR-015; the LLM eval gate is unchanged) | MED | 08 |
| 11 | In-memory store fallback when not Postgres | MED | 09 |
| 12 | Migrations run per process; LangGraph tables outside Alembic | LOW–MED | 09 |
| 13 | `/health` checks nothing; no readiness/backups | MED | 09 |
| 14 | Exception text leaked; PII in logs → CLOSED Phase 02 for touched files (ADR-008); repo-wide log sweep + public `/docs` open | LOW | 09 |
| 15 | Dead/duplicate code; unused deps/settings | LOW | 09 (kept per ADR-006) |
| 16 | Non-blocking CI lint; no frontend tests; live smoke disabled | LOW | 09 |
| 17 | Compose: unpinned qdrant, default creds, exposed ports, unused redis | LOW | 09 |

## Doc drift (README.md / ARCHITECTURE.md claims contradicted by source) — RESOLVED in Phase 01 (2026-11-02)
All items below were corrected in place; claim → evidence table in `docs/phase-reports/phase-01-report.md`.
- "8 specialised agents behind one supervisor"; per-agent "LangGraph pipelines".
- "Hybrid retrieval" (lexical only re-scores dense candidates); "cross-encoder reranking" (off, deps missing).
- Citation enforcement via `compose_grounded_answer` (never called); "tenacity retries" (not imported).
- Local Chroma fallback (broken); stricter auth rate limit 10/300 s (not implemented).
- "Stateless API, add workers freely" (several in-process states; job reaper hazard).
- `ARCHITECTURE.md` lists `mentor.py` as a feature endpoint (unregistered).
- "669 offline tests" (574 test functions counted; with parametrize UNVERIFIED).

## Backlog (out-of-scope findings; add file, evidence, severity)
- README metrics not re-measured in Phase 01 (LOC counts, bundle sizes, chunk count). Baseline build shows `index-*.js`
  257.43 kB raw vs README "244 KB raw" (`README.md` §5) — LOW, UNVERIFIED.
- Startup log line uses an emoji (`src/api/main.py:79`); on a cp1252 Windows console logging raises
  `UnicodeEncodeError` (seen with `pytest -s`) — LOW.
- Test warnings: Starlette `httpx` TestClient deprecation; Alembic `path_separator` deprecation (`alembic.ini`) — LOW.
- FastAPI now wraps included routers (`_IncludedRouter`): `app.routes` no longer lists `APIRoute`s. Smoke test uses
  `app.openapi()` instead; any future code introspecting `app.routes` will break — LOW.
- `src/api/routes/mentor.py` unregistered but tested by `tests/test_stream_citations.py` — LOW.
- `src/agents/upload/upload.py` duplicate unauthenticated router (unmounted) — LOW.
- `src/index.css` stray in backend package; `tests/.gitignore` is UTF-16 — LOW.
- Config fields never read: `groq_fallback_models`, `openai_compat_*`, `groq_whisper_model`,
  `job_max_retries`, `job_timeout_seconds`, `rq_queue_name`, `auth_rate_limit_*` — LOW.
- Missing referenced files: `src/worker.py`, `scripts/cron_cleanup.py`, `tests/test_smoke_live.py` — LOW.
- `.dockerignore` excludes `*.md`, so `data/mentor_kb/*.md` is not in the image — LOW.
- (Phase 09) `src/agents/upload/upload.py` (dead, unmounted, unauthenticated; kept by ADR-006) still calls
  `process_upload(content, filename)` / `ask_upload(...)` without the now-required `user_id` and returns
  `detail=str(e)`. It would fail closed if mounted. Delete with user approval in Phase 09 — LOW.
- (Risk, ADR-010) `/auth/refresh` is limited only by the global per-IP limiter; if Render's proxy peer is not in
  `FORWARDED_ALLOW_IPS`, all clients would share one global bucket — verify `request.client.host` on staging — MED.
- Version mismatch: pyproject 0.1.0 / app 2.0.0 / frontend 1.0.0; Streamlit-era defaults (port 8501) — LOW.

- (Phase 02) Legacy global collections `upload_<hash>` / `lecture_paste_<hash>` are kept but unread; plan a cleanup
  (needs user approval; never delete silently) — LOW.
- (Phase 02) Client-sent `book_info` (`/upload/chat`) and `topic_info` (`/lecture/chat`) dicts are unbounded and reach
  prompts unfenced (`src/agents/upload/graph.py` ask_upload, `src/agents/lecture/graph.py` ask_lecture); caller's own
  session only, capped by the 1 MiB body limit — LOW.
- (Phase 02) `src/agents/pyq/graph.py:_bank_key` falls back to a shared `"anon"` key for an empty user id; should raise — LOW.
- (Phase 02) Account enumeration: register returns "Email already registered"; `authenticate` skips bcrypt for unknown
  emails (timing) — MED.
- (Phase 02) `make_persist_key` lower-cases case-sensitive YouTube ids (public content collisions only) — LOW.
- (Phase 02) Upload memory: one request can hold ~2 × `MAX_UPLOAD_MB` and pypdf parses twice; queued jobs keep bytes in
  closures — MED (Phase 03/04).
- (Phase 02) NITs: `mount_mcp` logs a warning on every call (`src/mcp_server.py`); `_UploadCache.get` returns a shallow
  copy (nested `book_info` shared); f-string/filename/topic log lines remain (`src/agents/lecture/graph.py` "chat index
  ready", topic logs; `src/agents/pyq/graph.py` filename log); `TypeError` text still returned to the model
  (`src/graph/tools.py`); `UPLOAD_ENDPOINTS` hard-codes `/api/v1`; auth limiter is per worker without Redis — LOW.
- (Phase 03, Q2) AnyIO's default threadpool (40 tokens) is shared by every `def` handler, sync dependencies
  (`get_current_user`, `get_db`) and `guard_stream`'s `next()` calls; Phase 03 moved more long LLM calls into it, so under
  load auth/DB requests can queue behind them. Default kept by decision; revisit with Phase 04 jobs — MED.
- (Phase 03) `SaveMessageRequest` (`src/api/routes/history.py`) and `FeedbackRequest` (`src/api/routes/feedback.py`) have no
  `Field(max_length=...)`; bounded only by the 1 MiB body limit (feedback truncates on write) — LOW.
- (Phase 03 NITs, `docs/phase-reports/phase-03-report.md` → Remaining NITs) `src/api/rate_limit.py` `_check`: no deadline on
  waiting for the 8-slot Upstash budget; `uses_redis()` true while the Upstash breaker is OPEN (extra thread hop);
  `RateLimiter.check` resolves the client twice; `anyio` imported directly (transitive dep, pinned 4.13.0); test-only
  items (shared `make_pdf`, AST walker limits, fixed-delay sampling, timeout message) — LOW.
- (Phase 03) Pre-existing ruff I001 (unsorted imports) in `scripts/ingest_mentor_kb.py`, `scripts/ingest_topper_md.py`;
  CI lints only `src tests`, so not caught — LOW.
- (Phase 03) Benchmark showed the emoji startup logs raise `UnicodeEncodeError` tracebacks on a cp1252 console on
  every log call (see existing emoji backlog item) — confirms that item; LOW.

- (Phase 04) PYQ bank append is not idempotent: `src/agents/pyq/graph.py:build_question_bank` →
  `src/core/vector_store.py:upsert_documents` (`store.add_documents(docs)`, no ids), so re-running duplicates chunks.
  `pyq_bank_upload` uses `retries=0` until fixed — MED.
- (Phase 04) `src/core/vector_store.py:create_vector_store` returns any populated collection as-is, so a partial upsert
  stays partial on re-run. `upload_process` uses `retries=0` (Amendment 1). The lecture chat index has the same partial
  behaviour (best effort) — MED.
- (Phase 04, security L3) No per-user active-job cap. The executor queue is unbounded and holds upload bytes in closures.
  A timed-out job's thread keeps a `job_workers` slot until it returns (threads can't be killed) — MED.
- (Phase 04, review round 2 S2) A queued job has no deadline of its own, and a timed-out job's thread keeps its
  worker slot. If every `job_workers` slot is held by a hung dependency call, queued jobs stay `queued` (heartbeated,
  so never recovered) until a slot frees or the process exits. The frontend gives up after 10 min. Fix: fail queued
  jobs older than `job_timeout_seconds` in `_tick`, or stop heartbeating them — MED.
- (Phase 04, security L1) `src/core/job_queue.py:get_job` lets any authenticated user read a job row with
  `user_id IS NULL` (no production caller creates one; recovery-on-read is now owner-only) — LOW.
- (Phase 04) `_is_transient` counts every `sqlalchemy.exc.OperationalError` as transient, including permanent ones
  such as "no such table" (one wasted retry) — LOW.
- (Phase 04) The job lease compares the wall clocks of different processes. On multiple hosts, clock skew above
  `lease - 2*heartbeat` (60 s at defaults) could fail a live job. Postgres timestamptz comparison is UNVERIFIED (tests
  use SQLite) — LOW.
- (Phase 04, Q5) `.env.example` placeholders for `JOB_WORKERS`, `JOB_HEARTBEAT_SECONDS`, `JOB_LEASE_SECONDS`,
  `JOB_RETRY_BACKOFF_SECONDS` were approved, but Claude's permission rules deny reading/editing `.env.example`. The user
  added them by hand at Phase 04 close. Claude could not read the content; it passed `git diff --check`. The user should
  confirm the exact lines — LOW.

- (Phase 05, product feature) **Document-aware Mentor chat**: let a Mentor message target one of the caller's own
  documents (upload / lecture / NCERT) via the CRAG subgraph. Needs a request field, server-built owned keys
  (ADR-007), a frontend picker and ownership tests. Candidate Phase 07 — FEATURE.
- (Phase 05) HITL (`HITL_ENABLED`) would pause planner/evaluator in Mentor chat with no resume path in
  `src/api/routes/chat.py` (documented as unsupported; behaviour with HITL on UNVERIFIED). Keep it off — LOW.
- (Phase 05) Optional `plan_execute` route (`PLAN_EXECUTE_ENABLED`, **off by default**): its answer node
  (`synthesize`) is not in `chat.py:_FINAL_NODES`, so the answer is delivered from the final-state fallback (as
  for every route today — no route is token-streamed, see the MED streaming item below); and it does not store
  the student's question in history (separate item below). Behaviour over HTTP with it enabled is UNVERIFIED — LOW.
- ~~(Phase 05) `agent_subgraphs.py` evaluator Mains branch unreachable from Mentor chat~~ RESOLVED in Phase 05
  review (branch + `evaluate_mains` import removed from the Mentor node; the Answer Evaluator page keeps Mains).
- (Phase 05 review NIT) Stale-answer snapshot: `src/api/routes/chat.py` `generate()` treats the second "values"
  snapshot onward as this turn's state, but the post-supervisor snapshot still carries the PREVIOUS turn's
  `answer`. If a specialist ever ends without an `answer` and streams nothing, the previous reply is re-sent.
  Not triggered today (every specialist sets `answer`). Fix: compare against `prior_answer` or reset `answer` in
  `supervisor_node` — LOW.
- (Phase 05 review, security LOW) Router prompt injection: the student's text reaches the router unfenced
  (`src/graph/supervisor.py:supervisor_node`). Output is limited to the 4-value `RouteDecision` literal, all routes
  are allowed for the caller, and it is the caller's own text, so the impact is at most a different specialist for
  that message. Accepted by design; revisit if a route ever gains privileges — LOW.
- (Phase 05 review) Concurrent test runs interfere: every `uv run pytest` uses the same throwaway SQLite file
  (`tests/conftest.py`: `<tempdir>/upsc_smoke_test.db`, deleted at start). Two suites at once (e.g. a reviewer agent
  and the main session) produced 2 failures + 1 error that did not reproduce alone. Fix: per-run unique DB name
  (e.g. `upsc_smoke_test_<pid>.db`) — LOW.
- (Phase 05 review round 2) `src/graph/plan_execute.py` (`PLAN_EXECUTE_ENABLED`, off by default) returns only
  `AIMessage(answer)`, not the question. With the first-answer rule in `bound_history`, such an answer is an
  orphan and is dropped from later mentor windows (before: paired with the wrong question). Fix: store the
  question like `agent_subgraphs._reply` when plan_execute is enabled — LOW.
- (Phase 05 final review, pre-existing, not a Phase 05 regression) **Mentor chat answers are not shown token by
  token.** `src/api/routes/chat.py` streams the supervisor with `stream_mode=["messages","values"]` and the default
  `subgraphs=False`. Every specialist (mentor tool agent, planner, evaluator, current affairs) is a nested subgraph,
  so LangGraph does not emit their model tokens at this level (`langgraph/pregel/_messages.py`, nested `ns`
  skipped). Planner / evaluator / CA answers arrive as one completed message; mentor answers arrive whole via the
  final-state fallback (the top-level node `mentor` is not in `_FINAL_NODES`; `"agent"` only matches test fake
  graphs). Offline probe (2026-11-02): mentor `streamed_chunks=0`, planner 1 chunk. Impact: user-visible wait until
  generation completes (each model call capped at 45 s). README / `chat.py` docstring corrected at Phase 05 close.
  Not fixed in Phase 05 (needs a streaming design: e.g. `subgraphs=True` + namespace-aware filtering) — MED.
- (Phase 05 final review NIT) `src/api/routes/chat.py:_is_input_message` drops known input types (`human` / `system` /
  `tool` prefixes) instead of allowing only assistant output (`ai` / `AIMessageChunk`); a stricter allow-list would
  also block e.g. `ChatMessage` / `FunctionMessage` (not produced today) — LOW.
- (Phase 05 final review NIT) No test asserts the response-cache CONTENT for chat: `cache.set(answer=...)` is the same
  buffer as the asserted response body, but a fake-cache assertion would pin AC17 directly — LOW.
- (Phase 05 review round 2, security LOW, pre-existing) `src/graph/tools.py` `agent_node`:
  `logger.warning("extra_context failed: %s", exc)` logs the raw exception text. Fix: `describe_exception(exc)` — LOW.

- (Phase 06, deferred by user, risk #9) Mentor-chat response cache (`src/core/response_cache.py`, used by
  `src/api/routes/chat.py`) serves answers for 24 h (+ semantic matches), including time-sensitive web/current-affairs
  answers. Needs a bypass or short TTL for volatile questions — MED.
- (Phase 06, risk #4 kept open by user decision) Without `QDRANT_URL`/`QDRANT_PATH`, every vector path fails with an
  `ImportError` for `langchain_chroma` (not installed). Phase 06 added only a benchmark preflight; app startup is
  unchanged — MED (misconfig).
- (Phase 06) `src/agents/pyq/graph.py:_generate_grounded` puts retrieved PYQ-bank text into `BANK_GEN_PROMPT` without
  `harden_untrusted` (the caller's own uploaded content, so self-injection only) — LOW.
- (Phase 06) PYQ-bank chunks carry no `chunk_index` / `persist_key` metadata (`build_question_bank`), so
  `vector_store._doc_key` de-dup falls back to a content hash — LOW.
- (Phase 06) `src/core/mentor_kb.py:search_kb` and the unreachable `persist_key` branch of
  `src/graph/tools.py:knowledge_base_tool_fn` duplicate search logic outside the shared `similarity_search*` pipeline — LOW.
- (Phase 06) Upload / lecture / PYQ answers carry no source citations (only NCERT and Mentor do) — FEATURE (Phase 07
  candidate).
- (Phase 06) The committed `src/eval/retrieval_eval_report.md` (n=3, keyword-proxy labels that favour the lexical
  re-rank) is not usable as evidence; the Phase 06 baseline in `docs/ENGINEERING_METRICS.md` supersedes it. The
  nightly CI job still regenerates it — LOW (Phase 08).

- (Phase 06, owner decision, ADR-015) **Threshold calibration** for `SIMILARITY_THRESHOLD` (0.3) and
  `MENTOR_KB_THRESHOLD` (0.25). The Phase 06 baseline shows both pass every tested result (top-1 scores 0.75–0.90;
  no-answer false-positive retrieval rate 13/13). Requirements before any change:
  1. A sufficiently large, **independently authored held-out** dataset (not written by the same author as the
     Phase 06 set, and not reused for tuning).
  2. More unanswerable questions, represented **separately per retrieval workflow** (NCERT, PDF, Lecture, PYQ,
     Mentor KB).
  3. Measure both the answerable **false-rejection** rate and the unanswerable **false-positive** rate.
  4. Calibrate on development data and evaluate on held-out data.
  5. **Explicit owner approval** before changing any production threshold.
  The exploratory post-hoc 0.80 value is not approved — MED.
- (Phase 06, deferred, ADR-015) **Cross-encoder reranking and BM25 / sparse lexical retrieval** stay deferred. Measured
  headroom: Recall@10 − Recall@5 = 0.006, below the 0.10 trigger. Revisit only if new evidence (real student queries,
  production collections, NCERT) shows headroom; any implementation needs a spec + ADR (new dependency) — LOW.
- (Phase 06) NCERT retrieval is supported by `scripts/bench_retrieval.py` but **unmeasured** until the owner supplies
  chapters + gold questions/evidence (`src/eval/retrieval_gold.json` → `corpora.ncert`). Real uploads and production
  Qdrant are also unmeasured — OPEN.
- (Phase 06) Gold labels in `src/eval/retrieval_gold.json` are Claude-written with `human_verified: false`. A human
  review is needed before the baseline is used for further decisions — OPEN.
- (Phase 06) `src/core/retrieval.py` module docstring still opens with "Lightweight hybrid retrieval helpers"; the
  step re-orders the same k dense candidates (ADR-015). Docstring wording only; not changed (docs-only Stage 2) — LOW.

- (Phase 06 review NITs, not approved for remediation — recorded only; `scripts/bench_retrieval.py` unless noted) — LOW:
  - Security N1: the usage ledger is written only at the end of a run (non-atomic `write_text`). A hard kill loses
    the charges sent so far. Fix: save after each send via temp file + `os.replace`.
  - Security N2 / code NIT 4: the budget is an accident guard. Deleting the cache, a new `--cache` path or higher
    `--max-*` flags reset or lift it. The fixed `%TEMP%/upsc_retrieval_bench` dir could be pre-created by another
    user on a shared Linux `/tmp`. Pre-ledger cache migration sets tokens to 0.
  - Security N3 / code NIT 1: the post-load isolation re-check in `run_benchmark` runs after the first write. The
    pre-`yield` `_qdrant_local()` check is the real guard; move the re-check before the writes.
  - Security N4: the catch-all labels every runtime error "provider error". `_make_gemini_embeddings()` sits outside
    the guarded block (raw traceback possible; secret exposure UNVERIFIED, judged unlikely).
  - Security N5: `isolated_vector_store` mutates process-wide settings without a lock, so it must never run inside a
    server process (add a docstring warning). It does not reset the Langfuse `lru_cache`. `_mentor_sources` also
    reads untracked topper `.md` files.
  - Code NIT 2: report metadata lacks chunk size / overlap, thresholds, POOL_SIZE, RRF k and a dirty-tree flag
    (`git describe --dirty`).
  - Code NIT 5: `--resamples 0` raises `IndexError`. The bootstrap uses floor percentile indices; document this.
  - Code NIT 6: `validate_dataset` does not check empty questions or the grade range/type.
  - Code NIT 7 (for the owner's label review): weak gold quotes — "Denser content:", "They fixed a topic daily",
    "but has no right to vote", "The MPC has six members", "What is stagflation?", "Lothal had a dockyard.".
    `normalize_span` does not strip `_` emphasis.
  - Code NIT 8: plan function names differ from the code. The runner uses private vector-store / mentor-KB APIs and
    replicates PDF / Lecture / PYQ chunking.
  - Code NIT 9: `docs/ENGINEERING_METRICS.md` says the "nothing relevant" guard in `ask_ncert` never fires, but NCERT
    was not measured. Mentor "production" latency includes `kb_exists()` + `load_vector_store`. The dry-run CLI test
    reads the developer's `.env`.
  - ~~Line endings~~ RESOLVED in the final remediation (`.gitattributes`, evaluation assets only). Original note:
    Git warned that the corpora / gold JSON (LF) would become CRLF on Windows checkouts
    (`core.autocrlf`). That changes their sha256 and possibly the chunking, so recorded hashes would not reproduce on
    such checkouts. Fix: a `.gitattributes` rule (`src/eval/corpora/* -text`, `src/eval/*.json eol=lf`) — needs
    approval.
  - Leftover temp dirs from runs before the S2 fix (`%TEMP%/upsc_bench_retrieval_*`, 9 seen) were left in place on
    purpose. Delete them by hand if wanted.

- (Phase 06 review round 2 NITs, not approved for remediation — recorded only) — LOW:
  - Security N-a: `resolve_corpus_path` accepts Windows device names such as `src/eval/corpora/NUL` (`exists()`
    rather than `is_file()`). `CONIN$` / `COM1` could hang the operator's own run. Fix: require `is_file()`.
  - Security N-b: hard links are not detectable, and there is a small race between validation and read
    (needs local write access). Optional fix: compare `os.fstat` of the opened handle.
  - Security N-c: `network_blocked()` patches only `socket.socket.connect`. `connect_ex`, DNS, UDP, Proactor
    `ConnectEx`, gRPC C-core and subprocesses are not covered. The docs now say so. Not exploitable today.
  - Security N-d: cleanup uses `rmtree(ignore_errors=True)` and a suppressed `client.close()`, so a failed
    cleanup is silent. Suggest a warning when the dir survives.
  - Security N-e: NCERT corpus errors may not be caught by `except (CorpusPathError, ValueError)` (raw
    traceback). Inactive while `chapters: []`; re-review before NCERT chapters are added (`read_chapter_pages`
    may reach R2).
  - Code NIT 1: in the greedy fallback (> 8 distinct relevant chunks) the ideal can be beaten, so the
    `min(1.0)` cap is not inert there. The "no cap is needed" docstring overstates this. The current data has at
    most 3 sets. `measure_query` without `corpus_texts` silently falls back to the legacy NDCG under the `ndcg@k`
    key.
  - Code NIT 2: the `SHARED_PIPELINE` `k_note` lists "mentor_kb 4", and the "(combined for mentor KB)" label is
    also printed in non-mentor groups.
  - Code NIT 3: two verdicts within ~0.001 of 0 (pooled RRF MRR / NDCG) flip with the resample count (2k/5k vs
    10k/20k). Mark such verdicts inconclusive in the docs.
  - Code NIT 4: the corrected section lacks the top-10 row (RRF vs dense NDCG@10 +0.032, CI [+0.002, +0.063]).
  - Code NIT 5: the exit-6 message says "Cache and usage ledger saved" in modes that do not save
    (`--cache-only`, fake mode without `--cache`).
  - Code NIT 6: `--validate-only` without `--fake-embeddings` runs the live preflight (exits 2 without a key),
    although the docs describe it as "no embeddings".
  - Code NIT 7: test gaps — no unwritten-cache assertion in `test_exit_7_cache_miss_during_run`; no unit test for
    `_KEEPS_NONE` / None handling in `_production_expected`; two run_benchmark tests do not block sockets.
  - (Round-1 code NIT 3, temp-dir cleanup, is not listed because it was fixed as S2.)

- (Phase 06 final review round NITs, recorded at close; not fixed by design) — LOW:
  - Security NIT-1: approved roots are compared after resolving them. If `data/mentor_kb` (or `data/`) were a link to
    another folder inside the repo, files there would be accepted. A link to outside the repo would end the run with
    a raw `ValueError` traceback (fails closed). Fix: also require the resolved root to lie inside
    `REPO_ROOT.resolve()` and raise `CorpusPathError`.
  - Security NIT-2: the `_mentor_sources` docstring says "no file outside `data/mentor_kb/` is ever read". That is too
    absolute given hard links and the validation/open race. Reword to "validated before it is opened".
  - Code N1: in the phase report, the threshold and latency bullets sit under the Stage 1 (prior) subsection, although
    the threshold findings are unchanged in the corrected run.
  - Code N2: the `mentor_kb_file` docstring says links are "NOT followed before the check". The containment check
    itself resolves links; reword.
  - Code N3: the script docstring says the temp dir "is removed" (removal is best-effort), and its title still says
    "Stage 1: baseline only".
  - Code N4: `test_mentor_kb_rejects_a_linked_directory_escape` swaps `builtins.open` by hand (restored correctly);
    use `monkeypatch.setattr`.
  - Code N5: `test_mentor_kb_valid_regular_files_are_ingested_unchanged` checks only the hash dict's keys, not the
    hash value.
  - Code N6: unlabelled "headroom 0.006" (it is Recall@10 − Recall@5; production-k headroom is 0.012 pooled / 0.028
    mentor KB) in ADR-015, this file and the metrics limitations. The metrics index row "Offline test suite size"
    still says Phase 05 `1212 passed`. Plan Amendment 4 is listed before Amendment 3.
  - Mentor KB hash reproducibility: `data/mentor_kb/` is outside the LF `.gitattributes` rule, so the recorded
    mentor_kb hash can differ across checkouts (chunking unaffected). Extending the rule needs owner approval.

### Last handoff — 2026-11-03 (Phase 06 closed)
- Active phase: none in progress. **Phase 06 RAG quality — DONE (2026-11-03).** Report:
  `docs/phase-reports/phase-06-report.md`. ADR-015 (+ addenda). Phase 07 not started (wait for the user).
- Outcome: retrieval measured with evidence-span labels. **No production retrieval change**: thresholds, ranking,
  lexical/RRF re-order, k, chunking and indexing are unchanged. Threshold calibration, reranker and BM25 stay in the
  Backlog.
- Work done:
  - Stage 1: baseline benchmark + dataset.
  - Stage 2: docs-only closeout.
  - Round-1 review remediation (S1, S2, Q1–Q6).
  - Final remediation (SF-1, S-1, S-2, doc accuracy, LF `.gitattributes` for the evaluation assets only).
  - Close: two documentation NITs (mentor KB hash reproducibility wording; stale SQLite note).
- Final verified evidence (2026-11-03):
  - `uv run pytest -q` → `1272 passed, 2 skipped, 13 warnings in 151.97s (0:02:31)`.
  - `uv run pytest -q tests/test_bench_retrieval.py tests/test_retrieval_eval.py` → `68 passed, 2 skipped`.
  - `uv run ruff check src tests scripts/bench_retrieval.py` → `All checks passed!`; compileall exit 0;
    `git diff --check` exit 0.
  - `scripts/bench_retrieval.py --cache-only --repeats 5` → exit 0, 0 texts sent, cache sha256 unchanged.
  - Final reviews: security PASS (0 BLOCKING, 0 SHOULD-FIX); code quality no BLOCKING / no SHOULD-FIX. NITs are in
    the Backlog.
- Still UNVERIFIED: NCERT retrieval (owner-supplied gold set pending), real user uploads, production Qdrant
  collections and server latency, live workflow smoke. Gold labels are Claude-written: all 97 entries
  `human_verified: false` pending human review. The 2 skipped tests are file-symlink escape tests that need Windows
  symlink privilege.
- Repo hygiene: the `checkpoints.sqlite-shm` / `checkpoints.sqlite-wal` side files seen earlier in this session are
  no longer present. Only the git-ignored `checkpoints.sqlite` remains (not opened by Claude).
- Next step: the owner reviews and, if wanted, commits Phase 06. Then `/start-phase 7` when the owner asks.
- Uncommitted changes:
  - Modified: `.claude/specs/README.md`, `.claude/specs/phase-06-rag-quality.md`, `docs/DECISIONS.md`,
    `docs/ENGINEERING_METRICS.md`, `docs/PROJECT_STATE.md`, `src/eval/retrieval_eval.py`,
    `tests/test_retrieval_eval.py`.
  - Untracked: `.gitattributes`, `.claude/plans/phase-06-rag-quality.md`, `docs/phase-reports/phase-06-report.md`,
    `scripts/bench_retrieval.py`, `src/eval/corpora/`, `src/eval/retrieval_gold.json`,
    `tests/test_bench_retrieval.py`.
  - Nothing committed or pushed.
