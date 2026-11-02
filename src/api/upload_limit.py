"""Request-body size limits and PDF upload validation.

``MaxUploadSizeMiddleware`` (pure ASGI) limits every write request body:

* the binary PDF upload endpoints in ``UPLOAD_ENDPOINTS`` - an exact
  ``(method, path)`` allow-list, never a substring match - get
  ``settings.max_upload_mb``;
* every other ``POST/PUT/PATCH`` (JSON APIs, including ``/upload/chat``) gets
  ``settings.max_request_body_kb``.

The limit is enforced on the ``Content-Length`` header AND on the bytes
actually received, so chunked bodies or a missing / lying header cannot bypass
it. Over the limit -> 413 with a JSON ``detail``.

``read_pdf_upload`` validates an uploaded file before any work is queued:
extension, the ``%PDF-`` signature within the first 1,024 bytes (the ISO 32000
reader tolerance) and that the existing parser (pypdf) opens it with at least
one page and no encryption.
"""

import io
import json

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from src.core.config import settings

_WRITE_METHODS = {"POST", "PUT", "PATCH"}

# The only endpoints that accept binary file bodies. Exact match on purpose.
UPLOAD_ENDPOINTS = frozenset(
    {
        ("POST", "/api/v1/upload/process"),
        ("POST", "/api/v1/pyq/bank/upload"),
    }
)

PDF_SIGNATURE = b"%PDF-"
PDF_SIGNATURE_WINDOW = 1024

ONLY_PDF = "Only PDF files are supported"
NOT_A_PDF = "📄 This file is not a valid PDF. Please upload a PDF document."
UNREADABLE_PDF = (
    "📄 We couldn’t open this PDF. The file may be corrupted "
    "or password-protected. Please try a different file."
)


def is_upload_endpoint(method: str, path: str) -> bool:
    """True only for the exact binary-upload endpoints."""
    return ((method or "").upper(), path or "") in UPLOAD_ENDPOINTS


def _too_large(limit_text: str) -> bytes:
    return json.dumps({"detail": f"{limit_text}"}).encode("utf-8")


class _BodyTooLarge(Exception):
    pass


class MaxUploadSizeMiddleware:
    """Reject request bodies over the configured limits (header and streamed bytes)."""

    def __init__(self, app, max_mb: int | None = None, max_body_kb: int | None = None):
        self.app = app
        self.max_mb = max_mb or settings.max_upload_mb
        self.max_bytes = self.max_mb * 1024 * 1024
        self.max_body_kb = max_body_kb or settings.max_request_body_kb
        self.max_body_bytes = self.max_body_kb * 1024

    def _limit_for(self, method: str, path: str) -> tuple[int, str]:
        if is_upload_endpoint(method, path):
            return self.max_bytes, f"File too large. Maximum allowed size is {self.max_mb} MB."
        return (
            self.max_body_bytes,
            f"Request body too large. Maximum allowed size is {self.max_body_kb} KB.",
        )

    @staticmethod
    async def _send_413(send, message: str) -> None:
        body = _too_large(message)
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        method = scope.get("method", "") if scope.get("type") == "http" else ""
        if method not in _WRITE_METHODS:
            await self.app(scope, receive, send)
            return

        limit, message = self._limit_for(method, scope.get("path", ""))

        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break  # malformed header -> rely on counting below
                if declared > limit:
                    await self._send_413(send, message)
                    return
                break

        received = 0
        exceeded = False
        started = False

        async def limited_receive():
            nonlocal received, exceeded
            msg = await receive()
            if msg.get("type") == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return msg

        async def guarded_send(msg):
            nonlocal started
            if exceeded:
                # The app turned the aborted read into its own error response;
                # replace it with our 413 (sent once, below).
                return
            if msg.get("type") == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not started:
            await self._send_413(send, message)


async def read_pdf_upload(file: UploadFile, max_bytes: int | None = None) -> bytes:
    """Read and validate an uploaded PDF; raise HTTP 400/413 with fixed messages."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=ONLY_PDF)

    cap = max_bytes or settings.max_upload_mb * 1024 * 1024
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum allowed size is {cap // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    if PDF_SIGNATURE not in content[:PDF_SIGNATURE_WINDOW]:
        raise HTTPException(status_code=400, detail=NOT_A_PDF)

    if not await run_in_threadpool(_opens_as_pdf, content):
        raise HTTPException(status_code=400, detail=UNREADABLE_PDF)
    return content


def _opens_as_pdf(content: bytes) -> bool:
    """The existing parser must open it: not encrypted and at least one page."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            return False
        return len(reader.pages) >= 1
    except Exception:  # noqa: BLE001 - any parser failure means "not usable"
        return False
