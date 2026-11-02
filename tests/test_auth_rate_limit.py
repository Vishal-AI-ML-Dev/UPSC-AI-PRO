"""Phase 02 — auth-endpoint rate limit and trusted-proxy identity (spec AC7, AC8).

A minimal app wrapped in the real ``RateLimitMiddleware`` with a tiny auth
budget, so the test is fast and independent of the suite-wide high limits set
in ``conftest.py``. In-memory limiter only (no Redis).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.rate_limit import RateLimitMiddleware
from src.core.config import settings

AUTH_PATHS = [
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
]
REFRESH = "/api/v1/auth/refresh"  # ADR-010: global limit only


def _app(monkeypatch, auth_limit=3, global_limit=10_000):
    monkeypatch.setattr(settings, "rate_limit_redis_enabled", False)
    app = FastAPI()

    @app.post("/api/v1/auth/{name}")
    def auth_route(name: str):
        return {"ok": name}

    @app.post("/api/v1/planner/generate")
    def other_route():
        return {"ok": "other"}

    @app.get("/api/v1/auth/me")
    def me():
        return {"ok": "me"}

    app.add_middleware(
        RateLimitMiddleware,
        max_requests=global_limit,
        period=60,
        auth_max_requests=auth_limit,
        auth_period=300,
    )
    return TestClient(app)


@pytest.mark.parametrize("path", AUTH_PATHS)
def test_auth_post_is_limited_after_n_requests(monkeypatch, path):
    c = _app(monkeypatch, auth_limit=3)
    codes = [c.post(path).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.post(path)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_auth_budget_is_shared_across_auth_endpoints(monkeypatch):
    c = _app(monkeypatch, auth_limit=3)
    assert c.post("/api/v1/auth/login").status_code == 200
    assert c.post("/api/v1/auth/register").status_code == 200
    assert c.post("/api/v1/auth/forgot-password").status_code == 200
    assert c.post("/api/v1/auth/login").status_code == 429


def test_non_auth_routes_unaffected_by_auth_counter(monkeypatch):
    c = _app(monkeypatch, auth_limit=2)
    for _ in range(3):
        c.post("/api/v1/auth/login")
    assert c.post("/api/v1/auth/login").status_code == 429
    for _ in range(5):
        assert c.post("/api/v1/planner/generate").status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 200  # GET is not an auth POST


def test_forged_forwarded_for_does_not_change_limiter_key(monkeypatch):
    """The app never reads X-Forwarded-For itself; only uvicorn may, for trusted proxies."""
    c = _app(monkeypatch, auth_limit=3)
    codes = [
        c.post("/api/v1/auth/login", headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(5)
    ]
    assert codes == [200, 200, 200, 429, 429]
    r = c.post(
        "/api/v1/auth/login",
        headers={"Forwarded": "for=198.51.100.7", "X-Real-IP": "198.51.100.8"},
    )
    assert r.status_code == 429


def test_default_auth_limit_comes_from_settings():
    from src.api.rate_limit import RateLimitMiddleware as M

    mw = M(FastAPI())
    assert mw.auth_max_requests == settings.auth_rate_limit_requests
    assert mw.auth_period == settings.auth_rate_limit_period


# --------------------------------------------------------------------------- #
# ADR-010 — /auth/refresh is outside the dedicated auth budget
# --------------------------------------------------------------------------- #
def test_refresh_is_not_in_auth_limited_paths():
    from src.api.rate_limit import AUTH_LIMITED_PATHS

    assert REFRESH not in AUTH_LIMITED_PATHS
    assert AUTH_LIMITED_PATHS == frozenset(AUTH_PATHS)


def test_refresh_does_not_consume_auth_budget(monkeypatch):
    c = _app(monkeypatch, auth_limit=3)
    for _ in range(20):  # many tabs refreshing behind one IP
        assert c.post(REFRESH).status_code == 200
    # The dedicated budget is untouched: login still gets its full 3 requests.
    codes = [c.post("/api/v1/auth/login").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_refresh_still_works_after_auth_budget_is_exhausted(monkeypatch):
    c = _app(monkeypatch, auth_limit=2)
    for _ in range(3):
        c.post("/api/v1/auth/login")
    assert c.post("/api/v1/auth/register").status_code == 429
    assert c.post(REFRESH).status_code == 200


def test_refresh_remains_under_the_global_limit(monkeypatch):
    c = _app(monkeypatch, auth_limit=1000, global_limit=5)
    codes = [c.post(REFRESH).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
