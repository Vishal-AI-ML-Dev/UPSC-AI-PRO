"""RAG subgraph answer text, fully mocked (no provider, vector store or web call).

Gemini may return ``AIMessage.content`` as a list of blocks. The RAG path used
by Mentor chat (supervisor route ``rag``) must turn that into plain answer text
before appending sources and the trust note.
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.graph.rag_graph as rag_graph

QUESTION = "What are the three tiers of the Panchayati Raj system?"
CHUNKS = [
    {
        "score": 0.86,
        "metadata": {
            "source_type": "ncert",
            "source_title": "NCERT Class 6 Social Science - Chapter 11",
            "chunk_index": 4,
        },
    }
]
THOUGHT = "PRIVATE-THINKING-TEXT"
LIST_CONTENT = [
    {"type": "text", "text": "Village, block and district. "},
    {"type": "thinking", "thinking": THOUGHT},
    {"type": "text", "text": "The Gram Sabha elects the Gram Panchayat."},
]
EXPECTED = "Village, block and district. The Gram Sabha elects the Gram Panchayat."


@pytest.fixture
def run_rag(monkeypatch):
    """Invoke the real RAG subgraph with every external boundary patched."""
    verifier_inputs: list = []

    def fake_structured(schema):
        def respond(messages):
            if schema is rag_graph.GroundingCheck:
                verifier_inputs.append(messages[-1][1])
                return rag_graph.GroundingCheck(confidence="high", unsupported_claims=[])
            return rag_graph.RelevanceGrade(relevant=True)

        return RunnableLambda(respond)

    monkeypatch.setattr(rag_graph, "load_vector_store", lambda key: object())
    monkeypatch.setattr(
        rag_graph,
        "similarity_search_with_sources",
        lambda db, q, k, label: {"context": "Three tiers ...", "grounded": True, "chunks": CHUNKS},
    )
    monkeypatch.setattr(rag_graph, "_structured_llm", fake_structured)

    def run(content):
        monkeypatch.setattr(
            rag_graph, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content=content))
        )
        graph = rag_graph.build_rag_subgraph(
            label="rag", allow_web_fallback=False, enable_reflection=False
        )
        return graph.invoke({"question": QUESTION, "persist_key": "test_key"}), verifier_inputs

    return run


def test_list_content_becomes_plain_answer_with_sources(run_rag):
    out, verifier_inputs = run_rag(LIST_CONTENT)
    answer = out["answer"]
    assert isinstance(answer, str)
    assert answer.startswith(EXPECTED)
    assert THOUGHT not in answer
    assert "Sources used:" in answer
    assert "NCERT Class 6 Social Science - Chapter 11" in answer
    assert out["messages"][-1].content == answer
    # The grounding verifier sees the answer text, not a list repr or thinking.
    assert len(verifier_inputs) == 1
    assert EXPECTED in verifier_inputs[0]
    assert THOUGHT not in verifier_inputs[0]
    assert "'type'" not in verifier_inputs[0]


def test_string_content_unchanged(run_rag):
    out, _ = run_rag("Plain string answer.")
    assert out["answer"].startswith("Plain string answer.")
    assert "Sources used:" in out["answer"]
