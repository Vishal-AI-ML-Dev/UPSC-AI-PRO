"""Offline tests for vector-store source metadata propagation."""

from src.core.vector_store import get_text_splitter


def test_text_splitter_accepts_source_metadata():
    docs = get_text_splitter().create_documents(
        ["Science begins with curiosity. Observation matters."],
        metadatas=[{"source_type": "ncert", "source_title": "Class 6 Science"}],
    )
    assert docs
    assert docs[0].metadata["source_type"] == "ncert"
    assert docs[0].metadata["source_title"] == "Class 6 Science"


def test_vector_store_create_signature_supports_metadata():
    import inspect

    from src.core.vector_store import create_vector_store

    sig = inspect.signature(create_vector_store)
    assert "metadata" in sig.parameters
