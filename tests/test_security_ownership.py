"""Security regression tests (#9): IDOR ownership + refresh-token rotation.

These lock in two guarantees so a future refactor can't silently regress them:

1. A user can never read another user's conversation (IDOR is blocked -> 404).
2. Refresh tokens are single-use/rotating and revocable (logout kills a session).

Everything runs against the throwaway SQLite DB booted by the ``client`` fixture;
the verification email is monkeypatched so no network/SMTP is touched.
"""

import uuid
from urllib.parse import parse_qs, urlparse

AUTH = "/api/v1/auth"
HIST = "/api/v1/history"


def _signup(client, monkeypatch, password="secret123"):
    """Register -> verify -> return (email, access_token, refresh_token)."""
    email = f"sec_{uuid.uuid4().hex[:10]}@example.com"
    captured = {}
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "send_verification_email",
        lambda to_email, link: captured.update(link=link),
    )
    r = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": password, "name": "Sec"},
    )
    assert r.status_code == 201, r.text
    token = parse_qs(urlparse(captured["link"]).query)["verify_token"][0]
    v = client.post(f"{AUTH}/verify-email", json={"token": token})
    assert v.status_code == 200, v.text
    body = v.json()
    return email, body["access_token"], body["refresh_token"]


def _bearer(access):
    return {"Authorization": f"Bearer {access}"}


def test_user_cannot_read_others_conversation(client, monkeypatch):
    _, a_access, _ = _signup(client, monkeypatch)
    _, b_access, _ = _signup(client, monkeypatch)

    # User A creates a conversation by saving a message.
    saved = client.post(
        f"{HIST}/messages",
        json={"role": "user", "content": "A's private note", "agent": "mentor"},
        headers=_bearer(a_access),
    )
    assert saved.status_code == 201, saved.text
    convo_id = saved.json()["conversation_id"]

    # A can read own messages.
    own = client.get(f"{HIST}/conversations/{convo_id}/messages", headers=_bearer(a_access))
    assert own.status_code == 200
    assert any("private note" in m["content"] for m in own.json()["messages"])

    # B must NOT be able to read A's conversation (IDOR blocked -> 404).
    stolen = client.get(f"{HIST}/conversations/{convo_id}/messages", headers=_bearer(b_access))
    assert stolen.status_code == 404

    # And A's conversation must not leak into B's own list.
    b_list = client.get(f"{HIST}/conversations", headers=_bearer(b_access))
    assert b_list.status_code == 200
    assert all(c["id"] != convo_id for c in b_list.json()["conversations"])


def test_refresh_rotates_and_old_token_is_rejected(client, monkeypatch):
    _, _, refresh = _signup(client, monkeypatch)

    r1 = client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
    assert r1.status_code == 200, r1.text
    data = r1.json()
    assert data["access_token"]
    new_refresh = data["refresh_token"]
    assert new_refresh and new_refresh != refresh

    # The presented token was rotated out -> reuse is denied.
    reuse = client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401

    # The new token still works.
    r2 = client.post(f"{AUTH}/refresh", json={"refresh_token": new_refresh})
    assert r2.status_code == 200, r2.text


def test_logout_revokes_refresh_token(client, monkeypatch):
    _, _, refresh = _signup(client, monkeypatch)
    out = client.post(f"{AUTH}/logout", json={"refresh_token": refresh})
    assert out.status_code == 200
    dead = client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
    assert dead.status_code == 401


def test_refresh_with_garbage_token_is_401(client):
    r = client.post(f"{AUTH}/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401
