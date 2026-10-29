"""Pytest config + shared fixtures for smoke tests.

Lightweight smoke tests: they boot the REAL FastAPI app against a throwaway
SQLite database and exercise the critical paths (health, auth + email
verification, relevance gate, PYQ parser). The LLM, outbound email and
YouTube network calls are mocked, so these run fully offline with no API key.

Run:  uv run pytest -q
"""
import os
import sys
import pathlib
import tempfile

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
# Dummy SMTP creds so smtp_configured() -> True in EVERY environment (CI has no
# .env). verification_enforced() = require_email_verification AND smtp_configured();
# without these, CI (no SMTP secrets) silently auto-disables verification, so
# /register auto-logs-in instead of returning {"verification_required": true} and
# the auth smoke tests fail. No real email is ever sent: the two tests that hit
# /register both monkeypatch send_verification_email.
os.environ["SMTP_HOST"] = "smtp.test.local"
os.environ["SMTP_USER"] = "smoke@test.local"
os.environ["SMTP_PASSWORD"] = "test-smtp-password-not-real"
os.environ["JWT_SECRET"] = "test-secret-key-do-not-use-in-prod"
os.environ["GOOGLE_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["LANGFUSE_ENABLED"] = "false"

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
