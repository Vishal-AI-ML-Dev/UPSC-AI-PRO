# Project state — UPSC-AI-PRO

Read this first in every new Claude Code session. Facts here are verified from source unless marked UNVERIFIED.
Baseline evidence: `docs/audit/2026-11-02-architecture-audit.md`.

## Current phase
| Phase | Title | Status |
|---|---|---|
| 00 | Engineering workspace setup | DONE (2026-11-02) — `docs/phase-reports/phase-00-report.md` |
| 01 | Baseline & safety net | DONE (2026-11-02) — `docs/phase-reports/phase-01-report.md` |
| 02 | Security & ownership | **NEXT** — spec STUB `.claude/specs/phase-02-security-ownership.md` |
| 03–09 | See `.claude/specs/README.md` | STUB |

## The 8 workflows (must not regress)
| Workflow | Backend entry | Frontend | Status (last verified) |
|---|---|---|---|
| AI Mentor | `src/api/routes/chat.py` `POST /api/v1/agent/chat/stream` | `features/mentor/MentorChat.tsx` | Code present; runtime UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| NCERT Study | `src/api/routes/ncert.py` | `features/ncert/Ncert.tsx` | Code present; needs R2 or local PDFs; runtime UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| PYQ + Personal PYQ Bank | `src/api/routes/pyq.py` | `features/pyq/Pyq.tsx` | Code present; runtime UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| Answer Evaluator | `src/api/routes/evaluator.py` | `features/evaluator/Evaluator.tsx` | Code present; runtime UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| Current Affairs | `src/api/routes/current_affairs.py` | `features/current_affairs/CurrentAffairs.tsx` | Code present; live web; runtime UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| Study Planner | `src/api/routes/planner.py` | `features/planner/Planner.tsx` | Generates plans; not persisted; offline smoke (mount/401/422) PASS 2026-11-02 |
| Lecture Learning | `src/api/routes/lecture.py` | `features/lecture/Lecture.tsx` | Code present; YouTube reachability UNVERIFIED; offline smoke (mount/401/422) PASS 2026-11-02 |
| PDF / Document Chat | `src/api/routes/upload.py` | `features/upload/Upload.tsx` | Works per code; no per-user ownership; offline smoke (mount/401/422) PASS 2026-11-02 |

Test suite status (2026-11-02): baseline `897 passed, 13 warnings in 87.54s`; latest (closing gate) `930 passed, 13 warnings in 84.20s`
(post-review run `930 passed, 13 warnings in 104.17s`); 64 modules, +33 from `tests/test_workflow_smoke.py`
(`33 passed, 3 warnings in 6.46s`). Ruff: `All checks passed!`. compileall, frontend build (baseline) and prettier: clean.
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
- Jobs: in-process `ThreadPoolExecutor(2)`, DB-persisted status only (`src/core/job_queue.py`).
- Checkpointer/store: Postgres when `DATABASE_URL` is Postgres; else SQLite checkpointer + in-memory store.
- Jev / TypeSafe: not present anywhere in the repository.

## Open risks (from the audit risk register; ranked)
| # | Risk | Sev | Planned phase |
|---|---|---|---|
| 1 | No ownership at vector boundary; LLM-chosen `persist_key`; global upload collections | HIGH | 02 |
| 2 | Blocking work in `async def` handlers (single worker) | HIGH | 03 |
| 3 | Non-durable jobs; boot reaper fails sibling workers' jobs | HIGH/MED | 04 |
| 4 | Vector stack fails without Qdrant (Chroma not installed) | HIGH (misconfig) | 06 |
| 5 | Unbounded inputs + checkpoint history growth → cost | MED | 02 / 03 |
| 6 | Auth rate limit configured but unused; account enumeration | MED | 02 |
| 7 | Tokens in `localStorage` | MED | backlog |
| 8 | Upload size check relies on Content-Length; unbounded `_processed_cache` | MED | 02 |
| 9 | 24 h semantic cache can serve stale time-sensitive answers | MED | 06 |
| 10 | Eval gate tests CRAG path production never runs | MED | 08 |
| 11 | In-memory store fallback when not Postgres | MED | 09 |
| 12 | Migrations run per process; LangGraph tables outside Alembic | LOW–MED | 09 |
| 13 | `/health` checks nothing; no readiness/backups | MED | 09 |
| 14 | Exception text leaked; PII in logs; public `/docs` | LOW | 02 / 09 |
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
- Version mismatch: pyproject 0.1.0 / app 2.0.0 / frontend 1.0.0; Streamlit-era defaults (port 8501) — LOW.

### Last handoff — 2026-11-02
- Active phase: 01 Baseline & safety net — DONE (spec DONE, plan DONE). Next: 02 Security & ownership — spec STUB, plan none.
- Done this session: Phase 01 end to end — baseline gates; `tests/test_workflow_smoke.py`; `docs/SMOKE_CHECKLIST.md`;
  README/ARCHITECTURE drift corrected; ADR-006; review (0 BLOCKING, findings 1–5 fixed); phase report
  `docs/phase-reports/phase-01-report.md`; status updates in spec, plan, `.claude/specs/README.md`, this file.
- Verified: `uv run pytest -q` → closing gate `930 passed, 13 warnings in 84.20s` (post-review run `104.17s`); smoke `33 passed, 3 warnings in 6.46s`;
  ruff `All checks passed!`; compileall exit 0; `npm run lint` clean; `git diff --stat -- src frontend migrations .github` empty.
  Not verified: live workflow smoke (UNVERIFIED); README §5 size metrics; frontend build not re-run (frontend unchanged).
- In progress / next step: `/start-phase 2` (not started).
- Open questions for the user: none.
- Uncommitted changes: none — the 10 Phase 01 files are committed as `test: add workflow baseline and safety net` (not pushed).
