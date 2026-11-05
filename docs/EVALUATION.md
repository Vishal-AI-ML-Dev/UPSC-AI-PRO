# Evaluation

What the project evaluates, how, and — most importantly — what each result does and does not prove. The structure follows the
common layered view of LLM-application evaluation (component → retriever → generator → pipeline → application → safety → agents →
operations → regression → online), but every section describes **what this repository actually implements**. Labels: IMPLEMENTED,
ENABLED, OFFLINE-TESTED, PRODUCTION-OBSERVED, PLANNED, UNVERIFIED. Verified 2026-11-05; numbers are traced to
[ENGINEERING_METRICS.md](ENGINEERING_METRICS.md).

## Concepts used on this page
- **Reference-based** metrics compare an output to a known-good answer or label (for example "which chunk contains the gold quote").
  They need a **labelled or curated dataset**, which is why they are run **offline**.
- **Reference-free** metrics judge an output without a gold answer (a rubric or an LLM judge scoring faithfulness to retrieved context).
  They can run on **sampled live traffic (online)** but are only as trustworthy as the judge and its calibration.
- **Offline** = fixed inputs, repeatable, no users. **Online** = sampled real requests plus feedback proxies (thumbs, retries, abandonment).
- The project's evidence tiers: **T1** offline prompt-construction checks (deterministic), **T2** offline behaviour with **scripted fake
  models**, **T3** live real-model evaluation (**never run**).

## Summary
| # | Layer | Status | Strongest existing evidence | Biggest gap |
|---|---|---|---|---|
| 1 | Component | IMPLEMENTED, OFFLINE-TESTED | 1,847 collected pytest cases | Mocked dependencies only |
| 2 | Retriever | IMPLEMENTED, OFFLINE (live embeddings, small data) | 97-query benchmark with Recall/MRR/NDCG | AI-written labels; small, mostly synthetic corpora; NCERT unmeasured |
| 3 | Generator | Partial (legacy judge) | 3-case sample judge report | No generator eval on production paths |
| 4 | End-to-end RAG | Partial | Workflow evals T1/T2 | T3 never run |
| 5 | Application quality | Not measured | — | No human or judge scoring of real answers |
| 6 | Safety | IMPLEMENTED (T1/T2 only) | 21 → 0 prompt-construction failures | No live-model red-team |
| 7 | Agent routing and tools | OFFLINE-TESTED (stubbed) | 160/200 → 0/200 misroutes, stubbed | Real router accuracy unknown |
| 8 | Operational | Mostly UNVERIFIED | Synthetic latency/throughput benches | No production latency, token or cost data |
| 9 | Regression | IMPLEMENTED | Offline suite + CI | Live eval gate not run per commit |
| 10 | Online | PLANNED | Feedback endpoint and admin dashboards exist | No online scoring pipeline |

---

## 1. Component-level evaluation
- **Target:** pure units — RRF fusion, lexical scoring, scope gate, prompt fencing, grounding/citation helpers, parsers, rate limiter,
  circuit breaker, job queue.
- **Metric/check:** exact assertions on behaviour (for example the fused order for a fixed ranking; the marker-neutralisation outputs).
- **Dataset:** hand-written fixtures in `tests/`.
- **Execution:** `uv run pytest -q`.
- **Status:** IMPLEMENTED, OFFLINE-TESTED. **Result:** 1,847 collected cases (last recorded full run `1765 passed, 2 skipped` on 2026-11-04).
- **Limitations:** dependencies are mocked; passing proves logic, not model quality.
- **Next:** none required beyond keeping the suite green.

## 2. Retriever evaluation
- **Target:** whether the gold evidence chunk is retrieved and how high it ranks.
- **Metrics (implemented):** Recall@k, MRR@k, NDCG@k (corrected and legacy definition), paired bootstrap 95 % confidence intervals,
  no-answer false-positive rate, retrieval latency. `precision_at_k` and `hit_at_k` exist in `src/eval/retrieval_eval.py` (legacy report), but the Phase 06
  benchmark does not headline them.
- **Dataset:** `src/eval/retrieval_gold.json` — 84 answerable and 13 unanswerable queries over four labelled corpora (mentor KB, and three synthetic
  ones: `upload_style`, `lecture_style`, `pyq_style`; 11–61 chunks each). A chunk is relevant if it contains a gold evidence quote.
  NCERT support exists but the NCERT gold set is empty.
- **Execution:** `scripts/bench_retrieval.py` (embedded Qdrant, live Gemini embeddings cached, hard request and token budgets,
  `--cache-only` offline re-run).
- **Result:** production variant pooled Recall@k 0.988, MRR@k 0.935, NDCG@k 0.938. RRF re-ordering showed a marginal NDCG gain
  (+0.030, CI [+0.0004, +0.062]); over-fetching showed none. **No unanswerable query was filtered: 13 of 13 retrieved context at the configured thresholds.**
- **Limitations:** labels written by Claude and unverified by a human (`human_verified: false`); the script only proves quotes occur in chunks;
  queries share wording with the text, so the data sits at a ceiling; per-corpus comparisons are underpowered; embedded Qdrant is exact search,
  not the HNSW server. **Do not market 0.988 as production retrieval quality.**
- **Next (PLANNED, not started):** human-reviewed labels; an NCERT gold set; threshold calibration on a held-out set; real-query sampling.

## 3. Generator evaluation
- **Target:** faithfulness to context, answer relevance, citation accuracy of generated text.
- **Implemented:** a legacy LLM-as-judge harness `src/eval/llm_eval.py` (faithfulness, answer relevancy, context precision) run by the nightly CI job with
  gates 0.9 / 0.7 / 0.6. It evaluates the **CRAG graph, which is not reachable over HTTP**, so it does not exercise the production answer path.
- **Existing result:** a committed sample report (`src/eval/eval_report.md`) shows faithfulness 0.95 on **3 cases**; its provenance and recency are UNVERIFIED, and
  whether the nightly job has run in GitHub is UNVERIFIED. **There is no production faithfulness score, and no hallucination-reduction claim is supportable.**
- **Citation accuracy:** NCERT validation checks tag **format** only (`[S#]` matches a retrieved excerpt), and mentor citations are formatted from tool results.
  Neither verifies that a source supports a sentence. T2 tests assert the mechanics with scripted replies.
- **Limitations:** a judge from the same model family as the generator can be lenient; judge calibration has not been measured.
- **Next (PLANNED):** run the pinned judge in `evals/evaluators/judge.py` as a T3 run on the real workflow functions once token bounds are validated.

## 4. End-to-end RAG pipeline evaluation
- **Target:** the real workflow functions from retrieval through generation.
- **Implemented:** `evals/` (ADR-016) calls the production functions of all eight workflows with route-equivalent arguments over versioned cases
  (`evals/datasets/workflow_cases.json`, `redteam_cases.json`). Some adapters take documented shortcuts (for example the mentor case skips the chat route, supervisor and cache).
- **Execution:** `uv run python -m evals.runners.run_production_evals` (offline; fake embeddings, scripted models, network blocked).
- **Result (60 cases):** T1 33 pass / 0 fail / 1 known gap; T2 72 pass / 0 fail; **T3 not run**.
- **Limitations:** T2 uses scripted fake models, so passing shows plumbing and parsing — **not** how Gemini or Groq behave. Context relevance and
  end-to-end faithfulness with real models are UNVERIFIED.
- **Next:** T3 run (owner decision; needs validated per-model token bounds in `evals/pricing.json`).

## 5. Application-level quality
- **Target:** correctness, completeness, usefulness of answers for real aspirants (including the evaluator's marks and the planner's plans).
- **Status:** **not measured.** No human rubric review, no judge scoring of real outputs, and no comparison of evaluator marks against examiner marks exist.
- **Proxies present:** a thumbs-style `POST /feedback/submit` endpoint stores feedback; no analysis of it is published.
- **Next (PLANNED):** a small human-scored sample per workflow with a written rubric; judge calibration against it.

## 6. Safety evaluation
- **Target:** prompt-injection resistance, scope adherence, sensitive-information leakage, tool misuse, unsafe output.
- **Implemented:**
  - **T1** deterministic checks that every untrusted value reaches a built prompt only inside an untrusted-source fence and that control-only fields stay out.
  - **T2** red-team cases with canary strings, spoofed fence markers, score-manipulation attempts and system-prompt-extraction attempts, against scripted models.
  - Scope-gate unit tests; ownership/IDOR tests for DB rows, vector keys, threads and cache keys.
- **Result:** on the pre-fix code the T1 tier found **21 failures** in 55 cases (unfenced values in upload, lecture, PYQ, mentor-profile and evaluator prompts, plus spoofed-marker
  variants that survived the sanitiser). After fixes: 0 failures in 55, then 0 in 60 after review added five more marker-bypass cases. One **known gap** remains
  (`rt-scope-song`: the scope gate allows "write a song about X"; only the mentor prompt blocks it, which is untested with a real model).
- **Limitations:** this evaluates **prompt construction and mechanics**, not whether a real model obeys or resists injected instructions. Sensitive-information-leakage and
  tool-misuse testing against a live model have **not** been performed.
- **Next:** T3 live red-team with budget caps; keep the offline cases as regression tests.

## 7. Agent routing and tool behaviour
- **Target:** the supervisor sends each message to the right specialist; the mentor uses only allowed tools within the loop cap.
- **Implemented:** `scripts/bench_routing.py` runs 20 conversations × 10 messages through the real compiled supervisor graph with stubbed router and specialists; unit tests for tool dispatch,
  the 3-round cap, KB-only tool access (the model cannot select a collection) and per-turn history bounding.
- **Result:** misrouted messages 160/200 → 0/200 after fixing a state bug that reused the first message's route; router calls 0.1 → 1.0 per message.
  Router input about 229 estimated tokens per call (characters / 4, not a tokenizer).
- **Limitations:** stubbed models — it proves the graph no longer reuses a stale route, **not** that the real fast model classifies correctly. Real routing accuracy, latency
  and cost per message are UNVERIFIED.
- **Next:** a labelled routing set run against the real router; read live token usage from traces once Langfuse is configured.

## 8. Operational metrics
| Metric | Implemented? | Existing measurement |
|---|---|---|
| P50/P95/P99 latency | In-process HTTP metrics for an admin dashboard; synthetic benchmark harness | Synthetic only; **no production percentiles** |
| Retrieval latency | Benchmark timing | Embedded Qdrant ~1–3 ms; live query embedding ≈ 509 ms p50 in one run; production Qdrant UNVERIFIED |
| Generation latency | — | UNVERIFIED |
| Input/output tokens, estimated cost | Budget-capped eval harness estimates; `src/core/usage_tracker.py` | No production figures; pricing file is intentionally `null` |
| Error, timeout, retry rates | Counters/logging exist; job retry policy benchmarked | Offline only |
- **Benchmarks:** `scripts/bench_request_path.py` (synthetic, local, one worker; ≈ 13× throughput improvement in the blocking-LLM scenario after moving blocking work off the event loop),
  `scripts/bench_jobs.py` (0 false reaps; dead-job recovery about one lease length, scaled-down timings, SQLite).
- **Limitations:** none of these are production traffic. **No production P95, cost per workflow or user-traffic claim is supportable.**
- **Next (PLANNED):** enable tracing/error monitoring (owner decision), record real latency and token usage, then publish percentiles.

## 9. Regression testing
- **Implemented:** the offline pytest suite on every push and pull request; compile check; frontend type-check and build; the Phase 07 eval cases and Phase 06 benchmark are re-runnable
  offline (`--cache-only`, `--fake-embeddings`). Each bug fix carries a regression test by project rule.
- **Not in the per-commit gate:** the live judge gate (nightly/manual, needs secrets, targets the unreachable CRAG graph), lint and prettier (non-blocking), live smoke (disabled).
- **Next:** make lint blocking; replace the legacy judge gate with one over the production functions.

## 10. Online evaluation
- **Status:** **PLANNED / not implemented.** There is no sampling of live traffic, no online judge and no feedback analysis pipeline.
- **Building blocks that exist:** request IDs on every response and log line, optional Langfuse hooks (activation UNVERIFIED), a feedback endpoint, and admin overview endpoints.
- **Design intent:** sample a small fraction of production answers and score them with reference-free judges (faithfulness to retrieved context, answer relevance), and watch feedback
  proxies (thumbs, regenerations, abandonment). Judges must be calibrated offline against a human-labelled set first; otherwise online scores are unexplained numbers.
