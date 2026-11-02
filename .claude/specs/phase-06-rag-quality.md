# Phase 06 — RAG quality

Status: STUB  ·  Related audit sections: J, U#9–10

## Goal
Retrieval and citations as strong as documented, measured by evals.

## Candidate scope
- "Hybrid" retrieval is dense top-k plus a lexical re-score of the same candidates
  (`src/core/retrieval.py:rerank_scored_documents`); decide: a real lexical index or honest naming.
- Citation validation beyond form checks (`src/agents/ncert/graph.py:source_footer`, `src/core/grounding.py`).
- Cache TTL / bypass for time-sensitive questions (`src/core/response_cache.py`, 24 h default).
- Broken Chroma fallback (`langchain_chroma` not installed, `src/core/vector_store.py`): fix or remove (ADR).
- Optional: OCR for scanned PDFs (new dependency → ADR).

## Candidate acceptance criteria
- [ ] Retrieval eval report before/after on the same dataset.
- [ ] Volatile-fact answers are not served from a 24 h cache.
