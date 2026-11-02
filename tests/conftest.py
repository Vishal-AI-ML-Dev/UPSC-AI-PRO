"""Pytest config + shared fixtures for smoke tests.

Lightweight smoke tests: they boot the REAL FastAPI app against a throwaway
SQLite database and exercise the critical paths (health, auth + email
verification, relevance gate, PYQ parser). The LLM, outbound email and
YouTube network calls are mocked, so these run fully offline with no API key.

Run:  uv run pytest -q
"""

import os
import pathlib
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Environment MUST be set BEFORE any `src.*` import so pydantic-settings picks
# it up (real env vars take priority over the project's .env file). This keeps
# the tests away from your real database, API keys and SMTP server.
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "upsc_smoke_test.db"
if _TEST_DB.exists():
    try:
        _TEST_DB.unlink()
    except OSError:
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "true"
os.environ["JWT_SECRET"] = "test-secret-key-do-not-use-in-prod"
os.environ["GOOGLE_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["LANGFUSE_ENABLED"] = "false"
# All integration tests share one TestClient and therefore one client IP, so the
# production per-IP limit (100/60s) would throttle the suite itself. Limiter logic
# is covered directly in test_rate_limiter.py.
os.environ["RATE_LIMIT_REQUESTS"] = "100000"
# Same for the stricter auth budget (every make_user registers + verifies);
# the auth limiter itself is covered in test_auth_rate_limit.py.
os.environ["AUTH_RATE_LIMIT_REQUESTS"] = "100000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """Boot the real app once against the throwaway SQLite DB."""
    from src.core.db import init_db

    init_db()
    from src.api.main import app

    with TestClient(app) as test_client:
        yield test_client


@dataclass
class TestUser:
    """A verified test user. Auth headers are excluded from repr so failing
    tests never print bearer tokens."""

    __test__ = False  # not a test class

    id: str
    email: str
    headers: dict = field(repr=False)


@pytest.fixture
def make_user(client, monkeypatch):
    """Factory: register + verify a fresh user (verification email captured, never sent)."""
    import src.api.routes.auth as auth_mod

    def _make(label: str = "user") -> TestUser:
        captured = {}
        monkeypatch.setattr(
            auth_mod,
            "send_verification_email",
            lambda to_email, link: captured.update(link=link),
        )
        email = f"{label}_{uuid.uuid4().hex[:10]}@example.com"
        r = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "secret123", "name": label},
        )
        assert r.status_code == 201, f"register failed with status {r.status_code}"
        raw = parse_qs(urlparse(captured["link"]).query)["verify_token"][0]
        v = client.post("/api/v1/auth/verify-email", json={"token": raw})
        assert v.status_code == 200, f"verify failed with status {v.status_code}"
        headers = {"Authorization": f"Bearer {v.json()['access_token']}"}
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200, f"/me failed with status {me.status_code}"
        return TestUser(id=me.json()["id"], email=email, headers=headers)

    return _make
