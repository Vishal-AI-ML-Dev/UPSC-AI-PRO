# Testing

How the project is tested, what the numbers mean, and — equally important — what is **not** tested. Counts were taken on
2026-11-05 from a working tree with uncommitted Phase 09 changes, using `pytest --collect-only` (nothing was executed).

## Terminology (do not mix these up)
| Term | Meaning | Current value |
|---|---|---|
| Test **functions** | `def test_…` definitions | 1,111 |
| Collected **test cases** | What pytest runs after parametrization | **1,847** |
| Passing cases | Cases that passed in a full run | Last recorded: **1765 passed, 2 skipped** (Phase 08 close, 2026-11-04); `1790 passed, 2 skipped` on the dirty Phase 09 tree. **Not re-run for this documentation task.** |
| Skipped | Conditional skips | 2 (Windows file-symlink privilege), per project records |
| Live integration tests | Tests that call real external services | **None in the default suite** |
| Synthetic benchmarks | Scripted performance/quality harnesses | Separate scripts under `scripts/` and `evals/`; see [EVALUATION.md](EVALUATION.md) |

The offline suite is a **unit/integration suite with mocked dependencies**. It is *not* production integration testing and
must not be described as such. A figure of "1,845 passed" circulated in a task brief; it is not recorded anywhere in the repository and is
UNVERIFIED.

## Setup and commands
```bash
uv sync --frozen                       # install the locked environment
uv run pytest -q                       # whole offline suite (no keys, no network)
uv run pytest -q tests/test_scope_gate.py -k greeting      # focused run
uv run pytest --collect-only -q        # count cases without running them
uv run ruff check src tests            # lint (non-blocking in CI today)
uv run python -m compileall -q src tests
cd frontend && npm ci && npm run build && npm run lint     # type-check + build, prettier check
```
A full run took roughly 3.5 minutes on the author's Windows machine in Phase 08 (`213.62s`); CI timing is not recorded here.

## Test environment (`tests/conftest.py`)
- Environment variables are set **before** any `src.*` import, so the real `.env` is never used: a throwaway SQLite database in the
  OS temp directory, a test JWT secret, empty Google/Groq keys, Langfuse off, and very high rate limits so the shared test client is
  not throttled.
- The `client` fixture boots the **real FastAPI app** (lifespan, middleware, routers) with Starlette's `TestClient`; the
  `make_user` fixture registers and verifies a user and returns auth headers.
- **Mocked/faked:** LLMs (`RunnableLambda` returning `AIMessage`, or monkeypatched functions), outbound email, YouTube/network
  calls, web search, and embeddings in the retrieval-benchmark wiring tests. Qdrant is exercised in embedded local mode
  (`test_qdrant_local.py`), not against Qdrant Cloud.
- **Database:** SQLite only. PostgreSQL behaviour (advisory-lock migrations, timestamptz comparisons in the job lease, RLS) was checked
  manually on disposable PostgreSQL 15/17 staging databases during Phase 08 and is not part of the automated suite.

## Organisation (by area)
| Area | Representative files |
|---|---|
| Auth and sessions | `test_smoke_auth.py`, `test_single_session.py`, `test_auth_rate_limit.py`, `test_secret_utils.py` |
| Ownership / isolation | `test_security_ownership.py`, `test_chat_isolation.py`, `test_upload_ownership.py`, `test_lecture_ownership.py`, `test_pyq_ownership.py` |
| Prompt safety and scope | `test_prompt_safety.py`, `test_scope_gate.py`, `test_study_aids_fencing.py`, `test_*_prompts.py` |
| Mentor, routing and tools | `test_supervisor_routing.py`, `test_tools.py`, `test_tool_agent_turns.py`, `test_mentor_routes.py`, `test_mentor_history_bound.py`, `test_plan_execute.py` |
| Retrieval and grounding | `test_rrf_fusion.py`, `test_retrieval_rerank.py`, `test_advanced_retrieval.py`, `test_grounding.py`, `test_rag_citations.py`, `test_ncert_evidence.py` |
| Streaming | `test_stream_citations.py`, `test_stream_guard.py`, `test_stream_notice_prefix.py` |
| Jobs and uploads | `test_job_queue_durability.py`, `test_upload_limits.py`, `test_input_limits.py` |
| Persistence | `test_migrations.py`, `test_study_plans.py`, `test_evaluation_attempts.py`, `test_evaluation_analytics.py`, `test_progress_overview.py` |
| Operations | `test_circuit_breaker.py`, `test_rate_limiter.py`, `test_response_cache.py`, `test_error_monitoring.py`, `test_logging_privacy.py`, `test_async_routes.py` |
| Eval harness | `test_bench_retrieval.py`, `test_production_evals.py`, `test_eval_*.py`, `test_retrieval_eval.py` |
| End-to-end smoke (offline) | `test_workflow_smoke.py`, `test_smoke_health.py` |

Project rules (see `.claude/rules/testing.md`): every bug fix ships a regression test; every new endpoint tests 401, other-user
(404/403), happy path and invalid input; security changes include a test that the attack is refused.

## CI (`.github/workflows/ci.yml`)
| Job | Runs | Blocking? |
|---|---|---|
| `tests` | `uv sync --frozen`; `ruff check src tests` (**`continue-on-error`**); `compileall`; `pytest -q` | Tests and compile: yes. Ruff: no |
| `frontend` | `npm ci`; `npm run build` (tsc + vite); `npm run lint` (prettier, **`continue-on-error`**) | Build: yes. Prettier: no |
| `llm-eval` | Live LLM-as-judge gate via `python -m src.eval.llm_eval --gate 0.9 …` and retrieval report | Runs **only nightly or on manual dispatch**, needs secrets, and tests the legacy CRAG graph that production does not serve. Whether it has run or passed in GitHub is UNVERIFIED here |
| `smoke-live` | Live deployment smoke test | **Disabled** (`if: false`; `tests/test_smoke_live.py` does not exist) |

Consequently the CI badge/claims should read "offline tests and build", not "faithfulness gate enforced on every commit".

## Skipped and opt-in tests
- 2 skips: file-symlink escape checks in `tests/test_bench_retrieval.py` need symlink privilege on Windows; `test_ncert_evidence.py` skips
  when `data/ncert` is absent.
- Anything needing live keys (Gemini/Groq judge, T3 evals, live retrieval benchmark) is opt-in and **not** in `pytest`.

## Not covered (gaps)
No frontend unit or end-to-end tests; no load tests against production; no automated PostgreSQL or Qdrant Cloud integration tests; no
real-model evaluation in CI on every change; no external security test.
