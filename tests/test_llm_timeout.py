"""Gemini request timeout: units, streaming coverage, no fallback after timeout.

The real ChatGoogleGenerativeAI / google-genai / httpx stack is pointed at a
local 127.0.0.1 server that stalls on purpose, so the SDK's actual timeout
handling is exercised without contacting Google. A dummy key is used.
"""

import socket
import threading
import time
import uuid

import pytest
from pydantic import ValidationError

from src.core.config import Settings, settings

AUTH = "/api/v1/auth"


# --------------------------------------------------------------------------- #
# Local stalling server
# --------------------------------------------------------------------------- #
class _StallServer:
    """Minimal HTTP server on 127.0.0.1.

    mode="before_headers": accept, read the request, never answer.
    mode="after_headers":  send 200 + one SSE chunk, then stall.
    mode="error_500":      answer 500 immediately (a non-timeout failure).
    """

    def __init__(self, mode: str, stall: float = 5.0):
        self.mode, self.stall, self.requests = mode, stall, 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            conn.settimeout(5)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
            except OSError:
                return
            self.requests += 1
            if self.mode == "error_500":
                body = b'{"error": {"code": 500, "message": "synthetic", "status": "INTERNAL"}}'
                conn.sendall(
                    b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                    b"Content-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                return
            if self.mode == "after_headers":
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                )
                event = b'data: {"candidates": [{"content": {"parts": [{"text": "Hel"}], "role": "model"}}]}\r\n\r\n'
                conn.sendall(hex(len(event))[2:].encode() + b"\r\n" + event + b"\r\n")
            time.sleep(self.stall)

    def close(self):
        self._stop = True
        self.sock.close()


@pytest.fixture
def stall_server():
    servers = []

    def _make(mode, stall=5.0):
        s = _StallServer(mode, stall)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


def _gemini(base_url: str, timeout: float, model: str = "synthetic-model"):
    from src.core.llm import _GuardedGemini

    return _GuardedGemini(
        model=model,
        google_api_key="dummy-not-a-real-key",
        base_url=base_url,
        timeout=timeout,
        max_retries=1,
    )


def _pair(primary_url, fallback_url, timeout, chain):
    from src.core.llm import _chain_with_fallbacks

    return _chain_with_fallbacks(
        [("primary", _gemini(primary_url, timeout)), ("fallback", _gemini(fallback_url, timeout))],
        chain=chain,
    )


# --------------------------------------------------------------------------- #
# Config + units
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, -1, 601, "abc", ""])
def test_invalid_timeout_is_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(llm_request_timeout_seconds=bad)


def test_default_timeout_is_45_seconds():
    assert Settings().llm_request_timeout_seconds == 45.0


def test_factory_passes_timeout_and_units_reach_httpx_in_seconds(monkeypatch):
    """Settings (s) -> model.timeout (s) -> HttpOptions.timeout (ms) -> httpx (s)."""
    from google.genai._api_client import get_timeout_in_seconds
    from langchain_core.messages import HumanMessage

    from src.core import llm

    monkeypatch.setattr(settings, "google_api_key", "dummy-not-a-real-key")
    monkeypatch.setattr(settings, "llm_request_timeout_seconds", 45.0)
    model = llm._make_gemini("synthetic-model", 0.3)
    inner = getattr(model, "bound", model)  # _with_tracing may wrap in a RunnableBinding
    assert isinstance(inner, llm._GuardedGemini)
    assert inner.timeout == 45.0
    req = inner._prepare_request([HumanMessage(content="x")])
    assert req["config"].http_options.timeout == 45000  # milliseconds in the SDK
    assert get_timeout_in_seconds(req["config"].http_options.timeout) == 45.0


# --------------------------------------------------------------------------- #
# Real SDK against a stalling local server
# --------------------------------------------------------------------------- #
def test_timeout_while_waiting_for_response_headers(stall_server):
    from src.core.llm import LLMTimeoutError

    srv = stall_server("before_headers")
    model = _gemini(srv.url, timeout=0.5)
    t0 = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        model.invoke("hi")
    assert time.monotonic() - t0 < 3.0
    assert srv.requests == 1  # no SDK retry


def test_timeout_covers_a_stalled_stream_after_first_chunk(stall_server):
    from src.core.llm import LLMTimeoutError

    srv = stall_server("after_headers")
    model = _gemini(srv.url, timeout=0.5)
    got = []
    t0 = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        for chunk in model.stream("hi"):
            got.append(chunk.content)
    assert time.monotonic() - t0 < 3.0
    assert "Hel" in "".join(str(c) for c in got)  # first chunk arrived, then the gap timed out
    assert srv.requests == 1


def test_timeout_does_not_trigger_fallback_model(stall_server):
    from src.core.llm import LLMTimeoutError

    primary_srv, fallback_srv = stall_server("before_headers"), stall_server("before_headers")
    chain = _pair(primary_srv.url, fallback_srv.url, 0.5, "t-basic")
    with pytest.raises(LLMTimeoutError):
        chain.invoke("hi")
    with pytest.raises(LLMTimeoutError):
        list(chain.stream("hi"))
    assert primary_srv.requests == 2
    assert fallback_srv.requests == 0  # never contacted after a timeout


def test_tool_bound_chain_also_skips_fallback_after_timeout(stall_server):
    from src.core.llm import LLMTimeoutError
    from src.graph.tools import bind_tools_with_fallback, get_structured_tools

    primary_srv, fallback_srv = stall_server("before_headers"), stall_server("before_headers")
    base = _pair(primary_srv.url, fallback_srv.url, 0.5, "t-tools")
    bound = bind_tools_with_fallback(llm=base, tools=get_structured_tools())
    with pytest.raises(LLMTimeoutError):
        bound.invoke("hi")
    assert primary_srv.requests == 1 and fallback_srv.requests == 0


def test_non_timeout_error_still_uses_fallback(stall_server):
    primary_srv, fallback_srv = stall_server("error_500"), stall_server("error_500")
    chain = _pair(primary_srv.url, fallback_srv.url, 2.0, "t-500")
    with pytest.raises(Exception) as info:
        chain.invoke("hi")
    from src.core.llm import is_llm_timeout

    assert not is_llm_timeout(info.value)
    assert primary_srv.requests == 1 and fallback_srv.requests == 1  # fallback behaviour kept


def test_timeout_does_not_poison_the_next_invocation(stall_server):
    """After a timed-out invocation, the next one tries the primary AND (on a
    non-timeout error) the fallback again."""
    from src.core.llm import LLMTimeoutError

    primary_srv, fallback_srv = stall_server("before_headers"), stall_server("error_500")
    chain = _pair(primary_srv.url, fallback_srv.url, 0.4, "t-reset")
    with pytest.raises(LLMTimeoutError):
        chain.invoke("first")
    assert (primary_srv.requests, fallback_srv.requests) == (1, 0)
    primary_srv.mode = "error_500"  # second invocation: primary fails without a timeout
    with pytest.raises(Exception):
        chain.invoke("second")
    assert (primary_srv.requests, fallback_srv.requests) == (2, 1)


def test_first_available_model_is_primary_even_if_listed_second(stall_server):
    """If the listed primary is unavailable (None), the next model becomes the
    primary and resets the flag - no stale 'fallback skipped' state."""
    from src.core.llm import LLMTimeoutError, _chain_with_fallbacks

    srv = stall_server("before_headers")
    only = _chain_with_fallbacks(
        [("missing", None), ("model", _gemini(srv.url, 0.3))], chain="t-solo"
    )
    for _ in range(2):
        with pytest.raises(LLMTimeoutError):
            only.invoke("hi")
    assert srv.requests == 2  # both invocations really called the model


def test_chains_do_not_share_timeout_state(stall_server):
    from src.core.llm import LLMTimeoutError

    a_p, a_f = stall_server("before_headers"), stall_server("before_headers")
    b_p, b_f = stall_server("error_500"), stall_server("error_500")
    chain_a = _pair(a_p.url, a_f.url, 0.3, "t-a")
    chain_b = _pair(b_p.url, b_f.url, 2.0, "t-b")
    with pytest.raises(LLMTimeoutError):
        chain_a.invoke("a")
    with pytest.raises(Exception):
        chain_b.invoke("b")
    assert b_f.requests == 1  # chain B's fallback unaffected by chain A's timeout


def test_server_side_deadline_counts_as_timeout():
    from google.genai.errors import ServerError

    from src.core.llm import is_llm_timeout

    err = ServerError(504, {"error": {"code": 504, "message": "x", "status": "DEADLINE_EXCEEDED"}})
    assert is_llm_timeout(err)
    assert not is_llm_timeout(
        ServerError(500, {"error": {"code": 500, "message": "x", "status": "INTERNAL"}})
    )


# --------------------------------------------------------------------------- #
# Mentor endpoint behaviour
# --------------------------------------------------------------------------- #
class _RaisingGraph:
    def __init__(self, exc):
        self.exc = exc

    def stream(self, *a, **k):
        raise self.exc
        yield  # pragma: no cover


def test_mentor_stream_ends_with_timeout_message(client, make_user, monkeypatch):
    from src.core.llm import LLMTimeoutError

    u = make_user("timeout")
    monkeypatch.setattr(
        client.app.state, "agent_graph", _RaisingGraph(LLMTimeoutError("synthetic"))
    )
    r = client.post("/api/v1/agent/chat/stream", json={"question": "q"}, headers=u.headers)
    assert r.status_code == 200
    assert "did not respond in time" in r.text and "45 s" in r.text
    assert "Sorry, the agent hit an error" not in r.text


def test_mentor_stream_other_errors_keep_generic_message(client, make_user, monkeypatch):
    u = make_user("generic")
    monkeypatch.setattr(client.app.state, "agent_graph", _RaisingGraph(RuntimeError("synthetic")))
    r = client.post("/api/v1/agent/chat/stream", json={"question": "q"}, headers=u.headers)
    assert r.status_code == 200 and "Sorry, the agent hit an error" in r.text


def test_router_timeout_ends_request_without_agent_call(client, make_user, monkeypatch):
    """Real supervisor graph: a routing timeout must not start the agent chain."""
    from src.core import llm as llm_mod
    from src.core.llm import LLMTimeoutError
    from src.graph import supervisor

    class _TimeoutRouter:
        def invoke(self, *a, **k):
            raise LLMTimeoutError("synthetic router timeout")

    agent_calls = []

    def _no_agent(*a, **k):
        agent_calls.append(1)
        raise AssertionError("agent model must not be resolved after a routing timeout")

    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: _TimeoutRouter())
    monkeypatch.setattr(llm_mod, "get_llm_for_tier", _no_agent)
    monkeypatch.setattr(llm_mod, "get_llm", _no_agent)
    u = make_user("router")
    r = client.post(
        "/api/v1/agent/chat/stream",
        json={"question": f"explain article 21 {uuid.uuid4().hex[:6]}"},
        headers=u.headers,
    )
    assert r.status_code == 200 and "did not respond in time" in r.text
    assert agent_calls == []


# --------------------------------------------------------------------------- #
# Gemini EMBEDDING request timeout (real SDK vs local stalling server)
# --------------------------------------------------------------------------- #
def _embeddings_against(monkeypatch, base_url: str, timeout_s: float):
    import functools

    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    import src.core.vector_store as vs

    monkeypatch.setattr(settings, "google_api_key", "dummy-not-a-real-key")
    monkeypatch.setattr(settings, "embedding_request_timeout_seconds", timeout_s)
    monkeypatch.setattr(
        vs,
        "GoogleGenerativeAIEmbeddings",
        functools.partial(GoogleGenerativeAIEmbeddings, base_url=base_url),
    )
    return vs._make_gemini_embeddings()


@pytest.mark.parametrize("bad", [0, -1, 601, "abc"])
def test_invalid_embedding_timeout_is_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(embedding_request_timeout_seconds=bad)


def test_embedding_client_timeout_units(monkeypatch):
    """Settings seconds -> HttpOptions.timeout milliseconds -> httpx seconds."""
    from google.genai._api_client import get_timeout_in_seconds

    emb = _embeddings_against(monkeypatch, "http://127.0.0.1:9", 45.0)
    opts = emb._inner.client._api_client._http_options
    assert opts.timeout == 45000
    assert get_timeout_in_seconds(opts.timeout) == 45.0
    assert opts.base_url.startswith("http://127.0.0.1:9")  # original options kept
    assert emb._dim == 768  # pinning unchanged


def test_embedding_timeout_on_stalled_server(monkeypatch, stall_server):
    from src.core.llm import is_llm_timeout

    srv = stall_server("before_headers")
    emb = _embeddings_against(monkeypatch, srv.url, 0.5)
    t0 = time.monotonic()
    with pytest.raises(Exception) as info:
        emb.embed_documents(["chunk one", "chunk two", "chunk three"])
    assert time.monotonic() - t0 < 3.0
    assert is_llm_timeout(info.value)
    assert srv.requests == 1  # one batch request, no SDK retry


def test_query_embedding_timeout_on_stalled_server(monkeypatch, stall_server):
    from src.core.llm import is_llm_timeout

    srv = stall_server("before_headers")
    emb = _embeddings_against(monkeypatch, srv.url, 0.5)
    t0 = time.monotonic()
    with pytest.raises(Exception) as info:
        emb.embed_query("what is the gram sabha")
    assert time.monotonic() - t0 < 3.0
    assert is_llm_timeout(info.value)
    assert srv.requests == 1
