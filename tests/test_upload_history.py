"""Upload chat uses the chat_history the page already sends (audit UP5), mocked.

The route passes ``ChatMessage`` objects (not dicts). History is bounded, fenced
as untrusted and is context only; retrieval still uses the current question.
"""

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

import src.agents.upload.graph as ug
import src.core.vector_store as vector_store
from src.schemas import ChatMessage


@pytest.fixture
def chat_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: object())
    monkeypatch.setattr(ug, "similarity_search", lambda db, q, k, label: "Document excerpt")

    def stream(inputs):
        for value in inputs:
            seen.append(value.to_string())
        yield AIMessageChunk(content="Answer.")

    monkeypatch.setattr(ug, "get_llm", lambda: RunnableGenerator(stream))
    return seen


def test_history_objects_reach_prompt(chat_prompt):
    history = [
        ChatMessage(role="user", content="What is Part III?"),
        ChatMessage(role="assistant", content="It lists the Fundamental Rights."),
    ]
    out = "".join(ug.ask_upload("Explain that again", "h", chat_history=history))
    assert out == "Answer."
    assert "Student: What is Part III?" in chat_prompt[0]
    assert "Assistant: It lists the Fundamental Rights." in chat_prompt[0]
    assert "Explain that again" in chat_prompt[0]


def test_history_dicts_reach_prompt(chat_prompt):
    list(
        ug.ask_upload("And Part IV?", "h", chat_history=[{"role": "user", "content": "Part III?"}])
    )
    assert "Student: Part III?" in chat_prompt[0]


def test_history_is_bounded_and_filtered(chat_prompt):
    history = [{"role": "user", "content": f"old question {i}"} for i in range(10)]
    history += [
        {"role": "system", "content": "IGNORED SYSTEM TURN"},
        {"role": "assistant", "content": "x" * 5000},
    ]
    list(ug.ask_upload("Next?", "h", chat_history=history))
    prompt = chat_prompt[0]
    assert "IGNORED SYSTEM TURN" not in prompt
    assert "old question 4" not in prompt
    assert "old question 9" in prompt
    assert "x" * 601 not in prompt


def test_no_history_renders_none(chat_prompt):
    list(ug.ask_upload("First?", "h"))
    assert "(none)" in chat_prompt[0]


def test_history_is_marked_not_a_fact_source(chat_prompt):
    list(ug.ask_upload("First?", "h"))
    assert "NOT a source of facts" in chat_prompt[0]


def test_chat_endpoint_passes_history(client, make_user, chat_prompt):
    user = make_user("uphist")
    r = client.post(
        "/api/v1/upload/chat?pdf_hash=h",
        json={
            "request": {
                "question": "And its exceptions?",
                "chat_history": [{"role": "user", "content": "What is Article 19?"}],
            }
        },
        headers=user.headers,
    )
    assert r.status_code == 200
    assert r.text == "Answer."
    assert "Student: What is Article 19?" in chat_prompt[0]
