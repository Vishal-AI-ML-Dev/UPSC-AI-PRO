"""Regression test for the production RAG retrieve bug.

Background:
    ``retrieve_node`` used to hardcode a local Chroma directory
    (``persist_dir_for`` + ``os.path.exists`` + ``Chroma(persist_directory=...)``)
    instead of the backend-agnostic ``load_vector_store()``. On a Qdrant-backed
    deployment the on-disk dir never exists, so retrieval always returned empty
    context and the mentor answered WITHOUT grounding.

This test fails if that pattern ever comes back.
"""

import inspect

import src.graph.rag_graph as rag_graph


def test_retrieve_uses_backend_agnostic_loader():
    source = inspect.getsource(rag_graph.build_rag_subgraph)
    # Must retrieve through the backend-agnostic loader (Qdrant in prod, Chroma local).
    assert "load_vector_store(persist_key)" in source
    # Must NOT reconstruct a local Chroma store or probe the local filesystem.
    assert "Chroma(persist_directory" not in source
    assert "persist_dir_for(" not in source
    assert "os.path.exists(persist_dir)" not in source


def test_rag_grader_failure_is_cautious():
    source = inspect.getsource(rag_graph.build_rag_subgraph)
    assert "assuming relevant" not in source
    assert "treating context as unverified" in source
    assert '"rag_relevant": False' in source


def test_rag_has_grounding_verification_step():
    source = inspect.getsource(rag_graph.build_rag_subgraph)
    assert "verify_grounding" in source
    assert "GroundingCheck" in inspect.getsource(rag_graph)
    # Step 10: citation formatting centralised into src.core.grounding
    import src.core.grounding as grounding

    assert "Sources used:" in inspect.getsource(grounding)
    assert "format_sources" in inspect.getsource(rag_graph)
