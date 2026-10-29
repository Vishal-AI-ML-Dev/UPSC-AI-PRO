"""Offline tests for the retrieval-quality eval metrics.

These run in CI with no API key and no vector store: every function under test
is pure. The live run (`python -m src.eval.retrieval_eval`) is separate and
needs a populated vector store.
"""

from src.eval.retrieval_eval import (
    chunk_is_relevant,
    compare_summaries,
    hit_at_k,
    keyword_coverage,
    precision_at_k,
    reciprocal_rank,
    relevance_flags,
    summarize_retrieval_scores,
)


def test_keyword_coverage_whole_word_case_insensitive():
    assert keyword_coverage("Science begins with Curiosity.", ["science", "curiosity"]) == 1.0
    assert keyword_coverage("Science begins with wonder.", ["science", "curiosity"]) == 0.5
    assert keyword_coverage("anything at all", []) == 0.0


def test_keyword_coverage_does_not_match_substrings():
    # "science" must not be matched inside "scientific".
    assert keyword_coverage("the scientific method", ["science"]) == 0.0


def test_chunk_relevance_threshold():
    assert (
        chunk_is_relevant("science and curiosity", ["science", "curiosity"], min_overlap=0.6)
        is True
    )
    assert chunk_is_relevant("science only", ["science", "curiosity"], min_overlap=0.6) is False


def test_relevance_flags_preserve_rank_order():
    texts = ["unrelated sports news", "science and curiosity", "more science"]
    flags = relevance_flags(texts, ["science", "curiosity"], min_overlap=0.5)
    assert flags == [False, True, True]


def test_rank_metrics():
    flags = [False, True, False]
    assert hit_at_k(flags, 1) == 0.0
    assert hit_at_k(flags, 2) == 1.0
    assert reciprocal_rank(flags) == 0.5
    assert precision_at_k(flags, 3) == 0.333
    assert reciprocal_rank([False, False]) == 0.0


def test_summarize_and_gate():
    per_query = [[True, False], [False, True]]  # RR = 1.0, 0.5 -> mean MRR 0.75
    summary = summarize_retrieval_scores(per_query, k=2, gate=0.7)
    assert summary["mrr"] == 0.75
    assert summary["hit_at_k"] == 1.0
    assert summary["precision_at_k"] == 0.5
    assert summary["passed"] is True
    assert summarize_retrieval_scores(per_query, k=2, gate=0.8)["passed"] is False


def test_empty_is_zero_not_crash():
    summary = summarize_retrieval_scores([], k=5, gate=0.7)
    assert summary["mrr"] == 0.0
    assert summary["hit_at_k"] == 0.0
    assert summary["passed"] is False


def test_compare_summaries_reports_lift():
    dense = summarize_retrieval_scores([[False, True]], k=2, gate=0.7, label="dense")  # RR 0.5
    hybrid = summarize_retrieval_scores([[True, False]], k=2, gate=0.7, label="hybrid")  # RR 1.0
    ab = compare_summaries(dense, hybrid)
    assert ab["mrr_dense"] == 0.5
    assert ab["mrr_hybrid"] == 1.0
    assert ab["mrr_lift"] == 0.5
