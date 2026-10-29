"""Request-ID middleware: one correlation ID per request (tracing).

Pure-ASGI (not BaseHTTPMiddleware) so the contextvar it sets is visible to the
downstream endpoint and all its logs -- BaseHTTPMiddleware runs the app in a
separate task and would not propagate the contextvar. Honors an inbound
``X-Request-ID`` (client/proxy trace), else generates one; echoes it on the
response and tags Sentry.
"""

from __future__ import annotations

from src.core.error_monitoring import set_request_context
from src.core.request_context import new_request_id, reset_request_id, set_request_id

_HEADER = b"x-request-id"
_MAX_LEN = 200


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = ""
        for key, value in scope.get("headers", []):
            if key == _HEADER:
                raw = value.decode("latin-1").strip()[:_MAX_LEN]
                break
        rid = raw or new_request_id()
        token = set_request_id(rid)
        set_request_context(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HEADER, rid.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_request_id(token)
