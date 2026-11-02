"""Rate limiting middleware (distributed, with in-memory fallback).

Per client-IP, allows ``rate_limit_requests`` within ``rate_limit_period``
seconds (from settings). Beyond that -> 429 Too Many Requests.

Credential / email endpoints (``POST /api/v1/auth/{login,register,
forgot-password,reset-password,verify-email,resend-verification}``) additionally
share a stricter per-IP budget: ``auth_rate_limit_requests`` per
``auth_rate_limit_period`` (separate counter, key ``auth:<ip>``). The global
limit still applies to them. ``/auth/refresh`` is deliberately NOT in this
budget (ADR-010): refresh tokens are random, rotating and revocable, so they
cannot be guessed, and every open tab refreshes about every 30 minutes - many
students behind one shared IP would otherwise lock each other out. Refresh
stays under the global limiter like every other route.

Client identity comes ONLY from ``request.client.host``. The app never reads
``X-Forwarded-For`` / ``Forwarded`` itself: uvicorn ``--proxy-headers`` rewrites
the client address from those headers only for peers listed in
``FORWARDED_ALLOW_IPS`` (default 127.0.0.1; Render: private ranges; never "*"),
so a client cannot pick its own rate-limit bucket by forging a header.

Backend: an Upstash-Redis fixed-window counter (correct across >1 worker /
instance) when creds are configured and ``rate_limit_redis_enabled`` is on;
otherwise an in-process sliding window. A Redis outage trips a circuit breaker
and the limiter transparently falls back to in-memory -- it never adds latency
or blocks the request path.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.rate_limit_core import RateLimiter
from src.core.config import settings

# Requests exempt from rate limiting (health checks, docs).
_EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}

# POST endpoints that share the stricter auth budget (exact paths).
# /auth/refresh is intentionally excluded (ADR-010) - global limit only.
AUTH_LIMITED_PATHS = frozenset(
    f"/api/v1/auth/{name}"
    for name in (
        "login",
        "register",
        "forgot-password",
        "reset-password",
        "verify-email",
        "resend-verification",
    )
)


def _too_many(max_requests: int, period: int, retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": (
                f"Too many requests. Limit is {max_requests} "
                f"per {period}s. Retry after {retry_after}s."
            )
        },
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        max_requests: int | None = None,
        period: int | None = None,
        auth_max_requests: int | None = None,
        auth_period: int | None = None,
    ):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_requests
        self.period = period or settings.rate_limit_period
        self.auth_max_requests = auth_max_requests or settings.auth_rate_limit_requests
        self.auth_period = auth_period or settings.auth_rate_limit_period
        # Pass redis=False-y sentinel to force in-memory when disabled by config.
        redis = None if settings.rate_limit_redis_enabled else _Disabled()
        self._limiter = RateLimiter(self.max_requests, self.period, redis=redis)
        self._auth_limiter = RateLimiter(self.auth_max_requests, self.auth_period, redis=redis)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # Only the (proxy-header-resolved) peer address - never a request header.
        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = self._limiter.check(client_ip)
        if not allowed:
            return _too_many(self.max_requests, self.period, retry_after)

        if request.method == "POST" and request.url.path in AUTH_LIMITED_PATHS:
            allowed, retry_after = self._auth_limiter.check(f"auth:{client_ip}")
            if not allowed:
                return _too_many(self.auth_max_requests, self.auth_period, retry_after)
        return await call_next(request)


class _Disabled:
    """Sentinel redis object: never enabled -> RateLimiter uses in-memory."""

    enabled = False

    def command(self, command: list):  # pragma: no cover - never called
        return None
