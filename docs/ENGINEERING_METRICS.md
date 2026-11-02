# Engineering metrics — UPSC-AI-PRO

Canonical place for the project's engineering measurements: what is measured, how to reproduce it, and the recorded
results. Created in Phase 03 (2026-11-02). Every number here must come from a command shown on this page or from a
linked phase report; if it was not measured, it is marked **UNVERIFIED**.

## Measurement status
| Metric | Status | Source |
|---|---|---|
| Request-path latency p50 / p95 / p99 (synthetic) | Measured (Phase 03) | [§ Request-path benchmark](#request-path-benchmark-synthetic-local-single-worker) |
| `/health` responsiveness under load (synthetic) | Measured (Phase 03) | same |
| Throughput, req/s (synthetic) | Measured (Phase 03) | same |
| Upstash rate-limit overhead (synthetic RTT) | Measured (Phase 03) | same |
| Render (production) latency | **UNVERIFIED** — never measured | — |
| Real LLM latency (Gemini / Groq) | **UNVERIFIED** — never measured | — |
| Production throughput / capacity | **UNVERIFIED** — never measured | — |
| Real Upstash RTT; whether Upstash is configured in production | **UNVERIFIED** | — |
| Memory / CPU per request, multi-worker HTTP behaviour | **UNVERIFIED** | — |
| Background-job reliability: false reaps, dead-job recovery time, heartbeat tick cost, retry policy (offline, multi-process, SQLite) | Measured (Phase 04) | [§ Background-job reliability benchmark](#background-job-reliability-benchmark-offline-multi-process-sqlite) |
| Background jobs on Postgres / Render with `WEB_CONCURRENCY>1` | **UNVERIFIED** — never measured | — |
| Mentor routing: misrouted messages, router calls per message, router input size, graph overhead (offline, stubbed models) | Measured (Phase 05) | [§ Mentor routing benchmark](#mentor-routing-benchmark-phase-05-offline-stubbed-models) |
| Live router latency and money cost per message (Gemini / Groq) | **UNVERIFIED** — needs keys | — |
| Offline test suite size | `1212 passed` (Phase 05 review round 2 run) | [phase-05 report](phase-reports/phase-05-report.md) |

**Do not cite the synthetic numbers as production performance.** They show how the request path behaves when work
blocks (queueing, event-loop stalls, the Upstash thread budget), on one machine, with fake dependencies.

## Request-path benchmark (synthetic, local, single worker)
Harness: [`scripts/bench_request_path.py`](../scripts/bench_request_path.py) (driver) and
[`scripts/bench_server.py`](../scripts/bench_server.py) (server).

### What it does
- Starts the **real app** (lifespan, all middleware, all routers) on **one uvicorn worker** at `127.0.0.1`, over real
  HTTP, once per (scenario, version, run).
- **Synthetic LLM:** `evaluate_answer` blocks for `--llm-seconds` (default 0.5 s) like a sync provider call.
- **Synthetic Upstash:** each REST command blocks for the scenario's RTT. With RTT 0 the real client has empty
  credentials, so it is disabled and the in-memory limiter is used.
- **Auth:** `get_current_user` is overridden with a fixed user. JWT and DB session checks are not part of the measurement.
- **Load model:** closed loop. `concurrency` clients repeat the scenario request for `--warmup` + `--duration` seconds.
- **What counts:** a request counts if it *completes* inside the measurement window, and its latency includes queueing.
- **Health probe:** a separate client sends `GET /health` and sleeps `--probe-interval` seconds after each response
  (so the spacing is interval + latency). `/health` is exempt from rate limiting, so it shows whether the event loop
  itself is free. Probe failures are counted in the health `errors`.

### Scenarios
| Key | Request | Concurrency | Synthetic dependency |
|---|---|---|---|
| A | `POST /api/v1/evaluator/evaluate/sync` | 10 | LLM blocks 0.5 s |
| B | `GET /api/v1/cost/access` (pure handler) | 20 | Upstash off (in-memory limiter) |
| C | same as B | 20 | Upstash RTT 20 ms |
| D | same as B | 20 | Upstash RTT 200 ms |

### Requirements
- The repo's uv environment (`uv sync` already done; the harness adds no dependency: `httpx`, `uvicorn` and `anyio`
  are already installed).
- `git` on `PATH` for `--before-ref`; a free local port (default 8765). The driver refuses to start if the port is
  taken, and only uses a server that echoes its per-run random `X-Bench-Nonce`, so a stale or foreign process on the
  port can never be measured by mistake. Between servers it waits for the port to be released: on Windows the venv
  `python.exe` is a launcher whose child interpreter exits a moment after the launcher is stopped.
- **No `.env`, API keys or production credentials.**
  - The server `chdir`s into a fresh temp dir before importing `src`, so the project `.env` is never read.
  - Before importing `src` it drops every environment variable not on `ENV_ALLOW` in `scripts/bench_server.py` (OS
    basics such as `PATH`, `SYSTEMROOT`, `TEMP`). This happens however the server is started, so credentials exported
    in your shell never reach the app.
  - It then forces every key/service setting empty or local: throwaway SQLite, local Qdrant path, web search off,
    Sentry/Langfuse/LangSmith/R2/Upstash empty. The driver also starts it with the same allow-listed environment.
- App startup runs the Alembic migrations against the throwaway SQLite file only (same as the test suite).

### How to run
Run from the repo root. The commands are identical in Windows PowerShell, Git Bash and Linux/macOS shells, except the
`--out` path syntax:
```
uv run python scripts/bench_request_path.py                                  # current working tree only
uv run python scripts/bench_request_path.py --before-ref ad7d212             # before/after (Phase 03 comparison)
uv run python scripts/bench_request_path.py --before-ref ad7d212 --runs 2 --out "$env:TEMP\bench.json"   # PowerShell
uv run python scripts/bench_request_path.py --before-ref ad7d212 --runs 2 --out "$TEMP/bench.json"       # Git Bash
uv run python scripts/bench_request_path.py --scenarios A,D --duration 15    # subset, longer window
```
Write `--out` files outside the repo, or delete them afterwards; results belong in a phase report.

| Option | Default | Meaning |
|---|---|---|
| `--before-ref REF` | none | `git archive REF src migrations alembic.ini` into a temp dir and benchmark it as "before" |
| `--before-root DIR` | none | alternative to `--before-ref`: an existing dir with `src/`, `migrations/`, `alembic.ini` |
| `--after-root DIR` | repo root | code measured as "after" (or "current" when there is no before) |
| `--scenarios` | `A,B,C,D` | comma list |
| `--runs` | 1 | repetitions (fresh server each time) |
| `--warmup` | 1.0 s | not measured |
| `--duration` | 8.0 s | measured window — must exceed the worst queueing time (≈ 5 s for "before" A/D); too short a window yields 0 samples and a warning |
| `--probe-interval` | 0.05 s | sleep after each `/health` response |
| `--llm-seconds` | 0.5 | synthetic LLM block time |
| `--port` | 8765 | local port |
| `--python` | current interpreter | interpreter used for the server |
| `--out FILE` | none | write `{meta, results}` JSON (put it outside the repo) |

Invalid values are rejected before any server starts (`--duration` > 0, `--warmup` ≥ 0, `--probe-interval` > 0,
`--runs` ≥ 1, `--llm-seconds` ≥ 0; refs starting with `-`).

Total time ≈ scenarios × versions × runs × (startup ≈ 3 s + warm-up + duration); about 2.5 min for the default
before/after run.

### Output format
1. One JSON line `{"meta": {...}}`. It contains the label, the environment (OS, CPU, logical CPUs, server Python,
   uvicorn, anyio, fastapi, workers, host), the warm-up, duration, probe interval, LLM seconds and runs.
2. One JSON line per (scenario, version, run):
   `{"scenario", "concurrency", "version", "run", "route": S, "health": S}`.
   - `S` = `{"n", "errors", "rps", "p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms"}`.
   - The percentiles are linear-interpolated. They are `null` when `n == 0`.
   - `errors` maps a status code or exception name to its count.
3. A plain-text summary table, labelled *SYNTHETIC, local, single-worker*.
4. With `--out`, the same data as one JSON document.

Each run uses one private temp directory (`upsc_bench_run_*`) for the exported ref, the server work dirs and
`server.log`. It is deleted after a successful run. After a failure it is kept and its path is printed, so the server
log can be inspected. On Windows the log includes known harmless emoji-logging tracebacks (backlog item).

### Recorded results
| Date | Phase | Code | Environment | Results |
|---|---|---|---|---|
| 2026-11-02 | 03 | before `ad7d212` vs Phase 03 working tree | Windows 11 (10.0.26300), Intel64 Fam 6 Mod 140, 8 logical CPUs, Python 3.13.13, uvicorn 0.49.0, anyio 4.13.0, fastapi 0.137.0, 1 worker | [phase-03 report → Engineering metrics](phase-reports/phase-03-report.md#engineering-metrics-measured-in-this-session--synthetic-local) |

Phase 03 summary. All values are synthetic. Ranges cover all five runs: two original runs plus three reproducibility
runs with the committed harness. The report has every percentile.
- **A (blocking LLM):** throughput went from 1.38–1.62 to 19.1–20.0 req/s.
  - `/health` p99 went from 4.93–4.96 s to 7.9–21.9 ms.
  - The "before" `/health` values rest on only 2 samples per run, because the prober itself was stalled.
- **B (no Upstash):** no consistent before/after difference.
  - Absolute throughput varied with machine state, from 268–315 req/s in four runs to 519–553 req/s in one run.
- **C (Upstash 20 ms):** throughput went from 40.1–45.8 to 281–368 req/s.
  - `/health` p50 went from 0.39–0.44 s to 2.9–26.4 ms.
  - The p50 cost of the Upstash call compared with B ranged from −9 to +37 ms, which is within noise.
- **D (Upstash 200 ms):** throughput went from 4.88–5.0 to 39.1–39.4 req/s.
  - That is just under the 8-thread budget's ceiling of 8 ÷ 0.2 s = 40 req/s.
  - `/health` p50 went from 4.14–4.21 s to 4.8–5.8 ms.

## Background-job reliability benchmark (offline, multi-process, SQLite)
Harness: `scripts/bench_jobs.py` (Phase 04). It starts **real separate OS processes** that share one throwaway SQLite
file, which is what two uvicorn workers would share. Isolation is the same as `bench_server.py`: an allow-listed
environment, offline settings, and a `chdir` into a fresh temp dir so no `.env` is read. No network and no keys. The
temp dir is deleted after a passing run.

Scenarios:
- **A** — worker A runs 8 live jobs lasting 4 leases while sibling B re-runs boot recovery (`register_all`) every
  ¼ lease. The run passes only with 0 false reaps.
- **B1** — worker C holds 8 jobs (4 running, 4 queued) and is killed with `os._exit`. We measure the time until every
  job is `error`, recovered by a live sibling's periodic heartbeat sweep.
- **B2** — the same kill, recovered lazily by `get_job` polling, with no sibling running.
- **C** — the cost of one heartbeat tick with 1, 10 or 50 held jobs.
- **D** — 50 jobs per case: transient-once, permanent, and `retries=0`.

Timings are **scaled down**: heartbeat 0.2 s and lease 1.0 s (production defaults are 30 s and 120 s). Recovery
time is reported as a multiple of the lease.

```bash
uv run python scripts/bench_jobs.py                                   # defaults: --heartbeat 0.2 --lease 1.0 --jobs 8
uv run python scripts/bench_jobs.py --out <path outside the repo>.json
```

### Recorded results
2026-11-02, Phase 04 final working tree (after review fixes), Windows 11, Python 3.13.13, SQLite local file. Two runs:

| Scenario | Run 1 | Run 2 |
|---|---|---|
| A sibling boot (8 live jobs × 4 leases) | 16 sibling boot recoveries, **0 false reaps**, 8/8 `done` | 14 recoveries, **0 false reaps**, 8/8 `done` |
| B1 dead worker, periodic sweep (4 running + 4 queued) | 8/8 recovered, max 0.99 s (**0.99 leases**) | 8/8, max 1.03 s (1.03 leases) |
| B2 dead worker, lazy `get_job` | 8/8 recovered, max 1.07 s (**1.07 leases**) | 8/8, max 1.06 s (1.06 leases) |
| C heartbeat tick, median / p95 ms (1 / 10 / 50 held) | 7.0 / 7.1 / 7.6; 8.1 / 9.6 / 11.7 | 7.2 / 7.0 / 7.6; 7.8 / 7.8 / 8.9 |
| D retry policy (50 jobs per case) | transient-once → 2 attempts, `done`; permanent → 1; `retries=0` → 1 | same |

Both runs: `ALL PASS: True`. Run 3, after the review round-2 fixes (submit-failure handling, `HTTPException` never
transient): A 0 false reaps over 16 recoveries; B1/B2 max 0.88 / 0.88 leases; C median 7.0 / 6.9 / 7.7 ms; D unchanged;
`ALL PASS: True`. Two earlier runs on the pre-review code gave the same pass/fail results (B1/B2 max
0.87–1.15 leases). Recovery time varies with where the crash falls in the heartbeat cycle. Its upper bound is about
lease + heartbeat.

What this means at production defaults (an inference from the scaled run, not measured): a dead worker's jobs are
reported failed about 120–150 s after its last heartbeat. A live job is never failed while its process
heartbeats. One tick costs roughly one batched UPDATE plus one sweep on SQLite every 30 s.

Before the fix, a probe against the pre-Phase-04 code gave these results (phase-04 report → test-first evidence):
- One `reap_stale_jobs()` call failed a live, just-started job.
- The late result then overwrote that `error` with `done`.
- A transient `LLMTimeoutError` was not retried.

Postgres, real Render deploy overlap and `WEB_CONCURRENCY>1` under HTTP load are **UNVERIFIED**.

## Mentor routing benchmark (Phase 05, offline, stubbed models)
Harness: `scripts/bench_routing.py` (Phase 05). It runs 20 conversations of 10 mixed messages (planner, mentor,
current affairs, evaluator) through the **real compiled supervisor graph** on an `InMemorySaver` checkpointer. The
router LLM and every specialist are offline stubs, so it measures routing behaviour and graph overhead, **not model
latency**. Isolation is the same as `bench_jobs.py`: allow-listed environment, offline settings, `chdir` into a temp
dir. No network, no keys. Token counts are **estimates** (characters / 4), not a tokenizer.

```bash
uv run python scripts/bench_routing.py                       # defaults: --conversations 20
uv run python scripts/bench_routing.py --out <path outside the repo>.json
```

### Recorded results
2026-11-02, Windows 11, Python 3.13.13. "Before" ran on the unchanged pre-Phase-05 code; "after" on the Phase 05
working tree (three runs, results identical except overhead).

| Metric (200 messages, 20 conversations) | Before (route persisted in checkpoint) | After (per-message routing) |
|---|---|---|
| Messages answered by the wrong specialist | **160 / 200 (80.0 %)** | **0 / 200 (0 %)** |
| Router LLM calls per conversation / per message | 1 / 0.1 | 10 / 1.0 |
| Route labels | 5 (incl. unreachable `rag`) | 4 |
| Router system prompt (chars) | 551 | 462 |
| Structured-output schema, JSON (chars) | 471 | 406 |
| Router input per call, mean (system + message, chars) | 609 | 508.8 |
| Router input per call incl. schema, est. tokens | ~270 | ~229 (−15 %) |
| Offline graph overhead per message, p50 / p95 ms | 2.49 / 2.91 | 2.47 / 3.00 (runs 2–3: 2.45 / 2.82, 2.58 / 3.07) |

`ALL PASS` (0 misrouted and 1.0 router calls per message): before `False`, after `True` (3 / 3 runs).

### Impact of per-message routing (latency, tokens, cost)
- **Correctness.** Before Phase 05, the first message's route was saved in the checkpoint and reused for every later
  message in the conversation, so the router ran once per conversation. In this mix that sent 80 % of messages to the
  wrong specialist.
- **Router calls.** One extra fast-model call (`get_fast_llm()`, `llm_fast_model`, default `gemini-2.5-flash-lite`,
  Groq fallback) for every message after the first. A 10-message conversation goes from 1 to 10 router calls.
- **Tokens.** About 229 input tokens per router call (estimate: 462-char system prompt + ~406-char schema + the
  message; longer messages add about 1 token per 4 characters). Output is one small structured `RouteDecision`
  (a few tokens; not measured). Per 10-message conversation, router input goes from ~270 to ~2,290 estimated tokens.
  For scale, the mentor's system prompt alone is 2,858 chars (~715 tokens) before history and tool results, and it
  runs on the main tier (`get_llm()`), so the router stays a small share of each mentor message. Not measured.
- **Latency.** Each message now waits for one fast-model round trip before its specialist starts. That call is capped
  by `llm_request_timeout_seconds` (default 45 s); a timeout ends the request (no cascade). The real Gemini/Groq
  round-trip time is **UNVERIFIED**. Graph overhead without models is unchanged (about 2.5 ms p50).
- **Cost.** Extra cost per message is about `(input_tokens × fast-model input price) + (output_tokens × fast-model output
  price)`, with input ≈ 229 + message chars / 4. Provider prices and the real token counts are **UNVERIFIED** (no
  keys, no billing data). The comparison is not "cost added for nothing": before, messages 2…N ran a full specialist
  that was often the wrong one (for example the planner's hours prompt, or a whole daily current-affairs digest).
- **Planned check.** Live per-message router latency and token usage can be read from Langfuse traces
  (`make_config` already tags every run) once keys are configured. Phase 08 (evals) owns routing-accuracy measurement.
