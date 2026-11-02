# Phase 03 report — Request-path performance

Date: 2026-11-02 · Status: **DONE** (closed with `/complete-phase 3` on the user's instruction; not committed) ·
Spec: `.claude/specs/phase-03-request-path-performance.md` (DONE; Q1 yes, Q2 no, Q3 question+answer, Q4 6) ·
Plan: `.claude/plans/phase-03-request-path-performance.md` (DONE; Amendments 1, 2) · ADRs: ADR-011, ADR-012

## Summary
- 18 `async def` route handlers that ran sync DB / LLM / Qdrant / SMTP / HTTP work on the event loop are now plain
  `def` (FastAPI threadpool). The `sync=true` branches of `upload/process` and `pyq/bank/upload` stay `async` (they
  await the upload read) and offload the work with `run_in_threadpool`.
- The Upstash rate-limit check (blocking HTTP, up to the 2 s REST timeout) no longer runs on the loop: it runs on one
  dedicated, process-wide `anyio.CapacityLimiter(8)`, never on the default threadpool. The in-memory window is now
  lock-protected (it can run on several threads during an Upstash failure). `RateLimiter.uses_redis()` is public.
- The AI Mentor sends a bounded window of the checkpointed thread to the model: the whole current turn plus the last
  `mentor_history_max_turns` (default 6) earlier turns, each reduced to the question + that turn's last answer. Earlier
  tool calls/results never reach the provider. Unanswered earlier questions are preserved and each counts as one of the six turns
  (user decision, 2026-11-02). The checkpoint still stores everything.
- A static test pins every remaining `async def` route to a reviewed allow-list, so new blocking `async` routes fail CI.
- No API path, request/response shape, status code, error text, schema, migration, dependency or frontend change.

## Test-first evidence (failing before the fix, recorded in this session)
| Test | Before fix |
|---|---|
| `test_async_handlers_are_allow_listed` | failed, listing exactly the 18 handlers (spec 3a) |
| `test_blocking_sync_endpoint_does_not_stall_loop` | `/health waited 1.01s behind a blocking handler` |
| `test_sync_upload_branch_does_not_stall_loop` | `/health waited 1.01s behind a sync upload` (first version passed before the fix — rewritten to time from the moment the blocking work starts) |
| `test_rate_limiter_upstash_check_runs_off_loop` | `event loop stalled 0.80s on the Upstash call` |
| `test_in_memory_window_is_exact_under_concurrent_threads` | `80 == 20` (80 admitted, limit 20) |
| `test_upstash_outage_fallback_is_exact_under_concurrent_threads` | `50 == 10` |
| `test_concurrent_requests_during_upstash_outage_through_middleware` | all 30 requests 200 (limit 10) |
| `test_uses_redis_is_public_and_reflects_backend` | `AttributeError` |
| `test_upstash_checks_use_one_dedicated_budget_of_eight` | `AttributeError: _UPSTASH_THREADS` |
| `tests/test_mentor_history_bound.py` | module `ImportError` (no `bound_history`); after S1 tests were rewritten, `test_earlier_turn_keeps_only_its_last_answer` and `test_many_other_route_answers_do_not_bypass_the_bound` failed against the first implementation |
Not shown red: `test_sync_pyq_bank_upload_does_not_stall_loop` (added after the R2 fix, during review; same mechanism
as the upload test), `test_sync_upload_branch_keeps_error_mapping`, `test_rate_limiter_upstash_limit_still_enforced`
and the unanswered/interrupted-turn history tests (behaviour locks that already held).

## Files changed (`git status --porcelain`)
```
 M .claude/specs/phase-03-request-path-performance.md   (DRAFT → APPROVED, answers)
 M docs/PROJECT_STATE.md
 M src/api/rate_limit.py            R3/S3: dedicated CapacityLimiter(8), _check, docstring
 M src/api/rate_limit_core.py       S2/N1: lock in _InMemoryLimiter.hit, uses_redis()   (plan Amendment 1)
 M src/api/routes/auth.py           forgot_password, resend_verification → def
 M src/api/routes/evaluator.py      evaluate_sync, mains_eval_sync, model_answer_sync → def
 M src/api/routes/experiments.py    experiments_overview → def
 M src/api/routes/feedback.py       submit_feedback → def
 M src/api/routes/history.py        list_conversations, get_messages, save_message → def
 M src/api/routes/jobs.py           job_status → def
 M src/api/routes/lecture.py        process, process_text → def
 M src/api/routes/planner.py        generate_sync → def
 M src/api/routes/progress.py       progress_overview → def
 M src/api/routes/pyq.py            parse, bank_status, bank_clear → def; bank_upload sync branch offloaded
 M src/api/routes/upload.py         upload_pdf sync branch offloaded
 M src/core/config.py               mentor_history_max_turns: int = 6
 M src/graph/tools.py               bound_history(); agent_node sends the bounded window
?? .claude/plans/phase-03-request-path-performance.md
?? docs/ENGINEERING_METRICS.md        canonical metrics page (Amendment 2; did not exist before)
?? docs/phase-reports/phase-03-report.md
?? scripts/bench_request_path.py      benchmark driver (Amendment 2)
?? scripts/bench_server.py            offline benchmark server (Amendment 2)
?? tests/test_async_routes.py         (13 tests)
?? tests/test_mentor_history_bound.py (14 tests)
```
Every file is in the plan's **Files to touch** table (incl. Amendments 1 and 2). No existing test file was modified.

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC1 no `async def` route does sync work | MET | `test_async_handlers_are_allow_listed` + `test_allow_list_has_no_stale_entries`; allow-list = spec 3b (23 handlers: pure/in-memory, streaming generator builders, two upload handlers that offload) |
| AC2 loop responsiveness | MET | evaluator, upload `sync=true`, PYQ bank `sync=true` tests; benchmark scenario A below |
| AC3 Upstash check off the loop | MET | `test_rate_limiter_upstash_check_runs_off_loop`, dedicated-budget test; benchmark C/D |
| AC4 mentor prompt bound | MET | 14 tests incl. a 20-turn run through the real graph: model gets system + 2 earlier Q/A pairs + current turn; checkpoint keeps all 84 messages |
| AC5 shapes / status codes unchanged | MET | no existing test edited; full suite green; shape assertions in the new tests |
| 8 workflows (offline smoke) | MET | `tests/test_workflow_smoke.py` in the full run |
| Gates | MET | see below |
| Security + code-quality review, 0 BLOCKING | MET (see Reviews) | |

## Gate results (final run, verbatim)
`/complete-phase` gate run. All fixes were in place; no code changed after the closing run below.
- `uv run pytest -q` → `1113 passed, 13 warnings in 112.29s (0:01:52)`
- `uv run ruff check src tests` → `All checks passed!`
- `uv run python -m compileall -q src tests` → exit 0
- `frontend/`: 0 changed files, so `npm run build` / `npm run lint` do not apply.

Closing run after all review fixes, including the harness:
- **Focused tests**, 5 consecutive runs: `63 passed, 3 warnings in 9.75s` / `9.39s` / `9.58s` / `9.64s` / `9.65s`.
  - Files: `tests/test_async_routes.py`, `tests/test_rate_limiter.py`, `tests/test_auth_rate_limit.py`,
    `tests/test_mentor_history_bound.py`, `tests/test_tool_agent_turns.py`.
- **Full suite:** `uv run pytest -q` → `1113 passed, 13 warnings in 118.16s (0:01:58)` (Phase 02 close: 1086).
- **Lint:** `uv run ruff check src tests scripts/bench_request_path.py scripts/bench_server.py` →
  `All checks passed!`. `ruff format --check` on the 4 new Python files → `4 files already formatted`.
- **Compile:** `uv run python -m compileall -q src tests scripts/bench_request_path.py scripts/bench_server.py` →
  exit 0.
- **Whitespace:** `git diff --check` → exit 0. The untracked new files have no trailing whitespace and no control
  bytes.
- **Frontend:** not touched, so no build was needed.

Earlier verification run, before the harness review: focused `52 passed` ×5 (4 files); full suite
`1113 passed, 13 warnings in 116.71s`.

### Test hang observed once (cause UNVERIFIED)
One full-suite run hung inside `test_sync_upload_branch_does_not_stall_loop`. It did not reproduce in 15 isolated runs,
a full-collection run, or two later full runs. The code-quality review identified a plausible cause in the test
helper (`_slow_then_health` polled forever if the request failed before reaching the patched function); that loop
now exits when the request finishes, the exchange is capped with `asyncio.wait_for(15 s)`, and the failure status is
reported. An overlap with a previously stopped test process (shared SQLite file) was also possible but could not be
checked (process listing was denied by the permission classifier). Runs since use pytest's
`faulthandler_timeout` + `faulthandler_exit_on_timeout` so any hang dumps stacks and exits.

## Reviews
| Round | security-reviewer | code-quality-reviewer |
|---|---|---|
| 1 | 0 BLOCKING · 2 SHOULD-FIX (unlocked in-memory limiter on threads; limiter on default pool) | 0 BLOCKING · 3 SHOULD-FIX (S1 history unbounded with other routes' answers; S2, S3 as security) |
| 2 (after Amendment 1) | 0 BLOCKING · 0 SHOULD-FIX · 3 NIT | 0 BLOCKING · 2 SHOULD-FIX (test helper could hang; flaky timing asserts in budget test) · 5 NIT |
| 3 (test fixes) | — | SF1 RESOLVED, SF2 RESOLVED · 0 BLOCKING · 0 SHOULD-FIX · 2 optional NIT |
| 4 (harness + docs, Amendment 2) | 0 BLOCKING · 1 SHOULD-FIX (server relied on the driver's env allow-list) · 6 NIT | 0 BLOCKING · 3 SHOULD-FIX (stale/foreign server could be measured; stale handoff; report overclaims) · 9 NIT |
| 5 (harness fixes) | S1 RESOLVED · NITs resolved or accepted · 0 BLOCKING · 0 SHOULD-FIX · 5 NIT | SF1–SF3 RESOLVED · 0 BLOCKING · 1 SHOULD-FIX (a literal backspace byte in the PowerShell example of `docs/ENGINEERING_METRICS.md`) · 7 NIT |

After round 5 the backspace byte was fixed. Both reviewers had flagged it, the security reviewer as a NIT. Cheap NITs
were applied at the same time:
- `+36` → `+37` ms, prober wording, scoping the B "direction" claim, refreshed gate results;
- the server refuses a non-empty `--workdir`;
- the code-root comparison uses `normcase`.

Verification after those fixes:
- No source or doc file contains a control byte.
- A scenario-A before/after smoke run matched R3.
- The full gates above were re-run.

**Final review status: 0 BLOCKING, 0 SHOULD-FIX across both reviewers.** Every SHOULD-FIX from rounds 1, 2, 4 and 5
was fixed:
- S1–S3 via plan Amendment 1;
- SF1–SF2 in `tests/test_async_routes.py`;
- harness and doc items via Amendment 2.

Each was confirmed resolved by the reviewer that raised it, except the round-5 backspace byte. That fix is a one-byte
doc change, verified by a byte scan, and was not sent back for a sixth review round. Remaining NITs are listed below.

## Engineering metrics (measured in this session — synthetic, local)
> **SYNTHETIC, LOCAL, SINGLE-WORKER MEASUREMENTS.** The LLM and Upstash are fixed `time.sleep` fakes, the server is one
> uvicorn worker on `127.0.0.1`, and auth is stubbed. These numbers show how the *request path* behaves when work
> blocks (queueing, event-loop stalls, the Upstash thread budget). They are **not** production latency and must not be
> cited as a production performance improvement. **Render latency, real LLM (Gemini/Groq) latency and production
> throughput are UNVERIFIED** — never measured.

No Phase 03 document (spec, plan, audit, README) assigned numeric latency/throughput targets; these measurements were
requested at verification time. Canonical metrics page: [`docs/ENGINEERING_METRICS.md`](../ENGINEERING_METRICS.md).

**Environment.** Windows 11 Home (10.0.26300), Intel64 Family 6 Model 140 (8 logical CPUs), Python 3.13.13,
uvicorn 0.49.0 (one worker, `127.0.0.1`, real HTTP), anyio 4.13.0. Before = `git archive HEAD` (`ad7d212`) of `src/`,
`migrations/`, `alembic.ini`; After = working tree. Both run by the same harness and venv.

**Harness.** Original measurements (below) were taken with `serve.py` / `bench.py` in the session scratchpad; they were
then moved, unchanged in method, into the repository as `scripts/bench_server.py` / `scripts/bench_request_path.py`
(plan Amendment 2; adds an allow-listed server environment, `--before-ref`, JSON/`null` output and environment
capture). The harness boots the real app (lifespan, all
middleware) with cwd in a temp dir (no `.env` read), throwaway SQLite, local Qdrant path, all keys/Upstash/Sentry/R2
blank, web search off, rate limits raised; `get_current_user` overridden (fixed user). `evaluate_answer` replaced by a
generator that `time.sleep(0.5)` (simulated blocking LLM). Upstash replaced (when RTT > 0) by a fake whose `command`
sleeps RTT. Closed-loop load: N concurrent clients for 8 s after 1 s warm-up; a separate prober hits `GET /health`
and sleeps 50 ms after each response. A request counts if it completes inside the window; latency includes queueing. Two full runs.

| Scenario | Version | Route throughput (req/s) | Route p50 / p95 / p99 (ms) | `/health` p50 / p95 / p99 (ms) | `/health` samples |
|---|---|---|---|---|---|
| A `POST /evaluator/evaluate/sync`, LLM 0.5 s, 10 clients | before | 1.38 / 1.38 | 5125 / 5129 / 5130 · 5071 / 5073 / 5073 | 2733 / 4779 / 4961 · 2975 / 4771 / 4930 | 2 · 2 |
| | after | 19.62 / 19.62 | 516 / 543 / 549 · 517 / 544 / 553 | 4.8 / 8.9 / 13.5 · 5.2 / 11.9 / 20.9 | 128 · 124 |
| B `GET /cost/access`, Upstash off, 20 clients | before | 268.0 / 302.9 | 59 / 200 / 374 · 64 / 92 / 147 | 34.5 / 63.4 / 74.7 · 50.5 / 66.4 / 75.4 | 86 · 79 |
| | after | 279.4 / 305.3 | 59 / 203 / 355 · 57 / 170 / 315 | 34.1 / 63.1 / 68.3 · 22.8 / 64.4 / 66.9 | 88 · 93 |
| C same, Upstash RTT 20 ms | before | 40.1 / 40.8 | 494 / 523 / 531 · 495 / 518 / 530 | 436 / 459 / 466 · 441 / 458 / 458 | 16 · 17 |
| | after | 287.8 / 294.1 | 69 / 93 / 103 · 67 / 94 / 105 | 21.2 / 43.1 / 47.8 · 20.6 / 41.9 / 46.2 | 106 · 109 |
| D same, Upstash RTT 200 ms | before | 5.0 / 5.0 | 4249 / 4404 / 4404 · 4202 / 4338 / 4339 | 4209 / 4371 / 4385 · 4170 / 4304 / 4316 | 2 · 2 |
| | after | 39.25 / 39.25 | 507 / 578 / 601 · 508 / 593 / 601 | 5.5 / 8.9 / 12.3 · 5.2 / 10.6 / 14.5 | 129 · 129 |
(Original measurements, scratchpad harness. Values: run 1 / run 2, or run 1 · run 2.)

**Reproducibility (committed harness, documented command).**
`uv run python scripts/bench_request_path.py --before-ref ad7d212 --out <file outside the repo>` (defaults: warm-up
1 s, duration 8 s, probe 50 ms, LLM 0.5 s, scenarios A–D, 1 run). Three runs with the committed harness. None of
the harness changes between them altered the measurement logic.
- **R1:** taken before the environment-capture and `null`-output fixes.
- **R2:** taken before the review fixes (server-side environment isolation, `X-Bench-Nonce` readiness, port-release
  wait).
- **R3:** taken with the final scripts. Captured environment: Windows-11-10.0.26300, 8 logical CPUs, uvicorn 0.49.0,
  anyio 4.13.0, fastapi 0.137.0, Python 3.13.13. 8 rows, 0 route errors, 0 `/health` errors, run temp dir removed.

| Scenario | Version | rps R1 · R2 · R3 | Route p50 / p95 / p99 ms (R1 · R2 · R3) | `/health` p50 / p95 / p99 ms (R1 · R2 · R3) |
|---|---|---|---|---|
| A | before | 1.5 · 1.5 · 1.62 | 5101 / 5103 / 5103 · 5138 / 5140 / 5141 · 5064 / 5065 / 5065 | 3234 / 4809 / 4949 · 3241 / 4818 / 4958 · 3972 / 4868 / 4948 |
| A | after | 19.38 · 19.12 · 20.0 | 516 / 552 / 558 · 521 / 546 / 560 · 512 / 523 / 526 | 5.3 / 11.4 / 21.9 · 5.1 / 10.1 / 14.5 · 5.1 / 6.5 / 7.9 |
| B | before | 275.0 · 291.5 · 553.25 | 55 / 217 / 362 · 64 / 147 / 273 · 25 / 115 / 195 | 29.4 / 59.3 / 62.3 · 40.4 / 62.9 / 67.9 · 12.3 / 24.8 / 29.7 |
| B | after | 303.9 · 314.5 · 518.5 | 66 / 88 / 191 · 63 / 90 / 186 · 17 / 126 / 222 | 49.3 / 64.5 / 70.4 · 49.7 / 66.1 / 68.8 · 12.5 / 14.7 / 21.4 |
| C | before | 42.75 · 40.62 · 45.75 | 484 / 587 / 621 · 485 / 528 / 541 · 452 / 476 / 749 | 430 / 453 / 454 · 430 / 453 / 460 · 393 / 423 / 424 |
| C | after | 323.6 · 280.9 · 368.25 | 57 / 88 / 97 · 72 / 91 / 97 · 54 / 61 / 64 | 8.3 / 36.8 / 45.0 · 26.4 / 43.8 / 48.3 · 2.9 / 5.4 / 6.5 |
| D | before | 5.0 · 5.0 · 4.88 | 4223 / 4360 / 4361 · 4213 / 4370 / 4370 · 4293 / 4303 / 4303 | 4181 / 4314 / 4326 · 4185 / 4320 / 4332 · 4140 / 4276 / 4288 |
| D | after | 39.25 · 39.12 · 39.38 | 501 / 585 / 619 · 507 / 592 / 613 · 555 / 600 / 605 | 5.8 / 11.8 / 19.1 · 5.4 / 11.6 / 15.6 · 4.8 / 7.1 / 8.6 |

All five runs (two original, three reproducibility) agree on the **direction** of every effect in A, C and D (B has
no effect to agree on), with 0 errors in each. Magnitudes depend on machine state. In R3, B's absolute throughput was roughly double that of the other runs
(519–553 vs 268–315 req/s) for both versions, so treat absolute numbers as specific to one run.

**Reading.**
- *Blocking sync endpoint (A):* before, the 0.5 s "LLM" ran on the loop, so requests were served one at a time
  (1.38–1.62 req/s, ~5 s queueing) and `/health` waited up to ~5 s. After: 19.1–20.0 req/s (≈ 10 clients / 0.51 s)
  and `/health` p99 7.9–21.9 ms across the five runs.
- *No Upstash (B):* no consistent before/after difference (expected — the in-memory path stays inline). Run-to-run
  noise on this Windows machine is large (p95 88–217 ms; throughput 268–553 req/s for the same code).
- *Upstash overhead (C, D):* before, every request blocked the loop for one RTT, so throughput ≈ 1/RTT (40 req/s at
  20 ms, 5 req/s at 200 ms) and `/health` stalled 0.39–4.4 s.
  - **After, RTT 20 ms:** the p50 difference between C and B ranged from −9 to +37 ms across the five runs. That is not
    distinguishable from noise on this machine, so no fixed overhead figure is claimed. `/health` was unaffected.
  - **After, RTT 200 ms:** throughput stayed just under the 8 / 0.2 s = 40 req/s ceiling of the dedicated budget
    (39.1–39.4 measured), by design. `/health` stayed at ~5 ms p50.
- `/health` sample counts for "before" A/D are tiny (2) because the prober itself was stalled; their percentiles are
  not statistically meaningful beyond "seconds".

**Not measured (UNVERIFIED):** **Render (production) latency; real LLM (Gemini/Groq) latency; production throughput**;
real Upstash RTT and whether
Upstash is configured in production; behaviour with more than ~40 concurrent slow calls (threadpool saturation, risk
accepted under Q2); memory/CPU use; multi-worker deployments.

## 8-workflow status
| Workflow | Touched by | Status |
|---|---|---|
| AI Mentor | `bound_history` (6 earlier turns, Q + last answer) | offline tests green; live UNVERIFIED |
| NCERT Study | rate limiter only | offline smoke green; live UNVERIFIED |
| PYQ + Personal PYQ Bank | `parse`/`bank_status`/`bank_clear` → def; bank sync upload offloaded | offline green; live UNVERIFIED |
| Answer Evaluator | 3 sync endpoints → def | offline green; live UNVERIFIED |
| Current Affairs | rate limiter only | offline green; live UNVERIFIED |
| Study Planner | `generate/sync` → def | offline green; live UNVERIFIED |
| Lecture Learning | `process`/`process_text` → def | offline green; live UNVERIFIED |
| PDF / Document Chat | sync upload offloaded | offline green; live UNVERIFIED |

## Not verified
- Live workflow smoke (`docs/SMOKE_CHECKLIST.md`) — needs keys; not run.
- Production: Upstash configured? Render worker count / proxy peer — UNVERIFIED.
- Root cause of the one-off test hang (see above).

## Remaining NITs (not fixed; added to the backlog)
1. `src/api/rate_limit.py` `_check`: waiting for a budget slot has no deadline; consider `anyio.fail_after` →
   in-memory fallback (measure first).
2. `uses_redis()` stays true while the Upstash breaker is OPEN, so the fast in-memory fallback still hops to a worker
   thread — correct, small overhead.
3. `RateLimiter.check` calls `_get_redis()` twice (singleton; harmless).
4. `anyio` is imported directly (transitive dependency of starlette/httpx, pinned in `uv.lock` 4.13.0; `pyproject.toml`
   unchanged) — to be recorded in ADR-011; declare it if starlette ever drops it.
5. `tests/test_async_routes.py` reuses `make_pdf` from `tests/test_upload_limits.py` (couples test modules).
6. The AST allow-list walker only sees `@router.<verb>` / `@app.<verb>` decorators; middleware is not covered.
7. `test_default_setting_is_six` asserts a constant only.
8. `_upsc_date_cache` (`src/agents/planner/graph.py`) unlocked; global data only (pre-existing).
9. Dedicated-budget test samples the default pool at a fixed 0.1 s; on a heavily oversleeping CI box it could flake —
   sample when the fake reports activity instead, if it ever does.
10. `_slow_then_health`'s 15 s `wait_for` timeout does not say which request stalled (debugging only).
11. ~~Benchmark harness only in the scratchpad~~ — resolved: `scripts/bench_request_path.py`, `scripts/bench_server.py`
   (Amendment 2). Pre-existing ruff I001 in `scripts/ingest_mentor_kb.py`, `scripts/ingest_topper_md.py` (not in CI's
   `ruff check src tests`; untouched) → backlog.
12. Harness (round-5 NITs, accepted):
    - If `proc.wait(timeout=30)` raises `TimeoutExpired` after `kill`/`terminate`, the log handle is not closed.
    - The result of `wait_port_free` after a server stops is ignored. The next start re-checks, but its error then
      blames "that process".
    - A manual `bench_server.py` run still inherits Python startup variables (`PYTHONPATH`, …). Driver runs strip
      them.
    - `rmtree(ignore_errors=True)` may silently leave throwaway files behind on Windows.
    - The allow-list keeps `HOME`/`USERPROFILE`, and the server is reachable from loopback with auth bypassed while it
      runs. Both are accepted; the data is throwaway and there are no credentials.
13. ~~Four leftover `upsc_bench_run_*` dirs in `%TEMP%`~~. Resolved 2026-11-02 at the user's request. Each dir was
    inspected first and then deleted by its exact path, with no wildcard; the earlier wildcard attempt had been denied.
    - `upsc_bench_run_27f861o5` and `upsc_bench_run_d2jwapfb`: empty, from the invalid-argument and busy-port checks.
      Removed with `rmdir`.
    - `upsc_bench_run_9efaz048` and `upsc_bench_run_u57nfjq0`: 2.3 MB each, holding a `git archive` export of
      `ad7d212`, a throwaway bench SQLite/Qdrant workdir and `server.log`, from the two failed busy-port runs.
    No other `upsc_bench_run_*` dirs existed, and nothing inside the repository was touched.

## New backlog items (added to `docs/PROJECT_STATE.md` → Backlog)
- **MED:** threadpool capacity. AnyIO's default 40 threads are shared by `def` handlers, sync dependencies and
  `guard_stream`. Accepted under Q2; revisit in Phase 04.
- **LOW:** `SaveMessageRequest` / `FeedbackRequest` fields have no `max_length`.
- **LOW:** rate-limiter NITs (no deadline on the 8-slot wait, breaker-OPEN thread hop, double client lookup, direct
  `anyio` import) and test-only NITs.
- **LOW:** pre-existing ruff I001 in `scripts/ingest_*.py`, which CI doesn't lint.
- **LOW:** this phase's benchmark runs confirmed the emoji startup-log `UnicodeEncodeError` (existing backlog item).

## Follow-ups
- **Phase 04 (durable jobs):** revisit threadpool sizing (the Q2 risk) and the upload memory backlog item.
- **Production checks (UNVERIFIED):**
  - Is Upstash configured on Render?
  - How many workers does Render run, and what is the proxy peer?
  - Run the live smoke checklist with keys.
- **Measure real latency** (Render, Gemini/Groq) before quoting any performance figure. Only synthetic numbers exist
  today (`docs/ENGINEERING_METRICS.md`).
- **Mentor checkpoint storage** still grows without bound; only the prompt is bounded. Needs a pruning design in a
  later phase.
- ~~Remove the four leftover `upsc_bench_run_*` dirs in `%TEMP%`~~. Done (see Remaining NITs, item 13).

## Decisions
Recorded as **ADR-011** (no blocking work on the event loop; bounded mentor prompt window) and **ADR-012** (synthetic
request-path benchmark harness and canonical metrics page) in `docs/DECISIONS.md`.
