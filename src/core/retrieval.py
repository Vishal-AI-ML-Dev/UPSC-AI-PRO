"""Lightweight hybrid retrieval helpers.

No extra dependency (stdlib ``re`` only). Two candidate rankings -- dense
(vector relevance) and lexical (query-term coverage) -- are fused with
**Reciprocal Rank Fusion (RRF)** so retrieved chunks are ordered by agreement
of RANK, not by a fragile linear blend of scores living on different scales.

Why RRF instead of the old linear blend
---------------------------------------
The previous ``hybrid_score`` mixed an absolute cosine score (~0..1, but often
bunched in a narrow band) with a 0..1 lexical fraction via a fixed weight. When
the two signals live on different scales/spreads, that weighted sum is brittle:
a tiny vector-score gap can dominate a decisive lexical signal (or vice-versa),
and the right weight drifts per corpus. RRF only looks at each item's RANK in
each list (``1 / (k + rank)``), so it is scale-independent and robust.

Query rewriting (lexical arm)
-----------------------------
``rewrite_query`` / ``concept_coverage_score`` expand a small, curated set of
domain abbreviations (UPSC, IAS, NCERT, ...) and split hyphen/slash compounds so
the lexical arm matches more surface forms. Matching is CONCEPT-based: a query
concept counts as covered if the abbreviation OR its full expansion appears --
so expansion never dilutes an exact match (the denominator stays the number of
query concepts). This runs only on the lexical arm, never on the embedding
query, so it carries zero dense-retrieval risk. (LLM-based HyDE is a documented
follow-up; it adds per-query LLM latency/cost and needs the nightly harness.)

The public surface (``rerank_scored_documents`` return keys, ``hybrid_score``,
``lexical_overlap_score``) is kept backward compatible so ``vector_store`` and
the Step 5 retrieval-eval harness need no changes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Cached local cross-encoder models keyed by model name (loading one is heavy).
_CROSS_ENCODER_CACHE: dict[str, Any] = {}

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "was",
    "were",
    "what",
    "why",
    "how",
    "does",
    "do",
    "did",
    "according",
    "chapter",
    "explain",
    "main",
    "idea",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{2,}")

# Standard RRF constant. Larger -> flatter contribution curve (rank gaps matter
# less); 60 is the widely used default from the original RRF paper.
DEFAULT_RRF_K = 60

# Curated, high-precision domain expansions used ONLY by the lexical arm. Each
# key is a lowercase abbreviation; each value is a list of full-form phrases.
# Matching is concept-based (abbreviation OR full form), so these never dilute
# an exact hit. Extend as the corpus grows.
_QUERY_EXPANSIONS: dict[str, list[str]] = {
    "upsc": ["union public service commission"],
    "ias": ["indian administrative service"],
    "ips": ["indian police service"],
    "ifs": ["indian foreign service"],
    "ncert": ["national council educational research training"],
    "csat": ["civil services aptitude test"],
    "pyq": ["previous year question"],
    "pm": ["prime minister"],
    "cm": ["chief minister"],
    "dpsp": ["directive principles state policy"],
    "fr": ["fundamental rights"],
}


def query_terms(text: str) -> set[str]:
    """Return normalized, non-trivial lexical query terms."""
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def lexical_overlap_score(query: str, document: str) -> float:
    """Fraction of important query terms present in a document chunk.

    Kept for backward compatibility (and direct unit coverage). The re-ranker
    now uses the richer :func:`concept_coverage_score`, but this plain overlap
    remains a useful, dependency-free primitive.
    """
    terms = query_terms(query)
    if not terms:
        return 0.0
    doc_terms = query_terms(document)
    return round(len(terms & doc_terms) / len(terms), 3)


def hybrid_score(
    vector_score: float | None, lexical_score: float, *, vector_weight: float = 0.75
) -> float:
    """Legacy linear blend of vector relevance and lexical overlap.

    Retained for backward compatibility only; ranking now uses RRF via
    :func:`rerank_scored_documents`. Prefer :func:`reciprocal_rank_fusion`.
    """
    vector = 0.0 if vector_score is None else max(0.0, min(1.0, float(vector_score)))
    lexical = max(0.0, min(1.0, float(lexical_score)))
    weight = max(0.0, min(1.0, vector_weight))
    return round((weight * vector) + ((1.0 - weight) * lexical), 3)


# --------------------------------------------------------------------------- #
# Query rewriting (lexical arm only -- never touches the embedding query)
# --------------------------------------------------------------------------- #
def rewrite_query(query: str) -> str:
    """Rewrite a query for lexical matching: split hyphen/slash compounds and
    append curated abbreviation expansions.

    Returns a plain string (original terms + any expansion phrases) so it can be
    logged or reused. Concept-level matching lives in
    :func:`concept_coverage_score`; this string form is mainly for transparency.
    """
    if not query:
        return ""
    text = query.replace("-", " ").replace("/", " ")
    extras: list[str] = []
    for term in query_terms(text):
        for phrase in _QUERY_EXPANSIONS.get(term, ()):  # noqa: B007
            extras.append(phrase)
    return (text + " " + " ".join(extras)).strip() if extras else text.strip()


def query_concepts(query: str) -> list[set[str]]:
    """Break a query into concepts. Each concept is the set of accepted surface
    forms (the term itself plus any curated expansion phrases). A concept is
    matched if ANY of its forms is present, so expansion adds recall without
    diluting the coverage denominator.
    """
    text = (query or "").replace("-", " ").replace("/", " ")
    concepts: list[set[str]] = []
    seen: set[str] = set()
    for term in query_terms(text):
        if term in seen:
            continue
        seen.add(term)
        forms = {term}
        for phrase in _QUERY_EXPANSIONS.get(term, ()):  # noqa: B007
            forms.add(phrase)
        concepts.append(forms)
    return concepts


def concept_coverage_score(query: str, document: str) -> float:
    """Fraction of query CONCEPTS covered by a document.

    A concept is covered when the abbreviation appears OR every word of one of
    its full-form expansions appears. Denominator = number of distinct query
    concepts, so an exact abbreviation hit still scores 1.0 (no dilution).
    For a plain query with no expansions this equals term-coverage.
    """
    concepts = query_concepts(query)
    if not concepts:
        return 0.0
    doc_terms = query_terms(document)
    covered = 0
    for forms in concepts:
        for form in forms:
            form_terms = query_terms(form.replace("-", " "))
            if form_terms and form_terms <= doc_terms:
                covered += 1
                break
    return round(covered / len(concepts), 3)


# --------------------------------------------------------------------------- #
# Reciprocal Rank Fusion
# --------------------------------------------------------------------------- #
def reciprocal_rank_fusion(
    rankings: list[list[int]], *, k: int = DEFAULT_RRF_K
) -> dict[int, float]:
    """Fuse several ranked lists of item ids via Reciprocal Rank Fusion.

    Each ranking is a list of item ids ordered best-first. Returns a map
    ``{item_id: fused_score}`` where ``fused_score = sum(1 / (k + rank))`` over
    the lists the item appears in. Only ranks matter, so the result is immune to
    the vector/lexical score-scale mismatch.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank)
    return fused


def rerank_scored_documents(
    scored_docs: list[tuple[Any, float | None]], query: str, *, rrf_k: int = DEFAULT_RRF_K
) -> list[dict[str, Any]]:
    """Re-rank vector hits by fusing dense + lexical rankings with RRF.

    Returns rows sorted best-first. Each row keeps the original vector ``score``
    and ``lexical_score`` plus fusion diagnostics (``dense_rank``,
    ``lexical_rank``, ``rrf_score``). ``hybrid_score`` is retained as a
    backward-compatible alias of ``rrf_score`` (it now reflects RRF, not the old
    linear blend), so existing callers keep working unchanged.
    """
    rows: list[dict[str, Any]] = []
    for doc, score in scored_docs:
        excerpt = getattr(doc, "page_content", "") or ""
        rows.append(
            {
                "doc": doc,
                "score": round(score, 3) if score is not None else None,
                "lexical_score": concept_coverage_score(query, excerpt),
            }
        )
    if not rows:
        return rows

    n = len(rows)
    # Rank 1 = best. Dense: by raw vector score (None sinks). Lexical: by concept
    # coverage. Stable sort keeps input order as a deterministic tiebreak.
    dense_ranking = sorted(
        range(n),
        key=lambda i: (rows[i]["score"] is not None, rows[i]["score"] or 0.0),
        reverse=True,
    )
    lexical_ranking = sorted(range(n), key=lambda i: rows[i]["lexical_score"], reverse=True)
    fused = reciprocal_rank_fusion([dense_ranking, lexical_ranking], k=rrf_k)
    dense_rank = {idx: r for r, idx in enumerate(dense_ranking, start=1)}
    lexical_rank = {idx: r for r, idx in enumerate(lexical_ranking, start=1)}

    for i, row in enumerate(rows):
        row["dense_rank"] = dense_rank[i]
        row["lexical_rank"] = lexical_rank[i]
        row["rrf_score"] = round(fused[i], 6)
        row["hybrid_score"] = row["rrf_score"]  # backward-compat alias (now RRF)

    # Primary: fused RRF score. Tiebreak toward lexical relevance, then vector
    # score, so a symmetric rank swap still favours the on-topic chunk.
    rows.sort(
        key=lambda r: (r["rrf_score"], r["lexical_score"], r["score"] or 0.0),
        reverse=True,
    )
    return rows


# --------------------------------------------------------------------------- #
# #1 Cross-encoder reranking (precision pass AFTER RRF)
# --------------------------------------------------------------------------- #
# RRF orders by agreement of RANK, but it never actually reads the chunk text
# against the question. A cross-encoder does exactly that -- it scores each
# (question, chunk) pair jointly -- so it is the highest-precision reordering
# available for the final top-k. It is OFF by default and FAIL-OPEN: any missing
# dependency, API error, or empty input simply returns the incoming RRF order
# unchanged, so retrieval never regresses when reranking is unavailable.
def _local_cross_encoder(model_name: str):
    """Load (and cache) a sentence-transformers CrossEncoder."""
    ce = _CROSS_ENCODER_CACHE.get(model_name)
    if ce is None:
        from sentence_transformers import CrossEncoder  # lazy, optional dep

        ce = CrossEncoder(model_name)
        _CROSS_ENCODER_CACHE[model_name] = ce
    return ce


def _local_cross_scores(query: str, texts: list[str], model_name: str) -> list[float]:
    ce = _local_cross_encoder(model_name)
    pairs = [(query, text) for text in texts]
    return [float(s) for s in ce.predict(pairs)]


def _cohere_cross_scores(query: str, texts: list[str], model: str, api_key: str) -> list[float]:
    import cohere  # lazy, optional dep

    client = cohere.Client(api_key)
    resp = client.rerank(query=query, documents=texts, model=model, top_n=len(texts))
    scores = [0.0] * len(texts)
    for res in resp.results:
        scores[res.index] = float(res.relevance_score)
    return scores


def cross_encoder_rerank(
    query: str,
    rows: list[dict[str, Any]],
    *,
    provider: str = "local",
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    top_n: int = 5,
    cohere_api_key: str = "",
    cohere_model: str = "rerank-english-v3.0",
) -> list[dict[str, Any]]:
    """Re-order RRF-fused ``rows`` with a cross-encoder. FAIL-OPEN.

    ``rows`` are the dicts produced by :func:`rerank_scored_documents` (each has
    a ``doc`` with ``page_content``). On success every row gets a ``cross_score``
    and the list is re-sorted best-first, then truncated to ``top_n``. On ANY
    error (missing lib, no api key, provider failure) the input order is
    returned unchanged so retrieval quality never regresses.
    """
    if not rows or not (query or "").strip():
        return rows
    texts = [getattr(r.get("doc"), "page_content", "") or "" for r in rows]
    try:
        if (provider or "local").strip().lower() == "cohere":
            if not cohere_api_key:
                raise ValueError("COHERE_API_KEY missing for provider=cohere")
            scores = _cohere_cross_scores(query, texts, cohere_model, cohere_api_key)
        else:
            scores = _local_cross_scores(query, texts, model)
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.warning("cross-encoder rerank skipped (%s); keeping RRF order", exc)
        return rows
    if not scores or len(scores) != len(rows):
        return rows
    for row, score in zip(rows, scores):
        row["cross_score"] = round(float(score), 6)
    rows.sort(
        key=lambda r: (r.get("cross_score", float("-inf")), r.get("rrf_score", 0.0)),
        reverse=True,
    )
    return rows[:top_n] if top_n and top_n > 0 else rows


# --------------------------------------------------------------------------- #
# #2 Multi-query expansion (RAG-Fusion) -- LLM paraphrases (lazy, fail-open)
# --------------------------------------------------------------------------- #
_EXPAND_SYS = (
    "You rewrite a UPSC study question into short alternative search queries. "
    "Produce diverse paraphrases that expand abbreviations (FR, DPSP, CAG...) "
    "and use synonyms/full forms, so a vector search finds more relevant "
    "passages. Return ONE query per line, no numbering, no extra text."
)


def expand_queries(query: str, *, n: int = 3) -> list[str]:
    """Return up to ``n`` search queries (original first). FAIL-OPEN -> [query].

    Uses the fast LLM tier for cheap paraphrasing. Any error (no LLM, quota,
    parse failure) returns just the original query, so multi-query never breaks
    retrieval -- it can only add recall.
    """
    q = (query or "").strip()
    if not q or int(n) <= 1:
        return [q] if q else []
    try:
        from src.core.llm import get_fast_llm

        resp = get_fast_llm().invoke([("system", _EXPAND_SYS), ("human", q)])
        text = resp.content if hasattr(resp, "content") else str(resp)
        out = [q]
        for line in (text or "").splitlines():
            variant = line.strip().lstrip("-*0123456789. )").strip()
            if variant and variant.lower() not in {x.lower() for x in out}:
                out.append(variant)
            if len(out) >= int(n):
                break
        return out
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.warning("multi-query expansion failed (%s); using original only", exc)
        return [q]


# --------------------------------------------------------------------------- #
# #3 HyDE -- Hypothetical Document Embeddings (lazy, fail-open)
# --------------------------------------------------------------------------- #
_HYDE_SYS = (
    "You are a UPSC subject expert. Write a concise, factual 3-5 sentence "
    "passage that would directly answer the question, in the style of a "
    "textbook/reference. Do not add caveats or say you are unsure -- this text "
    "is only used to improve document retrieval, not shown to the student."
)


def generate_hypothetical_document(query: str) -> str:
    """Draft a hypothetical answer to embed for dense retrieval. FAIL-OPEN -> "".

    Answer-style text sits closer to real documents in embedding space than a
    short question does, so embedding this (instead of the raw query) improves
    the dense arm on conceptual questions. Any error returns "" so the caller
    transparently falls back to normal query embedding.
    """
    q = (query or "").strip()
    if not q:
        return ""
    try:
        from src.core.llm import get_fast_llm

        resp = get_fast_llm().invoke([("system", _HYDE_SYS), ("human", q)])
        text = resp.content if hasattr(resp, "content") else str(resp)
        return (text or "").strip()
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.warning("HyDE generation failed (%s); using raw query", exc)
        return ""
