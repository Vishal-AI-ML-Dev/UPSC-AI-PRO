# Phase 01 report — Baseline & safety net

Status: DONE (2026-11-02)

## Summary
Recorded a clean offline baseline (897 tests), added an offline smoke test for all 8 workflows (route mounted,
401, 422) and a manual live checklist, corrected the README/ARCHITECTURE doc drift in place, and recorded the
dead-code decision (ADR-006). No application code, dependency, schema, CI or frontend change.
Spec: `.claude/specs/phase-01-baseline-safety-net.md` · Plan: `.claude/plans/phase-01-baseline-safety-net.md`

## User decisions applied (2026-11-02)
Keep all dead code (no deletion/relocation) · correct README/ARCHITECTURE in place, no "Known limitations"
section · smoke tests limited to mount / auth / validation, no happy paths · verify each endpoint before
asserting · frontend checks only if deps already installed · baseline first · no edits under `src/`,
`frontend/`, `migrations/`, `.github/`.

## 1. Baseline (run before any test/doc change)
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q` | 0 | `897 passed, 13 warnings in 87.54s (0:01:27)` |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run python -m compileall -q src tests` | 0 | no output |
| `cd frontend && npm run build` | 0 | `✓ built in 6.87s` (`frontend/node_modules` present; script `tsc -b && vite build`) |
| `cd frontend && npm run lint` | 0 | `All matched files use Prettier code style!` |

Warnings (13): Starlette `httpx` TestClient deprecation (1); Alembic `path_separator` deprecation (12, from
`test_chat_isolation`, `test_logging_startup`, `test_migrations`). No pre-existing failures.

## 2. After implementation
Post-review verification (after review fixes 1–5):
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q tests/test_workflow_smoke.py` | 0 | `33 passed, 3 warnings in 6.46s` |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run pytest -q` | 0 | `930 passed, 13 warnings in 104.17s (0:01:44)` (same 13 deprecation warnings as baseline) |
| `git diff --check` | 0 | no whitespace errors (new files checked separately: no trailing whitespace) |

Close gates (`complete-phase`, 2026-11-02):
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q` | 0 | `930 passed, 13 warnings in 84.20s (0:01:24)` |
| `uv run ruff check src tests` | 0 | `All checks passed!` |
| `uv run python -m compileall -q src tests` | 0 | no output |
| `cd frontend && npm run lint` | 0 | `All matched files use Prettier code style!` |
| `cd frontend && npm run build` | — | not re-run: no `frontend/` file changed (baseline build passed, §1) |
| `git diff --stat -- src frontend migrations .github` | 0 | empty |

## 3. Smoke test (`tests/test_workflow_smoke.py`)
One primary endpoint per workflow — `/agent/chat/stream`, `/ncert/study`, `/pyq/generate`,
`/evaluator/evaluate/sync`, `/current-affairs/daily`, `/planner/generate/sync`, `/lecture/process-text`,
`/upload/chat` — × 4 checks (mounted in OpenAPI; 401 no token with a valid body; 401 invalid token with a valid
body, asserting `"Invalid or expired token"` + `error="invalid_token"`; 422 body-only errors with a valid session), plus `pdf_hash` query validation for `/upload/chat`.
Behaviour verified before asserting: request schemas read from `src/schemas.py` / `chat.py`; `OAuth2PasswordBearer`
returns `401 {"detail": "Not authenticated"}` + `WWW-Authenticate: Bearer`.
Finding during implementation: `app.routes` lists `_IncludedRouter` wrappers, not `APIRoute`s, in the installed
FastAPI, so the mount check uses `app.openapi()["paths"]` (Backlog entry added).

## 4. Doc drift — claim → evidence → fix
| Claim (before) | Evidence | Now says |
|---|---|---|
| "multi-agent" system; "8 specialised agents behind one supervisor"; "Supervisor + 8 sub-agents"; "Specialized Agents (8)" | `src/graph/supervisor.py` 5-route `RouteDecision`; NCERT/PYQ/Lecture/Upload routes call `src/agents/*/graph.py` directly | every feature page's route calls its feature functions directly; the 5-route supervisor serves mentor chat only; ReAct mentor is the only agent |
| `rag` route = "NCERT / Upload / Lecture / PYQ" | no route sets `persist_key`; CRAG only in `src/eval/llm_eval.py` | CRAG subgraph not reachable over HTTP |
| `agents/*/graph.py` are "LangGraph pipelines" (ARCHITECTURE) | plain functions | "plain Python functions, despite the name" |
| "Hybrid retrieval (dense + lexical)", "two arms" | `retrieval.py:213 rerank_scored_documents` scores dense candidates only | dense retrieval; lexical signal + RRF only re-orders dense hits |
| "cross-encoder reranking" | `config.py:320 rerank_enabled=False`; `sentence-transformers` not a dependency | optional, off by default |
| "citation-enforced grounding" via `compose_grounded_answer` | defined `grounding.py:199`, never called | citations formatted from tool/retrieval results, not enforced |
| "`tenacity` retries" | declared in `pyproject.toml:41`, not imported | `with_fallbacks` + provider SDK `max_retries` (`llm.py:265,291`) |
| "local Chroma fallback"; Qdrant fails open | `vector_store.py:543,578,606` import `langchain_chroma` (not installed) | Qdrant required; Chroma fallback does not work |
| auth limit `10 / 300s` | `config.py:178-179` never read | configured but not applied |
| "Stateless API … add workers freely"; job status "survives restarts" | in-process `ThreadPoolExecutor(2)`, upload caches, in-memory store fallback; `job_queue.py:136 reap_stale_jobs` errors all queued/running jobs | mostly externalised state; single worker until jobs are durable; in-flight jobs lost on restart |
| plan-execute escalation active | `config.py:301 plan_execute_enabled=False` | opt-in, off by default |
| `mentor.py` a feature endpoint (ARCHITECTURE) | not imported in `main.py:17-29` | labelled unregistered / dead code |
| "669 tests / 56 modules"; "17 route modules" | 930 passed, 64 modules; 17 files incl. unregistered `mentor.py` | 930 / 64; 16 registered route modules |
| CI "lint →" gate | `ci.yml:40,67 continue-on-error: true` | lint marked non-blocking |

AC4 grep (`compose_grounded_answer|tenacity|chroma|10 / 300|stateless|669|8 sub-agents|Specialized Agents|hybrid|cross-encoder|multi-agent|enforced`)
over README/ARCHITECTURE: every remaining hit is a corrected statement (or "stateless JWT", unrelated).

## 5. Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC1 baseline output recorded | MET | §1 — five commands, exit codes, summary lines |
| AC2 smoke test for all 8 workflows passes offline | MET | `tests/test_workflow_smoke.py` (8 workflow ids × 4 + 1); `33 passed, 3 warnings in 6.46s` |
| AC3 manual smoke checklist for all 8 workflows | MET | `docs/SMOKE_CHECKLIST.md` rows 1–8 |
| AC4 no README/ARCHITECTURE claim contradicts source | MET | §4 table + grep; review findings 1–2 fixed |
| AC5 dead-code ADR | MET | `docs/DECISIONS.md` ADR-006 |
| AC6 no change under `src/ frontend/ migrations/ .github/` | MET | close-gate `git diff --stat` empty |
| AC7 no new failures vs. baseline | MET | 897 passed → 930 passed, 0 failed |

Live workflow smoke is **not** an acceptance criterion of this phase and remains **UNVERIFIED**.

## 6. Review
`review-changes` (2026-11-02): 0 BLOCKING; SHOULD-FIX 1–3 and NIT 4–5 fixed at the user's request; NIT 6
(LF/CRLF working-copy warnings) accepted, no action. Reviewer agents not spawned (docs + one test file);
no `security-reviewer` needed — no auth/upload/prompt/vector code changed.

## 7. Files changed (`git status --porcelain`)
```
 M .claude/specs/README.md
 M .claude/specs/phase-01-baseline-safety-net.md
 M ARCHITECTURE.md
 M README.md
 M docs/DECISIONS.md
 M docs/PROJECT_STATE.md
?? .claude/plans/phase-01-baseline-safety-net.md
?? docs/SMOKE_CHECKLIST.md
?? docs/phase-reports/phase-01-report.md
?? tests/test_workflow_smoke.py
```

## 8. Workflow status (8)
No workflow code changed. All 8 (AI Mentor, NCERT Study, PYQ + Personal PYQ Bank, Answer Evaluator, Current
Affairs, Study Planner, Lecture Learning, PDF / Document Chat): offline smoke PASS 2026-11-02; live smoke UNVERIFIED.

## 9. Backlog added
README §5 size metrics unverified (bundle 257.43 kB vs 244 KB) · emoji startup log `UnicodeEncodeError` on cp1252
(`src/api/main.py:79`) · Starlette httpx / Alembic `path_separator` deprecation warnings · `app.routes` no longer
lists `APIRoute`s (`_IncludedRouter`). All LOW; see `docs/PROJECT_STATE.md` → Backlog.

## 10. Not verified / follow-ups
- Live workflow smoke (`docs/SMOKE_CHECKLIST.md`) — needs real keys / Qdrant / R2 / YouTube / web search: **UNVERIFIED**.
- README §5 size metrics — not re-measured.
- Next: Phase 02 (security & ownership) spec — not started.
