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
| Memory / CPU per request, multi-worker behaviour | **UNVERIFIED** | — |
| Offline test suite size | `1113 passed` (Phase 03 verification run) | [phase-03 report](phase-reports/phase-03-report.md#gate-results-final-run-verbatim) |

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
