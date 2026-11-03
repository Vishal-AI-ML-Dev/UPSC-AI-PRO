# evals/ — workflow evaluation harness (Phase 07, ADR-016)

Evals that call the **real production functions** of all 8 workflows over versioned quality and red-team
cases. The older shared helpers, the CRAG `llm_eval` and the Phase 06 retrieval benchmark stay in `src/eval/`
(unchanged, still run by CI). `evals/` may import from `src/eval/`, never the reverse.

| Path | What it holds |
|---|---|
| `datasets/workflow_cases.json` | Quality, abstain and routing cases (8 workflows) |
| `datasets/redteam_cases.json` | Safety cases: canary injections, spoofed fence markers, score manipulation, system-prompt extraction, jailbreak/off-topic |
| `datasets/ncert_synthetic_chapter.md` | Synthetic NCERT-style chapter (never `data/ncert/`) |
| `evaluators/checks.py` | Deterministic checks (stdlib) |
| `evaluators/judge.py` | Pinned LLM judge for live runs only (inputs fenced) |
| `runners/isolation.py` | Fail-closed offline isolation and live isolation; restores all state |
| `runners/budget.py` | Live budget: shared ledger, pre-send LLM callback, embedding guard, pricing/bound gate |
| `runners/adapters.py` | One adapter per workflow: seed isolated state → call the production function |
| `runners/run_production_evals.py` | CLI and three-tier report writer |
| `pricing.json` | Price and token-bound declarations per model (all `null` / `unvalidated`) |
| `reports/` | Generated reports (git-ignored) |

## Evidence tiers (always reported separately)
- **T1: offline prompt construction (deterministic).** Checks whether every untrusted value is inside an
  untrusted-source fence in the prompt that is actually built, whether control-only fields stay out, and whether
  the scope gate refuses.
- **T2: offline scripted behaviour.** Plumbing, sentinels, parsing, citations and score bounds, using
  **scripted fake models**.
- **T3: live real-model evaluation.** Owner-run, budget-capped, report-only. **Disabled in Phase 07.**

> Offline results (T1/T2) do not show real-model safety or quality. Real-model safety, answer quality and
> cost stay UNVERIFIED until the owner validates the token bounds and runs T3.

## Data sources
- **Synthetic:**
  - every query and red-team scenario;
  - the NCERT-style chapter;
  - the Phase 06 corpora in `src/eval/corpora/`.
- **Existing approved corpus (not synthetic):** `data/mentor_kb/`. It is read-only, read through the Phase 06
  path guards, and indexed only into the run's temp Qdrant.
- **Never accessed:** `data/ncert/`, private user uploads, user/production collections, the app database.

All cases are Claude-written: `author: "claude"`, `human_verified: false`.

## What each case actually calls
Every case calls a real production function with the arguments its route uses. A few take documented shortcuts
around that function; none changes the function under test.

| Workflow / op | Production function called | Shortcut (documented) |
|---|---|---|
| mentor `agent` | `check_scope`, then `src.graph.mentor_graph.build_mentor_graph().invoke` | Skips `routes/chat.py`, the supervisor graph, checkpointed history and the response cache |
| mentor `route` | `supervisor._structured_llm(RouteDecision)` with `_SUP_SYS`, as `supervisor_node` does | Calls the router only, not the supervisor graph |
| mentor `scope` | `src.core.scope_gate.check_scope` | none (no model) |
| ncert `ask` | `ask_ncert` | Synthetic chapter seeded with `build_chapter_documents` + `upsert_documents`, not `generate_study_session` |
| upload `ask` | `ask_upload` | Store seeded with `upsert_documents` instead of `process_upload` |
| upload `notes` | `process_upload` | `extract_pdf_text` is stubbed to return the synthetic corpus |
| lecture `ask` | `ask_lecture` | Store seeded with `upsert_documents` |
| lecture `notes` | `process_lecture_from_text` | none |
| pyq `generate` / `bank` / `parse` | `generate_questions` / `generate_from_bank` / `parse_questions` | Bank seeded with `upsert_documents`, not `build_question_bank` |
| evaluator `basic` / `mains` | `evaluate_answer` / `evaluate_mains` + `parse_*_evaluation` | `keywords` / `word_limit` not passed (defaults) |
| current_affairs `daily` / `editorial` | `get_daily_ca` / `get_editorial` | RSS headlines come from a fixture |
| planner `plan` | `generate_plan` | The live prelims-date lookup is fixed to "" |

Web search results also come from fixtures, in both modes.

## Offline mode (pytest-safe, the default)
```bash
uv run python -m evals.runners.run_production_evals                    # all cases, T1 + T2
uv run python -m evals.runners.run_production_evals --workflow upload --kind safety
```
Offline mode fails closed (exit 2) on any of these:
- a network attempt (`connect`, `connect_ex`, `create_connection`, `getaddrinfo`, and the UDP `sendto` / `sendmsg`
  are blocked and counted; gRPC C-core bypasses Python sockets, so the client tripwires cover it);
- a real Gemini / Groq / embeddings client being constructed (tripwires);
- a remote `QDRANT_URL`;
- a Qdrant path outside the run's temp dir.

Keys and service URLs are blanked for the run. Every env var, setting and patch is restored afterwards, on success
and on error. The LLM singletons, the Langfuse switches and LangSmith's env cache are reset. The response cache,
Upstash, NCERT storage and usage-tracker singletons and counters are restored. Temporary files are removed on a normal
exit or a handled error; a hard kill (or a locked file on Windows) can leave the temp Qdrant directory behind.

## Live mode (owner only; disabled in Phase 07)
```bash
uv run python -m evals.runners.run_production_evals --mode live        # request-free dry-run estimate
```
**The dry-run prints:**
- the model ids;
- calls and worst-case sends by role (workflow / judge / embedding), with SDK retries × provider chain;
- input-token bounds by role, and embedding requests and tokens (cache hits shown);
- the cost per model and in total.

All figures are labelled **experimental estimate, unvalidated** (`utf8_bytes` bound).

**A live start (`--yes`) refuses (exit 2) before any client is built or request sent unless all of these hold:**
- the pricing file is the repository's `evals/pricing.json`. Other `--pricing` paths are rejected. The owner may edit
  this file to enter verified prices; it does not have to stay unmodified.
- every model the run can reach is declared there with prices, and its `token_bound` is `status: "validated"` with
  `validated_on` and `evidence` (owner-recorded; never set by Claude);
- every required cap is given: `--max-llm-sends`, `--max-llm-input-tokens`, `--max-embedding-requests`,
  `--max-embedding-tokens`, `--max-cost-usd`;
- `--sdk-max-retries` (or the configured `LLM_MAX_RETRIES`) is at least 1. The Gemini SDK reads 0 as "provider
  default, 5 retries".
- every constructed client reports usage, uses the pinned `max_retries` and has an output limit: workflow chain
  members, the judge client and the raw embeddings client;
- the owner confirms: types `RUN` on an interactive terminal, or, when not interactive, sets
  `UPSC_EVALS_LIVE_CONFIRM=RUN` in addition to `--yes` and every cap.

**During a live run, every send is reserved first.** One ledger is enforced at three points:
- the LLM callback, re-attached to tool-bound (mentor tool agent) and structured-output (router) models and to the
  judge;
- a send guard inside every production client's send methods (invoke, stream, ainvoke, astream). A send without
  a reservation is refused before it leaves. A stream carries no run manager, so it claims the latest unclaimed
  reservation made on its thread, and each reservation can be claimed once. The callback runs inline, so async
  reservations are made on the event-loop thread.
- an embedding guard under the cache.

With the shipped `pricing.json` **every live start refuses**. A configured model id that is not declared in
`pricing.json` is refused as "not declared" until an entry is added. **Groq fallback (`--allow-fallback`) cannot
start:** the Groq clients have no output limit and use a hard-coded retry count, so the client preflight refuses
them. Defining an owner-approved token-bound validation procedure is a Backlog item.

**Exit codes:**
- 0: ok
- 1: safety-check failure (any failure with `--strict`)
- 2: refused (preflight, config, isolation, cap, price, bound, retry or output-limit mismatch, undeclared model, no
  owner confirmation)
- 3: budget cap reached before a send
- 4: token bound violated or unverifiable
- 5: execution error (an adapter or the runner crashed). This is not a safety result.

## Reports
`reports/<UTC>-<mode>.{json,md}` (git-ignored).

**They hold:**
- case ids, check results, scores and judge scores;
- source ids and sha256 hashes plus the lengths of prompts, retrieved chunks and outputs;
- usage by role;
- provenance (commit, dirty flag, dataset and corpus hashes).

**They never hold:** prompts, corpus or Mentor KB excerpts, env values, credentials or user data.

Full model outputs are written only with `--include-outputs`, for the owner's local inspection. The flag prints a
warning, because outputs can quote corpus or Mentor KB text.

A failed canary check keeps at most 80 characters of model output around the canary:
- Any overlap with captured source text is replaced by `[source:<id>]`. The comparison ignores case and whitespace.
- When an op's sources are not fully captured (mentor agent, planner, notes, daily current affairs), no excerpt is
  written.
