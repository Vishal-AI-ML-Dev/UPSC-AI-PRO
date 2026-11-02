"""Regression tests: running streams stop when their session is revoked.

Deterministic fake upstream generators replace the agents/LLMs; the session
check runs against the real (throwaway SQLite) database. Revocation is done with
the same core helpers the auth routes use (start_session = a login elsewhere,
end_session = logout, end_all_sessions = password reset).
"""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from contextlib import suppress
from types import SimpleNamespace

import pytest
from jose import jwt
from sqlalchemy.exc import OperationalError

from src.api import stream_guard as sg
from src.core import refresh_tokens as rt
from src.core.db import SessionLocal

INTERVAL = 0.2
SLACK = 0.6  # scheduling/DB tolerance on top of the interval (SQLite, threadpool)


def _session(user_id):
    """Start a fresh session (as a login would); return (user dict, raw refresh)."""
    with SessionLocal() as db:
        sid, raw = rt.start_session(db, user_id)
        db.commit()
    return {"id": user_id, "sid": sid}, raw


def _new_login(user_id):
    with SessionLocal() as db:
        rt.start_session(db, user_id)
        db.commit()


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append((time.monotonic(), chunk))
    return out


def _run(source, user, interval=INTERVAL):
    return asyncio.run(_collect(sg.guard_stream(source, user, interval=interval)))


def _texts(items):
    return [c for _, c in items]


SUPERSEDED = sg.STOP_NOTICES["session_superseded"]
ENDED = sg.STOP_NOTICES["session_ended"]
UNAVAILABLE = sg.STOP_NOTICES["unavailable"]


# --------------------------------------------------------------------------- #
# Direct guard behaviour
# --------------------------------------------------------------------------- #
def test_same_session_stream_continues(make_user, monkeypatch):
    user, _ = _session(make_user("a").id)
    calls = {"n": 0}
    real = sg.check_session

    def counting(uid, sid):
        calls["n"] += 1
        return real(uid, sid)

    monkeypatch.setattr(sg, "check_session", counting)

    def src():
        for i in range(5):
            time.sleep(0.15)  # slower than the interval: forces re-checks
            yield f"S{i}"

    out = _texts(_run(src(), user))
    assert out == [f"S{i}" for i in range(5)]
    assert calls["n"] >= 4  # initial + periodic re-checks happened


def test_new_login_stops_running_stream_and_nothing_after_detection(make_user):
    uid = make_user("a").id
    user, _ = _session(uid)
    marks = {}

    def src():
        yield "C1"
        _new_login(uid)
        marks["revoked"] = time.monotonic()
        for i in range(40):  # keeps producing quickly after revocation
            time.sleep(0.05)
            yield f"LEAK{i}"

    items = _run(src(), user)
    texts = _texts(items)
    assert texts[0] == "C1"
    assert texts[-1] == SUPERSEDED  # the notice is the last thing delivered
    leaked = [t for t in texts if t.startswith("LEAK")]
    delay = items[-1][0] - marks["revoked"]
    print(f"[measure] flowing upstream: detection delay {delay:.3f}s, chunks before detection {len(leaked)}")
    assert delay <= INTERVAL + SLACK
    assert len(leaked) < 40


def test_revocation_detected_while_upstream_is_silent(make_user):
    uid = make_user("a").id
    user, _ = _session(uid)
    marks, release, closed = {}, threading.Event(), threading.Event()

    def src():
        try:
            yield "C1"
            _new_login(uid)
            marks["revoked"] = time.monotonic()
            release.wait(10)  # blocked upstream (e.g. a slow provider call)
            yield "LATE"
        finally:
            closed.set()

    items = _run(src(), user)
    delay = items[-1][0] - marks["revoked"]
    print(f"[measure] silent upstream: detection delay {delay:.3f}s (interval {INTERVAL}s)")
    assert _texts(items) == ["C1", SUPERSEDED]
    assert delay <= INTERVAL + SLACK
    assert not closed.is_set()  # still blocked inside the provider step
    release.set()
    assert closed.wait(5), "upstream generator was not closed after its step returned"


@pytest.mark.parametrize("how", ["logout", "reset"])
def test_logout_and_reset_stop_stream(make_user, how):
    uid = make_user("a").id
    user, raw = _session(uid)

    def src():
        yield "C1"
        with SessionLocal() as db:
            if how == "logout":
                rt.end_session(db, raw)
            else:
                rt.end_all_sessions(db, uid)
            db.commit()
        time.sleep(0.6)
        yield "LEAK"

    assert _texts(_run(src(), user)) == ["C1", ENDED]


def test_db_check_failure_stops_output(make_user, monkeypatch):
    user, _ = _session(make_user("a").id)
    real = sg.SessionLocal
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] > 1:
            raise OperationalError("SELECT ...", {}, Exception("disk I/O error"))
        return real()

    monkeypatch.setattr(sg, "SessionLocal", flaky)

    def src():
        yield "C1"
        time.sleep(0.6)
        yield "LEAK"

    out = _texts(_run(src(), user))
    assert out == ["C1", UNAVAILABLE]
    assert "disk" not in out[-1]


def test_stale_session_rejected_before_first_chunk(make_user):
    uid = make_user("a").id
    user, _ = _session(uid)
    _new_login(uid)
    started = threading.Event()

    def src():
        started.set()
        yield "SHOULD-NOT-APPEAR"

    assert _texts(_run(src(), user)) == [SUPERSEDED]


def test_client_disconnect_cleans_up(make_user):
    user, _ = _session(make_user("a").id)
    release, closed, got_first = threading.Event(), threading.Event(), asyncio.Event

    def src():
        try:
            yield "D1"
            release.wait(10)
            yield "D2"
        finally:
            closed.set()

    async def main():
        first = got_first()
        received = []

        async def consume():
            async for c in sg.guard_stream(src(), user, interval=INTERVAL):
                received.append(c)
                first.set()

        task = asyncio.ensure_future(consume())
        await asyncio.wait_for(first.wait(), 5)
        task.cancel()  # what the server does when the client disconnects
        with suppress(asyncio.CancelledError):
            await task
        blocked_still_open = not closed.is_set()
        release.set()
        for _ in range(100):
            if closed.is_set() and not sg._cleanups:
                break
            await asyncio.sleep(0.05)
        return received, blocked_still_open

    received, blocked_still_open = asyncio.run(main())
    assert received == ["D1"]
    assert blocked_still_open  # cannot close a generator mid-step...
    assert closed.is_set()  # ...but it is closed once the step returns
    assert not sg._cleanups  # no lingering background work


def test_aclose_closes_idle_upstream(make_user):
    user, _ = _session(make_user("a").id)
    closed = threading.Event()

    def src():
        try:
            yield "A1"
            yield "A2"
        finally:
            closed.set()

    async def main():
        agen = sg.guard_stream(src(), user, interval=INTERVAL)
        first = await agen.__anext__()
        await agen.aclose()
        for _ in range(100):
            if closed.is_set():
                break
            await asyncio.sleep(0.02)
        return first

    assert asyncio.run(main()) == "A1"
    assert closed.is_set()


def test_other_user_stream_unaffected(make_user):
    a_id, b_id = make_user("a").id, make_user("b").id
    a_user, _ = _session(a_id)
    b_user, _ = _session(b_id)

    def a_src():
        yield "A1"
        _new_login(a_id)
        time.sleep(0.6)
        yield "A-LEAK"

    def b_src():
        for i in range(6):
            time.sleep(0.1)
            yield f"B{i}"

    async def main():
        return await asyncio.gather(
            _collect(sg.guard_stream(a_src(), a_user, interval=INTERVAL)),
            _collect(sg.guard_stream(b_src(), b_user, interval=INTERVAL)),
        )

    a_items, b_items = asyncio.run(main())
    assert _texts(a_items) == ["A1", SUPERSEDED]
    assert _texts(b_items) == [f"B{i}" for i in range(6)]


# --------------------------------------------------------------------------- #
# Every registered streaming endpoint is guarded (end-to-end through the API)
# --------------------------------------------------------------------------- #
ENDPOINTS = [
    ("current_affairs", "get_daily_ca", "/api/v1/current-affairs/daily", {"date": "1 January 2026"}),
    ("current_affairs", "get_editorial", "/api/v1/current-affairs/editorial", {"topic": "t"}),
    ("current_affairs", "get_monthly_summary", "/api/v1/current-affairs/monthly", {"month": "January", "year": "2026"}),
    ("lecture", "ask_lecture", "/api/v1/lecture/chat", {"question": "q", "video_id": "vid12345678"}),
    ("ncert", "ask_ncert", "/api/v1/ncert/chat", {"question": "q", "class_name": "6", "subject": "s", "chapter": "c"}),
    ("planner", "generate_plan", "/api/v1/planner/generate", {"goal": "UPSC 2026"}),
    ("pyq", "generate_questions", "/api/v1/pyq/generate", {"topic": "polity"}),
    ("pyq", "generate_from_bank", "/api/v1/pyq/bank/generate", {}),
    ("upload", "ask_upload", "/api/v1/upload/chat?pdf_hash=0123456789abcdef0123456789abcdef", {"request": {"question": "q"}}),
]


def _api_login(client, email):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": "secret123"})
    assert r.status_code == 200, f"login failed with status {r.status_code}"
    return r.json()["access_token"]


@pytest.mark.parametrize("module, attr, url, body", ENDPOINTS, ids=[e[2] for e in ENDPOINTS])
def test_endpoint_stream_stops_on_new_login(client, make_user, monkeypatch, module, attr, url, body):
    from src.core.config import settings

    monkeypatch.setattr(settings, "stream_session_check_seconds", INTERVAL)
    a = make_user("a")
    access = _api_login(client, a.email)

    def fake(*args, **kwargs):
        yield "EP-C1"
        _new_login(a.id)
        time.sleep(0.6)
        yield "EP-LEAK"

    monkeypatch.setattr(importlib.import_module(f"src.api.routes.{module}"), attr, fake)
    r = client.post(url, json=body, headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.text.startswith("EP-C1")
    assert "EP-LEAK" not in r.text
    assert r.text.endswith(SUPERSEDED)


def test_agent_chat_stream_stops_on_new_login(client, make_user, monkeypatch):
    from src.core import response_cache as rc
    from src.core.config import settings

    monkeypatch.setattr(settings, "stream_session_check_seconds", INTERVAL)
    monkeypatch.setattr(rc, "_cache_singleton", rc.ResponseCache(enabled=False, rest_url="", rest_token=""))
    a = make_user("a")
    access = _api_login(client, a.email)
    assert jwt.get_unverified_claims(access)["sid"]

    class FakeGraph:
        def stream(self, inp, config, stream_mode=None):
            yield "messages", (SimpleNamespace(content="CHAT-C1"), {"langgraph_node": "generate"})
            _new_login(a.id)
            time.sleep(0.6)
            yield "messages", (SimpleNamespace(content="CHAT-LEAK"), {"langgraph_node": "generate"})

    monkeypatch.setattr(client.app.state, "agent_graph", FakeGraph())
    r = client.post(
        "/api/v1/agent/chat/stream",
        json={"question": "q"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200
    assert r.text.startswith("CHAT-C1")
    assert "CHAT-LEAK" not in r.text
    assert r.text.endswith(SUPERSEDED)


def test_agent_chat_stream_same_session_unchanged(client, make_user, monkeypatch):
    from src.core import response_cache as rc

    monkeypatch.setattr(rc, "_cache_singleton", rc.ResponseCache(enabled=False, rest_url="", rest_token=""))
    a = make_user("a")
    access = _api_login(client, a.email)

    class FakeGraph:
        def stream(self, inp, config, stream_mode=None):
            for i in range(3):
                yield "messages", (SimpleNamespace(content=f"OK{i} "), {"langgraph_node": "generate"})

    monkeypatch.setattr(client.app.state, "agent_graph", FakeGraph())
    r = client.post(
        "/api/v1/agent/chat/stream",
        json={"question": "q"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200 and r.text == "OK0 OK1 OK2 "
