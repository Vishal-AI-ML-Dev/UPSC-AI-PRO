"""Offline, dependency-free tests for the advanced-retrieval helpers (#1-#3).

These assert the FAIL-OPEN contract: when the optional libs / LLM are missing
(as in CI), the new features degrade to the prior behaviour instead of raising.
They import retrieval.py directly (stdlib-only at module top) so they run even
when langchain / sentence-transformers are not installed.
"""

import importlib.util
import os

_HERE = os.path.dirname(__file__)
_RETRIEVAL = os.path.join(_HERE, "..", "src", "core", "retrieval.py")

_spec = importlib.util.spec_from_file_location("_retrieval_under_test", _RETRIEVAL)
retrieval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retrieval)


class _Doc:
    def __init__(self, text):
        self.page_content = text
        self.metadata = {}


def _rows(query="right to privacy article 21"):
    scored = [
        (_Doc("Article 21 right to privacy Puttaswamy judgment"), 0.81),
        (_Doc("unrelated generic paragraph"), 0.20),
    ]
    return retrieval.rerank_scored_documents(scored, query)


def test_rerank_backward_compatible_keys():
    rows = _rows()
    assert len(rows) == 2
    for key in ("rrf_score", "hybrid_score", "lexical_score", "dense_rank"):
        assert key in rows[0]


def test_cross_encoder_rerank_fail_open_without_dep():
    rows = _rows()
    out = retrieval.cross_encoder_rerank("query", rows, provider="local")
    # No sentence-transformers in CI -> input returned unchanged (no raise).
    assert out == rows


def test_cross_encoder_rerank_cohere_needs_key():
    rows = _rows()
    out = retrieval.cross_encoder_rerank("q", rows, provider="cohere", cohere_api_key="")
    assert out == rows  # missing key -> fail-open, order preserved


def test_expand_queries_fail_open_returns_original():
    assert retrieval.expand_queries("DPSP kya hai", n=3) == ["DPSP kya hai"]
    assert retrieval.expand_queries("", n=3) == []
    assert retrieval.expand_queries("single", n=1) == ["single"]


def test_hyde_fail_open_returns_empty():
    assert retrieval.generate_hypothetical_document("cooperative federalism") == ""
    assert retrieval.generate_hypothetical_document("") == ""
