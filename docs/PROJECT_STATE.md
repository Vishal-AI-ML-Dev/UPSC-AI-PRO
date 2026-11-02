# Project state — UPSC-AI-PRO

Read this first in every new Claude Code session. Facts here are verified from source unless marked UNVERIFIED.
Baseline evidence: `docs/audit/2026-11-02-architecture-audit.md`.

## Current phase
| Phase | Title | Status |
|---|---|---|
| 00 | Engineering workspace setup | DONE (2026-11-02) — `docs/phase-reports/phase-00-report.md` |
| 01 | Baseline & safety net | **NEXT** — spec STUB `.claude/specs/phase-01-baseline-safety-net.md` |
| 02–09 | See `.claude/specs/README.md` | STUB |

## The 8 workflows (must not regress)
| Workflow | Backend entry | Frontend | Status (last verified) |
|---|---|---|---|
| AI Mentor | `src/api/routes/chat.py` `POST /api/v1/agent/chat/stream` | `features/mentor/MentorChat.tsx` | Code present; runtime UNVERIFIED |
| NCERT Study | `src/api/routes/ncert.py` | `features/ncert/Ncert.tsx` | Code present; needs R2 or local PDFs; runtime UNVERIFIED |
| PYQ + Personal PYQ Bank | `src/api/routes/pyq.py` | `features/pyq/Pyq.tsx` | Code present; runtime UNVERIFIED |
| Answer Evaluator | `src/api/routes/evaluator.py` | `features/evaluator/Evaluator.tsx` | Code present; runtime UNVERIFIED |
| Current Affairs | `src/api/routes/current_affairs.py` | `features/current_affairs/CurrentAffairs.tsx` | Code present; live web; runtime UNVERIFIED |
| Study Planner | `src/api/routes/planner.py` | `features/planner/Planner.tsx` | Generates plans; not persisted |
| Lecture Learning | `src/api/routes/lecture.py` | `features/lecture/Lecture.tsx` | Code present; YouTube reachability UNVERIFIED |
| PDF / Document Chat | `src/api/routes/upload.py` | `features/upload/Upload.tsx` | Works per code; no per-user ownership |

Test suite status: **not run yet** (574 `def test_` functions counted statically). Phase 01 records the baseline.

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
| 15 | Dead/duplicate code; unused deps/settings | LOW | 01 / 09 |
| 16 | Non-blocking CI lint; no frontend tests; live smoke disabled | LOW | 09 |
| 17 | Compose: unpinned qdrant, default creds, exposed ports, unused redis | LOW | 09 |

## Doc drift (README.md / ARCHITECTURE.md claims contradicted by source) — fix in Phase 01
- "8 specialised agents behind one supervisor"; per-agent "LangGraph pipelines".
- "Hybrid retrieval" (lexical only re-scores dense candidates); "cross-encoder reranking" (off, deps missing).
- Citation enforcement via `compose_grounded_answer` (never called); "tenacity retries" (not imported).
- Local Chroma fallback (broken); stricter auth rate limit 10/300 s (not implemented).
- "Stateless API, add workers freely" (several in-process states; job reaper hazard).
- `ARCHITECTURE.md` lists `mentor.py` as a feature endpoint (unregistered).
- "669 offline tests" (574 test functions counted; with parametrize UNVERIFIED).

## Backlog (out-of-scope findings; add file, evidence, severity)
- `src/api/routes/mentor.py` unregistered but tested by `tests/test_stream_citations.py` — LOW.
- `src/agents/upload/upload.py` duplicate unauthenticated router (unmounted) — LOW.
- `src/index.css` stray in backend package; `tests/.gitignore` is UTF-16 — LOW.
- Config fields never read: `groq_fallback_models`, `openai_compat_*`, `groq_whisper_model`,
  `job_max_retries`, `job_timeout_seconds`, `rq_queue_name`, `auth_rate_limit_*` — LOW.
- Missing referenced files: `src/worker.py`, `scripts/cron_cleanup.py`, `tests/test_smoke_live.py` — LOW.
- `.dockerignore` excludes `*.md`, so `data/mentor_kb/*.md` is not in the image — LOW.
- Version mismatch: pyproject 0.1.0 / app 2.0.0 / frontend 1.0.0; Streamlit-era defaults (port 8501) — LOW.

### Last handoff — 2026-11-02
- Active phase: 00 Engineering workspace setup — DONE.
- Done this session: read-only architecture audit; created the Claude Code workspace (`CLAUDE.md`, `.claude/`,
  `docs/`, `evals/`); `.gitignore` now versions `.claude/` except local settings.
- Verified: see `docs/phase-reports/phase-00-report.md`. Not verified: test suite, app runtime.
- Next step: `/start-phase 1` to expand the Phase 01 spec and draft its plan.
- Open questions for the user: none.
- Uncommitted changes: all Phase 00 files (no commit made).
