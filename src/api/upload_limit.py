"""Max upload size middleware.

Upload endpoints par incoming file ka size check karta hai (Content-Length
header se). Agar settings.max_upload_mb se bada hua -> 413 Request Entity
Too Large, bina file ko memory mein load kiye.

Ye sirf un paths par lagta hai jinme 'upload' aata hai, taaki normal
chhote JSON requests par koi asar na ho.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import settings

_WRITE_METHODS = {"POST", "PUT", "PATCH"}


class MaxUploadSizeMiddleware(BaseHTTPMiddleware):
    """Reject too-large uploads early using the Content-Length header."""

    def __init__(self, app, max_mb: int | None = None):
        super().__init__(app)
        mb = max_mb or settings.max_upload_mb
        self.max_mb = mb
        self.max_bytes = mb * 1024 * 1024

    async def dispatch(self, request: Request, call_next):
        is_upload = "upload" in request.url.path.lower()
        if request.method in _WRITE_METHODS and is_upload:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > self.max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "detail": (
                                    f"File too large. Maximum allowed size is {self.max_mb} MB."
                                )
                            },
                        )
                except ValueError:
                    # malformed header -> let it pass, route validation handle karega
                    pass
        return await call_next(request)
