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
| 07 | LLM evaluation, safety & quality — `.claude/specs/phase-07-llm-eval-safety.md` | DONE (2026-11-03) — offline T1/T2 evals + prompt fencing fixes F0–F7 + budget-gated live harness; T3 live NOT RUN (bounds unvalidated, AC12 open by design; ADR-016) — `docs/phase-reports/phase-07-report.md` |
| 08 | Product persistence & gaps — `.claude/specs/phase-08-product-gaps.md` (APPROVED rev 4) + `.claude/plans/phase-08-product-gaps.md` | **COMPLETE (2026-11-04)** — all slices **A (+ A-1), B (+ B-1), C1, C2 CLOSED**; migrations 0005 / 0006 written but **never executed against production or any real DB** (owner deploy steps UNVERIFIED); spec/plan APPROVED, ADR-017; slices A → B → C1 → C2, each approved separately; **Slice A + Amendment A-1 CLOSED (2026-11-03)** — verified offline, code-quality review findings S1 / NIT 1 / NIT 2 fixed, final review no BLOCKER / SHOULD-FIX, owner manual browser checks all PASS — `docs/phase-reports/phase-08-slice-a-report.md`; separate frontend polish: 17 literal Unicode-escape UI fixes (browser-verified); **Slice B CLOSED (2026-11-03)** — migration 0005 written (never run by Claude against a real DB), 4 `/planner/plan` endpoints, atomic upsert, Planner reconciliation + unsaved-draft preservation (Amendment B-1); full suite `1642 passed, 2 skipped`, closure focused gates `181 passed`; security review no CRIT/HIGH/MED (LOW #1 fixed); code-quality S1/S2 fixed; all 9 owner browser checks PASS — `docs/phase-reports/phase-08-slice-b-report.md`; **Slice C1 CLOSED (2026-11-04)** — 6 owner-confirmed browser checks PASS (isolated env; check 2 + DB row independently verified); migration 0006 `evaluation_attempts` (never run by Claude against a real DB), optional allow-listed subject on Basic/Mains, best-effort recording; full suite `1706 passed, 2 skipped`; security review no CRIT/HIGH/MED; code-quality no BLOCKER/SHOULD-FIX — `docs/phase-reports/phase-08-slice-c1-report.md`; **Slice C2 CLOSED (2026-11-04)** — no migration; Average Evaluation Score, subject mastery, weak subjects, subject-based revision on `/progress/overview` (additive), Dashboard cards/chart/lists, Planner autofill = weak subjects first then profile `weak_areas` (Amendment C2-1); full suite `1765 passed, 2 skipped`, focused `162 passed`; security review no CRIT/HIGH/MED (LOW rollback fixed); code-quality no BLOCKER/SHOULD-FIX; all 8 owner browser checks PASS (isolated env; check 6 independently verified, check 8 browser request not captured) — `docs/phase-reports/phase-08-slice-c2-report.md` |
| 09 | Operations — `.claude/specs/phase-09-operations.md` | STUB — **NOT STARTED** (needs explicit owner approval) |

## The 8 workflows (must not regress)
| Workflow | Backend entry | Frontend | Status (last verified) |
|---|---|---|---|
| AI Mentor | `src/api/routes/chat.py` `POST /api/v1/agent/chat/stream` | `features/mentor/MentorChat.tsx` | Code present; runtime UNVERIFIED; KB tool limited to mentor KB (Phase 02); offline smoke PASS 2026-11-02; prompt bounded to current turn + 6 earlier Q/A turns (Phase 03) (Phase 03 close); every message routed afresh — the first message's route no longer sticks to the conversation; supervisor has 4 routes, no `rag`; specialists store the question once, mentor history = coherent Q/A pairs; replies never echo the question (Phase 05, ADR-014); answers arrive whole, not token by token (pre-existing, backlog MED); offline smoke PASS 2026-11-02 (Phase 05 close); student profile fenced (Phase 07 F4); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close); conversation id persisted across reloads ("New chat" race guarded), stream notices incl. specialist failure fallbacks never saved as answers (Phase 08 Slice A + A-1); owner browser checks PASS 2026-11-03 (reload continuity, New Chat, in-flight save race, specialist failures not in Session History) |
| NCERT Study | `src/api/routes/ncert.py` | `features/ncert/Ncert.tsx` | Code present; needs R2 or local PDFs; runtime UNVERIFIED; input bounds (Phase 02); offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed); study aids fenced (Phase 07 F7); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close) |
| PYQ + Personal PYQ Bank | `src/api/routes/pyq.py` | `features/pyq/Pyq.tsx` | Code present; runtime UNVERIFIED; PDF validation + cross-user regression test (Phase 02); offline smoke PASS 2026-11-02; parse/bank status/clear off the event loop, bank `sync=true` offloaded (Phase 03); bank upload job: heartbeat lease, deadline, no auto-retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close); bank context + parser input fenced (Phase 07 F1); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close) |
| Answer Evaluator | `src/api/routes/evaluator.py` | `features/evaluator/Evaluator.tsx` | Code present; runtime UNVERIFIED; offline smoke PASS 2026-11-02; `*/sync` endpoints off the event loop (Phase 03) (Phase 03 close); answer/question fenced, parsed score bounded to [0, max] (Phase 07 F5/F6); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close); failure fallbacks are stream notices (Phase 08 Slice A-1, text unchanged after the prefix); Basic/Mains attempts recorded with an optional subject (Phase 08 Slice C1, migration 0006) and shown as Dashboard analytics (Slice C2); owner browser checks PASS 2026-11-04 |
| Current Affairs | `src/api/routes/current_affairs.py` | `features/current_affairs/CurrentAffairs.tsx` | Code present; live web; runtime UNVERIFIED; offline smoke PASS 2026-11-02 (Phase 03 close; only the rate limiter changed); eval-only in Phase 07 (no app change); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close); failure fallbacks are stream notices (Phase 08 Slice A-1, text unchanged after the prefix) |
| Study Planner | `src/api/routes/planner.py` | `features/planner/Planner.tsx` | Generates plans; latest plan persisted server-side per user, unsaved generated drafts preserved (Phase 08 Slice B + B-1, migration 0005; owner browser checks PASS 2026-11-03); offline smoke PASS 2026-11-02; `generate/sync` off the event loop (Phase 03) (Phase 03 close); eval-only in Phase 07 (no app change); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close); blank weak areas filled from the caller's Mentor profile, fail-open (Phase 08 Slice A; owner browser check PASS 2026-11-03); blank weak areas now use the caller's evaluation weak subjects first, then profile `weak_areas`, each source fail-open (Phase 08 Slice C2; owner browser check PASS 2026-11-04) |
| Lecture Learning | `src/api/routes/lecture.py` | `features/lecture/Lecture.tsx` | Code present; YouTube reachability UNVERIFIED; pasted transcripts per user (old pastes must be re-pasted); offline smoke PASS 2026-11-02; `process`/`process-text` off the event loop (Phase 03); lecture jobs: heartbeat lease, deadline, 1 transient retry (Phase 04); offline smoke PASS 2026-11-02 (Phase 04 close); chat + notes metadata fenced, `medium` allow-listed, study aids fenced (Phase 07 F3/F7); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close) |
| PDF / Document Chat | `src/api/routes/upload.py` | `features/upload/Upload.tsx` | Per-user ownership (Phase 02; old uploads must be re-uploaded); runtime UNVERIFIED; offline smoke PASS 2026-11-02; upload `sync=true` offloaded (Phase 03); upload job: heartbeat lease, deadline, no auto-retry (Phase 04 Amendment 1); offline smoke PASS 2026-11-02 (Phase 04 close); chat + notes metadata fenced (Phase 07 F2); offline suite + T1/T2 evals PASS 2026-11-03, real-model UNVERIFIED (Phase 07 close) |

Test suite status (2026-11-04, Phase 08 Slice C2 / Phase 08 close): full suite `1765 passed, 2 skipped, 19 warnings in 213.62s` (last full run); focused rerun after two post-suite edits `162 passed`; ruff clean; compileall 0; frontend build + lint + JSX escape check clean. Phase 08 Slice C1: `1706 passed, 2 skipped`. Phase 08 Slice B close: full suite `1642 passed, 2 skipped, 16 warnings in 246.17s`; closure focused gates `181 passed`; ruff clean; compileall 0; frontend build + lint clean. Phase 08 Slice A: `1566 passed, 2 skipped, 13 warnings in 173.67s`; ruff clean;
compileall 0; frontend build + lint clean. Phase 07 close: `1520 passed, 2 skipped in 163.72s`. Offline evals (Phase 07): T1 33 pass /
0 fail / 1 known gap, T2 72 pass / 0 fail; T3 live NOT RUN. Phase 06 final remediation: `1272 passed, 2 skipped, 13 warnings` (the 2 skips are
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
| 10 | Eval gate tests CRAG path production never runs — PARTLY CLOSED Phase 07: offline T1/T2 workflow evals now run the 8 production functions (`evals/`, ADR-016); Phase 06 retrieval benchmark (ADR-015). Still open: the old `src/eval/` CRAG gate is unchanged, and real-model safety/quality/cost is UNVERIFIED until the owner validates the token bounds and runs T3 | MED | backlog (T3 owner run; `src/eval/` migration) |
| 11 | In-memory store fallback when not Postgres | MED | 09 |
| 12 | Migrations run per process (concurrent runners now serialized by a PostgreSQL advisory lock, ADR-018; still run twice per boot); LangGraph tables outside Alembic | LOW–MED | 09 |
| 13 | `/health` checks nothing; no readiness/backups | MED | 09 |
| 14 | Exception text leaked; PII in logs → CLOSED Phase 02 for touched files (ADR-008); repo-wide log sweep + public `/docs` open | LOW | 09 |
| 15 | Dead/duplicate code; unused deps/settings | LOW | 09 (kept per ADR-006) |
| 16 | Non-blocking CI lint; no frontend tests; live smoke disabled | LOW | 09 |
| 17 | Compose: unpinned qdrant, default creds, exposed ports, unused redis | LOW | 09 |
| 18 | ~~Supabase grants / RLS for the new tables~~ CLOSED in code (ADR-019): migrations 0005 / 0006 enable RLS (no policies) and revoke the API roles; verified on PostgreSQL 15.19 / 17.11 staging. The owner confirmed RLS is already on for all 14 existing public tables. Open: the first run on real Supabase (post-deploy SQL checks, `docs/phase-reports/phase-08-release-preflight.md` §E) | MED | at first deploy |
| 19 | Release/rollback: an image with migration 0006 applies it at boot; an older image cannot boot against a newer revision; downgrade is destructive; dashboard "Rollback" would fail. Runbook: preflight §B–C | MED | before first deploy |

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

- (Phase 07) **Scope gate misses creative-writing requests.** `src/core/scope_gate.py`: "Write a song about cricket
  for my friend's birthday." and "write a poem about my cat" are ALLOWED (`reason=default`). `_CREATIVE_RE` only covers
  shayari / love story / bedtime story, and the singing patterns need sing / gao. The mentor system prompt's scope rule
  is the only remaining defence (real-model behaviour UNVERIFIED). Eval case `rt-scope-song` reports it as
  `known_gap` — MED.
- (Phase 07, ADR-016) **Token-bound validation procedure + T3 live run.** `evals/pricing.json` ships every model
  `token_bound.status: "unvalidated"` with null prices, so live mode refuses. An owner-approved procedure is needed
  to validate the `utf8_bytes` bound per model, e.g. compare provider-reported usage or `count_tokens` on a small
  approved sample. After that the owner can fill in prices and authorise T3. Real-model safety, quality and cost stay
  UNVERIFIED until then — OPEN.
- (Phase 07) `evals/pricing.json` declares only the code-default models. A deployment configured with
  non-default model ids is refused by the live gate as "not declared" until the owner adds entries for them — LOW.
- (Phase 07) Live-mode client preflight reads `max_retries` from the raw Gemini embeddings client. Whether
  `GoogleGenerativeAIEmbeddings` exposes it, and whether Gemini streaming responses carry `usage_metadata` (exit 4
  otherwise), is UNVERIFIED without keys. Either would make live mode refuse or stop: fail-closed, but it may need
  work before T3 — LOW.
- (Phase 07 review, owner decision D) Recorded, not fixed — LOW:
  - `evals/runners/run_production_evals.py` builds `_judge_bounds` twice per estimate (`build_estimate` and
    `worst_single_input_bound`).
  - `evals/runners/adapters.py:TOOL_AGENT_WORST_CASE_CALLS = 4` duplicates `src.graph.tools.DEFAULT_MAX_TOOL_LOOPS + 1`.
  - A hard kill, or a locked file on Windows, can leave the run's `%TEMP%/upsc_evals_*` Qdrant dir behind. The live
    embedding cache (`%TEMP%/upsc_evals/embedding_cache.json`) persists vectors of corpus / KB text between runs.
  - Groq fallback live runs (`--allow-fallback`) always refuse: `src/core/llm.py:_make_groq` sets no output limit and a
    hard-coded `max_retries`, and `run_models` ignores `groq_fallback_models`.
- (Phase 07 final review NITs, recorded, not fixed) — LOW:
  - An unreserved send is latched as `BudgetExceeded` (exit 3). It is really a coverage / config failure, so exit 2
    would be more accurate (`evals/runners/budget.py:require_reservation`).
  - `validate_live_config` checks single-request feasibility for the LLM caps only, not `--max-embedding-*`. The
    runtime guard still enforces them.
  - `worst_single_input_bound` is the largest prompt seen in the scripted pass; later tool-loop prompts can be
    larger. The pre-run check only; runtime reservations use the real bytes.
  - `JUDGE_TAG` is defined twice (`evals/evaluators/judge.py`, `evals/runners/budget.py`), with a "must equal"
    comment.
  - AC1 "reached" is the string the adapter returns after its call. The T1 prompt checks are the real evidence that
    the prompts were built.
  - `src/agents/lecture/graph.py` lines ~193-195: an unrelated `logger.warning` was rewrapped by `ruff format`.
  - Live mode leaves non-LLM keys (`DATABASE_URL`, `QDRANT_API_KEY`, `TAVILY_API_KEY`, `COHERE_API_KEY`) in the
    environment. No live-reached path uses them.
  - The report-excerpt capture relies on `_plain_scored_search`. The `similarity_search` fallback branch
    (`src/core/vector_store.py` ~760) is not captured.
  - The lecture `medium` request field has no `max_length` (`src/schemas.py:162,177`). It is now harmless (allow-list).
  - `sanitize_untrusted` runs at roughly 0.5-1 MB/s in pure Python. That is fine for the current bounded inputs;
    don't run it over whole PDFs.
  - The live embeddings preflight reads `GoogleGenerativeAIEmbeddings.max_retries`, which defaults to None, so live
    mode always refuses today. `_with_request_timeout` rebuilds the client without retry options. Verify the real
    retry behaviour before T3.
  - Cyrillic / other-script homoglyph markers are not neutralised (documented).
  - (Round-3 N-3, SUPERSEDED by the final budget review: one managed reservation now covers one send; see ADR-016)
    A send that carries a run manager may be checked any number of times while its run is open. That
    is safe for today's clients: SDK retries happen inside one `_generate` and are reserved as `1 + retries`. A
    provider wrapper that re-sends from outside the SDK would not be counted. A client whose `_stream` calls
    `self._generate(run_manager=None)` is refused (fail-closed).
  - (Round-3 N-4) The two detectors disagree on a marker word split by a line break or (detector side) a combining mark
    (`UNTRUS` + newline + `TED`). Production removes it; the eval detector does not count it, so such a production regression
    would not show in evals.
  - (Round-3 N-5) "...the END. UNTRUSTED sources?" is treated as a bare core marker by both detectors and becomes
    "...the [marker removed]ources?" (rare study-text false positive). Since round 4 a replacement next to brackets
    is limited to a few words, so a false positive can no longer delete long passages.
  - (Round-5, accepted residuals of the word-bounded design; no exact marker can be produced):
    - Three or more padding words between the bracket run and a glued marker word pass both the sanitiser and the
      eval detector (`<<<aa bb cc xBEGIN_UNTRUSTED_SOURCE`). Widening the window would bring back the round-3 study-text
      deletion.
    - A noise word between the marker words (`<<<BEGIN x UNTRUSTED x SOURCE>>>`, `<<<END OF THE UNTRUSTED SOURCE ...>>>`)
      is not neutralised by either; paraphrases are unbounded.
    - The eval detector drops non-ASCII bracket look-alikes (`‹ ›`, `ᐸ ᐳ`, `≪ ≫`), so around them it can over-count
      (spurious eval failures, never a missed production marker; 0 disagreements on an ASCII fuzz).
    - The eval detector counts one hit per bracket segment; a second fragment in the same segment is not counted
      separately.
    - `LLMBudgetCallback` keeps entries for a run that never ends (an abandoned stream generator); negligible for one
      eval run.
  - (Round-4) Bracket-like letters beyond the mapped set (e.g. other scripts' angle-shaped letters) are not
    neutralised when glued to the marker words. Cyrillic / other-script homoglyph letters remain uncovered
    (documented).
- (Phase 07) Unexplained: one offline CLI run (`run_production_evals` with stdout and stderr discarded) exited 5
  (execution error). Three identical re-runs exited 0, and the in-process `_run` succeeded. The cause is unknown
  because the stderr line naming the exception class was discarded — UNVERIFIED, LOW.
- (Phase 07 review) Production usage tracking (`src/core/usage_tracker.py`) misses tool-bound and structured-output
  model calls (mentor tool rounds, router). Callbacks bound with `with_config` are dropped by `bind_tools` /
  `with_structured_output` (`src/core/llm.py:_with_tracing`). The eval live path re-attaches them; production does
  not — LOW.
- (Phase 07) Planner form fields (`goal`, `weak`, `optional`) and the mentor-KB `kb_context` reach `PLANNER_PROMPT`
  unfenced (`src/agents/planner/graph.py:generate_plan`). These are the caller's own text plus first-party KB, so
  self-injection only. The eval keeps an output-only canary check (`rt-planner-injection`) — LOW.
- (Phase 07) `src/agents/evaluator/graph.py:_log_eval` still logs the raw, unbounded `Score: X/Y` to observability.
  F6 bounds only the parsed API field — LOW.
- (Phase 07) The eval harness records retrieved-chunk hashes through `vector_store._plain_scored_search`.
  `mentor_kb.search_kb` queries the store directly, so mentor-KB chunks are not captured; only the injected test
  passage is — LOW.
- (Phase 07) All 60 eval cases are Claude-written (`human_verified: false`). Review them by hand before using them
  as evidence for decisions — OPEN.

- (Phase 08 audit, deferred by owner — Slice D) Session history has no delete / clear / pagination (list capped at
  50, `src/core/history.py:list_conversations`); a past conversation cannot be resumed in the Mentor (no thread id on
  `conversations`); uploads / pasted lectures have no server-side "my documents" list or delete, so after sign-out
  they are reachable only by re-uploading (`src/agents/upload/graph.py`, `src/agents/lecture/graph.py`) — FEATURE / MED (privacy).
- (Phase 08 audit) Study plan **history** (more than the latest plan) is out of scope by ADR-017 — FEATURE.
- (Phase 08 audit) Mentor history is written by the browser after the stream (`MentorChat.tsx:persist`); a closed tab
  mid-answer leaves the checkpoint without a history row. Server-side persistence would change the chat contract — MED.
- (Phase 08 audit) Progress streak / activity days are UTC (`src/api/routes/progress.py`); IST activity between
  00:00 and 05:30 counts on the previous day — LOW.
- (Phase 08 audit) Experiments dashboard returns `experiments: []` (`src/api/routes/experiments.py`, admin-only);
  implement or hide later — LOW.
- (Phase 08 audit) `SaveMessageRequest.role` accepts any string (`src/api/routes/history.py`); extends the existing
  max_length backlog item — LOW.
- ~~(Phase 08 Slice A) `generate_plan` failure text lacks the `"\n\n"` of `STREAM_NOTICE_PREFIX`~~ RESOLVED in
  Slice A Amendment A-1: planner / evaluator / CA failure fallbacks use `src/core/stream_notices.py`.
- (Phase 08 Slice A review) Mentor chat's response cache stores and replays specialist failure text
  (`src/api/routes/chat.py` `cache.set` after the stream; no failure filter in `src/core/response_cache.py`). The same
  question gets "⚠️ Plan generation failed" again until the entry expires. Out of Slice A scope (no cache redesign) — MED.
- (Phase 08 Slice A) `MentorChat` reads `conversationId` from the render that started `send()`; a second message
  sent before the first exchange's save returns can still start a new conversation (same race as the old ref).
  The "New chat" race is fixed (A-1) — LOW.
- (Phase 08 final Slice A review) `src/agents/current_affairs/graph.py` logs raw exception text in its daily /
  editorial / monthly failure handlers (`logger.error(f"... failed: {e}")`). Pre-existing; Slice A changed only the
  yielded notice. Fix: `describe_exception(e)` like the evaluator / planner — LOW.
- (Frontend polish 2026-11-03) `frontend/scripts/check-jsx-escapes.mjs` resolves its own path with
  `new URL(import.meta.url).pathname`, which breaks on checkout paths with spaces / non-ASCII (percent-encoded);
  use `fileURLToPath`. The check is manual (`node scripts/check-jsx-escapes.mjs`); wiring it into
  `package.json` / CI needs owner approval — LOW.
- (Frontend polish 2026-11-03) `frontend/README.md` contains 31 literal `\uXXXX` escapes (renders wrongly on
  GitHub; documentation only, not UI) — LOW.

- (Phase 08 Slice B security review LOW #2) `src/api/routes/planner.py` GET / PATCH / DELETE `/planner/plan`: DB
  errors return the global fixed-message 500 instead of 503 (spec R-B4 only covers PUT) — LOW.
- (Phase 08 Slice B security review INFO) `frontend/src/lib/planMarkdown.ts` `slugify` has no length cap; a plan
  heading longer than ~140 chars yields progress keys > 160 chars, so that section's ticks get 422 on PATCH — LOW.

- (Phase 08 Slice B code review N1) `src/core/study_plans.py:update_progress`: a DB error on PATCH (e.g. SQLite lock)
  → global 500 + full exception log (progress keys only); map `SQLAlchemyError` to a fixed 503 like PUT — LOW.
- (Slice B review N2) `frontend/src/features/planner/Planner.tsx` debounced PATCH: two overlapping PATCHes could
  arrive out of order and leave older ticks saved while the client shows synced; send one PATCH at a time — LOW.
- (Slice B review N3) `Planner.tsx` mount reconcile: under React StrictMode (dev only) both effect runs complete
  (2 GETs, possibly 2 identical re-sent PATCHes); use a per-effect cancel flag — LOW.
- (Slice B review N5) `tests/test_study_plans.py` concurrency test does not assert that at least one round was a
  real conflict update (correctness assertions hold either way) — LOW.
- (Slice B review N6, residual) a plan cached before Slice B has no recorded inputs, so its explicit Save stores the
  current form inputs — LOW.
- (Slice C1 security LOW #1) `src/core/evaluation_history.py:record_attempt`: no per-user row cap or retention
  for `evaluation_attempts`; each row costs an LLM call and the per-IP rate limit applies. Decide retention/cap and
  use bounded queries when C2 adds reads — LOW.
- (Slice C1 security INFO #3) `migrations/versions/0006_evaluation_attempts.py`: `ix_evaluation_attempts_user_id` is
  redundant with the composite `(user_id, subject, created_at)` index; kept because spec R-C1-2 requires both —
  INFO.
- (Slice C1 code-quality NIT 4) `frontend/src/lib/api.ts` `EVALUATION_SUBJECTS` mirrors `src/schemas.py` `Subject`
  by hand, with no automated parity check. Drift would make the server return 422 for a subject the UI offers — LOW.
- (Slice C1 browser check, pre-existing) `frontend/src/features/evaluator/Evaluator.tsx` `MIN_ANSWER = 50` counts
  **characters**, while `src/agents/evaluator/graph.py:evaluate_answer` requires 50 **words**. A 50-character answer
  gets "Answer too short" and is recorded as unscored (correct per R-C1-3, but confusing UX) — LOW.
- (Slice C2 code-quality NIT 1) `src/core/evaluation_history.py:load_attempts` reads the latest 500 attempts
  (scored and unscored). For a user above 500, counts / average cover only those and a subject not evaluated within
  them drops out of mastery / weak / revision. Option: exact `COUNT` + per-subject window — LOW.
- (Slice C2 code-quality NIT 2) No index serves `WHERE user_id ORDER BY created_at DESC` exactly (composite is
  `(user_id, subject, created_at)`); fine at current scale; a new index needs a migration — INFO.
- (Slice C2 code-quality NIT 3, pre-existing since Slice A) `src/api/routes/planner.py:_resolve_weak` truncates the
  joined weak areas at `SHORT_TEXT_MAX`, which can cut the last item mid-word; option: drop whole items — LOW.
- (Slice C2 code-quality NIT 4) `frontend/src/features/dashboard/Dashboard.tsx` overdue revision rows show "Due now"
  without the date they became due — LOW.
- (Slice C2 security LOW/INFO, pre-existing) `src/api/routes/progress.py` outer `except` in `progress_overview` logs
  with `exc_info=True` (a driver traceback may include SQL / the bound user id); `/progress/overview` and planner
  generation rely only on the global per-IP limiter — LOW.
- (Slice C2) Revision `next_due` / `due` use UTC dates (like the UTC streak item above) — LOW.

### Last handoff — 2026-11-04 (Phase 08 FINAL RELEASE prepared; deployment is the owner's manual step)
- Phase 08 stays **COMPLETE** (slices A + A-1, B + B-1, C1, C2 CLOSED). **Phase 09 NOT STARTED.** Render Auto-Deploy is
  **OFF**: pushing to `main` deploys nothing; the owner clicks Manual Deploy. Deployment is NOT done and NOT verified.
- Table security (ADR-019): migrations 0005 and 0006 (never deployed, so edited in place) enable RLS with **no policies**
  on `study_plans` / `evaluation_attempts` and revoke all privileges from `anon`, `authenticated`, `service_role`
  (skipped for roles that do not exist), PostgreSQL only, same transaction as `CREATE TABLE`; no `FORCE`, no change to
  existing tables / grants / policies / global default privileges; SQLite unchanged; the advisory lock (ADR-018) is
  unchanged.
- Owner-confirmed production facts: existing public application tables are owned by `postgres`; RLS is enabled on all 14
  currently listed public tables (including `alembic_version`), `force_rls` false; existing API-role grants still appear
  in privilege checks and were deliberately left alone; PostgreSQL is 17.11.
- Verification (this session): `uv run pytest -q tests/test_migrations.py` → `17 passed`; `uv run ruff check src tests`
  clean; `compileall` OK; `git diff --check` clean; focused set (migrations, study plans, evaluation attempts and
  analytics, progress overview, planner autofill, ownership / chat isolation, workflow smoke) → `269 passed, 23 warnings
  in 64.39s`. Disposable PostgreSQL 15.19 and
  17.11 staging: **236 checks passed, 0 failed** (non-superuser owner, plain and Supabase-like clusters, existing-table
  grants / RLS / default ACL unchanged, API roles refused, lock + 20 / 20 concurrent rounds, downgrade / re-upgrade,
  `init_db()` no-op, Mentor store persistence). Details: `docs/phase-reports/phase-08-release-preflight.md` section E.
  The full 1765-test suite was not re-run (no application code changed).
- Deployment: ONE combined release of 0005 + 0006 (an image containing 0006 applies it at boot, `Dockerfile:74`);
  take and verify a Supabase backup first; roll back by forward-fix (an older image cannot boot against a newer
  revision); post-deploy SQL checks and the eight-workflow smoke are in the preflight document and
  `docs/SMOKE_CHECKLIST.md`.
- Still UNVERIFIED: the migrations on the real Supabase (pooler mode), Render's behaviour on a failed deploy, the live
  workflow smoke, and the owner's backup.

### Earlier handoff — 2026-11-04 (Phase 08 release preflight written; NOT a deployment authorization)
- Phase 08 stays **COMPLETE** (slices A + A-1, B + B-1, C1, C2 CLOSED); **Phase 09 NOT STARTED**. Review and
  documentation only: no SQL executed, no migration edited, nothing staged / committed / pushed / deployed, the
  advisory-lock code is unchanged. Full document: `docs/phase-reports/phase-08-release-preflight.md`.
- Owner evidence incorporated (Supabase SQL Editor, PostgreSQL 17.11): `pg_default_acl` grants `arwdDxtm` on future
  public tables to `anon`, `authenticated` and `service_role` for creators `postgres` and `supabase_admin`. Supabase
  documents that `supabase_admin`'s defaults cannot be changed by the project (`42501`) and apply only to objects that
  role creates; tables the app creates take `postgres`'s defaults, which can be changed with
  `ALTER DEFAULT PRIVILEGES FOR ROLE postgres …`. Nothing in the repo uses `service_role`, the Data API or a Supabase
  client (all access is direct SQLAlchemy / psycopg).
- **Open release blockers:** (B1) choose and apply the grants / RLS lock-down (proposed SQL P1 before the deploy, P2
  after; NOT EXECUTED); (B2) confirm the app / migration role (assumed `postgres`) with read-only queries Q3 + Q4;
  (B3) check the RLS / grant state of the EXISTING tables (potentially CRITICAL if the Data API exposes `public`;
  UNVERIFIED); (B4) confirm backup capability and take a `pg_dump`.
- Sequencing: ONE combined release of 0005 + 0006 is recommended — an image that contains 0006 applies it at boot
  (`Dockerfile:74`, `init_db()`); ADR-017's "deploy separately" is marked superseded. Rollback: an older image cannot
  boot against a newer revision (`Can't locate revision identified by '0006_evaluation_attempts'`, probe-confirmed);
  downgrade drops both tables and their data; prefer forward-fix, and revert application code while keeping the
  migration files. Runbook in the preflight document, section C.
- Docs corrected in this step: `docs/SMOKE_CHECKLIST.md` (rows 4 and 6, new "Phase 08 persistence checks"),
  `.claude/specs/README.md` (row 08), ADR-017 sequencing bullet (kept, marked superseded), this file.
- Still stale, not in this step's scope: the Phase 08 spec / plan headers say "APPROVED" without a COMPLETE marker;
  `PROJECT_STATE.md` still says "Frontend unchanged in Phase 03 …" and mentions `checkpoints.sqlite-shm/-wal` side
  files that no longer exist.
- Nightly CI `llm-eval` (`.github/workflows/ci.yml:83`) receives `secrets.DATABASE_URL`; the eval modules never call
  `init_db()` / Alembic. What the secret points at is UNKNOWN — owner to confirm it is not production — LOW.

### Earlier handoff — 2026-11-04 (Phase 08 release hardening: Alembic advisory lock, ADR-018)
- Not a reopening of any Phase 08 slice and not Phase 09 (**Phase 09 still NOT STARTED**). Owner-approved hardening
  before the first production deploy. Nothing committed, pushed or deployed; production and Supabase never touched.
- Facts confirmed by the owner (not verifiable from the repository): Render service is **Docker**, Docker Command
  blank (image `CMD` runs: `alembic upgrade head && uvicorn …`, `Dockerfile:74`), Pre-Deploy Command unavailable,
  Auto-Deploy **Off**; production PostgreSQL is Supabase **17.11**. `render.yaml` (`runtime: python`, `startCommand`)
  does not govern this service.
- Code: `migrations/env.py` takes `pg_advisory_xact_lock(80080601)` inside the migration transaction, same
  connection, right before `run_migrations()`; PostgreSQL only, SQLite unchanged. `tests/test_migrations.py` +8 tests
  (lock helper, PostgreSQL vs other dialects, call-site wiring, SQLite emits no advisory statement). The format hook
  also applied two cosmetic changes in `env.py` (blank line after the docstring, one collapsed call) and reformatted
  three pre-existing long asserts in `tests/test_migrations.py`; no behaviour change.
- Gates run: `uv run pytest -q tests/test_migrations.py` → `12 passed`; `uv run ruff check src tests` → clean;
  `git diff --check` clean. The full suite was **not** re-run (not required by the change; last full run
  `1765 passed, 2 skipped`).
- Disposable-PostgreSQL staging (Docker, `postgres:15.19-alpine` and `postgres:17.11-alpine`, internal network, tmpfs,
  throwaway credentials, no `.env` in the build context): **85 checks passed, 0 failed.** Covers 0004→0005→0006,
  constraints / indexes, real plan upsert + attempts + analytics, cascade, downgrade / re-upgrade, `init_db()` at
  head, lock blocking a second runner, 3 concurrent runners × 10 rounds per version (20 / 20 rounds: all exit 0, final
  `0006_evaluation_attempts`) and Mentor `PostgresStore` persistence across containers. The old `env.py` failed 3 / 10
  rounds on 15.19 (races are real). Details: ADR-018.
- **Still UNVERIFIED:** Supabase itself (pooler mode, role privileges, RLS / grants for `study_plans` and
  `evaluation_attempts`), a restore of real production data, Render's deploy overlap, `Store: Postgres` in the real
  deployment, and the live smoke of the 8 workflows. The staging runner script lives in the session scratchpad, not in
  the repository (adding it under `scripts/` needs owner approval).
- Consequence for deployment: ADR-017's "deploy 0005 and 0006 separately" cannot be done with one image — any image
  containing `0006_evaluation_attempts.py` applies it on boot. Choose: deploy a commit with only 0005 first, or one
  combined deploy. Roll back database first, then code.
- Backlog (LOW): `ruff check migrations/env.py` reports a pre-existing I001 (import order, lines untouched by this
  change); CI lints only `src tests`. `docs/SMOKE_CHECKLIST.md` row 6 still says plans are not persisted and has no
  rows for `/planner/plan` or `/progress/overview`. `.claude/specs/README.md` row 08 still says "Slice A next".

### Earlier handoff — 2026-11-04 (Phase 08 COMPLETE — Slice C2 CLOSED)
- **Phase 08 — COMPLETE.** Slices **A + A-1, B + B-1, C1 and C2 are all CLOSED.** Reports:
  `docs/phase-reports/phase-08-slice-{a,b,c1,c2}-report.md`. **Phase 09 NOT STARTED** (needs explicit owner approval).
- C2 delivered (no migration): Average Evaluation Score, subject mastery (latest 10 scored), weak subjects (≥ 3, < 60 %),
  subject-based revision (1/3/7/14 days by consecutive passes, UTC) on `/progress/overview` (additive; legacy
  `topics` / `weak_topics` = `[]`); Dashboard cards / chart / lists; Planner autofill = weak subjects first, then
  profile `weak_areas` (Amendment C2-1, which also updated two shape expectations in `tests/test_progress_overview.py`).
- Gates: full suite `1765 passed, 2 skipped`; focused `162 passed`; ruff, compileall, frontend build + lint, JSX escape
  check clean. Reviews: security no CRIT/HIGH/MED (LOW rollback fixed); code-quality no BLOCKER/SHOULD-FIX.
- Browser checks 1–8 owner-confirmed PASS on an isolated environment (scratchpad SQLite via `create_all`, no Alembic,
  external services off, outbound network blocked — 0 attempts, mock LLMs, backend 8012, frontend 5176). Check 6
  independently verified in the request log; check 8 browser request not captured (API smoke + tests only).
- **Migrations 0005 and 0006 have never been executed against production or any real DB** (throwaway SQLite in
  tests only). Owner before deploying: Supabase backup before each; grants / RLS for `study_plans` and
  `evaluation_attempts`; confirm the LangGraph store is Postgres; deploy B and C1 separately; post-deploy smoke.
- Uncommitted working tree (A + A-1 + polish + B + B-1 + C1 + C2); no commit or push made.
- Next step: owner commits if wanted; Phase 09 only after explicit approval.

### Earlier handoff — 2026-11-04 (Phase 08 Slice C1 CLOSED)
- Active phase: **Phase 08 — ACTIVE.** **Slice A + A-1, Slice B + B-1 and Slice C1 are CLOSED.** **Slice C2 NOT
  STARTED** (needs explicit owner approval). Report `docs/phase-reports/phase-08-slice-c1-report.md`.
- C1 delivered:
  - `evaluation_attempts` (migration 0006) with no question or answer text
  - optional allow-listed `subject` on Basic and Mains
  - best-effort recording with a redacted warning
  - "Subject (optional)" dropdown
- Gates:
  - full suite `1706 passed, 2 skipped`; focused `161 passed`
  - ruff, compileall, frontend build and lint, and the JSX escape check all clean
- Reviews: security has no CRIT/HIGH/MED; code-quality has no BLOCKER/SHOULD-FIX.
- Browser checks 1–6: owner-confirmed PASS on an isolated environment.
  - Setup: scratchpad SQLite built by `create_all` (no Alembic), all external services off, mock evaluator,
    backend 8011, frontend 5175.
  - Independently verified: check 2 (`subject='Geography'` → row `basic / Geography / 7.0 / 10`; no-subject request →
    NULL) and the DB row columns.
  - Check 4 backend evidence partial; check 5 browser request not captured (API smoke + unit test only).
- **Migration 0006 has never been executed against production or any real DB.** It ran only on throwaway SQLite in
  tests.
- Owner before deploying 0006: Supabase backup; grants / RLS for `evaluation_attempts`; deploy C1 separately from B.
- Uncommitted working tree (A + A-1 + polish + B + C1); no commit or push made.
- Next step: owner commits if wanted, then explicitly approves **Slice C2** (no migration).

### Earlier handoff — 2026-11-04 (Phase 08 Slice C1 implemented; awaiting owner check)
- Active phase: **Phase 08 — ACTIVE.** A + A-1 and B + B-1 CLOSED. **Slice C1 IMPLEMENTED, verified offline, not yet
  closed.** Report `docs/phase-reports/phase-08-slice-c1-report.md`.
- C1 files: `src/core/models.py` (`EvaluationAttempt`), `migrations/versions/0006_evaluation_attempts.py`,
  `src/schemas.py` (`Subject`), `src/core/evaluation_history.py`, `src/api/routes/evaluator.py`,
  `frontend/src/lib/api.ts`, `frontend/src/features/evaluator/Evaluator.tsx`, `tests/test_evaluation_attempts.py`.
- Gates (2026-11-04):
  - full suite `1706 passed, 2 skipped`; focused `161 passed` (re-run after test-only NIT fixes)
  - ruff clean; compileall OK
  - frontend build + lint clean; JSX escape check OK
  - migration 0006 upgrade, CASCADE and downgrade verified on throwaway SQLite only
- Reviews: security has no CRIT/HIGH/MED (2 LOW → backlog / noted); code-quality has no BLOCKER/SHOULD-FIX
  (NIT 1, 2 fixed).
- UNVERIFIED: AC-C1-5 owner browser check of the dropdown (6 checks in the report); Supabase 0006 apply / grants /
  RLS / backup (owner only; deploy C1 separately from B).
- Uncommitted working tree (A + A-1 + polish + B + C1); no commit or push made.
- Next step: owner runs the browser checks and approves the C1 close. **Slice C2 NOT STARTED**, and it needs
  explicit approval.

### Earlier handoff — 2026-11-03 (Phase 08 Slice B CLOSED)
- Active phase: **Phase 08 — ACTIVE.** Slice A + A-1 CLOSED; **Slice B + Amendment B-1 CLOSED** (all 9 owner browser
  checks PASS; final closure review no BLOCKER / SHOULD-FIX). Report `docs/phase-reports/phase-08-slice-b-report.md`.
- Check-1 render crash fixed (pre-existing `Planner.tsx` `onToggle` read `e.currentTarget` inside a deferred
  state updater); verified by a scratchpad headless-Chrome check 14/14.
- Closure gates: focused pytest `181 passed`; ruff clean; compileall 0; `git diff --check` clean; frontend build +
  lint clean; JSX escape check OK; Node check of `reconcilePlan.ts` 15/15. Full suite not re-run (last `1642 passed,
  2 skipped`).
- Owner before deploying 0005: Supabase backup; grants / RLS for `study_plans`; deploy B separately from C1.
- Uncommitted working tree (Slice A + A-1 + polish + Slice B); no commit or push made.
- Next step: owner commits if wanted, then explicitly approves **Slice C1** (migration 0006). **C1 / C2 NOT STARTED.**

### Earlier handoff — 2026-11-03 (Phase 08 Slice B implemented; awaiting owner checks)
- Slice B implemented and verified offline: `src/core/models.py` `StudyPlan`; `migrations/versions/0005_study_plans.py`
  (written, never run by Claude; downgrade verified on throwaway SQLite only); `src/schemas.py`;
  `src/core/study_plans.py`; 4 endpoints in `src/api/routes/planner.py`; `api.ts`; `Planner.tsx` reconciliation;
  `tests/test_study_plans.py` (40). Full suite `1642 passed, 2 skipped` (before security fix #1; focused re-run 45
  passed after it); ruff, compileall, diff --check, frontend build + lint clean. Report
  `docs/phase-reports/phase-08-slice-b-report.md`.
- Slice A / A-1 / polish changes verified intact. Uncommitted; no commit or push.
- Code-quality review: no BLOCKER; S1 (option a) and S2 fixed by owner-approved Amendment B-1 (frontend only:
  `Planner.tsx`, new `features/planner/reconcilePlan.ts`; Node check 15/15; build + lint clean); NITs in backlog.
- Next step: owner runs the 9 manual browser checks (isolated local env, report § Manual browser checks), takes a
  Supabase backup before any deploy of 0005, then approves Slice B close. **C1 / C2 NOT started.**

### Earlier handoff — 2026-11-03 (Phase 08 Slice A + Amendment A-1 CLOSED)
- Active phase: **Phase 08 — ACTIVE.** Slice A (A1–A5) and Amendment A-1 **CLOSED**. Final read-only review of the
  full diff: no BLOCKER / SHOULD-FIX (2 LOW NITs → backlog above). Owner manual browser checks all **PASS**: Mentor
  reload + conversation continuity; New Chat creates a separate conversation; in-flight save race does not restore
  the previous conversation; specialist failures not saved in Session History; Dashboard labels; Planner weak-area
  autofill. Report `docs/phase-reports/phase-08-slice-a-report.md`.
- Separate frontend polish (not part of Slice A scope): 17 literal Unicode escapes rendered as text fixed in
  `index.html`, `Topbar.tsx`, `Lecture.tsx`, `Upload.tsx`, `Planner.tsx` (2 lines), `AppLayout.tsx`; new
  `frontend/scripts/check-jsx-escapes.mjs` (TS-AST check of JSX text / JSX attribute strings; passes). All 17
  browser-verified by the owner. Build + lint clean.
- Gates: initial full suite `1566 passed, 2 skipped`; after A-1, the related files `402 passed` (full suite not
  re-run, per owner instruction); ruff clean; compileall 0; `git diff --check` clean; frontend build + lint clean;
  Node check of `persistExchange.ts` 10/10.
- Uncommitted working tree (no commit or push made).
- Next step: owner commits Slice A (+ polish) if wanted, then explicitly approves **Slice B** (migration 0005).
  **Slice B NOT STARTED.**

### Earlier handoff — 2026-11-03 (Phase 08 planned; documentation only)
- Active phase: **Phase 08 Product persistence & gaps — ACTIVE.** Spec `.claude/specs/phase-08-product-gaps.md`
  (APPROVED rev 4), plan `.claude/plans/phase-08-product-gaps.md`, ADR-017. No application code, tests or migrations
  changed in this session.
- Slices (each approved, implemented, verified, reviewed and reported separately):
  A bug fixes + progress tests (no schema) → B study-plan persistence (migration 0005, atomic upsert + concurrency
  test) → C1 evaluation recording + subject dropdown (0006) → C2 subject analytics, weak subjects, revision, Dashboard.
- Confirmed bugs for Slice A: B1 false Planner autofill claim; B2 Mentor history splits after reload; B3 error notices
  saved as Mentor answers; B4 misleading Dashboard labels; B5 `/progress/overview` untested.
- Next step: implement **Slice A only** in a fresh chat (`/implement-phase`), then stop for the Slice A report.
- Owner-only before B / C1 deploys: Supabase backup; confirm the LangGraph store is Postgres; table grants / RLS.
- Test baseline unchanged: `1520 passed, 2 skipped` (Phase 07 close; not re-run in this documentation session).

### Previous handoff — 2026-11-03 (Phase 07 closed)
- Active phase: none in progress. **Phase 07 LLM evaluation, safety & quality — DONE (2026-11-03).** Report:
  `docs/phase-reports/phase-07-report.md`. ADR-016 (+ addenda through the final budget review). Phase 08 (product gaps)
  not started; wait for the user.
- Outcome:
  - Prompt fencing fixes F0–F7 in production.
  - Offline T1/T2 eval harness in `evals/` (60 synthetic cases, all 8 workflows).
  - Budget-gated live mode. The final budget review made ledger reservations, usage validation and the stop latch
    atomic under one shared lock, and limited one managed reservation to one send.
  - **T3 live NOT RUN (AC12 open by design).** Bounds ship `unvalidated`, so live mode refuses.
- Final verified evidence (2026-11-03):
  - `uv run pytest -q` → `1520 passed, 2 skipped in 163.72s (0:02:43)`.
  - `uv run ruff check src tests evals` → `All checks passed!`; compileall exit 0; `git diff --check` exit 0;
    frontend `npm run lint` clean (frontend unchanged, build not run).
  - Offline evals → exit 0; T1 33 pass / 0 fail / 1 known gap; T2 72 pass / 0 fail.
  - Ownership tests → `17 passed`. `src/eval/` and `scripts/bench_retrieval.py` unchanged.
- Still UNVERIFIED:
  - real-model safety, quality and cost (T3);
  - the real clients' retry / streaming-usage behaviour;
  - live workflow smoke;
  - a reviewer-agent pass on the final budget-concurrency delta (tests only; AC11 caveat).
  - The 60 eval cases are Claude-written (`human_verified: false`).
- Repo hygiene: `checkpoints.sqlite-shm` / `checkpoints.sqlite-wal` side files are present (local checkpoint DB; not
  opened by Claude).
- Next step (superseded): Phase 07 committed as `86a7753`; Phase 08 planned (see above).
- Uncommitted changes: see `git status --porcelain` in the phase report (Phase 07 code, tests, `evals/`, docs and the
  07→08 spec renumbering). Nothing committed or pushed.
