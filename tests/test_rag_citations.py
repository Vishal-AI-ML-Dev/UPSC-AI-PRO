"""Offline tests for user-facing RAG source formatting."""

from src.graph.rag_graph import _format_sources


def test_format_sources_uses_metadata_when_available():
    text = _format_sources(
        [
            {
                "score": 0.891,
                "metadata": {
                    "source_type": "ncert",
                    "source_title": "NCERT Class 6 Science - Chapter 1",
                    "chunk_index": 3,
                },
            }
        ]
    )
    assert "Sources used:" in text
    assert "NCERT Class 6 Science - Chapter 1" in text
    assert "type=ncert" in text
    assert "chunk=3" in text
    assert "score=0.891" in text


def test_format_sources_falls_back_without_metadata():
    text = _format_sources([{"score": 0.7, "metadata": {}}])
    assert "retrieved context" in text
    assert "score=0.7" in text
