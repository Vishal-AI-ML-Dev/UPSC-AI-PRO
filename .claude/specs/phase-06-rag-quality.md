# Phase 06 — Advanced RAG & Retrieval Quality

Status: DONE (2026-11-03) — measurement + documentation; no production retrieval change (ADR-015)  ·  Owner: user  ·  Created: 2026-11-02  ·  Related risks: #4 (Chroma), #9 (cache TTL), #10 (eval gate)
Related audit sections: J, U#9–10

## 1. Goal
Measure retrieval quality with standard metrics (Recall@k, MRR, NDCG@10) on a labelled dataset, over the paths
production actually runs. Change retrieval only where a measured, statistically supported improvement justifies it.
Whatever the outcome, docs must describe what retrieval really does.

## 1a. Approval record (2026-11-02, user)
- **Q1 NCERT → option (b):** runner + dataset support NCERT, but the NCERT section stays EMPTY until the owner
  supplies questions and gold labels. Claude does not read `data/ncert/` or copy its PDFs.
- **Q2 → R9 replaced:** no change to application startup. Instead the **benchmark runner** gets an actionable
  preflight error when `qdrant-client` / `langchain-qdrant` or the embedding configuration is unavailable.
  **R10 deferred** to the Backlog.
- **Q3 → bounded live run approved** under guardrails: already-configured local key only (never printed); before
  the first request print the embedding model, estimated input tokens, request count and estimated cost; hard
  limits **1,000,000 input tokens** and **500 embedding requests** (stop immediately when reached); embeddings cached
  by unique input text and reused across variants; **no LLM generation calls**; no real user uploads or unrelated
  private data. If model / token estimate / API config is unclear, stop before any network request.
- **Q4 → approved:** dataset + synthetic corpora in `src/eval/`, runner in `scripts/`, summary in
  `docs/ENGINEERING_METRICS.md`, generated reports kept out of Git.
- **Evaluation refinements:** target ≥ 60 answerable + ≥ 10 unanswerable queries across the real retrieval paths.
  If smaller, report CIs and underpowered comparisons honestly. Relevance = source/chunk evidence labels (not
  keyword overlap). Paired, query-level 95% bootstrap CIs. For unanswerable queries report the **no-answer
  false-positive retrieval rate** (share returning ≥ 1 chunk above the configured threshold). **No "false-grounding
  rate" claim** (that needs generated answers; it stays separate from this retrieval-only baseline).
- **Stage 1 authorised only.** No production retrieval behaviour, threshold, ranking, reranking, chunking or
  indexing change. Mandatory checkpoint after the baseline.

## 2. Non-goals
- No new dependencies (no BM25 library, no `sentence-transformers`, no Cohere SDK, no `fastembed`). If the
  baseline suggests one is worthwhile, Phase 06 records the evidence and proposes it as a separate spec + ADR.
- No new infrastructure (no new vector DB, no Redis, no sparse-vector collections, no re-indexing of existing
  production collections).
- No re-chunking / re-embedding of existing user collections (that would force every user to re-upload).
- No `rag` supervisor route and no HTTP path to `src/graph/rag_graph.py` (ADR-014). CRAG stays eval-only.
- No change to ownership rules or `persist_key` construction (ADR-007).
- No move of `src/eval/` into `evals/` (ADR-003; Phase 08). No change to CI (`.github/`) in this phase.
- No API path / response-shape change; no frontend change.
- No document-aware Mentor chat (Phase 07 candidate).
- No LLM-as-judge rework (Phase 08). Answer faithfulness is out of scope except where noted in R6.

## 3. Current behaviour (verified from source, 2026-11-02)

### 3.1 Ingestion, chunking, metadata, embeddings, storage
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Splitter | `src/core/vector_store.py:get_text_splitter` | `RecursiveCharacterTextSplitter`, `chunk_size=1000`, `chunk_overlap=150` chars (`config.py:109-110`), separators `\n\n`, `\n`, `।`, `.`, space. |
| Embeddings | `vector_store.py:_make_gemini_embeddings`, `_DimPinnedEmbeddings` | `models/gemini-embedding-001`, pinned to 768 dims (Matryoshka truncation + L2 norm). 45 s request timeout. Same model for documents and queries (no task-type distinction; UNVERIFIED whether the wrapper sets `task_type`). |
| Store | `vector_store.py:upsert_documents`, `_ensure_qdrant_collection` | One Qdrant collection per `persist_key`, cosine distance. `add_documents` with no ids, so re-ingest appends duplicates (backlog, Phase 04). |
| Chroma fallback | `vector_store.py:load_vector_store`, `upsert_documents`, `create_vector_store` | Imports `langchain_chroma`; **not installed** (verified: `import langchain_chroma` → ImportError). Without `QDRANT_URL`/`QDRANT_PATH` every vector path raises. Risk #4. |
| NCERT ingest | `src/agents/ncert/graph.py:build_chapter_documents` | Split **per PDF page**; metadata `persist_key, source_type, class_name, subject, chapter, document, source_title, source_path, pdf_page, chunk_index`. Global key `ncert_…`. |
| Upload ingest | `src/agents/upload/graph.py` → `create_vector_store` | Whole text split; metadata `persist_key, chunk_index, source_type, source_title, filename, pdf_hash, book, subject, paper`. No page numbers. Key `upload_<user>_<md5>`. |
| Lecture ingest | `src/agents/lecture/graph.py:build_lecture_chat_index` → `create_vector_store` | Whole transcript split; metadata `persist_key, chunk_index, source_type, source_title, video_id`. No timestamps. Key `lecture_<vid>` / `lecture_<user>_paste_<id>`. |
| PYQ bank ingest | `src/agents/pyq/graph.py:build_question_bank` | Whole text split; metadata `source_type, source_title, filename, pdf_hash` — **no `chunk_index`, no `persist_key`**. Key `pyqbank_<user>`; appended per upload. |
| Mentor KB ingest | `src/core/mentor_kb.py:build_kb`, `scripts/ingest_mentor_kb.py` | `data/mentor_kb/upsc_facts.md` + 3 topper strategy files (~43 KB total); metadata `source` label. Global key `mentor_kb`. |

### 3.2 Retrieval paths per workflow (they are NOT all the same)
| Workflow | Entry | Retrieval call | k | Threshold | Lexical/RRF? | Output to model |
|---|---|---|---|---|---|---|
| NCERT chat | `ncert/graph.py:ask_ncert` | `similarity_search_with_sources` | 5 | 0.3 | yes | Numbered `[S1..Sn]` with `document, PDF p.N` |
| PDF / Document chat | `upload/graph.py:ask_upload` | `similarity_search` | 5 | 0.3 | yes | Joined text, fenced; **no citations** |
| Lecture chat | `lecture/graph.py:ask_lecture` | `similarity_search` | 5 | 0.3 | yes | Joined text, fenced; **no citations** |
| PYQ bank / lecture question generation | `pyq/graph.py:_generate_grounded` | `similarity_search` | 8 | 0.3 | yes | Joined text, **not fenced** (see §3.6) |
| AI Mentor (KB tool) | `graph/tools.py:knowledge_base_tool_fn` → `mentor_kb.search_kb` | raw `db.similarity_search_with_relevance_scores` | 4 | 0.25 (`MENTOR_KB_THRESHOLD`) | **no** (dense only, bypasses `vector_store` pipeline) | `[source] text`, fenced |
| AI Mentor (web) / Current Affairs | `graph/tools.py` `web_search`, `agents/current_affairs/` | `search_web()` | — | — | n/a (live web, not vector) | — |
| Answer Evaluator, Study Planner | — | none | — | — | — | — |
| CRAG (eval only) | `graph/rag_graph.py:retrieve_node` (used by `src/eval/llm_eval.py`) | `similarity_search_with_sources` | 5 | 0.3 | yes | — |

### 3.3 The "hybrid" / RRF step — what it really does
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Candidate set | `vector_store.py:gather_scored_documents` | One dense search of **k** candidates (multi-query and HyDE are off by default: `config.py:346,352`). |
| Re-rank | `retrieval.py:rerank_scored_documents` | RRF over two rankings **of the same k candidates**: dense score rank and lexical concept-coverage rank (stdlib regex, curated abbreviation expansions). |
| Truncation | `vector_store.py:similarity_search*` | None: all k candidates above the threshold are returned. `rerank_top_n` applies only when the cross-encoder is on. |
| **Consequence** | — | With the defaults, the lexical/RRF step **never changes which chunks reach the model**, only their order. It cannot change Recall@k. It can change MRR/NDCG and, for NCERT, which chunk is labelled `S1`. For upload/lecture/PYQ, order only changes position in the prompt. |
| Cross-encoder | `retrieval.py:cross_encoder_rerank`, `config.py:336` | Off. `local` needs `sentence-transformers` (**not installed**); `cohere` needs the `cohere` SDK (**not installed**) + key. Fail-open. |
| Threshold | `vector_store.py:SIMILARITY_THRESHOLD` (env, 0.3) | Applied to the raw relevance score after re-rank. How the LangChain-Qdrant relevance function maps cosine to 0..1 is UNVERIFIED; the benchmark must record the score distribution. |

### 3.4 Ownership & source isolation (Phase 02, ADR-007) — verified unchanged
- Owned keys are built server-side: `_upload_key`, `_lecture_key`, `_bank_key`; the model-facing KB tool is pinned to
  the mentor KB (`graph/tools.py:mentor_kb_search` ignores any `persist_key`).
- There is no metadata filtering inside a collection; isolation comes from one collection per owner and document.
  This phase keeps that model.

### 3.5 Citations & grounding
| Aspect | File / function | Observed behaviour |
|---|---|---|
| NCERT | `ncert/graph.py:number_sources`, `source_footer` | `[S#]` labels from metadata only. Footer checks citation **form** (label exists among retrieved sources). Answers with no valid citation get a warning. Whether the cited text supports the sentence is not checked. |
| Mentor | `core/grounding.py` (`format_sources_block`, `filter_kb_citations_by_overlap`, web citations) | Sources block built from tool results. KB citations filtered by token overlap with the answer. |
| Upload / Lecture / PYQ | — | No citations in the answer. |
| `compose_grounded_answer` | `grounding.py:199` | Never called by production code (doc drift, resolved in Phase 01). |

### 3.6 Evaluation infrastructure
| Aspect | File | Observed behaviour |
|---|---|---|
| Retrieval eval | `src/eval/retrieval_eval.py` | Pure metrics: hit@k, MRR, precision@k. A/B of dense order vs RRF order **on the same k candidates**. Relevance = keyword coverage ≥ 0.5 of `relevant_keywords`. Recall@k deliberately omitted; NDCG not implemented. |
| Dataset | `src/eval/eval_dataset.json` | **3 rows**, all on one NCERT chapter (`ncert_class6_science_1-…`), 3 keywords each. |
| Last report | `src/eval/retrieval_eval_report.md` (committed; run date and environment UNVERIFIED) | n=3, k=5: MRR dense 0.833 → RRF 1.0; hit@5 1.0 → 1.0; precision@5 0.467 → 0.467. |
| Bias in that report | — | The relevance label (keyword presence) and the lexical arm (query-term presence) measure the same thing, so this setup **favours the lexical re-rank by construction**. With n=3, the +0.167 MRR is one query moving from rank 2 to rank 1. **Not usable as evidence.** |
| LLM eval | `src/eval/llm_eval.py`, `gates.py` | LLM-as-judge (faithfulness / relevancy / context precision) via the CRAG graph. Same 3 rows. |
| CI | `.github/workflows/ci.yml:70-116` | Nightly / manual: `llm_eval --strict`, then `retrieval_eval` (report only). Needs secrets and a populated Qdrant; whether the nightly job currently passes is UNVERIFIED. |
| Offline tests | `tests/test_retrieval_eval.py`, `test_rrf_fusion.py`, `test_retrieval_rerank.py`, `test_advanced_retrieval.py`, `test_rag_*`, `test_ncert_evidence.py`, `test_grounding.py` | Pure helpers and mocked stores. |

### 3.7 Local environment (verified 2026-11-02, booleans only)
`QDRANT_URL` unset, `QDRANT_PATH` unset, Google key set, `EMBEDDING_PROVIDER=gemini`, dim 768, rerank / multi-query /
HyDE off. So **no live vector retrieval runs in this checkout today**, and no production collection can be read from
here. The NCERT corpus (`data/ncert/`) is protected from Claude by `protect_paths.py`.

### 3.8 Baseline metrics — what exists today
| Metric | Value | Trust |
|---|---|---|
| Recall@5 | not measured | — |
| MRR | 0.833 dense / 1.0 RRF (n=3, keyword proxy) | **Not trustworthy** (n=3, biased label, run date unknown) |
| NDCG@10 | not measured (production fetches k=5 at most, k=8 for PYQ) | — |
| hit@5 | 1.0 / 1.0 (n=3) | same caveats |

**There is no trustworthy retrieval baseline.** Producing one is the first deliverable (R1–R3) and comes before any
retrieval change.

## 4. Requirements

### WP-A — Measurement (always in scope)
- **R1 Gold dataset.** A retrieval benchmark dataset with ≥ 60 queries over ≥ 2 corpora, labelled by **evidence
  span**, not keywords. Each query has 1–3 short verbatim quotes (≤ 200 chars) from the source. A chunk is relevant
  when it contains a quote (whitespace/case-normalised). Optional grade (2 = primary evidence, 1 = partial) for NDCG.
  Labels are independent of chunk boundaries and of the lexical arm's term matching. Corpora:
  1. **Mentor KB** (`data/mentor_kb/**`, global, readable) — production chunking via `mentor_kb._make_docs`.
  2. **Upload-style document** — a synthetic UPSC study text (≥ 15 KB, written for the benchmark, no user data),
     chunked exactly like `create_vector_store` (whole-text split). Stands in for PDF chat, lecture chat and the PYQ
     bank, which share this chunking and the `similarity_search` pipeline.
  3. **NCERT** (per-page chunking via `build_chapter_documents`) — **only if the user supplies or approves access to
     2–3 chapters** (open question Q1). Without it, NCERT results are UNVERIFIED and the report says so.
  The dataset includes ≥ 10 "unanswerable" queries (no gold) to measure false grounding.
- **R2 Metrics.** Add pure functions: `recall_at_k` (fraction of a query's gold quotes found in the top-k),
  `ndcg_at_k` (graded, log2 discount), `mrr_at_k`. Report hit@k, precision@k, the per-query distribution, and
  threshold metrics: **gold-dropped-by-threshold rate** (a gold chunk in the top-k scored below 0.3) and the
  **no-answer false-positive retrieval rate** (an unanswerable query still returns ≥ 1 chunk above the threshold). Existing functions
  and their signatures stay unchanged.
- **R3 Benchmark runner.** A reproducible live runner that:
  - indexes the benchmark corpora into an **isolated embedded Qdrant in a temp directory** (forces `QDRANT_PATH` and
    blanks `QDRANT_URL` before any `src.*` import; never touches production or user collections);
  - uses the production embedding config (Gemini, 768) and the production pipeline functions, not a re-implementation;
  - caches embeddings to a local file outside the repo so re-runs cost no API calls;
  - evaluates these variants on the **same** candidate pools:
    - **V0** dense order, top-k;
    - **V1** current production (RRF on the same k);
    - **V2** over-fetch 2k → RRF → keep k (the only stdlib way the lexical arm can change Recall@k);
    - **V3** mentor KB as production calls it (raw dense, k=4, threshold 0.25) vs the shared pipeline;
  - reports, per corpus and overall: Recall@5, Recall@10, MRR@10, NDCG@10, hit@5, threshold metrics, retrieval
    latency p50/p95 per variant (embedding call excluded and reported separately), and **paired bootstrap 95% CIs**
    for every V-vs-V1 delta (stdlib `random`, fixed seed);
  - writes a markdown report and a JSON result file to paths given on the CLI. The default output location is outside
    `src/` (open question Q4).
- **R4 Baseline record.** The V1 baseline (date, commit, embedding model/dim, chunk size, corpus SHA-256s, dataset
  size, command) is recorded in `docs/ENGINEERING_METRICS.md` (ADR-012: one canonical metrics page).

### WP-B — Evidence-gated changes (each lands only if its gate passes; otherwise production is unchanged)
- **R5 Decision rules** (fixed before the measurement is run):
  - **G-fetch (adopt V2 over-fetch):** overall Recall@5 delta vs V1 has a 95% CI lower bound > 0, **and** no corpus
    loses more than 0.02 Recall@5 or MRR@10, **and** added p95 retrieval latency ≤ 50 ms. If adopted: a new setting
    `retrieval_fetch_k_multiplier` (default from the evidence, `1` = today's behaviour) in `config.py`, used by
    `similarity_search` / `similarity_search_with_sources`; the output is still capped at k.
  - **G-threshold (change 0.3 / 0.25):** only if one threshold reduces the gold-dropped rate without raising the
    no-answer false-positive retrieval rate by more than 5 points on the same data. Otherwise unchanged; the distribution goes into the
    report.
  - **G-mentor (route the mentor KB through the shared pipeline):** same rule as G-fetch on the mentor KB corpus,
    using V3 vs the pipeline variant.
  - **G-lexical (keep or drop the lexical/RRF re-order):** if V1 vs V0 shows no significant MRR@10/NDCG@10
    difference (CI includes 0), **keep the code**: no behaviour change and no deletion (ADR-006). Rename it honestly in
    docs and docstrings ("re-orders the same k dense candidates"). If V1 is significantly **worse**, open question to
    the user before changing the default.
  - **G-rerank (cross-encoder):** no dependency is added in Phase 06. Record **headroom** = Recall@10 − Recall@5 and
    the MRR gap to 1.0. If headroom ≥ 0.10, the report recommends a follow-up spec (dependency + latency budget +
    ADR). Otherwise reranking is documented as "not justified by current evidence".
  - **True lexical index (BM25 / sparse):** not built in Phase 06. Same headroom-style evidence decides whether a
    follow-up spec is proposed: share of queries whose gold quote is absent from the dense top-10 but has exact rare
    terms in the query.
- **R6 Citation accuracy (measurement, NCERT path; report-only).** Using the gold dataset, measure the share of
  retrieved `S#` sources that contain a gold quote (**citation-source precision**), and whether the gold chunk is
  among the numbered sources (**citation recall ceiling**). Answer-level citation checks need generation and an LLM
  judge, which belong to Phase 08. Phase 06 does not change `source_footer`.

### WP-C — Honesty & hygiene
- **R7 Docs truthfulness.** README / ARCHITECTURE / `retrieval.py` module docstring describe the measured behaviour
  (V1 re-orders the same k candidates; the measured effect with CIs; reranking off and uninstalled). No "hybrid
  retrieval" claim unless a lexical candidate source exists.
- **R8 Existing eval preserved.** `src/eval/retrieval_eval.py` CLI, `run_retrieval_eval`, its report format and
  `eval_dataset.json` keep working (CI calls them). Changes are additive.

### WP-D — Per user decision (Q2)
- **R9 (as approved): benchmark preflight only.** The runner fails fast with an actionable message when the Qdrant
  libraries or the embedding configuration are missing. Application startup and routes are unchanged. Risk #4
  stays open (Backlog). ~~Original proposal:~~ *(superseded)* Fail fast with one clear error when neither `QDRANT_URL` nor
  `QDRANT_PATH` is set: `ensure_vector_storage()` logs an ERROR at startup and returns False; vector calls raise
  `RuntimeError("Vector store not configured: set QDRANT_URL or QDRANT_PATH")` instead of `ImportError`. Chroma
  branches stay in the code (ADR-006, deletion needs approval). ADR-015.
- **R10 Volatile-fact cache bypass (risk #9): DEFERRED to the Backlog (user decision).** It touches the Mentor chat path
  (`routes/chat.py`, `response_cache.py`) and is not about retrieval. Proposal: backlog / Phase 07.

## 5. Affected workflows
| Workflow | Impact |
|---|---|
| NCERT, PDF chat, Lecture chat, PYQ bank | Only if G-fetch / G-threshold pass: the shared `similarity_search*` set or threshold changes; the default keeps today's behaviour unless the evidence says otherwise. Ownership untouched. |
| AI Mentor | Only if G-mentor passes: `mentor_kb.search_kb` uses the shared pipeline (same KB, same fence, same tool contract). |
| Current Affairs, Evaluator, Planner | Not touched. |
| All | R9 (as approved) touches only the benchmark runner; no workflow is affected. |

## 6. Acceptance criteria (testable)
- [ ] AC1 Gold dataset: ≥ 60 queries, ≥ 2 corpora (3 if Q1 = yes), ≥ 10 unanswerable. A validator confirms 100% of
      gold quotes are found in ≥ 1 production-chunked chunk. Corpus files are hashed in the report.
- [ ] AC2 New pure metrics (`recall_at_k`, `ndcg_at_k`, `mrr_at_k`, threshold metrics, paired bootstrap CI) match
      hand-computed values in offline tests (including empty, no-gold and all-relevant edge cases).
- [ ] AC3 Runner runs offline in tests with a fake deterministic embedding and an embedded Qdrant temp dir.
      Assertions: no network; `QDRANT_URL` is ignored even if set; every collection it touches is under the temp dir
      with a `bench_` prefix.
- [ ] AC4 A live baseline run (V0–V3) has been executed, and its report (numbers + CIs + command + commit + corpus
      hashes) is in `docs/ENGINEERING_METRICS.md`. If the user does not approve a live run (Q3), AC4 is marked
      UNVERIFIED and **no WP-B change lands**.
- [ ] AC5 Each WP-B gate (G-fetch, G-threshold, G-mentor, G-lexical, G-rerank, lexical index) has a written decision
      citing the measured numbers, recorded in ADR-015. A change that failed its gate is not in the diff.
- [ ] AC6 If any WP-B change lands: the same benchmark re-run shows the gated improvement, and a regression test pins
      the new behaviour (e.g. over-fetch returns ≤ k chunks, and returns today's set when the multiplier is 1).
- [ ] AC7 Ownership preserved: `tests/test_security_ownership.py`, `tests/test_chat_isolation.py` and the Phase 02
      key tests pass unchanged. No new code path accepts a client- or model-supplied `persist_key`.
- [ ] AC8 Existing retrieval-eval CLI still works: `uv run python -m src.eval.retrieval_eval --help` exits 0 and
      `tests/test_retrieval_eval.py` passes unchanged.
- [ ] AC9 Docs (README, ARCHITECTURE, `retrieval.py` docstring) match the measured behaviour. No unqualified
      "hybrid retrieval" claim.
- [ ] AC10 (R9 as approved) The runner's preflight exits non-zero with an actionable message when the Qdrant
      libraries or the embedding configuration are unavailable, and before any network request (offline test).
- [ ] AC11 All 8 workflows still pass their offline smoke checks (`docs/PROJECT_STATE.md`).
- [ ] AC12 `uv run pytest -q` green; `uv run ruff check src tests` clean; compileall exit 0. Frontend untouched.

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Author-written queries and labels favour whoever wrote them | Evidence-span labels (not keywords), written **before** any variant is run. ≥ 10 unanswerable queries. NCERT queries from the user if possible. Dataset frozen (hash recorded) before measurement. |
| Small n → noise read as signal | Paired bootstrap CIs. Gates require CI lower bound > 0 plus a per-corpus no-regression rule. |
| Benchmark corpora ≠ production corpora | Report states the corpora. Upload/lecture results are a proxy (synthetic doc, same chunking + pipeline). NCERT UNVERIFIED without Q1. Production quality stays UNVERIFIED. |
| Runner writes to a real Qdrant | Runner forces `QDRANT_PATH=<tempdir>` and `QDRANT_URL=""` before importing `src.*`; tested (AC3). |
| Live embedding cost / quota | ~60 queries + ~100 chunks ≈ a few hundred embedding calls once; cached afterwards. Needs user approval (Q3). |
| Changing the threshold or k alters answers in 4 workflows | Gate-only. Defaults stay as today unless the evidence passes. Setting-based, so it reverts by env var without a deploy. |
| Over-fetch adds latency | Measured p95 bound in the gate (≤ 50 ms added retrieval latency). |
| Embedded Qdrant behaves differently from the server | Same client library, same cosine distance; noted as a limitation in the report. |
| Mixing measurement and changes in one phase | WP-A is completed and reviewed before WP-B code is written (plan checkpoint). |

## 8. Test plan
| Test | Pattern copied |
|---|---|
| `tests/test_retrieval_eval.py` (extend): `recall_at_k`, `ndcg_at_k`, `mrr_at_k`, evidence-span relevance, threshold metrics, bootstrap CI determinism | pure-helper style of `tests/test_rrf_fusion.py` |
| `tests/test_bench_retrieval.py` (new): dataset validator; runner end-to-end with a fake `Embeddings` (hash-based vectors) on an embedded Qdrant temp dir; QDRANT_URL override; `bench_` prefix; V1 output identical to `similarity_search_with_sources` | `monkeypatch.setattr` pattern; fake embeddings like `tests/test_advanced_retrieval.py` |
| If G-fetch lands: `tests/test_retrieval_rerank.py` (extend): multiplier=1 → identical set; multiplier=2 → ≤ k chunks, includes a lexically matching chunk from the extended pool | existing rerank tests |
| If G-mentor lands: `tests/test_advanced_retrieval.py` / mentor KB tests: same fence, citations, threshold semantics | existing |
| R9 (as approved): runner preflight tests in `tests/test_bench_retrieval.py` (missing library / non-gemini provider / no key → actionable exit before any request) | `monkeypatch` |
| Regression: ownership suites unchanged and green | `tests/test_security_ownership.py`, `tests/test_chat_isolation.py` |

## 9. Open questions
- **Q1 NCERT corpus:** `data/ncert/` is protected from Claude. Should the NCERT corpus (a) be skipped (NCERT results
  UNVERIFIED), (b) be run by you with Claude writing only the runner and query file, where you add the NCERT queries
  and gold quotes, or (c) use 2–3 chapter PDFs you copy into a non-protected benchmark folder? Recommendation: (b).
- **Q2 Optional scope:** R9 Chroma fail-fast **in** (recommended); R10 volatile-cache bypass **deferred** to
  backlog/Phase 07 (recommended)?
- **Q3 Live run approval:** OK for Claude to run the benchmark with your Gemini key (a few hundred embedding calls,
  then cached), against an embedded Qdrant in a temp dir?
- **Q4 Locations:** dataset + synthetic corpus in `src/eval/` (ADR-003) and runner in `scripts/bench_retrieval.py`
  (like `bench_routing.py`). Generated reports are not committed; the summary goes into `docs/ENGINEERING_METRICS.md`.
  OK?

## 10. Out-of-scope findings (to add to the Backlog at phase close)
- `src/agents/pyq/graph.py:_generate_grounded` passes retrieved PYQ-bank text into `BANK_GEN_PROMPT` **without
  `harden_untrusted`** (the caller's own uploaded content, so self-injection only) — LOW.
- PYQ bank chunks lack `chunk_index` / `persist_key` metadata, so multi-query de-dup falls back to a content hash
  (`vector_store.py:_doc_key`) — LOW.
- Upload / lecture / PYQ answers carry no source citations (product gap; Phase 07 candidate) — FEATURE.
- `mentor_kb.py` and `graph/tools.py:knowledge_base_tool_fn` (non-empty `persist_key` branch, unreachable from the
  model) duplicate the search logic outside the shared pipeline — LOW.

## 11. Outcome (Stage 2 decisions, owner, 2026-11-02; ADR-015)
Stage 2 is **documentation only**: no threshold, ranking, fetch-k, reranker/BM25, chunking, indexing, config, route,
test or dependency change. NCERT stays supported but unmeasured. Threshold calibration is deferred to the Backlog.

| AC | Outcome | Evidence |
|---|---|---|
| AC1 dataset | MET (size/coverage) · labels **not human-verified** | 84 + 13 queries, 4 corpora, 101 quotes; `--validate-only` = source containment only; `tests/test_bench_retrieval.py::test_gold_dataset_size_coverage_and_validity` |
| AC2 metrics | MET | `tests/test_retrieval_eval.py` (hand-computed recall/MRR/NDCG, bootstrap, threshold helpers) |
| AC3 offline isolated runner | MET | `test_run_benchmark_offline_isolated_and_consistent` (no socket, URL ignored, settings restored). The mentor KB uses its production name `mentor_kb` **inside** the temp store, so the real `search_kb` can be called; synthetic corpora use the `bench_` prefix |
| AC4 live baseline recorded | MET | `docs/ENGINEERING_METRICS.md` → Retrieval quality benchmark |
| AC5 gate decisions | MET | ADR-015; no change passed its criteria |
| AC6 re-run after change | N/A | no WP-B change made |
| AC7 ownership preserved | MET | no `src/core`, `src/api`, `src/agents`, `src/graph` change; full suite incl. `test_security_ownership.py`, `test_chat_isolation.py` passes |
| AC8 existing eval CLI | MET | `retrieval_eval --help` exit 0; existing tests unchanged (diff: additions only) |
| AC9 docs truthfulness | PARTIAL | README / ARCHITECTURE make no "hybrid retrieval" claim (checked). The `src/core/retrieval.py` docstring still says "hybrid" → Backlog LOW (no code edits in Stage 2) |
| AC10 runner preflight | MET | `test_preflight_*` tests |
| AC11 8 workflows | MET (offline) | no runtime code changed; full suite green |
| AC12 gates | MET | Final (2026-11-03): `1272 passed, 2 skipped, 13 warnings` (2 file-symlink tests need Windows symlink privilege); ruff `All checks passed!`; compileall exit 0; `git diff --check` exit 0. Earlier runs: `1231 passed` (Stage 2), `1267 passed, 1 skipped` (round-1 remediation) |

## 12. Review remediation (owner-approved S1, S2, Q1–Q6; 2026-11-02)
- **S1:** corpus paths are restricted to the approved roots and checked before any read; 16 path tests pass, including a
  directory-link escape. The file-symlink test is skipped on this Windows machine (needs symlink privilege); it runs
  where symlinks are allowed.
- **S2:** temp-dir cleanup with `try/finally`.
- **Q1:** each workflow is checked against its real production function.
- **Q2:** one verified pool per query.
- **Q3:** the over-fetch effect is isolated, and mentor KB / shared pipeline are reported separately.
- **Q4:** per-corpus k labels and headroom at production k.
- **Q5:** achievable-ideal NDCG, with the legacy definition kept.
- **Q6:** tests for exit codes 3–7 and for divergence detection.
- **Corrected cache-only measurement:** `docs/ENGINEERING_METRICS.md`. Decisions in §11 and ADR-015 are unchanged.
- **AC9 note:** the `retrieval.py` docstring is still in the Backlog.

## 13. Final remediation (owner-approved SF-1, S-1, S-2, doc accuracy, LF protection; 2026-11-02)
- **SF-1:** every mentor-KB file is validated individually before it is read (`mentor_kb_file`, guarded `open` for
  the topper-ingest script, validated reads in `_corpus_hashes`). Tests cover valid files, identical sources on the
  real repo, forbidden names, root containment, a directory-link escape (junction on Windows) and a file-symlink
  escape (skipped without symlink privilege).
- **Docs:** the phase report separates the corrected headline from the Stage 1 prior values, with corrected line
  counts; ADR-015 now gives NDCG 0.938 and labels 0.939 as historical. The security wording is precise (link
  resolution, a connect-only network guard, best-effort cleanup).
- **`.gitattributes`:** LF for `src/eval/retrieval_gold.json` and `src/eval/corpora/**` only.
- No measurement, label, corpus or decision changed.
