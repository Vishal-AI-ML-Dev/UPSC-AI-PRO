# Plan — Phase 03 Request-path performance

Spec: `.claude/specs/phase-03-request-path-performance.md`  ·  Status: DONE (2026-11-02; Amendments 1–2) — report `docs/phase-reports/phase-03-report.md` (approved: Q1 yes → R3 in, Q4 → no `.env.example`)

## Files to touch (exhaustive — anything else needs a plan amendment)
| File | Change | Req |
|---|---|---|
| `src/api/routes/evaluator.py` | `evaluate_sync`, `mains_eval_sync`, `model_answer_sync`: `async def` → `def` | R1 |
| `src/api/routes/planner.py` | `generate_sync`: `async def` → `def` | R1 |
| `src/api/routes/pyq.py` | `parse`, `bank_status`, `bank_clear`: → `def`; `bank_upload` sync branch: `return await run_in_threadpool(_work)` (+ import) | R1, R2 |
| `src/api/routes/upload.py` | `upload_pdf` sync branch: `return await run_in_threadpool(_work)` (+ import) | R2 |
| `src/api/routes/lecture.py` | `process`, `process_text`: → `def` | R1 |
| `src/api/routes/history.py` | `list_conversations`, `get_messages`, `save_message`: → `def` | R1 |
| `src/api/routes/auth.py` | `forgot_password`, `resend_verification`: → `def` | R1 |
| `src/api/routes/feedback.py` | `submit_feedback`: → `def` | R1 |
| `src/api/routes/progress.py` | `progress_overview`: → `def` | R1 |
| `src/api/routes/experiments.py` | `experiments_overview`: → `def` | R1 |
| `src/api/routes/jobs.py` | `job_status`: → `def` | R1 |
| `src/api/rate_limit.py` | `dispatch`: run `check` via `run_in_threadpool` when the limiter's redis is enabled; in-memory path inline (small helper, e.g. `_uses_redis()`) | R3 |
| `src/api/rate_limit_core.py` | **Amendment 1 (review, approved 2026-11-02):** `threading.Lock` in `_InMemoryLimiter.hit`; public `RateLimiter.uses_redis()` | R3 (S2, N1) |
| `scripts/bench_request_path.py` (new) | **Amendment 2:** benchmark driver (scenarios, load, percentiles, JSON output, `--before-ref` via `git archive`) | metrics |
| `scripts/bench_server.py` (new) | **Amendment 2:** offline benchmark server: real app, one uvicorn worker, synthetic LLM + Upstash, no `.env`/credentials | metrics |
| `docs/ENGINEERING_METRICS.md` (new) | **Amendment 2:** canonical metrics doc (definitions, how to run, Phase 03 synthetic results, UNVERIFIED list) | metrics |
| `src/graph/tools.py` | new pure `bound_history(messages, max_turns)`; `agent_node` sends the bounded list (reads `settings.mentor_history_max_turns`) | R5 |
| `src/core/config.py` | `mentor_history_max_turns: int = 6` | R6 |
| `tests/test_async_routes.py` (new) | allow-list guard; loop-responsiveness tests (evaluator, upload `sync=true`); rate-limiter off-loop test (Q1) | R4, AC1–AC3 |
| `tests/test_mentor_history_bound.py` (new) | `bound_history` unit tests + `agent_node` integration with recording fake LLM | AC4 |
| `docs/DECISIONS.md` | ADR-011 | R7 |
| `docs/PROJECT_STATE.md` | phase table, risks #2/#5, workflow table, backlog, handoff (at close) | R7 |
| `docs/phase-reports/phase-03-report.md` (new, at close) | evidence: allow-list, test output, review results | AC1 |
| `.claude/specs/phase-03-request-path-performance.md`, this plan | status updates | — |

Not touched: frontend (no contract change), `src/api/main.py`, `src/api/stream_guard.py`, agents' `graph.py` files,
`src/core/upstash.py`, migrations, dependencies.

## Steps
1. **Tests first (red).** Write `tests/test_async_routes.py`:
   - `test_async_handlers_are_allow_listed`: `ast`-parse `src/api/routes/*.py` (skip unregistered `mentor.py`) and
     `src/api/main.py`; collect `AsyncFunctionDef`s decorated with `@router.<verb>` / `@app.<verb>`; assert the set ⊆
     `ALLOWED_ASYNC` (spec 3b, as `"module:function"`), message explains the rule. Also assert every name in the
     allow-list exists (no stale entries).
   - `test_blocking_sync_endpoint_does_not_stall_loop`: override `get_current_user` via `app.dependency_overrides`;
     patch `src.api.routes.evaluator.evaluate_answer` with a generator that `time.sleep(1.0)` then yields; with
     `httpx.AsyncClient(transport=ASGITransport(app))`, `gather` the POST and a `GET /health` started ~50 ms later;
     assert `/health` finished in < 0.5 s and the POST still returned 200 with the same shape.
   - Same for `POST /upload/process?sync=true` with `process_upload` patched to sleep (valid minimal PDF bytes as in
     `tests/test_upload_limits.py`).
   - (Q1) `test_rate_limiter_upstash_check_off_loop`: middleware built with a fake enabled redis whose `command`
     sleeps 1 s; while a request goes through the middleware, an `asyncio` ticker task must keep ticking (loop lag
     < 0.2 s). (`/health` may be rate-limit-exempt, so loop lag is measured directly.)
   Write `tests/test_mentor_history_bound.py` (unit + `agent_node` via `build_tool_agent` with a `RunnableLambda` that
   records the messages it receives).
   Run them; confirm the loop/bound tests fail for the expected reason.
2. **R1** — mechanical `async def` → `def` edits in the 10 route files listed. No other line changes.
3. **R2** — `run_in_threadpool` in `upload.py` / `pyq.py` sync branches.
4. **R3** (if Q1 = yes) — rate-limit `dispatch` change.
5. **R5/R6** — `config.py` setting; `bound_history` + `agent_node` wiring.
6. Run focused tests, then the full gates (below). Fix only in-scope failures; log others to the backlog.
7. `security-reviewer` + `code-quality-reviewer` agents on the diff; address BLOCKING/SHOULD-FIX.
8. `/complete-phase`: report, ADR-011, PROJECT_STATE.

## Reused code
- `starlette.concurrency.run_in_threadpool` (already used in `src/api/upload_limit.py:170`, `src/api/stream_guard.py:116`).
- Turn helpers `current_turn`, `_message_type` (`src/graph/tools.py:385`); `settings` (`src/core/config.py`).
- Test patterns: `httpx.ASGITransport` (`tests/test_rate_limiter.py:104`), fake LLM `RunnableLambda`
  (`tests/test_tool_agent_turns.py`), `client`/`make_user` fixtures (`tests/conftest.py`).

## Verification
```bash
uv run pytest -q tests/test_async_routes.py tests/test_mentor_history_bound.py
uv run pytest -q tests/test_tool_agent_turns.py tests/test_mentor_routes.py tests/test_stream_citations.py \
  tests/test_rate_limiter.py tests/test_auth_rate_limit.py tests/test_planner_evaluator.py \
  tests/test_upload_limits.py tests/test_pyq_ownership.py tests/test_lecture_ownership.py tests/test_workflow_smoke.py
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
git diff --stat   # only files from the table above
```
Manual / workflow checks: live smoke (`docs/SMOKE_CHECKLIST.md`) needs keys — UNVERIFIED unless the user runs it.
Optional manual: run the API locally, start a `POST /evaluator/evaluate/sync`, and confirm `GET /health` answers
immediately.

## Rollback
Revert the listed files (`git checkout -- <files>` or revert the phase commit). No migration, no dependency, no data
change; the new setting has a default, so removing it needs no env change.

## Amendment 1 — review fixes (approved by the user 2026-11-02)
Review findings S1–S3 + N1 from `/review-changes`:
- **S1** `src/graph/tools.py` `bound_history`: each earlier turn keeps its question + only its LAST non-tool-call AI
  message (other routes' answers no longer pile up); unanswered earlier questions are kept as the question alone and
  count as a turn; no tool calls/results from earlier turns; current turn intact. Tests in
  `tests/test_mentor_history_bound.py` that encoded the old behaviour are rewritten.
- **S2/N1** `src/api/rate_limit_core.py` (added to the table): lock around the in-memory window; public
  `RateLimiter.uses_redis()`; `src/api/rate_limit.py` uses it; `_check` annotated `-> Decision`.
- **S3** `src/api/rate_limit.py`: Upstash checks run on one module-level `anyio.CapacityLimiter(8)` via
  `anyio.to_thread.run_sync` (shared by all requests; never the default threadpool); docstring corrected.
- Tests first (`tests/test_async_routes.py`, `tests/test_rate_limiter.py` untouched → new tests go in
  `tests/test_async_routes.py`): concurrent in-memory correctness during an Upstash outage, dedicated shared budget,
  saturated budget does not consume the default threadpool or block unrelated requests.

## Amendment 2 — benchmark harness + final decisions (approved by the user 2026-11-02)
- **S1 review decision APPROVED** (separate from spec Q1, which was the Upstash offload): unanswered earlier mentor questions are preserved and each counts as one of the six
  earlier turns; orphaned tool calls/results are never included (already implemented and tested).
- **Benchmark harness in the repo:** the scratchpad `bench.py` / `serve.py` become `scripts/bench_request_path.py` and
  `scripts/bench_server.py` (documented, reusable; never read `.env` or credentials; synthetic LLM + Upstash kept).
- **`docs/ENGINEERING_METRICS.md`** did not exist; created as the canonical metrics document. The phase report keeps
  the original measurements; `docs/PROJECT_STATE.md` and this plan reference both.
- Verification adds: a reproducibility run with the documented command; `ruff check` + `compileall` on the two scripts.
- Review fixes (2026-11-02): server-side environment isolation (`ENV_ALLOW` in `bench_server.py`), per-run
  `X-Bench-Nonce` readiness + busy-port refusal + port-release wait, argument validation, `-`-ref guard, probe error
  counting, per-run temp dir removed on success; doc claims aligned with all five runs.
