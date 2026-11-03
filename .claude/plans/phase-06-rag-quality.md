# Plan — Phase 06 Advanced RAG & Retrieval Quality

Spec: `.claude/specs/phase-06-rag-quality.md`  ·  Status: DONE (2026-11-03) — Stage 1 measurement, Stage 2 docs-only, review remediations (Amendments 1–4); no production retrieval change.

The phase runs in three stages with a hard checkpoint between them:
**Stage 1 (WP-A) measure → CHECKPOINT: user reviews the baseline + gate decisions → Stage 2 (WP-B) only the gated
changes → Stage 3 (WP-C/D) docs, hygiene, close.** If no gate passes, Stage 2 is empty and production retrieval code
is unchanged.

## Files to touch (exhaustive — anything else needs a plan amendment)

### Stage 1 — measurement (always)
| File | Change |
|---|---|
| `src/eval/retrieval_eval.py` | **Additive.** Pure `recall_at_k`, `mrr_at_k`, `ndcg_at_k`, `span_relevance` (evidence-quote match), `threshold_metrics`, `paired_bootstrap_ci` (stdlib `random`, fixed seed). Existing functions, CLI and report unchanged. |
| `src/eval/retrieval_gold.json` | **New.** Gold dataset: `{id, corpus, question, gold: [{quote, grade}], answerable}`; ≥ 60 rows; ≥ 10 unanswerable. |
| `src/eval/corpora/upload_style_polity.md` | **New.** Synthetic upload-style study text (≥ 15 KB, written for the benchmark, no user data). |
| `scripts/bench_retrieval.py` | **New.** Live runner: isolated embedded Qdrant in a temp dir, production chunkers + pipeline, embedding cache, variants V0–V3, metrics, CIs, latency, markdown + JSON outputs. `--fake-embeddings` for offline use. |
| `tests/test_retrieval_eval.py` | **Extend** with tests for the new pure functions (existing tests untouched). |
| `tests/test_bench_retrieval.py` | **New.** Dataset validator, offline end-to-end runner (fake embeddings, temp Qdrant), isolation assertions. |
| `docs/ENGINEERING_METRICS.md` | Add a "Retrieval quality (Phase 06)" section: baseline table, CIs, command, commit, corpus hashes, limitations. |

### Stage 2 — gated changes (each row only if its gate passed at the checkpoint)
| File | Change | Gate |
|---|---|---|
| `src/core/config.py` | `retrieval_fetch_k_multiplier: int = Field(default=<evidence>, ge=1, le=4)`; threshold defaults if G-threshold passes | G-fetch / G-threshold |
| `src/core/vector_store.py` | `similarity_search` / `similarity_search_with_sources`: fetch `k*m` candidates, re-rank, cap at k. `SIMILARITY_THRESHOLD` default if G-threshold passes | G-fetch / G-threshold |
| `src/core/mentor_kb.py` | `search_kb` uses `similarity_search_with_sources` (same return shape, same fence by the caller) | G-mentor |
| `tests/test_retrieval_rerank.py` | Over-fetch regression tests (m=1 same set as today; m=2 ≤ k) | G-fetch |
| `tests/test_advanced_retrieval.py` | Mentor KB through the pipeline: same contract | G-mentor |

### Stage 3 — honesty, optional hygiene, close
| File | Change |
|---|---|
| `src/core/retrieval.py` | Module docstring / `rerank_scored_documents` docstring: describe the measured behaviour (re-orders the same candidates unless over-fetch is on). **No logic change.** |
| `README.md`, `ARCHITECTURE.md` | Retrieval sections match the measurements (R7). |
| `docs/DECISIONS.md` | ADR-015: retrieval measurement method + gate decisions (+ Chroma fail-fast if R9). |
| `docs/PROJECT_STATE.md` | Phase status, workflow table, risks #4/#9/#10 updates, backlog items from spec §10, handoff. |
| `docs/phase-reports/phase-06-report.md` | **New.** Phase report. |
| `.claude/specs/phase-06-rag-quality.md`, `.claude/plans/phase-06-rag-quality.md`, `.claude/specs/README.md` | Status updates. |

**Not touched:** `src/api/**`, `src/graph/**` (incl. `rag_graph.py`), `src/agents/**`, `src/eval/llm_eval.py`,
`src/eval/gates.py`, `src/eval/eval_dataset.json`, `.github/`, `pyproject.toml`, `uv.lock`, `frontend/**`,
migrations.

### Amendment 1 (Stage 1, 2026-11-02) — files added during implementation
| File | Change | Why |
|---|---|---|
| `src/eval/corpora/lecture_style_economy.txt` | **New.** Synthetic lecture transcript (spoken style, no paragraph breaks). | Lecture chat chunks transcripts differently from PDFs. Covering the real retrieval paths needs a transcript-style corpus (user: "distributed across the actual retrieval paths"). |
| `src/eval/corpora/pyq_style_paper.md` | **New.** Synthetic practice paper (not a real UPSC paper). | PYQ bank generation uses k=8 and question-paper text. |
| `docs/PROJECT_STATE.md` | Phase row → ACTIVE; Backlog lines (spec §10, R10 deferral). | Already listed for Stage 3; touched early for the phase status and the findings. |
Guardrail additions in `scripts/bench_retrieval.py` after the first live attempt hit a free-tier 429 (100 embed
texts/min): a cumulative usage ledger in the embedding cache (limits cover all live runs), pacing at 90 texts/min,
batch splitting, and a clean stop on provider errors (no auto-retry). Tests added for each.

### Stage 1 status (checkpoint reached 2026-11-02)
Steps 0–6 done. Live baseline recorded in `docs/ENGINEERING_METRICS.md`. **Stopped at the checkpoint**; no Stage 2
work started.

### Amendment 2 (Stage 2, owner decision 2026-11-02) — documentation-only closeout
None of the gated Stage 2 code rows (config / vector_store / mentor_kb / tests) are implemented, and the Stage 3
`retrieval.py` docstring and README/ARCHITECTURE edits are dropped. Files touched in Stage 2: `docs/DECISIONS.md`
(ADR-015), `docs/ENGINEERING_METRICS.md`, `docs/PROJECT_STATE.md`, `docs/phase-reports/phase-06-report.md` (new),
this plan, the spec, `.claude/specs/README.md`. No code, tests, config, data labels or dependencies changed.

### Amendment 4 (final remediation, owner-approved; 2026-11-02)
Files: `scripts/bench_retrieval.py` (SF-1 per-file mentor-KB validation; docstring accuracy),
`tests/test_bench_retrieval.py` (SF-1 tests), `.gitattributes` (new; evaluation assets only), and docs
(`docs/phase-reports/phase-06-report.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`, `docs/ENGINEERING_METRICS.md`,
the spec, this plan). No production code, label, corpus content or dependency changed.

### Amendment 3 (review remediation, owner-approved S1, S2, Q1–Q6; 2026-11-02)
Files: `scripts/bench_retrieval.py`, `src/eval/retrieval_eval.py` (achievable-ideal NDCG, explicit None-score
semantics), `tests/test_bench_retrieval.py`, `tests/test_retrieval_eval.py`, plus docs (`docs/ENGINEERING_METRICS.md`,
`docs/DECISIONS.md` ADR-015 addendum, `docs/PROJECT_STATE.md`, the phase report, this plan, the spec). No production
code, config, corpus content, gold label or dependency changed. The corrected benchmark was re-run with
`--cache-only` (0 API requests). NITs from both reviews went to the Backlog.

## Steps

### Stage 1 — measurement
0. **Approved guardrails (live run):** preflight (libraries, provider = gemini, key present → boolean only) →
   print model, estimated tokens (chars/3, conservative), request count (1 per text, conservative), estimated cost →
   require `--confirm-live` → hard limits 1,000,000 tokens / 500 requests enforced in the embedding wrapper → cache by
   (model, dim, kind, sha256(text)). Multi-query / HyDE / cross-encoder forced off (no generation calls); Langfuse
   off. Corpora: mentor KB (global, repo-tracked) + synthetic text written for the benchmark only.
1. **Freeze the labelling method.** Write `retrieval_gold.json` rows for the mentor KB (from `data/mentor_kb/**`) and
   the synthetic upload-style corpus: questions phrased the way students ask, gold = verbatim quotes. Include ≥ 10
   unanswerable rows. If Q1 = (b), add an empty NCERT section with a documented row format for the user to fill in.
   Commit nothing; record the SHA-256 of the dataset before step 4.
2. **Pure metrics** in `retrieval_eval.py` + tests first (TDD, hand-computed expectations:
   e.g. gold at ranks [2, 7] with 2 gold → Recall@5 = 0.5, MRR = 0.5, NDCG@10 = (1/log2 3 + 1/log2 8)/(1 + 1/log2 3)).
3. **Runner** `scripts/bench_retrieval.py`:
   - Before any `src.*` import: `os.environ["QDRANT_URL"] = ""`, `os.environ["QDRANT_PATH"] = <tempdir>`, and turn
     off the response cache. Collections are named `bench_<corpus>`.
   - Chunk each corpus with the production function for its workflow (`mentor_kb._make_docs`,
     `get_text_splitter().create_documents` + `create_vector_store` metadata, `build_chapter_documents` for NCERT).
   - Validate the dataset: every gold quote is found in ≥ 1 chunk, otherwise exit non-zero listing the bad rows.
   - Embedding cache: wrap `get_embeddings()` in a caching `Embeddings` keyed by (model, dim, sha256(text)), stored
     at `--cache` (default: OS temp dir, outside the repo).
   - Per query: one dense search with k = 10 (scores kept). Derive V0/V1/V2 from that **same pool**. V1 = production
     `rerank_scored_documents` on the top-5. V2 = re-rank all 10, keep 5. V3 = mentor KB as production calls it (k=4,
     threshold 0.25). Also time the production functions (`similarity_search_with_sources`) end to end, so V1 is
     checked against the real code and not only against the simulation (assert same chunk ids).
   - Metrics + paired bootstrap CIs (10 000 resamples, seed 0), latency p50/p95, threshold metrics, citation-source
     precision / recall ceiling for the NCERT corpus (R6).
   - Outputs: `--report <md>` and `--json <path>` (defaults in the OS temp dir).
   - `--fake-embeddings`: deterministic hash vectors for offline tests.
4. **Offline tests** `tests/test_bench_retrieval.py`: run the runner in-process with `--fake-embeddings`. Assert: it
   completes; `QDRANT_URL` set to a dummy value is ignored; all collections live under the temp path with the
   `bench_` prefix; V1 chunk ids equal `similarity_search_with_sources` output; a corrupted gold quote fails
   validation.
5. **Live baseline** (only if Q3 = yes): `uv run python scripts/bench_retrieval.py --report <tmp>/retrieval_baseline.md
   --json <tmp>/retrieval_baseline.json`. Paste the summary into `docs/ENGINEERING_METRICS.md` with the date, commit,
   dataset hash and corpus hashes.
6. **Apply the gates** (spec R5) to the numbers and write a one-table decision summary.

**▶ CHECKPOINT — stop and show the user:** the baseline table, CIs, and gate decisions. Stage 2 starts only on the
user's go-ahead, and only for the gates that passed.

### Stage 2 — gated changes (skip any whose gate failed)
7. G-fetch: test first (`test_retrieval_rerank.py`), then `config.py` setting + `vector_store.py` over-fetch with an
   output cap of k. Default = the measured winner. `1` reproduces today's behaviour exactly.
8. G-threshold: change the default(s) only; the env overrides stay.
9. G-mentor: test first, then `mentor_kb.search_kb` → `similarity_search_with_sources`, keeping the
   `{"context","citations","grounded"}` shape and `[source]` prefixes.
10. Re-run the live benchmark. The gated metric must reproduce its improvement (AC6), or the change is reverted.

### Stage 3 — honesty, hygiene, close
11. Docstrings in `retrieval.py`; README / ARCHITECTURE retrieval wording from the measured results (AC9).
12. (R9 is implemented in Stage 1 as the runner preflight; no application change.)
13. Reviews: `code-quality-reviewer` and `security-reviewer` (the vector store and ownership-adjacent code changed).
14. `/complete-phase`: ADR-015, report, PROJECT_STATE (backlog items from spec §10, risks #4/#9/#10), status updates.

## Reused code
- `src/core/vector_store.py`: `get_embeddings`, `get_text_splitter`, `upsert_documents`, `load_vector_store`,
  `similarity_search_with_sources`, `gather_scored_documents`, `SIMILARITY_THRESHOLD`.
- `src/core/retrieval.py`: `rerank_scored_documents`, `reciprocal_rank_fusion`.
- `src/core/mentor_kb.py`: `_make_docs`, `KB_THRESHOLD`.
- `src/agents/ncert/graph.py`: `build_chapter_documents`, `number_sources` (R6).
- `src/eval/retrieval_eval.py`: `hit_at_k`, `precision_at_k`, `reciprocal_rank`, `dense_order`, `hybrid_order`.
- Benchmark script conventions: `scripts/bench_routing.py`.
- Tests: `tests/conftest.py` env bootstrap; fake embeddings / monkeypatch patterns in `tests/test_advanced_retrieval.py`.

## Verification
```bash
uv run pytest -q tests/test_retrieval_eval.py tests/test_bench_retrieval.py
uv run pytest -q tests/test_rrf_fusion.py tests/test_retrieval_rerank.py tests/test_advanced_retrieval.py \
  tests/test_rag_retrieve.py tests/test_rag_citations.py tests/test_ncert_evidence.py
uv run pytest -q tests/test_security_ownership.py tests/test_chat_isolation.py
uv run pytest -q                                   # full suite
uv run ruff check src tests scripts/bench_retrieval.py
uv run python -m compileall -q src tests
uv run python -m src.eval.retrieval_eval --help    # AC8: existing CLI intact
uv run python scripts/bench_retrieval.py --fake-embeddings --report "$TMP/b.md" --json "$TMP/b.json"  # offline
uv run python scripts/bench_retrieval.py --report "$TMP/b.md" --json "$TMP/b.json"   # live (Q3 approval)
```
Manual / workflow checks: offline smoke for the 8 workflows (`docs/SMOKE_CHECKLIST.md` offline section). Live
workflow smoke stays UNVERIFIED unless the user runs it against a configured Qdrant.

## Rollback
- Stage 1 is additive (new files + additive functions). Revert by deleting the new files (with user approval) and
  reverting `retrieval_eval.py` / `ENGINEERING_METRICS.md`. No runtime effect.
- Stage 2 is setting-driven: `RETRIEVAL_FETCH_K_MULTIPLIER=1`, `SIMILARITY_THRESHOLD=0.3`,
  `MENTOR_KB_THRESHOLD=0.25` restore today's behaviour without a deploy. A code revert of `vector_store.py` /
  `mentor_kb.py` / `config.py` is clean (no data, schema or collection change).
- R9: revert `vector_store.py`; behaviour returns to the ImportError path.
- No migrations, no re-indexing, no collection changes in production, so no data rollback is needed.
