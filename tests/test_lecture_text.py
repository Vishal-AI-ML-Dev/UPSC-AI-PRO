"""Lecture model-text handling, fully mocked (no provider, vector store or network).

Gemini may return ``AIMessage.content`` as a list of blocks. Topic detection,
notes generation and the streamed lecture chat must use the text, and notes /
chat must show their existing failure message when the model produced no text.
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableGenerator, RunnableLambda

import src.agents.lecture.graph as lg
import src.core.study_aids as study_aids
import src.core.vector_store as vector_store

API = "/api/v1"
THOUGHT = "PRIVATE-THINKING-TEXT"
TRANSCRIPT = "The Gram Sabha is the assembly of all adult voters of a village. " * 40
TOPIC_TEXT = (
    "TOPIC: Panchayati Raj\nPAPER: GS2\nSYLLABUS: Local government\n"
    "SUBTOPICS: Gram Sabha, Gram Panchayat\nCONTENT_TYPE: teaching\nRELEVANT: yes"
)
NON_TEACHING = "TOPIC: Vlog\nPAPER: GS1\nCONTENT_TYPE: entertainment\nRELEVANT: no"
NOTES_TEXT = "## Panchayati Raj\n- Three tiers: village, block, district."
NOTES_FAIL = "⚠️ Notes generation failed. Please retry."
CHAT_FAIL = "⚠️ Could not process your question. Please try again."
NO_TEXT = [[], "", "   ", [{"type": "thinking", "thinking": THOUGHT}]]
NO_TEXT_IDS = ["empty-list", "empty-string", "blank", "thinking-only"]


def blocks(text):
    return [{"type": "thinking", "thinking": THOUGHT}, {"type": "text", "text": text}]


@pytest.fixture
def invoke_model(monkeypatch):
    """Patch the model to return the given contents in call order (topic, notes)."""
    aids_calls = []

    def fake_aids(text, topic, paper):
        aids_calls.append(topic)
        return "<mindmap>", "<questions>"

    monkeypatch.setattr(study_aids, "generate_study_aids", fake_aids)

    def use(*contents):
        queue = list(contents)
        model = RunnableLambda(lambda _: AIMessage(content=queue.pop(0)))
        monkeypatch.setattr(lg, "get_llm", lambda: model)
        return aids_calls

    return use


@pytest.fixture
def stream_model(monkeypatch):
    """Patch the model to stream the given chunk contents; fake lecture store."""
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(lg, "similarity_search", lambda db, q, k, label: "Lecture excerpt ...")

    def use(contents):
        def stream(inputs):
            for _ in inputs:
                pass
            for content in contents:
                yield AIMessageChunk(content=content)

        monkeypatch.setattr(lg, "get_llm", lambda: RunnableGenerator(stream))

    return use


# ----------------------------- topic detection ------------------------------ #
def test_detect_topic_parses_list_content(invoke_model):
    invoke_model(blocks(TOPIC_TEXT))
    info = lg._detect_topic(TRANSCRIPT)
    assert info["topic"] == "Panchayati Raj"
    assert info["paper"] == "GS2"
    assert info["subtopics"] == ["Gram Sabha", "Gram Panchayat"]


def test_detect_topic_list_content_keeps_relevance_gate(invoke_model):
    invoke_model(blocks(NON_TEACHING))
    info = lg._detect_topic(TRANSCRIPT)
    assert info["relevant"] is False
    assert info["content_type"] == "entertainment"
    invoke_model(blocks(NON_TEACHING))
    with pytest.raises(ValueError, match="genuine teaching lecture"):
        lg.process_lecture_from_text(TRANSCRIPT)


def test_detect_topic_string_unchanged(invoke_model):
    invoke_model(TOPIC_TEXT)
    assert lg._detect_topic(TRANSCRIPT)["topic"] == "Panchayati Raj"


# ----------------------------- notes ---------------------------------------- #
def test_notes_list_content_becomes_text(invoke_model):
    aids_calls = invoke_model(blocks(TOPIC_TEXT), blocks(NOTES_TEXT))
    result = lg.process_lecture_from_text(TRANSCRIPT)
    notes = result["notes"]
    assert isinstance(notes, str)
    assert notes.startswith("> ⚠️ AI-generated notes")
    assert notes.endswith(NOTES_TEXT)
    assert THOUGHT not in notes
    assert aids_calls == ["Panchayati Raj"]
    assert result["mindmap_html"] == "<mindmap>"


def test_notes_string_unchanged(invoke_model):
    invoke_model(TOPIC_TEXT, NOTES_TEXT)
    notes = lg.process_lecture_from_text(TRANSCRIPT)["notes"]
    assert notes.endswith(NOTES_TEXT)


@pytest.mark.parametrize("content", NO_TEXT, ids=NO_TEXT_IDS)
def test_notes_without_text_show_failure(invoke_model, content):
    aids_calls = invoke_model(TOPIC_TEXT, content)
    result = lg.process_lecture_from_text(TRANSCRIPT)
    assert result["notes"] == NOTES_FAIL
    assert aids_calls == []
    assert result["mindmap_html"] == ""


# ----------------------------- chat ----------------------------------------- #
LIST_STREAM = [
    [{"type": "text", "text": "The Gram Sabha "}],
    [{"type": "thinking", "thinking": THOUGHT}],
    [{"type": "text", "text": "elects the Gram Panchayat."}],
]
CHAT_TEXT = "The Gram Sabha elects the Gram Panchayat."


def test_chat_list_stream_yields_text(stream_model):
    stream_model(LIST_STREAM)
    chunks = list(lg.ask_lecture("What does the Gram Sabha do?", "vid12345678"))
    assert all(isinstance(c, str) and c for c in chunks)
    body = "".join(chunks)
    assert body == CHAT_TEXT
    assert THOUGHT not in body


def test_chat_string_stream_unchanged(stream_model):
    stream_model(["The Gram Sabha ", "elects the Gram Panchayat."])
    assert "".join(lg.ask_lecture("What does the Gram Sabha do?", "vid12345678")) == CHAT_TEXT


@pytest.mark.parametrize(
    "contents",
    [[], [""], [[]], [[{"type": "thinking", "thinking": THOUGHT}]]],
    ids=["no-chunks", "empty-string", "empty-list", "thinking-only"],
)
def test_chat_without_text_shows_failure(stream_model, contents):
    stream_model(contents)
    chunks = list(lg.ask_lecture("What does the Gram Sabha do?", "vid12345678"))
    assert all(isinstance(c, str) for c in chunks)
    assert "".join(chunks) == CHAT_FAIL


def test_chat_endpoint_streams_text(client, make_user, stream_model):
    stream_model(LIST_STREAM)
    user = make_user("lecture")
    r = client.post(
        f"{API}/lecture/chat",
        json={"question": "What does the Gram Sabha do?", "video_id": "vid12345678"},
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.text == CHAT_TEXT
