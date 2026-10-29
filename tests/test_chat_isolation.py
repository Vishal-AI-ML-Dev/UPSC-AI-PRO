"""Regression tests: per-user isolation of chat thread state and the response cache.

A client-supplied ``thread_id`` must only ever address the authenticated user's
own checkpoint state and cache entries. These tests drive the REAL chat route,
auth, config and ``ResponseCache`` key/scope/similarity logic; only three
boundaries are replaced:

* ``app.state.agent_graph`` -> a deterministic LangGraph graph (real
  ``InMemorySaver`` checkpointer where thread state matters),
* the Upstash REST transport -> an in-memory store that keeps exactly the
  commands the real cache sends (it has no key logic of its own),
* the embedding provider -> a deterministic bag-of-words embedder.

Every isolation test has a same-user positive control so a broken harness cannot
pass silently.
"""

from __future__ import annotations

import operator
import uuid
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.api.routes.chat import scoped_thread_id

CHAT = "/api/v1/agent/chat/stream"
HIST = "/api/v1/history"


# --------------------------------------------------------------------------- #
# Deterministic graphs + fake transport
# --------------------------------------------------------------------------- #
class _State(TypedDict, total=False):
    question: str
    student_context: dict
    answer: str
    history: Annotated[list, operator.add]


class _Counter:
    def __init__(self):
        self.calls = 0


def _stateful_graph(counter: _Counter):
    """Answers with every earlier question stored in this thread's checkpoint."""

    def remember(state):
        return {"history": [state["question"]]}

    def generate(state):
        counter.calls += 1
        return {"answer": f"prior={list(state.get('history') or [])[:-1]!r}"}

    g = StateGraph(_State)
    g.add_node("remember", remember)
    g.add_node("generate", generate)
    g.add_edge(START, "remember")
    g.add_edge("remember", "generate")
    g.add_edge("generate", END)
    return g.compile(checkpointer=InMemorySaver())


def _stateless_graph(counter: _Counter):
    """No checkpointer: every run returns a unique answer, so reuse == cache hit."""

    def generate(state):
        counter.calls += 1
        return {"answer": f"FRESH-{uuid.uuid4().hex}"}

    g = StateGraph(_State)
    g.add_node("generate", generate)
    g.add_edge(START, "generate")
    g.add_edge("generate", END)
    return g.compile()


class FakeRedisTransport:
    """Stores exactly the commands the real ResponseCache sends."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list] = {}

    def command(self, cmd):
        op = cmd[0].upper()
        if op == "SET":
            self.kv[cmd[1]] = cmd[2]
            return "OK"
        if op == "GET":
            return self.kv.get(cmd[1])
        if op == "LPUSH":
            lst = self.lists.setdefault(cmd[1], [])
            for v in cmd[2:]:
                lst.insert(0, v)
            return len(lst)
        if op == "LTRIM":
            lst = self.lists.get(cmd[1], [])
            start, stop = int(cmd[2]), int(cmd[3])
            self.lists[cmd[1]] = lst[start:] if stop == -1 else lst[start : stop + 1]
            return "OK"
        if op == "LRANGE":
            return list(self.lists.get(cmd[1], []))
        return None


_VOCAB = ["please", "kindly", "explain", "article", "21", "right", "to", "life"]


def _bow_embed(text: str):
    toks = text.lower().split()
    return [float(toks.count(w)) for w in _VOCAB]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def counter():
    return _Counter()


@pytest.fixture
def stateful_graph(client, monkeypatch, counter):
    graph = _stateful_graph(counter)
    monkeypatch.setattr(client.app.state, "agent_graph", graph)
    return graph


@pytest.fixture
def stateless_graph(client, monkeypatch, counter):
    monkeypatch.setattr(client.app.state, "agent_graph", _stateless_graph(counter))


@pytest.fixture
def no_cache(monkeypatch):
    from src.core import response_cache as rc

    monkeypatch.setattr(
        rc, "_cache_singleton", rc.ResponseCache(enabled=False, rest_url="", rest_token="")
    )


@pytest.fixture
def install_cache(monkeypatch):
    """Build the real cache singleton from (patched) settings with a fake transport."""
    from src.core import response_cache as rc
    from src.core import usage_tracker as ut
    from src.core.config import settings

    def _install(*, scope="thread", semantic=False):
        monkeypatch.setattr(settings, "response_cache_enabled", True)
        monkeypatch.setattr(settings, "upstash_redis_rest_url", "https://cache.invalid")
        monkeypatch.setattr(settings, "upstash_redis_rest_token", "dummy-token")
        monkeypatch.setattr(settings, "response_cache_scope", scope)
        monkeypatch.setattr(settings, "response_cache_semantic", semantic)
        monkeypatch.setattr(rc, "_cache_singleton", None)  # restored on teardown
        cache = rc.get_response_cache()
        transport = FakeRedisTransport()
        cache._redis_command = transport.command
        cache._embed_fn = _bow_embed
        ut.reset()
        assert cache.enabled
        return cache, transport, ut

    return _install


def _chat(client, user, question, thread_id=None):
    body = {"question": question}
    if thread_id is not None:
        body["thread_id"] = thread_id
    r = client.post(CHAT, json=body, headers=user.headers)
    assert r.status_code == 200, f"chat failed with status {r.status_code}"
    return r.text


def _marker(label):
    return f"SYNTH-{label}-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# Thread identity
# --------------------------------------------------------------------------- #
def test_scoped_thread_id_is_deterministic_and_user_bound():
    assert scoped_thread_id("u1", "t") == scoped_thread_id("u1", "t")
    assert scoped_thread_id("u1", "t") != scoped_thread_id("u2", "t")
    assert scoped_thread_id("u1", None) == scoped_thread_id("u1", "")
    assert scoped_thread_id("u1", None) != scoped_thread_id("u2", None)
    # Unambiguous encoding: shifting a separator between parts changes the id.
    assert scoped_thread_id("a:b", "c") != scoped_thread_id("a", "b:c")
    # Supplying another user's derived id maps into the caller's own namespace.
    victim = scoped_thread_id("u1", "t")
    assert scoped_thread_id("u2", victim) not in (victim, scoped_thread_id("u2", "t"))


# --------------------------------------------------------------------------- #
# Checkpoint / thread state
# --------------------------------------------------------------------------- #
def test_same_user_thread_continuity(client, make_user, stateful_graph, no_cache):
    a = make_user("a")
    marker, thread = _marker("A"), f"t-{uuid.uuid4().hex}"
    _chat(client, a, f"remember {marker}", thread)
    assert marker in _chat(client, a, "what did I say?", thread)


def test_two_users_same_client_thread_are_isolated(client, make_user, stateful_graph, no_cache):
    a, b = make_user("a"), make_user("b")
    marker, thread = _marker("A"), f"t-{uuid.uuid4().hex}"
    _chat(client, a, f"remember {marker}", thread)
    assert marker in _chat(client, a, "recall", thread)  # control

    b_first = _chat(client, b, "recall", thread)
    assert marker not in b_first
    assert "prior=[]" in b_first  # B starts an empty thread of its own
    assert "recall" in _chat(client, b, "again", thread)  # B keeps its own continuity

    a_after = _chat(client, a, "still there?", thread)
    assert marker in a_after
    a_state = stateful_graph.get_state({"configurable": {"thread_id": scoped_thread_id(a.id, thread)}})
    assert "again" not in a_state.values["history"]  # B never wrote into A's thread


def test_default_thread_is_per_user(client, make_user, stateful_graph, no_cache):
    a, b = make_user("a"), make_user("b")
    marker = _marker("A")
    _chat(client, a, f"remember {marker}")
    assert marker in _chat(client, a, "recall")  # control: default thread persists
    assert marker not in _chat(client, b, "recall")


@pytest.mark.parametrize(
    "spoof",
    ["derived", "legacy_default", "legacy_raw", "prefixed"],
)
def test_client_cannot_address_another_users_thread(
    client, make_user, stateful_graph, no_cache, spoof
):
    a, b = make_user("a"), make_user("b")
    marker, thread = _marker("A"), f"t-{uuid.uuid4().hex}"
    _chat(client, a, f"remember {marker}", thread)
    _chat(client, a, f"remember too {marker}")  # A's default thread
    assert marker in _chat(client, a, "recall", thread)  # control

    spoofed = {
        "derived": scoped_thread_id(a.id, thread),  # A's real internal id
        "legacy_default": f"{a.id}:default",  # pre-fix default format
        "legacy_raw": thread,  # pre-fix raw id
        "prefixed": f"{a.id}:{thread}",  # client-supplied owner prefix
    }[spoof]
    assert marker not in _chat(client, b, "recall", spoofed)


# --------------------------------------------------------------------------- #
# Response cache
# --------------------------------------------------------------------------- #
def test_exact_cache_isolated_across_users(
    client, make_user, stateless_graph, counter, install_cache
):
    _, _, ut = install_cache(scope="thread", semantic=False)
    a, b = make_user("a"), make_user("b")
    thread, q = f"t-{uuid.uuid4().hex}", "Synthetic question about Article 21"

    first = _chat(client, a, q, thread)
    assert _chat(client, a, q, thread) == first  # control: same-user exact hit
    assert ut.snapshot()["cache"]["hit_exact"] == 1 and counter.calls == 1

    b_answer = _chat(client, b, q, thread)
    assert b_answer != first
    assert counter.calls == 2  # B's request ran the graph (cache miss)
    assert ut.snapshot()["cache"]["hit_exact"] == 1


def test_semantic_cache_isolated_across_users(
    client, make_user, stateless_graph, counter, install_cache
):
    cache, _, ut = install_cache(scope="thread", semantic=True)
    a, b = make_user("a"), make_user("b")
    thread = f"t-{uuid.uuid4().hex}"

    first = _chat(client, a, "explain article 21 right to life", thread)
    assert _chat(client, a, "please explain article 21 right to life", thread) == first  # control
    snap = ut.snapshot()["cache"]
    assert snap["hit_semantic"] == 1 and snap["hit_exact"] == 0 and counter.calls == 1

    b_answer = _chat(client, b, "kindly explain article 21 right to life", thread)
    assert b_answer != first
    assert counter.calls == 2
    assert ut.snapshot()["cache"]["hit_semantic"] == 1
    assert cache.semantic_threshold > 0


def test_global_scope_is_never_shared_across_users(
    client, make_user, stateless_graph, counter, install_cache
):
    cache, _, ut = install_cache(scope="global", semantic=True)
    assert cache.scope == "user"  # unsafe config is downgraded, not honoured
    a, b = make_user("a"), make_user("b")
    q = "Synthetic global-scope question"

    first = _chat(client, a, q, f"t-{uuid.uuid4().hex}")
    # Control: per-user reuse still works across A's own threads.
    assert _chat(client, a, q, f"t-{uuid.uuid4().hex}") == first
    assert counter.calls == 1

    assert _chat(client, b, q, f"t-{uuid.uuid4().hex}") != first
    assert _chat(client, b, "synthetic global-scope question?", None) != first
    assert counter.calls == 2  # B ran the graph once, then hit only its own entry


def test_legacy_unscoped_cache_entries_are_not_reused(
    client, make_user, stateless_graph, counter, install_cache
):
    import hashlib
    import json

    from src.core.response_cache import _normalize

    _, transport, _ = install_cache(scope="thread", semantic=False)
    a = make_user("a")
    thread, q = f"t-{uuid.uuid4().hex}", "Synthetic legacy question"
    digest = hashlib.sha256(_normalize(q).encode("utf-8")).hexdigest()
    poisoned = json.dumps({"answer": "LEGACY-ENTRY", "route": "mentor"})
    for scope_part in (f"t:{thread}", f"t:{a.id}:default", "g", f"u:{a.id}"):
        transport.kv[f"respcache:v1:{scope_part}:{digest}"] = poisoned

    assert "LEGACY-ENTRY" not in _chat(client, a, q, thread)
    assert "LEGACY-ENTRY" not in _chat(client, a, q)
    assert counter.calls == 2


# --------------------------------------------------------------------------- #
# History ownership (conversation ids are server-generated, separate from threads)
# --------------------------------------------------------------------------- #
def test_history_is_owner_only(client, make_user):
    a, b = make_user("a"), make_user("b")
    marker = _marker("HIST")
    r = client.post(
        f"{HIST}/messages",
        json={"role": "user", "content": marker, "agent": "mentor"},
        headers=a.headers,
    )
    assert r.status_code == 201
    conv = r.json()["conversation_id"]
    own = client.get(f"{HIST}/conversations/{conv}/messages", headers=a.headers)
    assert own.status_code == 200 and marker in own.text  # control

    other = client.get(f"{HIST}/conversations/{conv}/messages", headers=b.headers)
    assert other.status_code == 404 and marker not in other.text
    assert conv not in client.get(f"{HIST}/conversations", headers=b.headers).text

    w = client.post(
        f"{HIST}/messages",
        json={"role": "user", "content": "SYNTH-B-WRITE", "agent": "mentor", "conversation_id": conv},
        headers=b.headers,
    )
    assert w.status_code == 201 and w.json()["conversation_id"] != conv
    after = client.get(f"{HIST}/conversations/{conv}/messages", headers=a.headers)
    assert "SYNTH-B-WRITE" not in after.text
