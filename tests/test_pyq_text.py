"""PYQ model-text handling, fully mocked (no provider, vector store or network).

Gemini may return ``AIMessage.content`` as a list of blocks. Question
generation (topic and personal bank), the self-check pass and the pasted-text
parser must use the text, and the generators must show their failure message
when the model produced no usable text.
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.pyq.graph as pyq
import src.core.vector_store as vector_store

API = "/api/v1"
THOUGHT = "PRIVATE-THINKING-TEXT"
RAW = "Q1. Original question about the Gram Sabha?\n(a) A (b) B\n"
VERIFIED = "Q1. Verified question about the Gram Sabha?\n(a) A (b) B\n"
TOPIC_FAIL = "Could not generate questions. Please try again."
BANK_FAIL = "Could not generate questions from your bank. Please try again."


def blocks(text):
    return [
        {"type": "thinking", "thinking": THOUGHT},
        {"type": "text", "text": text},
    ]


@pytest.fixture
def fake_model(monkeypatch):
    """Patch the model to return the given contents in call order; fake bank store."""
    monkeypatch.setattr(vector_store, "vector_store_exists", lambda key: True)
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(
        vector_store, "similarity_search", lambda db, q, k, label: "Bank context ..."
    )

    def use(*contents):
        queue = list(contents)
        model = RunnableLambda(lambda _: AIMessage(content=queue.pop(0)))
        monkeypatch.setattr(pyq, "get_llm", lambda: model)
        return queue

    return use


GENERATORS = {
    "topic": (lambda: pyq.generate_questions("Panchayati Raj"), TOPIC_FAIL),
    "bank": (lambda: pyq.generate_from_bank("user-1", topic="Panchayati Raj"), BANK_FAIL),
}


@pytest.mark.parametrize("name", list(GENERATORS))
def test_list_generation_and_check_show_verified_text(fake_model, name):
    left = fake_model(blocks(RAW), blocks(VERIFIED))
    chunks = list(GENERATORS[name][0]())
    assert all(isinstance(c, str) for c in chunks)
    body = "".join(chunks)
    assert body.endswith(VERIFIED.rstrip())
    assert THOUGHT not in body
    assert GENERATORS[name][1] not in body
    assert left == []  # generation + self-check both ran


@pytest.mark.parametrize("name", list(GENERATORS))
def test_list_self_check_is_used_not_skipped(fake_model, name):
    fake_model(RAW, blocks(VERIFIED))
    body = "".join(GENERATORS[name][0]())
    assert body.endswith(VERIFIED.rstrip())
    assert "Original question" not in body


@pytest.mark.parametrize("name", list(GENERATORS))
def test_string_replies_unchanged(fake_model, name):
    fake_model(RAW, VERIFIED)
    body = "".join(GENERATORS[name][0]())
    assert body.endswith(VERIFIED.rstrip())


@pytest.mark.parametrize("name", list(GENERATORS))
@pytest.mark.parametrize(
    "content",
    ["", "   ", [], [{"type": "thinking", "thinking": THOUGHT}]],
    ids=["empty-string", "blank", "empty-list", "thinking-only"],
)
def test_no_text_shows_failure_message(fake_model, name, content):
    fake_model(content)
    chunks = list(GENERATORS[name][0]())
    assert all(isinstance(c, str) for c in chunks)
    body = "".join(chunks)
    assert body.endswith(GENERATORS[name][1])
    assert THOUGHT not in body


def test_parse_questions_list_json_uses_model_path(fake_model):
    payload = (
        '[{"question": "What is the capital of India?", "type": "prelims", '
        '"options": ["Delhi", "Mumbai", "Kolkata", "Chennai"], "answer": "Delhi"}]'
    )
    fake_model(blocks(payload))
    questions = pyq.parse_questions("any pasted text")
    assert len(questions) == 1
    assert questions[0]["question"].startswith("What is the capital")
    assert questions[0]["options"][0] == "Delhi"


def test_generate_endpoint_streams_questions(client, make_user, fake_model):
    fake_model(blocks(RAW), blocks(VERIFIED))
    user = make_user("pyq")
    r = client.post(f"{API}/pyq/generate", json={"topic": "Panchayati Raj"}, headers=user.headers)
    assert r.status_code == 200
    assert r.text.endswith(VERIFIED.rstrip())
    assert THOUGHT not in r.text
    assert TOPIC_FAIL not in r.text
