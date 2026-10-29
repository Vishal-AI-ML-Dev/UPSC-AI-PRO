"""Offline tests for lightweight hybrid retrieval ranking (RRF fusion)."""

from dataclasses import dataclass

from src.core.retrieval import hybrid_score, lexical_overlap_score, rerank_scored_documents


@dataclass
class Doc:
    page_content: str


def test_lexical_overlap_ignores_stopwords():
    score = lexical_overlap_score(
        "What is curiosity in science?", "Science starts with curiosity and observation."
    )
    assert score > 0.5


def test_hybrid_score_combines_vector_and_lexical():
    # Legacy linear blend kept for backward compatibility.
    assert hybrid_score(0.8, 0.4) == 0.7
    assert hybrid_score(None, 1.0) == 0.25


def test_rerank_tie_breaks_toward_lexical_relevance():
    # RRF: a symmetric rank swap (dense #1 vs lexical #1) ties on fused score;
    # the lexical tiebreak keeps the on-topic chunk first even though the
    # off-topic one has a marginally higher vector score.
    rows = [
        (Doc("unrelated chunk about sports"), 0.82),
        (Doc("science curiosity observation discovery"), 0.8),
    ]
    ranked = rerank_scored_documents(rows, "science curiosity discovery")
    assert ranked[0]["doc"].page_content == "science curiosity observation discovery"
    # hybrid_score is retained as a backward-compat alias of the RRF score.
    assert ranked[0]["hybrid_score"] == ranked[0]["rrf_score"]
