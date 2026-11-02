# Phase 01 — Baseline & safety net

Status: DONE (2026-11-02)
Owner: user  ·  Created: 2026-11-02  ·  Related risks: #15, #16 (audit sections O, P, T)

## 1. Goal
Establish a trustworthy baseline before any behaviour change: recorded test/lint/build results, a repeatable
smoke check for each of the 8 workflows, and README/ARCHITECTURE text that matches the code.

## 2. Non-goals
- No change to application behaviour in `src/` or `frontend/src/` (no fixes, refactors or reformatting).
- No dependency, schema, migration, CI, Docker or `render.yaml` change. CI lint stays non-blocking (Phase 09).
- No fixing of failing tests found by the baseline run — they are recorded in the Backlog (testing rules).
- No deletion of dead code in this phase (deletion needs explicit approval; decision recorded as an ADR only).
- Security/ownership gaps (Phase 02), blocking handlers (Phase 03) are out of scope.

## 3. Current behaviour (verified from source, 2026-11-02)
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Test inventory | `tests/` | 63 `test_*.py` modules, 574 `def test_` functions, 66 `parametrize` lines. README claims "669 tests / 56 modules" (`README.md:12,25,283`). Collected count UNVERIFIED until run. |
| Suite results | — | Never run in this workspace. UNVERIFIED. |
| CI lint | `.github/workflows/ci.yml:40,67` | ruff and prettier steps are `continue-on-error: true`. |
| Router registration | `src/api/main.py:211-241` | `auth` open; `planner, ncert, lecture, current_affairs, upload, pyq, evaluator, history, feedback, chat` + optional `progress, cost, jobs, experiments, monitoring` behind `get_current_user`. `mentor` not imported. |
| Workflow endpoints (`/api/v1` +) | `src/api/routes/*.py` | Mentor `POST /agent/chat/stream`; NCERT `GET /ncert/classes`, `…/subjects/{c}`, `…/chapters/{c}/{s}`, `POST /ncert/study`, `/ncert/chat`; PYQ `POST /pyq/generate`, `/pyq/parse`, `GET /pyq/topics/{t}`, `POST /pyq/bank/{upload,generate,clear}`, `GET /pyq/bank/status`; Evaluator `POST /evaluator/{evaluate,mains,model-answer}/sync`; Current Affairs `POST /current-affairs/{daily,editorial,monthly}`, `GET …/{topics,dates,months}`; Planner `POST /planner/generate`, `/planner/generate/sync`; Lecture `POST /lecture/{process,process-text,chat}`; Upload `POST /upload/{process,chat}`. |
| Existing smoke tests | `tests/test_smoke_auth.py`, `tests/test_smoke_health.py` | Auth flow + health only. No single per-workflow smoke check; workflow routes are exercised piecemeal (e.g. `test_stream_guard.py`, `test_planner_evaluator.py`). |
| "8 agents behind a supervisor" | `src/graph/supervisor.py` | One structured routing call to 5 routes (mentor/planner/evaluator/current_affairs/rag). NCERT/PYQ/Lecture/Upload are called directly by routes. |
| "Per-agent LangGraph pipelines" | `src/agents/*/graph.py` | Plain functions (audit). |
| Plan-execute escalation | `src/graph/supervisor.py:99-134`, `config.py:301` | Exists but `plan_execute_enabled=False` by default. README presents it as active (`README.md:130`). |
| Hybrid retrieval | `src/core/retrieval.py:213 rerank_scored_documents` | Lexical arm only re-scores dense candidates (RRF over the same hits); no independent lexical retrieval. |
| Cross-encoder rerank | `src/core/vector_store.py:698`, `config.py:320` | `rerank_enabled=False`; local provider needs `sentence-transformers` (not a dependency). |
| `compose_grounded_answer` | `src/core/grounding.py:199` | Defined, never called in `src/`. |
| tenacity retries | `pyproject.toml:41` | Declared dependency; not imported anywhere in `src/`. |
| Chroma fallback | `src/core/vector_store.py:543,578,606` | Imports `langchain_chroma`, not installed → fails. |
| Auth rate limit 10/300 s | `src/core/config.py:178-179` | Settings exist; never read. Only the global limiter applies. |
| "Stateless API" | `job_queue.py`, `vector_store.py`, `memory.py` | In-process thread pool, in-process caches, in-memory store fallback (audit). |
| `ARCHITECTURE.md` route list | `ARCHITECTURE.md:40` | Lists `mentor.py` as a feature endpoint; it is unregistered. |
| Dead code | `src/api/routes/mentor.py`, `src/agents/upload/upload.py`, `src/index.css` | Unregistered router (still imported by `tests/test_stream_citations.py`); unmounted duplicate router; stray CSS in backend package. |

## 4. Requirements
- R1 Run the offline gates and record exact summary lines: `uv run pytest -q`, `uv run ruff check src tests`,
  `uv run python -m compileall -q src tests`, `cd frontend && npm run build`, `cd frontend && npm run lint`.
- R2 Any failing test or lint finding is recorded (file, evidence, severity) in `docs/PROJECT_STATE.md` → Backlog,
  not fixed.
- R3 Add an **offline automated workflow smoke test** (`tests/test_workflow_smoke.py`): for each of the 8 workflows,
  the primary endpoint is mounted, returns 401 without a token, and returns 422 for an invalid body with a token.
  No LLM, network or vector store is touched.
- R4 Add a **manual live smoke checklist** (`docs/SMOKE_CHECKLIST.md`): per workflow — UI page, endpoint, sample
  request, expected response shape, required external services (keys, Qdrant, R2, YouTube, web search).
- R5 Correct every doc-drift item in §3 in `README.md` and `ARCHITECTURE.md` so no claim contradicts source.
  Factual edits only; keep structure and tone.
- R6 Record an ADR for dead code: decision + the phase in which removal happens.
- R7 Update `docs/PROJECT_STATE.md`: test-suite status, workflow table "last verified" column, Doc-drift section.

## 5. Affected workflows
None behaviourally. Only tests and docs change. All 8 are covered by the new smoke test (route mount + auth +
validation) and the manual checklist.

## 6. Acceptance criteria (testable)
- [x] AC1 Phase report contains the exact summary lines of all five R1 commands.
- [x] AC2 `tests/test_workflow_smoke.py` exists, covers all 8 workflows, and passes offline.
- [x] AC3 `docs/SMOKE_CHECKLIST.md` lists all 8 workflows with endpoint, sample request, expected shape, dependencies.
- [x] AC4 `grep` of README/ARCHITECTURE for each §3 drift claim finds no contradicting statement (list in report).
- [x] AC5 ADR for dead code appended to `docs/DECISIONS.md`.
- [x] AC6 `git diff --stat -- src frontend migrations .github` is empty.
- [x] AC7 `uv run pytest -q`: no new failures vs. the baseline run (pre-existing failures listed in Backlog).

## 7. Risks & mitigations
- Baseline may reveal failing tests → recorded, not fixed; AC7 compares against the baseline, not "all green".
- Frontend build/lint may need `node_modules`; if absent we cannot `npm install` → mark UNVERIFIED and ask the user.
- 422 assertions may depend on body schemas; read each route's Pydantic model before writing the test.
- README edits could remove portfolio framing the user wants → factual corrections only; user reviews the diff.

## 8. Test plan
- New `tests/test_workflow_smoke.py`, parametrised over 8 workflows. Copies `client` / `make_user` fixtures
  (`tests/conftest.py`) and the 401 pattern from `tests/test_security_ownership.py`. Asserts status **and**
  `detail` shape (behaviour, not just codes).
- No test for dead code (`mentor.py` is unregistered — testing rules).

## 9. Open questions
1. Dead code ADR: keep `mentor.py` / `upload/upload.py` / `src/index.css` until Phase 09 (recommended — `mentor.py`
   is still imported by a test), or delete in this phase?
2. README tone: correct claims in place (recommended), or also add a short "Known limitations" section?
3. Is the offline smoke test (mount + 401 + 422) enough for R3, or do you also want mocked-LLM happy paths for
   all 8 workflows (larger; many already exist piecemeal)?
