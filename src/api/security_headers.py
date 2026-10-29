"""Security headers + request-timeout middleware (drop-in).

Place this file at: src/api/security_headers.py
Then wire it in src/api/main.py (see comments at bottom).

Adds the headers scanners/pen-tests expect and a hard per-request timeout so a
stuck upstream (LLM / vector DB) can never hold a worker forever on the free
tier (single worker => one stuck request = total outage without this).
"""

import asyncio
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import settings

logger = logging.getLogger(__name__)

# Docs/OpenAPI need a relaxed CSP so Swagger UI can load its CDN assets.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard hardening headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # HSTS only makes sense over HTTPS (prod). 2 years + preload.
        if settings.is_production:
            h.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        # A conservative CSP for a JSON API. Relaxed on the docs pages so the
        # Swagger/ReDoc CDN bundles still render.
        if not request.url.path.startswith(_DOCS_PATHS):
            h.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
        return response


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Fail a request with 504 if it exceeds REQUEST_TIMEOUT_SECONDS."""

    def __init__(self, app, timeout_seconds: int = 60):
        super().__init__(app)
        self.timeout = timeout_seconds

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.warning("Request timed out: %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=504,
                content={"detail": "Upstream timed out. Please try again."},
            )


# ---------------------------------------------------------------------------
# WIRE-UP (add to src/api/main.py, after the CORS middleware block):
#
#   from src.api.security_headers import SecurityHeadersMiddleware, TimeoutMiddleware
#   app.add_middleware(SecurityHeadersMiddleware)
#   app.add_middleware(TimeoutMiddleware, timeout_seconds=90)  # > slowest LLM call
#
# Order note: add_middleware wraps outermost-last, so add SecurityHeaders AFTER
# CORS so CORS headers are present on error responses too.
# ---------------------------------------------------------------------------
