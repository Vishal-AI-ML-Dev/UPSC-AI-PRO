# UPSC-AI-PRO — Claude Code guide

AI-assisted UPSC exam preparation: FastAPI + LangGraph backend (root `src/`), React/Vite/TypeScript SPA
(`frontend/`). Gemini primary LLM with Groq fallback, Qdrant vectors, Postgres (SQLite locally).

**Read first in every fresh session:** `docs/PROJECT_STATE.md` (current phase, open risks, handoff), then the
active spec in `.claude/specs/` and plan in `.claude/plans/`. Decisions live in `docs/DECISIONS.md`.
Baseline evidence: `docs/audit/2026-11-02-architecture-audit.md`.

## Layout (keep it — do NOT introduce a `backend/` directory; see ADR-001)
| Path | What it is |
|---|---|
| `src/api/main.py` | FastAPI app, middleware, router registration (`/api/v1`) |
| `src/api/routes/*.py` | One module per feature. `mentor.py` is **not registered** (dead code). |
| `src/api/deps.py` | `get_current_user` (JWT + DB single-session check); `admin_access.require_admin` |
| `src/agents/<feature>/` | Feature logic as **plain functions** (files are named `graph.py` but are not LangGraph) |
| `src/graph/` | The actual LangGraph code: `supervisor.py` (LLM router), `tools.py` (ReAct mentor agent), `rag_graph.py` (CRAG, not reachable over HTTP), `memory.py` (checkpointer/store) |
| `src/core/` | Shared services: `llm.py`, `vector_store.py`, `retrieval.py`, `grounding.py`, `db.py`, `models.py`, `job_queue.py`, … |
| `src/schemas.py` | Pydantic API contracts. ORM models are in `src/core/models.py`. |
| `src/eval/` | Current LLM/retrieval eval harness (stays until the eval phase; see ADR-003) |
| `migrations/` | Alembic (0001–0004) |
| `tests/` | pytest; `tests/conftest.py` boots the real app on throwaway SQLite with LLM/email mocked |
| `frontend/src/lib/api.ts` | The only API client (`request`, `streamAgent`, `pollJob`) |
| `evals/` | Future home of evals (skeleton only) |

## The 8 product workflows — must never regress
| Workflow | Backend entry | Frontend |
|---|---|---|
| AI Mentor | `routes/chat.py` `POST /agent/chat/stream` → `graph/supervisor.py` | `features/mentor/MentorChat.tsx` |
| NCERT Study | `routes/ncert.py` → `agents/ncert/graph.py` | `features/ncert/Ncert.tsx` |
| PYQ + Personal PYQ Bank | `routes/pyq.py` → `agents/pyq/graph.py` | `features/pyq/Pyq.tsx` |
| Answer Evaluator | `routes/evaluator.py` → `agents/evaluator/graph.py` | `features/evaluator/Evaluator.tsx` |
| Current Affairs | `routes/current_affairs.py` → `agents/current_affairs/` | `features/current_affairs/CurrentAffairs.tsx` |
| Study Planner | `routes/planner.py` → `agents/planner/graph.py` | `features/planner/Planner.tsx` |
| Lecture Learning | `routes/lecture.py` → `agents/lecture/graph.py` | `features/lecture/Lecture.tsx` |
| PDF / Document Chat | `routes/upload.py` → `agents/upload/graph.py` | `features/upload/Upload.tsx` |

## Commands
```bash
uv run pytest -q                       # offline test suite (no API keys needed)
uv run ruff check src tests            # lint (CI runs this non-blocking today)
uv run python -m compileall -q src tests
cd frontend && npm run build           # tsc + vite build
cd frontend && npm run lint            # prettier --check
```
Never run: `alembic`/migrations, `uv add`/`npm install`, `docker compose up`, `git push` — denied in
`.claude/settings.json`. Ask the user instead.

## Working method (spec-driven, one phase at a time)
1. **Spec** — `.claude/specs/phase-NN-*.md` (from `_TEMPLATE.md`): goal, non-goals, current behaviour with file refs, acceptance criteria.
2. **Plan** — `.claude/plans/phase-NN-*.md`: exact files to touch, steps, verification, rollback. User approves before code.
3. **Implement** — only files listed in the plan (`/implement-phase` skill). Out-of-scope findings go to the backlog in `docs/PROJECT_STATE.md`.
4. **Verify** — tests + build; never claim a check passed without running it.
5. **Close** — `/complete-phase` skill: phase report in `docs/phase-reports/`, update `docs/PROJECT_STATE.md`, ADRs in `docs/DECISIONS.md`.

## Golden rules
- Read the code before trusting docs: `README.md` / `ARCHITECTURE.md` overstate several features (list in `docs/PROJECT_STATE.md` → "Doc drift").
- No rewrites. Small, reviewable changes that preserve the 8 workflows.
- No new dependencies, schema changes or migrations without a spec + ADR.
- Never read, print or edit `.env*`, keys, cookies, `*.sqlite`/`*.db`, or `data/ncert/` (blocked by `.claude/hooks/protect_paths.py`).
- Every bug fix gets a regression test. Every schema change gets an Alembic migration.
- Commits only when the user asks; use the user's message verbatim, no co-author trailer.

## Rules (load the relevant one before editing)
- `.claude/rules/backend.md` — FastAPI, agents, LLM, vector store, DB
- `.claude/rules/frontend.md` — React SPA and API client
- `.claude/rules/testing.md` — test patterns and evidence
- `.claude/rules/security.md` — auth, ownership, secrets, untrusted input
- `.claude/rules/scope-control.md` — what you may and may not change in a phase

## Skills, agents, commands
- Skills: `implement-phase`, `write-tests`, `review-changes`, `complete-phase` (`.claude/skills/`)
- Agents: `test-engineer`, `code-quality-reviewer`, `security-reviewer` (`.claude/agents/`)
- Commands: `/start-phase <n>`, `/phase-status`, `/handoff` (`.claude/commands/`)

## Known facts that surprise newcomers
- The "supervisor" is one structured LLM routing call to one of 5 subgraphs; there is no parallel or multi-step orchestration.
- Background jobs run in an in-process thread pool; only status is persisted (`src/core/job_queue.py`).
- Vector collections are keyed by `persist_key`; uploads are keyed by file MD5 with no owner check.
- The Chroma fallback imports `langchain_chroma`, which is not installed — Qdrant (`QDRANT_URL`/`QDRANT_PATH`) is required.
- Jev / TypeSafe is not present anywhere in the repository.
