"""Regression tests: server-enforced single active session.

A new successful login wins: the previous session's access AND refresh tokens are
rejected on the server. Covers stale logout, refresh replay, password reset,
verification auto-login, legacy tokens, fail-closed DB errors, separate users and
concurrency (deterministic interleavings + real threads).

Runs on the throwaway SQLite database booted by the ``client`` fixture. Postgres
locking is NOT exercised here. Tokens are never included in assertion messages.
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from src.core import refresh_tokens as rt
from src.core.db import SessionLocal
from src.core.models import RefreshToken, User

AUTH = "/api/v1/auth"
HIST = "/api/v1/history"


@dataclass
class Pair:
    access: str = field(repr=False)
    refresh: str = field(repr=False)

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access}"}


def _pair(resp) -> Pair:
    j = resp.json()
    return Pair(j["access_token"], j["refresh_token"])


def _login(client, email, password="secret123") -> Pair:
    r = client.post(f"{AUTH}/login", data={"username": email, "password": password})
    assert r.status_code == 200, f"login failed with status {r.status_code}"
    return _pair(r)


def _reason(resp) -> str:
    h = resp.headers.get("www-authenticate", "")
    return h.split('error_description="')[1].split('"')[0] if "error_description" in h else ""


def _me(client, pair):
    return client.get(f"{AUTH}/me", headers=pair.headers)


def _refresh(client, raw):
    return client.post(f"{AUTH}/refresh", json={"refresh_token": raw})


def _active_sid(user_id):
    with SessionLocal() as db:
        return db.scalar(select(User.active_session_id).where(User.id == user_id))


def _unrevoked_sessions(user_id):
    with SessionLocal() as db:
        return set(
            db.scalars(
                select(RefreshToken.session_id).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #
def test_new_login_supersedes_previous_session(client, make_user):
    a = make_user("a")
    s1 = _login(client, a.email)
    s2 = _login(client, a.email)

    old = _me(client, s1)
    assert old.status_code == 401 and _reason(old) == "session_superseded"
    assert client.get(f"{HIST}/conversations", headers=s1.headers).status_code == 401
    old_refresh = _refresh(client, s1.refresh)
    assert old_refresh.status_code == 401 and _reason(old_refresh) == "session_superseded"

    assert _me(client, s2).status_code == 200
    assert client.get(f"{HIST}/conversations", headers=s2.headers).status_code == 200
    assert _refresh(client, s2.refresh).status_code == 200


def test_rotation_within_session_and_replay_rejected(client, make_user):
    a = make_user("a")
    s = _login(client, a.email)
    r1 = _refresh(client, s.refresh)
    assert r1.status_code == 200
    s_b = _pair(r1)
    replay = _refresh(client, s.refresh)
    assert replay.status_code == 401 and _reason(replay) == "invalid"
    assert _me(client, s).status_code == 200  # same session: earlier access token still valid
    assert _me(client, s_b).status_code == 200
    assert _refresh(client, s_b.refresh).status_code == 200


def test_stale_logout_keeps_newer_session(client, make_user):
    a = make_user("a")
    s1 = _login(client, a.email)
    s2 = _login(client, a.email)
    assert client.post(f"{AUTH}/logout", json={"refresh_token": s1.refresh}).status_code == 200
    assert _me(client, s2).status_code == 200
    assert _refresh(client, s2.refresh).status_code == 200


def test_logout_with_rotated_token_keeps_session(client, make_user):
    a = make_user("a")
    s = _login(client, a.email)
    s_b = _pair(_refresh(client, s.refresh))
    assert client.post(f"{AUTH}/logout", json={"refresh_token": s.refresh}).status_code == 200
    assert _me(client, s_b).status_code == 200
    assert _refresh(client, s_b.refresh).status_code == 200


def test_current_logout_ends_session_immediately(client, make_user):
    a = make_user("a")
    s = _login(client, a.email)
    assert client.post(f"{AUTH}/logout", json={"refresh_token": s.refresh}).status_code == 200
    me = _me(client, s)
    assert me.status_code == 401 and _reason(me) == "session_ended"
    assert _refresh(client, s.refresh).status_code == 401


def test_separate_users_unaffected(client, make_user):
    a, c = make_user("a"), make_user("c")
    sc = _login(client, c.email)
    _login(client, a.email)
    _login(client, a.email)
    assert _me(client, sc).status_code == 200
    assert _refresh(client, sc.refresh).status_code == 200


# --------------------------------------------------------------------------- #
# Flows that issue or invalidate tokens
# --------------------------------------------------------------------------- #
def _reset_link_token(client, monkeypatch, email):
    import src.api.routes.auth as auth_mod

    captured = {}
    monkeypatch.setattr(auth_mod, "send_reset_email", lambda to, link: captured.update(link=link))
    assert client.post(f"{AUTH}/forgot-password", json={"email": email}).status_code == 200
    return parse_qs(urlparse(captured["link"]).query)["reset_token"][0]


def test_password_reset_ends_all_sessions(client, make_user, monkeypatch):
    a = make_user("a")
    s = _login(client, a.email)
    token = _reset_link_token(client, monkeypatch, a.email)
    r = client.post(f"{AUTH}/reset-password", json={"token": token, "new_password": "newpass123"})
    assert r.status_code == 200
    me = _me(client, s)
    assert me.status_code == 401 and _reason(me) == "session_ended"
    assert _refresh(client, s.refresh).status_code == 401
    assert client.post(f"{AUTH}/login", data={"username": a.email, "password": "secret123"}).status_code == 401
    assert _me(client, _login(client, a.email, "newpass123")).status_code == 200
    # The link is single-use.
    again = client.post(f"{AUTH}/reset-password", json={"token": token, "new_password": "other123"})
    assert again.status_code == 400


def test_password_reset_is_atomic_when_token_claim_fails(client, make_user, monkeypatch):
    import src.api.routes.auth as auth_mod

    a = make_user("a")
    s = _login(client, a.email)
    token = _reset_link_token(client, monkeypatch, a.email)
    monkeypatch.setattr(auth_mod, "claim_reset_token", lambda db, token_id: False)  # lost a race
    r = client.post(f"{AUTH}/reset-password", json={"token": token, "new_password": "newpass123"})
    assert r.status_code == 400
    # Nothing was applied: old password works, session still valid.
    assert _me(client, s).status_code == 200
    assert client.post(f"{AUTH}/login", data={"username": a.email, "password": "secret123"}).status_code == 200


def test_invalid_new_password_does_not_burn_reset_link(client, make_user, monkeypatch):
    a = make_user("a")
    token = _reset_link_token(client, monkeypatch, a.email)
    bad = client.post(f"{AUTH}/reset-password", json={"token": token, "new_password": "x"})
    assert bad.status_code == 400
    ok = client.post(f"{AUTH}/reset-password", json={"token": token, "new_password": "newpass123"})
    assert ok.status_code == 200


def test_verify_email_starts_session_and_supersedes(client, make_user):
    from src.core.verification_tokens import create_verification_token

    a = make_user("a")
    s1 = _login(client, a.email)
    with SessionLocal() as db:
        raw = create_verification_token(db, a.id)
    v = client.post(f"{AUTH}/verify-email", json={"token": raw})
    assert v.status_code == 200
    s2 = _pair(v)
    assert _me(client, s1).status_code == 401
    assert _me(client, s2).status_code == 200


def test_verify_email_is_atomic_when_token_claim_fails(client, monkeypatch):
    import src.api.routes.auth as auth_mod

    captured = {}
    monkeypatch.setattr(auth_mod, "send_verification_email", lambda to, link: captured.update(link=link))
    email = "atomic_verify_{}@example.com".format(threading.get_ident())
    assert client.post(f"{AUTH}/register", json={"email": email, "password": "secret123"}).status_code == 201
    raw = parse_qs(urlparse(captured["link"]).query)["verify_token"][0]
    monkeypatch.setattr(auth_mod, "claim_verification_token", lambda db, token_id: False)
    assert client.post(f"{AUTH}/verify-email", json={"token": raw}).status_code == 400
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user.email_verified is False and user.active_session_id is None


def test_register_auto_login_starts_session_when_verification_off(client, monkeypatch):
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "verification_enforced", lambda: False)
    email = "autologin_{}@example.com".format(threading.get_ident())
    r = client.post(f"{AUTH}/register", json={"email": email, "password": "secret123"})
    assert r.status_code == 201
    s = _pair(r)
    assert _me(client, s).status_code == 200
    assert _refresh(client, s.refresh).status_code == 200


# --------------------------------------------------------------------------- #
# CORS: allowed frontends can read the 401 reason; origin policy unchanged
# --------------------------------------------------------------------------- #
def test_allowed_origin_can_read_session_reason(client, make_user):
    from src.core.config import settings

    origin = settings.cors_origins[0]
    a = make_user("a")
    s1 = _login(client, a.email)
    _login(client, a.email)
    r = client.get(f"{AUTH}/me", headers={**s1.headers, "Origin": origin})
    assert r.status_code == 401 and _reason(r) == "session_superseded"
    assert r.headers.get("access-control-allow-origin") == origin
    exposed = [h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")]
    assert "www-authenticate" in exposed


def test_disallowed_origin_gets_no_cors_grant(client):
    # The grant is Access-Control-Allow-Origin: without it the browser exposes
    # nothing (Starlette adds its fixed simple headers, e.g. the expose list,
    # whenever an Origin is present; they are inert without an allow-origin).
    r = client.get(f"{AUTH}/me", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    pre = client.options(
        f"{AUTH}/me",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert pre.status_code == 400
    assert "access-control-allow-origin" not in pre.headers


# --------------------------------------------------------------------------- #
# Legacy tokens (no compatibility bypass)
# --------------------------------------------------------------------------- #
def test_legacy_access_token_without_sid_is_rejected(client, make_user):
    from src.core.security import create_access_token

    a = make_user("a")
    legacy = create_access_token({"sub": a.id, "email": a.email, "name": "a"})
    r = client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 401 and _reason(r) == "session_outdated"


def test_legacy_refresh_token_without_session_is_rejected(client, make_user):
    a = make_user("a")
    current = _login(client, a.email)
    raw = "legacy-refresh-token-for-tests"
    with SessionLocal() as db:
        from datetime import datetime, timedelta, timezone

        db.add(
            RefreshToken(
                user_id=a.id,
                token_hash=rt._hash_token(raw),
                session_id=None,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.commit()
    r = _refresh(client, raw)
    assert r.status_code == 401 and _reason(r) == "session_outdated"
    # Logging out with the legacy token must not end the current session.
    assert client.post(f"{AUTH}/logout", json={"refresh_token": raw}).status_code == 200
    assert _me(client, current).status_code == 200


# --------------------------------------------------------------------------- #
# Fail closed on database errors
# --------------------------------------------------------------------------- #
class _FailingSession:
    def __init__(self, exc):
        self.exc = exc

    def scalar(self, *a, **k):
        raise self.exc

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize(
    "message, retryable",
    [("database is locked", True), ("disk I/O error", False)],
)
def test_session_check_fails_closed_on_db_error(client, make_user, message, retryable):
    from src.core.db import get_db

    a = make_user("a")
    s = _login(client, a.email)
    exc = OperationalError("SELECT ...", {}, sqlite3.OperationalError(message))
    client.app.dependency_overrides[get_db] = lambda: _FailingSession(exc)
    try:
        r = _me(client, s)
    finally:
        client.app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 503
    assert ("retry-after" in r.headers) is retryable
    assert message not in r.text and "sqlite" not in r.text.lower()


def test_retryable_classification_is_narrow():
    from src.api.deps import is_retryable_db_error

    class _Pg(Exception):
        def __init__(self, code):
            self.sqlstate = code

    def wrap(orig):
        return OperationalError("stmt", {}, orig)

    assert is_retryable_db_error(wrap(sqlite3.OperationalError("database is locked")))
    assert not is_retryable_db_error(wrap(sqlite3.OperationalError("no such table: users")))
    assert is_retryable_db_error(wrap(_Pg("40P01")))
    assert is_retryable_db_error(wrap(_Pg("40001")))
    assert not is_retryable_db_error(wrap(_Pg("42P01")))
    assert not is_retryable_db_error(RuntimeError("boom"))


# --------------------------------------------------------------------------- #
# Concurrency: deterministic interleavings (competitor commits mid-operation)
# --------------------------------------------------------------------------- #
def _inject_before_lock(monkeypatch, action):
    """Run ``action`` (in its own session) just before the first lock_user call."""
    real = rt.lock_user
    state = {"done": False}

    def wrapped(db, user_id):
        if not state["done"]:
            state["done"] = True
            action()
        return real(db, user_id)

    monkeypatch.setattr(rt, "lock_user", wrapped)


def test_refresh_loses_to_login_committed_mid_refresh(client, make_user, monkeypatch):
    a = make_user("a")
    old = _login(client, a.email)

    def competing_login():
        with SessionLocal() as db:
            rt.start_session(db, a.id)
            db.commit()

    _inject_before_lock(monkeypatch, competing_login)
    r = _refresh(client, old.refresh)
    assert r.status_code == 401 and _reason(r) == "session_superseded"
    assert len(_unrevoked_sessions(a.id)) == 1


def test_refresh_token_cannot_rotate_twice(client, make_user, monkeypatch):
    a = make_user("a")
    s = _login(client, a.email)

    def competing_rotation():
        with SessionLocal() as db:
            rt.rotate_session_token(db, s.refresh)
            db.commit()

    _inject_before_lock(monkeypatch, competing_rotation)
    r = _refresh(client, s.refresh)
    assert r.status_code == 401 and _reason(r) == "invalid"
    assert _unrevoked_sessions(a.id) == {_active_sid(a.id)}


def test_stale_logout_racing_new_login(client, make_user, monkeypatch):
    a = make_user("a")
    old = _login(client, a.email)
    newer = {}

    def competing_login():
        with SessionLocal() as db:
            newer["sid"], _ = rt.start_session(db, a.id)
            db.commit()

    _inject_before_lock(monkeypatch, competing_login)
    assert client.post(f"{AUTH}/logout", json={"refresh_token": old.refresh}).status_code == 200
    assert _active_sid(a.id) == newer["sid"]  # the newer session survived the old logout


# --------------------------------------------------------------------------- #
# Concurrency: real threads (SQLite serialises writers; busy -> 503 retry)
# --------------------------------------------------------------------------- #
def _parallel(n, fn):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


def test_concurrent_core_logins_leave_exactly_one_session(make_user):
    a = make_user("a")

    def one_login(_):
        with SessionLocal() as db:
            try:
                sid, _raw = rt.start_session(db, a.id)
                db.commit()
                return sid
            except OperationalError as exc:
                db.rollback()
                from src.api.deps import is_retryable_db_error

                assert is_retryable_db_error(exc), "unexpected non-busy DB error"
                return None

    sids = [s for s in _parallel(8, one_login) if s]
    assert sids, "no login committed"
    active = _active_sid(a.id)
    assert active in sids
    assert _unrevoked_sessions(a.id) == {active}  # exactly one live refresh token's session


def test_concurrent_api_logins_exactly_one_valid(client, make_user):
    a = make_user("a")

    def one_login(_):
        r = client.post(f"{AUTH}/login", data={"username": a.email, "password": "secret123"})
        return r.status_code, (_pair(r) if r.status_code == 200 else None)

    results = _parallel(6, one_login)
    assert all(code in (200, 503) for code, _ in results)
    pairs = [p for code, p in results if code == 200]
    assert pairs
    valid = [p for p in pairs if _me(client, p).status_code == 200]
    assert len(valid) == 1
    live_refresh = [p for p in pairs if _refresh(client, p.refresh).status_code == 200]
    assert len(live_refresh) == 1


def test_concurrent_refresh_same_token_single_winner(client, make_user):
    a = make_user("a")
    s = _login(client, a.email)

    results = _parallel(2, lambda _: _refresh(client, s.refresh))
    codes = sorted(r.status_code for r in results)
    assert codes[0] == 200 and codes[1] in (401, 503)  # exactly one rotation succeeded
    winner = _pair(next(r for r in results if r.status_code == 200))
    assert _me(client, winner).status_code == 200
    assert _unrevoked_sessions(a.id) == {_active_sid(a.id)}


@pytest.mark.parametrize("round_", range(4))
def test_concurrent_login_vs_old_refresh(client, make_user, round_):
    a = make_user("a")
    old = _login(client, a.email)

    def act(i):
        if i == 0:
            return client.post(f"{AUTH}/login", data={"username": a.email, "password": "secret123"})
        return _refresh(client, old.refresh)

    login_resp, refresh_resp = _parallel(2, act)
    assert login_resp.status_code == 200
    winner = _pair(login_resp)
    assert _me(client, winner).status_code == 200
    assert _me(client, old).status_code == 401
    if refresh_resp.status_code == 200:  # refreshed before the login committed
        assert _me(client, _pair(refresh_resp)).status_code == 401
    else:
        assert refresh_resp.status_code in (401, 503)
    assert _unrevoked_sessions(a.id) == {_active_sid(a.id)}
