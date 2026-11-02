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
| 05–09 | See `.claude/specs/README.md` | STUB |

## The 8 workflows (must not regress)
| Workflow | Backend entry | Frontend | Status (last verified) |
|---|---|---|---|
| AI Mentor | `src/api/routes/chat.py` `POST /api/v1/agent/chat/stream` | `features/mentor/MentorChat.tsx` | Code present; runtime UNVERIFIED; KB tool limited to mentor KB (Phase 02); offline smoke PASS 2026-11-02; prompt bounded to current turn + 6 earlier Q/A turns (Phase 03) (Phase 03 close) |
| NCERT Study | `src/api/routes/ncert.py` | `features/ncert/Ncert.tsx` | Code present; needs R2 or local PDFs; runtime UNVERIFIED; input bounds (Phase 02); offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed) |
| PYQ + Personal PYQ Bank | `src/api/routes/pyq.py` | `features/pyq/Pyq.tsx` | Code present; runtime UNVERIFIED; PDF validation + cross-user regression test (Phase 02); offline smoke PASS 2026-11-02; parse/bank status/clear off the event loop, bank `sync=true` offloaded (Phase 03); bank upload job: heartbeat lease, deadline, no auto-retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close) |
| Answer Evaluator | `src/api/routes/evaluator.py` | `features/evaluator/Evaluator.tsx` | Code present; runtime UNVERIFIED; offline smoke PASS 2026-11-02; `*/sync` endpoints off the event loop (Phase 03) (Phase 03 close) |
| Current Affairs | `src/api/routes/current_affairs.py` | `features/current_affairs/CurrentAffairs.tsx` | Code present; live web; runtime UNVERIFIED; offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed) |
| Study Planner | `src/api/routes/planner.py` | `features/planner/Planner.tsx` | Generates plans; not persisted; offline smoke PASS 2026-11-02; `generate/sync` off the event loop (Phase 03) (Phase 03 close) |
| Lecture Learning | `src/api/routes/lecture.py` | `features/lecture/Lecture.tsx` | Code present; YouTube reachability UNVERIFIED; pasted transcripts per user (old pastes must be re-pasted); offline smoke PASS 2026-11-02; `process`/`process-text` off the event loop (Phase 03); lecture jobs: heartbeat lease, deadline, 1 transient retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close) |
| PDF / Document Chat | `src/api/routes/upload.py` | `features/upload/Upload.tsx` | Per-user ownership (Phase 02; old uploads must be re-uploaded); runtime UNVERIFIED; offline smoke PASS 2026-11-02; upload `sync=true` offloaded (Phase 03); upload job: heartbeat lease, deadline, no auto-retry (Phase 04 Amendment 1); offline smoke PASS 2026-11-02 (Phase 04 close) |

Test suite status (2026-11-02, Phase 04 close): `1182 passed, 13 warnings in 128.48s (0:02:08)` (Phase 03 close: `1113 passed`;
Phase 02 close: `1086 passed`;
Phase 01 close: `930 passed`).
Engineering metrics (synthetic request-path benchmark, how to rerun, UNVERIFIED production metrics):
`docs/ENGINEERING_METRICS.md`; harness `scripts/bench_request_path.py` + `scripts/bench_server.py`.
Ruff: `All checks passed!`. compileall exit 0. Frontend unchanged in Phase 03 (last build/lint clean at Phase 02 close).
Live workflow smoke (`docs/SMOKE_CHECKLIST.md`): not run — UNVERIFIED.

## Verified architecture facts
- Backend root is `src/` (ADR-001). API prefix `/api/v1`; all feature routers JWT-protected in `src/api/main.py`.
- "Supervisor" (`src/graph/supervisor.py`) = one structured LLM routing call → one of mentor / planner /
  evaluator / current_affairs / rag. No parallel or multi-step orchestration.
- The `rag` route is unreachable over HTTP (no route sets `persist_key`); CRAG (`src/graph/rag_graph.py`) only
  runs in `src/eval/llm_eval.py`.
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
| 4 | Vector stack fails without Qdrant (Chroma not installed) | HIGH (misconfig) | 06 |
| 5 | Unbounded inputs → CLOSED Phase 02 (field + 1 MiB body limits, ADR-009); checkpoint history growth: mentor *prompt* bounded in Phase 03 (6 earlier turns); stored checkpoint still grows | MED | 03 (prompt ✓) / later (storage) |
| 6 | Auth rate limit → CLOSED Phase 02 (ADR-010, refresh excluded); account enumeration (register message, no dummy bcrypt) still open | MED | backlog |
| 7 | Tokens in `localStorage` | MED | backlog |
| 8 | ~~Upload size check relies on Content-Length; unbounded `_processed_cache`~~ CLOSED Phase 02 (byte-counted limits, bounded thread-safe per-user cache); upload memory ~2×50 MB/request open | MED | 03/04 |
| 9 | 24 h semantic cache can serve stale time-sensitive answers | MED | 06 |
| 10 | Eval gate tests CRAG path production never runs | MED | 08 |
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

### Last handoff — 2026-11-02
- Active phase: none in progress. Phase 04 Durable background jobs — DONE (spec DONE, plan DONE with Amendments 1–2
  approved by the user; report `docs/phase-reports/phase-04-report.md`; ADR-013). Next: Phase 05 — spec STUB; not
  started (wait for the user).
- Done this session:
  - Phase 04 end to end, test-first, with pre-fix failures recorded in the report.
  - `src/core/job_queue.py` (heartbeat lease, lease-based and owner-only on-read recovery, conditional transitions,
    transient allow-list, retry capped at 3, deadline, graceful shutdown that keeps running jobs heartbeated).
  - `config.py` (4 settings), `job_handlers.py`, `src/api/main.py` (lifespan shutdown).
  - `routes/pyq.py` and `routes/upload.py`: `retries=0`.
  - `tests/test_job_queue_durability.py` (69 tests).
  - `scripts/bench_jobs.py`; `docs/ENGINEERING_METRICS.md` (jobs section); ADR-013; report.
  - Two review rounds per reviewer.
  - The user added the `.env.example` placeholders by hand.
- Verified:
  - `uv run pytest -q` → `1182 passed, 13 warnings in 128.48s (0:02:08)` (also 125.24 s and 126.66 s runs).
  - focused → `215 passed, 3 warnings in 41.05s`.
  - `ruff check src tests` and `ruff check scripts/bench_jobs.py` → `All checks passed!`. compileall exit 0.
    `git diff --check` exit 0.
  - `scripts/bench_jobs.py` → `ALL PASS: True` (5 runs).
  - All 14 ACs MET.
- Not verified:
  - `.env.example` content: reading or diffing it is denied to Claude.
  - Postgres lease comparison; Render deploy overlap; `WEB_CONCURRENCY>1` under HTTP load; live smoke.
  - Frontend build (frontend unchanged).
- In progress / next step: the user confirms `.env.example` with `git diff -- .env.example`, reviews and (if wanted)
  commits Phase 04; then `/start-phase 5` when the user asks.
- Open questions for the user: none (only the `.env.example` self-check above).
- Uncommitted changes:
  - Modified: `.env.example` (by the user), `src/` 6 files, spec, `.claude/specs/README.md`,
    `docs/{DECISIONS,ENGINEERING_METRICS,PROJECT_STATE}.md`.
  - Untracked: plan, `docs/phase-reports/phase-04-report.md`, `scripts/bench_jobs.py`,
    `tests/test_job_queue_durability.py`.
  - Nothing committed or pushed.
