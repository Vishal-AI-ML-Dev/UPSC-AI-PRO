# Phase 06 report — RAG quality (baseline measurement + documentation closeout)

Date: 2026-11-02 · Spec: `.claude/specs/phase-06-rag-quality.md` · Plan: `.claude/plans/phase-06-rag-quality.md` ·
ADR: ADR-015 · Status: **DONE (2026-11-03)** — measurement + documentation; no production retrieval change

## Summary
- **Stage 1** built a retrieval benchmark over the retrieval functions production actually runs and measured a live
  baseline with Gemini embeddings (`gemini-embedding-001` @ 768) in an isolated embedded Qdrant.
- **Stage 2** was documentation only, by owner decision. No threshold, ranking, fetch-k, reranker/BM25, chunking,
  indexing, config, route, test or dependency change.
- Production retrieval behaviour is **unchanged**.

## Experiment
- **Dataset** `src/eval/retrieval_gold.json`: 84 answerable + 13 unanswerable queries over four corpora:

  | Corpus | Chunks | Answerable + unanswerable | Production search |
  |---|---:|---|---|
  | Mentor KB | 61 | 36 + 6 | dense only, k=4, threshold 0.25 |
  | Synthetic polity notes (upload-style) | 23 | 22 + 3 | k=5, threshold 0.3, RRF re-order |
  | Synthetic lecture transcript | 13 | 16 + 2 | k=5, threshold 0.3, RRF re-order |
  | Synthetic practice paper (PYQ-style) | 11 | 10 + 2 | k=8, threshold 0.3, RRF re-order |

  NCERT is supported but empty (owner-supplied).
- **Labels:**
  - 101 evidence quotes, **written by Claude** from the corpus text.
  - `--validate-only` checks that each quote occurs verbatim inside at least one production chunk (100 of 101 in
    exactly one). This proves **source containment, not independent relevance judgement**.
  - Every entry keeps `human_verified: false` until a real human review.
- **Configuration:** exact table in `docs/ENGINEERING_METRICS.md` → Retrieval quality benchmark → Experiment
  configuration. Paired query-level bootstrap 95% CIs, 10,000 resamples, seed 0.
- **API usage (approved guardrails):**
  - 206 embedding texts counted and ~29,020 estimated tokens, cumulative over two runs. Limits: 500 / 1,000,000.
  - The first run stopped on a free-tier 429 (100 texts/min). Pacing and a cumulative ledger were added before it
    resumed.
  - No LLM generation calls. No user data sent.

## Results (pooled) — CORRECTED measurement (current)
Cache-only re-run after the review fixes (0 API requests). "@k" = mean over each corpus's production k (mentor KB 4,
PDF/Lecture 5, PYQ 8). NDCG uses the achievable ideal (real chunks). Full tables: `docs/ENGINEERING_METRICS.md` →
"Corrected measurement".

| Variant | Recall@k | MRR@k | NDCG@k |
|---|---:|---:|---:|
| dense_k | 0.988 | 0.928 | 0.933 |
| production | 0.988 | 0.935 | 0.938 |
| rrf_same_k | 0.988 | 0.968 | 0.963 |
| overfetch_2k | 1.000 | 0.968 | 0.969 |

- **Dense retrieval** performed near the ceiling **on these particular datasets**. Headroom (dense Recall@10 −
  Recall@production k) is 0.012 pooled and 0.028 for the mentor KB. This does not necessarily hold for real student
  queries or production collections.
- **RRF** showed a promising ordering signal: pooled NDCG@k +0.030, CI [+0.0004, +0.062]. It is not significant within
  the mentor KB (MRR +0.079, CI includes 0) or the shared pipeline alone.
- **Over-fetch** (effect isolated from re-ordering): Recall@k +0.012, CI [0.000, +0.030]; NDCG@k +0.007, CI
  [0.000, +0.017]. No significant gain; rejected.
- **No ranking change met the predeclared criteria.**

### Stage 1 headline (PRIOR measurement, superseded definitions — kept as historical evidence)
Live run before the review fixes. NDCG used the legacy definition (one quote per rank, capped at 1.0), and the
production check for PDF / Lecture / PYQ called the wrong function (see the metrics page).

| Variant | Recall@k | MRR@k | NDCG@k (legacy) |
|---|---:|---:|---:|
| dense_k | 0.988 | 0.928 | 0.933 |
| production | 0.988 | 0.935 | 0.939 |
| rrf_same_k | 0.988 | 0.968 | 0.964 |
| overfetch_2k | 1.000 | 0.968 | 0.970 |

Stage 1 also reported: headroom Recall@10 − Recall@5 = 0.006; RRF NDCG@k +0.031, CI [+0.001, +0.063]; and
"over-fetch vs production NDCG +0.031 (marginal)". The last one mixed the mentor KB's RRF re-order into the
over-fetch comparison.
- **Thresholds:** 0.25 and 0.3 passed **all** tested results (top-1 scores 0.75–0.90).
  - **No-answer false-positive retrieval rate: 13/13.** This is a retrieval finding, **not** an answer
    false-grounding measurement; no answers were generated.
  - The 0.80 value from a post-hoc sweep is **exploratory** and **not** an approved production setting.
- **Latency** (embedded Qdrant, cached embeddings; compare variants within one run only): corrected run p95
  0.95–1.55 ms across variants; Stage 1 run p95 2.3–3.8 ms. Live query embedding ≈ 509 ms p50 (Stage 1 live run).

## Limitations
- Small corpora (11–61 chunks); three are synthetic.
- Queries were written from the source text (wording overlap), which likely inflates recall.
- Only 13 unanswerable queries.
- Exact local Qdrant search (a server uses HNSW, which is approximate).
- **Unmeasured:** NCERT, real user uploads, production Qdrant collections and server-side latency.

## Files changed (`git status --porcelain`)
```
 M .claude/specs/README.md
 M .claude/specs/phase-06-rag-quality.md
 M docs/DECISIONS.md
 M docs/ENGINEERING_METRICS.md
 M docs/PROJECT_STATE.md
 M src/eval/retrieval_eval.py          (+202 lines, additive only)
 M tests/test_retrieval_eval.py        (+137 lines, additive only)
?? .gitattributes                      (new: LF for the evaluation assets only)
?? .claude/plans/phase-06-rag-quality.md
?? docs/phase-reports/phase-06-report.md
?? scripts/bench_retrieval.py
?? src/eval/corpora/                   (upload_style_polity.md, lecture_style_economy.txt, pyq_style_paper.md)
?? src/eval/retrieval_gold.json
?? tests/test_bench_retrieval.py
```
No change under `src/core`, `src/api`, `src/agents`, `src/graph`, `frontend`, `.github`, `pyproject.toml`, `uv.lock`.

## Acceptance criteria
See the spec §11 for the full table. Summary:

| AC | Outcome |
|---|---|
| AC1–AC5, AC7, AC8, AC10–AC12 | MET (AC1 labels **not human-verified**) |
| AC6 | N/A (no change adopted) |
| AC9 | PARTIAL: README/ARCHITECTURE accurate; `src/core/retrieval.py` docstring still says "hybrid" (Backlog LOW) |

## Gate results (verbatim)
- `uv run pytest -q` → `1231 passed, 13 warnings in 171.88s (0:02:51)` (Stage 2 closeout; superseded by the
  final gate below)
- `uv run ruff check src tests` → `All checks passed!`
- `uv run python -m compileall -q src tests` → exit 0
- Frontend: not touched (build/lint not run).

## 8-workflow status
No runtime code changed, so every workflow behaves as at Phase 05 close. The offline suite is green. Live workflow
smoke is UNVERIFIED.

## New backlog items (`docs/PROJECT_STATE.md`)
- Threshold calibration (independent held-out set, per-workflow unanswerable questions, false-rejection +
  false-positive rates, dev/held-out split, owner approval) — MED.
- Reranker / BM25 deferred unless new evidence shows headroom — LOW.
- NCERT, real uploads and production Qdrant unmeasured; gold labels need human review — OPEN.
- `retrieval.py` docstring wording — LOW.
- From Stage 1:
  - 24 h answer cache (risk #9) — MED.
  - Chroma fallback (risk #4) — MED.
  - PYQ context not fenced — LOW.
  - PYQ chunk metadata — LOW.
  - Duplicated mentor KB search logic — LOW.
  - No citations in upload / lecture / PYQ — FEATURE.
  - Stale `retrieval_eval_report.md` — LOW.

## Review remediation (owner-approved S1, S2, Q1–Q6)
First review (code-quality + security): 0 BLOCKING; 2 security SHOULD-FIX (S1 corpus paths, S2 temp cleanup) and
6 code-quality SHOULD-FIX (Q1–Q6). All eight were fixed in `scripts/bench_retrieval.py`, `src/eval/retrieval_eval.py`
and the two test files; the NITs went to the Backlog. Corrected cache-only re-run (0 API requests, cache unchanged):
see `docs/ENGINEERING_METRICS.md` → "Corrected measurement". Recall / MRR / threshold results are unchanged.
- **Over-fetch, isolated:** no significant gain.
- **Pooled RRF re-order NDCG gain:** still marginal (+0.030, CI [+0.0004, +0.062]).
- **Headroom at production k:** pooled 0.012, mentor KB 0.028.
- **Decisions:** unchanged.

Gates after remediation (run once, no concurrent runs):
- `uv run pytest -q tests/test_bench_retrieval.py tests/test_retrieval_eval.py` → `63 passed, 1 skipped, 1 warning in 7.22s`
- `uv run pytest -q` → `1267 passed, 1 skipped, 13 warnings in 159.58s (0:02:39)` (the skip is the file-symlink escape
  test, which needs symlink privilege on Windows)
- `uv run ruff check src tests scripts/bench_retrieval.py` → `All checks passed!`; compileall exit 0;
  `git diff --check` exit 0 (untracked files checked separately: no whitespace errors)
- `uv run python scripts/bench_retrieval.py --cache-only --repeats 5` → exit 0, 0 texts sent, cache sha256 unchanged

Final gates after the final remediation (2026-11-03, run once, serially):
- `uv run pytest -q tests/test_bench_retrieval.py tests/test_retrieval_eval.py` → `68 passed, 2 skipped, 1 warning in 17.69s`
- `uv run pytest -q` → `1272 passed, 2 skipped, 13 warnings in 151.97s (0:02:31)` (the 2 skips are file-symlink escape
  tests that need Windows symlink privilege; the directory-link escape tests run and pass)
- `uv run ruff check src tests scripts/bench_retrieval.py` → `All checks passed!`; compileall exit 0;
  `git diff --check` exit 0; untracked files: no whitespace errors
- `uv run python scripts/bench_retrieval.py --cache-only --repeats 5` → exit 0, 0 texts sent, cache sha256 unchanged,
  temp work dir removed; summary identical (latency aside) to the corrected measurement

## Final review round (2026-11-03)
- Security: **PASS** — 0 BLOCKING, 0 SHOULD-FIX, 4 NITs; SF-1 FIXED.
- Code quality: no BLOCKING, no SHOULD-FIX; 6 NITs.
- NIT-3 (mentor KB hash reproducibility wording in `docs/ENGINEERING_METRICS.md`) and NIT-4 (stale SQLite note in
  `docs/PROJECT_STATE.md`) were corrected at close. All other NITs are recorded in the `docs/PROJECT_STATE.md`
  Backlog.

## Close (2026-11-03)
- Phase 06 marked **DONE** by owner instruction after the final review round.
- Final verified test result: `1272 passed, 2 skipped, 13 warnings in 151.97s (0:02:31)`.
- No application code, test, configuration, dependency or retrieval behaviour changed at close (docs only).
- Still UNVERIFIED and carried forward:
  - NCERT retrieval (owner-supplied gold set pending);
  - real user uploads;
  - production Qdrant collections and server latency;
  - live workflow smoke;
  - human review of the gold labels (all 97 `human_verified: false`).
