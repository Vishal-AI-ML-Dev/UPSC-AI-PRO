"""Regression tests: citations must reach streaming clients.

Answer nodes stream the model's tokens, then append the deterministic
"Sources used" block to ``state['answer']``. That block is not part of the token
stream, so before this fix streamed answers (what the Mentor UI renders, saves
to history and caches) never carried their citations.

The streaming routes now send the un-streamed tail after the tokens. These
tests drive the real ``/agent/chat/stream`` and ``/mentor/chat`` routes with a
deterministic graph whose answer node streams tokens from a fake chat model.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.core.grounding import SOURCES_HEADER, format_sources, unstreamed_tail

CHAT = "/api/v1/agent/chat/stream"
MENTOR = "/mentor/chat"

BODY = "Prelims is held in May every year."
SOURCES = format_sources(["web — https://upsc.gov.in/notice"])


class _State(TypedDict, total=False):
    question: str
    student_context: dict
    answer: str
    citations: list


def _graph(*, checkpointer=None):
    """Answer node "agent": streams BODY; cites only questions mentioning 'date'."""

    def agent(state):
        model = GenericFakeChatModel(messages=iter([AIMessage(content=BODY)]))
        text = model.invoke(state["question"]).content
        cites = state["question"].endswith("date?")
        return {"answer": text + (SOURCES if cites else ""), "citations": []}

    g = StateGraph(_State)
    g.add_node("agent", agent)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=checkpointer)


class _RecordingCache:
    def __init__(self):
        self.saved = []

    def get(self, **_kw):
        return None

    def set(self, **kw):
        self.saved.append(kw["answer"])


@pytest.fixture
def cache(monkeypatch):
    from src.core import response_cache as rc

    recorder = _RecordingCache()
    monkeypatch.setattr(rc, "_cache_singleton", recorder)
    return recorder


@pytest.fixture
def agent_graph(client, monkeypatch):
    graph = _graph(checkpointer=InMemorySaver())
    monkeypatch.setattr(client.app.state, "agent_graph", graph)
    return graph


def _post(client, user, path, question, **extra):
    r = client.post(path, json={"question": question, **extra}, headers=user.headers)
    assert r.status_code == 200, f"{path} failed with status {r.status_code}"
    return r.text


# --------------------------------------------------------------------------- #
# /agent/chat/stream (the Mentor UI's endpoint)
# --------------------------------------------------------------------------- #
def test_agent_stream_includes_sources_after_tokens(client, make_user, agent_graph, cache):
    user = make_user("cite")
    text = _post(client, user, CHAT, "When is the prelims date?", thread_id=uuid.uuid4().hex)
    assert text == BODY + SOURCES  # tokens once, then the sources block once
    assert text.count(BODY) == 1
    assert "https://upsc.gov.in/notice" in text


def test_agent_stream_caches_answer_with_sources(client, make_user, agent_graph, cache):
    user = make_user("cache")
    _post(client, user, CHAT, "When is the prelims date?", thread_id=uuid.uuid4().hex)
    assert cache.saved == [(BODY + SOURCES).strip()]


def test_agent_stream_never_resends_previous_turn(client, make_user, agent_graph, cache):
    user = make_user("turns")
    thread = uuid.uuid4().hex
    first = _post(client, user, CHAT, "When is the prelims date?", thread_id=thread)
    assert SOURCES_HEADER.strip() in first
    second = _post(client, user, CHAT, "Motivate me", thread_id=thread)
    assert second == BODY  # no stale sources, no repeated earlier answer


def test_agent_stream_without_sources_is_unchanged(client, make_user, agent_graph, cache):
    user = make_user("plain")
    assert _post(client, user, CHAT, "Explain federalism", thread_id=uuid.uuid4().hex) == BODY


# --------------------------------------------------------------------------- #
# /mentor/chat (stateless streaming route). The router is not mounted in
# src/api/main.py, so its handler is exercised on a minimal standalone app.
# --------------------------------------------------------------------------- #
@pytest.fixture
def mentor_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.routes.mentor as mentor_routes

    monkeypatch.setattr(mentor_routes, "_mentor_graph", _graph())
    app = FastAPI()
    app.include_router(mentor_routes.router)
    return TestClient(app)


def _mentor(mentor_client, question):
    r = mentor_client.post(MENTOR, json={"question": question})
    assert r.status_code == 200, f"{MENTOR} failed with status {r.status_code}"
    return r.text


def test_mentor_stream_includes_sources(mentor_client):
    assert _mentor(mentor_client, "When is the prelims date?") == BODY + SOURCES


def test_mentor_stream_without_sources_is_unchanged(mentor_client):
    assert _mentor(mentor_client, "Explain federalism") == BODY


# --------------------------------------------------------------------------- #
# unstreamed_tail (pure)
# --------------------------------------------------------------------------- #
def test_tail_is_remainder_when_answer_extends_stream():
    assert unstreamed_tail(BODY + SOURCES, BODY) == SOURCES


def test_tail_empty_when_everything_streamed():
    assert unstreamed_tail(BODY, BODY) == ""
    assert unstreamed_tail("", BODY) == ""


def test_tail_is_sources_block_when_stream_had_preamble():
    streamed = "Let me check the latest notice. " + BODY
    assert unstreamed_tail(BODY + SOURCES, streamed) == SOURCES


def test_tail_not_duplicated_when_stream_already_has_sources():
    streamed = "preamble " + BODY + SOURCES
    assert unstreamed_tail(BODY + SOURCES, streamed) == ""


def test_tail_empty_for_unrelated_answer_without_sources():
    assert unstreamed_tail("different answer", BODY) == ""
