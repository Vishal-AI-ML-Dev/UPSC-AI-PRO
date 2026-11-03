"""Offline tests for the retrieval-quality eval metrics.

These run in CI with no API key and no vector store: every function under test
is pure. The live run (`python -m src.eval.retrieval_eval`) is separate and
needs a populated vector store.
"""

import math

import pytest

from src.eval.retrieval_eval import (
    any_above_threshold,
    chunk_is_relevant,
    compare_summaries,
    gold_dropped_by_threshold,
    hit_at_k,
    ideal_dcg,
    keyword_coverage,
    mrr_at_k,
    ndcg_at_k,
    normalize_span,
    paired_bootstrap_ci,
    precision_at_k,
    ranked_span_hits,
    recall_at_k,
    reciprocal_rank,
    relevance_flags,
    span_hits,
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


# --------------------------------------------------------------------------- #
# Phase 06: evidence-span metrics (hand-computed expectations)
# --------------------------------------------------------------------------- #
def test_span_hits_normalises_case_whitespace_and_markdown_emphasis():
    chunk = "Spend **5 min\n brainstorming** right then."
    assert span_hits(chunk, ["spend 5 min brainstorming right then", "absent"]) == {0}
    assert normalize_span("  A `b`  **C** ") == "a b c"


def test_span_hits_is_not_keyword_overlap():
    # Every word is present, but not as the contiguous evidence span.
    assert span_hits("then right brainstorming min 5 spend", ["spend 5 min brainstorming"]) == set()


def test_recall_mrr_ndcg_hand_computed():
    # Two gold quotes; quote 0 first found at rank 2, quote 1 at rank 7.
    hits = [set(), {0}, set(), set(), set(), set(), {1}, set(), set(), set()]
    assert recall_at_k(hits, 2, 5) == 0.5
    assert recall_at_k(hits, 2, 10) == 1.0
    assert mrr_at_k(hits, 5) == 0.5
    assert mrr_at_k(hits[2:], 3) == 0.0
    expected = (2 / math.log2(3) + 1 / math.log2(8)) / (2 / 1 + 1 / math.log2(3))
    assert ndcg_at_k(hits, [2, 1], 10) == pytest.approx(expected, abs=1e-6)


def test_ndcg_counts_each_quote_once_and_caps_at_one():
    # Duplicate chunk with the same quote earns nothing the second time.
    assert ndcg_at_k([{0}, {0}], [2], 2) == 1.0
    # One chunk containing both quotes would exceed the ideal; capped.
    assert ndcg_at_k([{0, 1}], [2, 2], 5) == 1.0
    assert ndcg_at_k([set()], [2], 5) == 0.0


def test_metrics_edge_cases():
    assert recall_at_k([], 0, 5) == 0.0
    assert recall_at_k([{0}], 1, 0) == 0.0
    assert mrr_at_k([], 5) == 0.0
    assert ndcg_at_k([{0}], [], 5) == 0.0
    assert ranked_span_hits(["a b", "c"], ["c"]) == [set(), {0}]


def test_threshold_helpers():
    hits = [set(), {0}, set()]
    # Gold at rank 2 scored below the threshold -> dropped.
    assert gold_dropped_by_threshold(hits, [0.9, 0.2, 0.1], 0.3, 3)
    assert not gold_dropped_by_threshold(hits, [0.9, 0.35, 0.1], 0.3, 3)
    # Gold outside the top k is a retrieval miss, not a threshold drop.
    assert not gold_dropped_by_threshold(hits, [0.9, 0.2, 0.1], 0.3, 1)
    assert not gold_dropped_by_threshold(hits, [0.9, None, 0.1], 0.3, 3)
    assert any_above_threshold([0.1, 0.31], 0.3, 2)
    assert not any_above_threshold([0.1, 0.31], 0.3, 1)
    assert any_above_threshold([None], 0.3, 1)


def test_paired_bootstrap_ci_is_deterministic_and_paired():
    base = [0.0, 0.5, 1.0, 0.0, 1.0, 0.5]
    better = [b + 0.25 for b in base]
    ci = paired_bootstrap_ci(base, better, resamples=500, seed=0)
    assert ci == paired_bootstrap_ci(base, better, resamples=500, seed=0)
    # Constant per-query gain: every resample has the same mean.
    assert ci["delta"] == ci["low"] == ci["high"] == 0.25
    assert ci["significant"] is True
    same = paired_bootstrap_ci(base, list(base), resamples=500)
    assert same["delta"] == 0.0 and same["significant"] is False
    mixed = paired_bootstrap_ci([0, 1, 0, 1], [1, 0, 1, 0], resamples=2000)
    assert mixed["low"] < 0 < mixed["high"] and mixed["significant"] is False
    assert paired_bootstrap_ci([], [])["n"] == 0
    with pytest.raises(ValueError):
        paired_bootstrap_ci([1.0], [])


# --------------------------------------------------------------------------- #
# Phase 06 review Q5: achievable ideal DCG when quotes share a chunk
# --------------------------------------------------------------------------- #
def test_ndcg_achievable_ideal_distinguishes_orderings_of_colocated_quotes():
    # pyq-07 shape: grades 2, 2, 1; quotes 0 and 2 share one chunk, quote 1 is alone.
    grades = [2, 2, 1]
    corpus_chunks = [{0, 2}, {1}, set(), set()]
    best = [{0, 2}, {1}]
    worse = [{1}, {0, 2}]
    assert ndcg_at_k(best, grades, 5, ideal_hits=corpus_chunks) == 1.0
    expected_worse = (2 + 3 / math.log2(3)) / (3 + 2 / math.log2(3))
    assert ndcg_at_k(worse, grades, 5, ideal_hits=corpus_chunks) == pytest.approx(
        expected_worse, abs=1e-6
    )
    # The legacy definition caps both at 1.0 and cannot tell them apart.
    assert ndcg_at_k(best, grades, 5) == ndcg_at_k(worse, grades, 5) == 1.0


def test_ndcg_one_chunk_with_all_quotes():
    assert ndcg_at_k([{0, 1}], [2, 2], 5, ideal_hits=[{0, 1}]) == 1.0
    assert ndcg_at_k([set(), {0, 1}], [2, 2], 5, ideal_hits=[{0, 1}]) == pytest.approx(
        1 / math.log2(3), abs=1e-6
    )


def test_ideal_dcg_matches_legacy_when_every_quote_has_its_own_chunk():
    hits = [set(), {0}, set(), set(), set(), set(), {1}, set(), set(), set()]
    assert ndcg_at_k(hits, [2, 1], 10, ideal_hits=[{0}, {1}]) == ndcg_at_k(hits, [2, 1], 10)
    assert ideal_dcg([{0}, {1}], [2, 1], 10) == pytest.approx(2 + 1 / math.log2(3))
    assert ideal_dcg([], [2], 5) == 0.0
    assert ideal_dcg([{0}], [2], 0) == 0.0


def test_ideal_dcg_greedy_fallback_for_many_chunks():
    chunks = [{i} for i in range(10)]
    grades = [1] * 10
    assert ideal_dcg(chunks, grades, 3) == pytest.approx(1 + 1 / math.log2(3) + 0.5)


# --------------------------------------------------------------------------- #
# Phase 06 review Q1: None-score behaviour follows the production function
# --------------------------------------------------------------------------- #
def test_threshold_helpers_none_score_semantics():
    hits = [{0}]
    # similarity_search (PDF / Lecture / PYQ) drops None-score chunks.
    assert gold_dropped_by_threshold(hits, [None], 0.3, 1, none_passes=False)
    assert not any_above_threshold([None], 0.3, 1, none_passes=False)
    # similarity_search_with_sources (NCERT) and mentor_kb.search_kb keep them.
    assert not gold_dropped_by_threshold(hits, [None], 0.3, 1, none_passes=True)
    assert any_above_threshold([None], 0.3, 1, none_passes=True)
