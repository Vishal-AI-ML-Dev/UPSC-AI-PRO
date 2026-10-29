"""Offline tests for Reciprocal Rank Fusion + query rewriting.

All pure (stdlib only) -> run in the offline CI ``tests`` job, no API key or
vector store required.
"""

from dataclasses import dataclass

from src.core.retrieval import (
    DEFAULT_RRF_K,
    concept_coverage_score,
    query_concepts,
    reciprocal_rank_fusion,
    rerank_scored_documents,
    rewrite_query,
)


@dataclass
class Doc:
    page_content: str


# --------------------------------------------------------------------------- #
# Reciprocal Rank Fusion (pure)
# --------------------------------------------------------------------------- #
def test_rrf_uses_ranks_only_scale_independent():
    # Two lists; item 0 is rank-1 in the first, item 2 is rank-1 in the second.
    fused = reciprocal_rank_fusion([[0, 1, 2], [2, 1, 0]], k=DEFAULT_RRF_K)
    # Symmetric swap => items 0 and 2 tie; item 1 (rank 2 in both) is distinct.
    assert round(fused[0], 6) == round(fused[2], 6)
    assert fused[0] > fused[1]  # rank-1-somewhere beats rank-2-everywhere


def test_rrf_rewards_agreement_across_lists():
    # Item 1 is rank-1 in list B and rank-2 in list A -> should top the fusion.
    fused = reciprocal_rank_fusion([[0, 1, 2], [1, 2, 0]], k=DEFAULT_RRF_K)
    top = max(fused, key=fused.get)
    assert top == 1


def test_rrf_k_flattens_contributions():
    small_k = reciprocal_rank_fusion([[0, 1]], k=1)
    big_k = reciprocal_rank_fusion([[0, 1]], k=1000)
    # Larger k shrinks the gap between rank 1 and rank 2.
    assert (small_k[0] - small_k[1]) > (big_k[0] - big_k[1])


# --------------------------------------------------------------------------- #
# rerank_scored_documents with RRF
# --------------------------------------------------------------------------- #
def test_rrf_promotes_lexically_relevant_over_top_vector():
    # X has the best vector score but zero lexical overlap; Y is rank-2 dense but
    # rank-1 lexical -> RRF lifts Y to the top with strictly ordered scores.
    rows = [
        (Doc("quantum sports unrelated matter"), 0.9),  # X
        (Doc("science curiosity discovery observation"), 0.7),  # Y
        (Doc("science only here"), 0.6),  # Z
    ]
    ranked = rerank_scored_documents(rows, "science curiosity discovery")
    assert ranked[0]["doc"].page_content == "science curiosity discovery observation"
    assert ranked[0]["rrf_score"] > ranked[1]["rrf_score"] > ranked[2]["rrf_score"]


def test_rerank_preserves_backward_compatible_keys():
    rows = [(Doc("science curiosity"), 0.8), (Doc("unrelated"), 0.5)]
    ranked = rerank_scored_documents(rows, "science curiosity")
    for row in ranked:
        assert set(["doc", "score", "lexical_score", "hybrid_score"]) <= set(row)
        # new fusion diagnostics are present too
        assert set(["dense_rank", "lexical_rank", "rrf_score"]) <= set(row)
        assert row["hybrid_score"] == row["rrf_score"]


def test_rerank_empty_is_safe():
    assert rerank_scored_documents([], "anything") == []


def test_rerank_handles_all_none_scores():
    # Fallback path (plain search) passes None scores; must not crash and should
    # still let the lexical arm order the results.
    rows = [(Doc("off topic text"), None), (Doc("science curiosity discovery"), None)]
    ranked = rerank_scored_documents(rows, "science curiosity discovery")
    assert ranked[0]["doc"].page_content == "science curiosity discovery"
    assert ranked[0]["score"] is None


# --------------------------------------------------------------------------- #
# Query rewriting (lexical arm)
# --------------------------------------------------------------------------- #
def test_rewrite_query_splits_compounds_and_expands_abbreviations():
    out = rewrite_query("prime-minister UPSC").lower()
    assert "prime" in out and "minister" in out  # hyphen split
    assert "union public service commission" in out  # abbreviation expanded


def test_rewrite_query_empty_is_safe():
    assert rewrite_query("") == ""


def test_query_concepts_group_surface_forms():
    concepts = query_concepts("UPSC syllabus")
    assert len(concepts) == 2  # one concept per distinct term
    upsc_concept = next(c for c in concepts if "upsc" in c)
    assert "union public service commission" in upsc_concept


def test_concept_coverage_matches_expansion_without_dilution():
    # Doc uses the full form, query uses the abbreviation -> still fully covered.
    assert (
        concept_coverage_score("UPSC syllabus", "the union public service commission syllabus")
        == 1.0
    )
    # Doc uses the abbreviation directly -> covered, not diluted by the expansion.
    assert concept_coverage_score("UPSC", "UPSC exam notes") == 1.0
    # Plain query with no expansions behaves like term coverage.
    assert concept_coverage_score("science curiosity", "science and curiosity here") == 1.0


def test_concept_coverage_partial_and_empty():
    assert concept_coverage_score("science curiosity", "only science here") == 0.5
    assert concept_coverage_score("", "anything") == 0.0
