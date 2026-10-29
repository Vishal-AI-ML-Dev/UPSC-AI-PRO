"""Rate limiting middleware (distributed, with in-memory fallback).

Per client-IP, allows ``rate_limit_requests`` within ``rate_limit_period``
seconds (from settings). Beyond that -> 429 Too Many Requests.

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


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int | None = None, period: int | None = None):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_requests
        self.period = period or settings.rate_limit_period
        # Pass redis=False-y sentinel to force in-memory when disabled by config.
        redis = None if settings.rate_limit_redis_enabled else _Disabled()
        self._limiter = RateLimiter(self.max_requests, self.period, redis=redis)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = self._limiter.check(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Too many requests. Limit is {self.max_requests} "
                        f"per {self.period}s. Retry after {retry_after}s."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


class _Disabled:
    """Sentinel redis object: never enabled -> RateLimiter uses in-memory."""

    enabled = False

    def command(self, command: list):  # pragma: no cover - never called
        return None
