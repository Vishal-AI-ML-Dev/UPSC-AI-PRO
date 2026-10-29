"""Offline tests for the rate limiter (in-memory + Redis + fallback) and the
client identity it sees behind a reverse proxy."""

import asyncio
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.rate_limit_core import RateLimiter


class FakeRedis:
    def __init__(self, enabled=True, fail=False):
        self.enabled = enabled
        self.fail = fail
        self.store = {}
        self.calls = []

    def command(self, command):
        self.calls.append(command)
        if self.fail:
            return None
        op = command[0]
        if op == "INCR":
            k = command[1]
            self.store[k] = self.store.get(k, 0) + 1
            return self.store[k]
        if op == "EXPIRE":
            return 1
        return None


def test_in_memory_window():
    lim = RateLimiter(3, 60, redis=FakeRedis(enabled=False))
    t = 1000.0
    assert lim.check("ip", mono=t)[0]
    assert lim.check("ip", mono=t)[0]
    assert lim.check("ip", mono=t)[0]
    allowed, retry = lim.check("ip", mono=t)
    assert not allowed and retry > 0
    assert lim.check("other", mono=t)[0]
    assert lim.check("ip", mono=t + 61)[0]


def test_redis_fixed_window():
    fake = FakeRedis(enabled=True)
    lim = RateLimiter(3, 60, redis=fake)
    for _ in range(3):
        assert lim.check("ip", wall=100.0)[0]
    allowed, retry = lim.check("ip", wall=100.0)
    assert not allowed and retry > 0
    assert sum(1 for c in fake.calls if c[0] == "EXPIRE") == 1


def test_redis_unavailable_falls_back():
    fake = FakeRedis(enabled=True, fail=True)
    lim = RateLimiter(2, 60, redis=fake)
    assert lim.check("ip", wall=100.0, mono=1.0)[0]
    assert lim.check("ip", wall=100.0, mono=1.0)[0]
    assert not lim.check("ip", wall=100.0, mono=1.0)[0]


# --------------------------------------------------------------------------- #
# Client identity behind a reverse proxy (uvicorn ProxyHeadersMiddleware +
# FORWARDED_ALLOW_IPS from render.yaml) -> per-client buckets, no spoofing.
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
PROXY = "10.20.30.40"  # a private-network peer, like Render's proxy


def _render_trusted() -> str:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    m = re.search(r'key:\s*FORWARDED_ALLOW_IPS\s*\n\s*value:\s*"([^"]+)"', text)
    assert m, "render.yaml must set FORWARDED_ALLOW_IPS"
    return m.group(1)


def _stack(monkeypatch, trusted, limit=2):
    from src.api import rate_limit
    from src.api.rate_limit import RateLimitMiddleware

    monkeypatch.setattr(rate_limit.settings, "rate_limit_redis_enabled", False)
    app = FastAPI()

    @app.get("/x")
    def _x():
        return {"ok": True}

    @app.get("/health")
    def _h():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, max_requests=limit, period=60)
    # Same wrapping uvicorn applies with --proxy-headers.
    return ProxyHeadersMiddleware(app, trusted_hosts=trusted)


def _statuses(asgi, peer, xffs, path="/x"):
    async def run():
        transport = httpx.ASGITransport(app=asgi, client=(peer, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            out = []
            for xff in xffs:
                headers = {"X-Forwarded-For": xff} if xff else {}
                r = await c.get(path, headers=headers)
                out.append((r.status_code, r.headers.get("retry-after")))
            return out

    return asyncio.run(run())


def test_render_config_trusts_only_private_proxies_never_wildcard():
    trusted = _render_trusted()
    assert "*" not in trusted
    start = re.search(r"startCommand:\s*\"([^\"]+)\"", (ROOT / "render.yaml").read_text("utf-8"))
    assert "--proxy-headers" in start.group(1)
    assert "--proxy-headers" in (ROOT / "Dockerfile").read_text("utf-8")


def test_separate_clients_behind_proxy_get_separate_buckets(monkeypatch):
    asgi = _stack(monkeypatch, _render_trusted())
    a = _statuses(asgi, PROXY, ["198.51.100.1"] * 3)
    b = _statuses(asgi, PROXY, ["198.51.100.2"] * 2)
    assert [s for s, _ in a] == [200, 200, 429] and a[2][1]  # Retry-After set
    assert [s for s, _ in b] == [200, 200]  # not blocked by client A


def test_client_cannot_spoof_by_prepending_its_own_header(monkeypatch):
    """Proxy appends the real IP; rotating a fake left-most entry does not help."""
    asgi = _stack(monkeypatch, _render_trusted())
    xffs = [f"203.0.113.{i}, 198.51.100.7" for i in range(1, 4)]
    assert [s for s, _ in _statuses(asgi, PROXY, xffs)] == [200, 200, 429]


def test_untrusted_peer_header_is_ignored(monkeypatch):
    """A direct (non-proxy) caller is limited by its own address, whatever it sends."""
    asgi = _stack(monkeypatch, _render_trusted())
    xffs = [f"198.51.100.{i}" for i in range(10, 13)]
    assert [s for s, _ in _statuses(asgi, "203.0.113.99", xffs)] == [200, 200, 429]


def test_default_trust_still_shares_proxy_bucket(monkeypatch):
    """Documents the bug being fixed: uvicorn's default trusts only 127.0.0.1."""
    asgi = _stack(monkeypatch, "127.0.0.1")
    a = _statuses(asgi, PROXY, ["198.51.100.1"] * 2)
    b = _statuses(asgi, PROXY, ["198.51.100.2"])
    assert [s for s, _ in a + b] == [200, 200, 429]


def test_wildcard_trust_would_be_spoofable(monkeypatch):
    """Why render.yaml must never use "*": the left-most (client) entry wins."""
    asgi = _stack(monkeypatch, "*")
    xffs = [f"203.0.113.{i}, 198.51.100.7" for i in range(1, 5)]
    assert [s for s, _ in _statuses(asgi, PROXY, xffs)] == [200, 200, 200, 200]


@pytest.mark.parametrize("peer", [PROXY, "203.0.113.99"])
def test_health_stays_exempt(monkeypatch, peer):
    asgi = _stack(monkeypatch, _render_trusted(), limit=1)
    assert [s for s, _ in _statuses(asgi, peer, [None] * 3, path="/health")] == [200] * 3
