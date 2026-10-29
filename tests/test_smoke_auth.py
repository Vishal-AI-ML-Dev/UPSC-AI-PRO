"""Smoke: full auth + email-verification flow on a real DB (email mocked).

We never send a real email; instead we monkeypatch send_verification_email to
capture the link, then pull the raw token out of it - exercising the real
token create/consume logic end to end.
"""

import uuid
from urllib.parse import parse_qs, urlparse

API = "/api/v1/auth"


def _unique_email():
    return f"smoke_{uuid.uuid4().hex[:10]}@example.com"


def _register_capturing_token(client, monkeypatch, email, password="secret123"):
    """Register a user and return the raw verification token from the (mocked) email."""
    captured = {}
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "send_verification_email",
        lambda to_email, link: captured.update(link=link, to=to_email),
    )
    r = client.post(
        f"{API}/register",
        json={"email": email, "password": password, "name": "Smoke"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("verification_required") is True
    assert "access_token" not in body  # strict: no auto-login before verify
    link = captured.get("link")
    assert link, "verification email was not sent"
    return parse_qs(urlparse(link).query)["verify_token"][0]


def test_full_verify_then_login(client, monkeypatch):
    email = _unique_email()
    pw = "secret123"
    token = _register_capturing_token(client, monkeypatch, email, pw)

    # Login is blocked until the email is verified.
    blocked = client.post(f"{API}/login", data={"username": email, "password": pw})
    assert blocked.status_code == 403

    # Verify the email -> auto-login token returned.
    verified = client.post(f"{API}/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    assert "access_token" in verified.json()

    # Now a normal login works.
    ok = client.post(f"{API}/login", data={"username": email, "password": pw})
    assert ok.status_code == 200
    access = ok.json()["access_token"]

    # And the token is accepted by a protected endpoint.
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_wrong_password_is_401(client, monkeypatch):
    email = _unique_email()
    token = _register_capturing_token(client, monkeypatch, email, "secret123")
    client.post(f"{API}/verify-email", json={"token": token})
    bad = client.post(f"{API}/login", data={"username": email, "password": "wrong-pass"})
    assert bad.status_code == 401


def test_verify_with_garbage_token_is_400(client):
    r = client.post(f"{API}/verify-email", json={"token": "not-a-real-token"})
    assert r.status_code == 400


def test_duplicate_register_is_400(client, monkeypatch):
    email = _unique_email()
    _register_capturing_token(client, monkeypatch, email)
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "send_verification_email", lambda *a, **k: None)
    dup = client.post(f"{API}/register", json={"email": email, "password": "secret123"})
    assert dup.status_code == 400
