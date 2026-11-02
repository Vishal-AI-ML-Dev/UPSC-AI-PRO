"""Phase 02 — binary upload endpoints: exact classification, byte-counted size
limit and PDF validation (spec AC10b upload side, AC11, AC12).
"""

import io

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pypdf import PdfWriter

API = "/api/v1"
UPLOAD_ENDPOINTS = ["/upload/process", "/pyq/bank/upload"]


def make_pdf(*, pages=1, pad_bytes=0, password=None) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if pad_bytes:
        w.add_attachment("pad.bin", b"0" * pad_bytes)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture
def no_jobs(monkeypatch):
    """Keep uploads from starting real background work."""
    import src.api.routes.pyq as pyq_routes
    import src.api.routes.upload as upload_routes

    queued = []

    def fake_enqueue(job_type, work, **kw):
        queued.append(job_type)
        return "job-test"

    monkeypatch.setattr(upload_routes, "enqueue", fake_enqueue)
    monkeypatch.setattr(pyq_routes, "enqueue", fake_enqueue)
    return queued


def _post_file(client, user, path, data: bytes, name="notes.pdf"):
    return client.post(
        API + path,
        files={"file": (name, data, "application/pdf")},
        headers=user.headers,
    )


# --------------------------------------------------------------------------- #
# AC10b — classification is an exact (method, path) allow-list
# --------------------------------------------------------------------------- #
def test_is_upload_endpoint_exact_match_only():
    from src.api.upload_limit import UPLOAD_ENDPOINTS as EP
    from src.api.upload_limit import is_upload_endpoint

    assert EP == frozenset(
        {("POST", "/api/v1/upload/process"), ("POST", "/api/v1/pyq/bank/upload")}
    )
    assert is_upload_endpoint("POST", "/api/v1/upload/process")
    assert is_upload_endpoint("POST", "/api/v1/pyq/bank/upload")
    assert not is_upload_endpoint("GET", "/api/v1/upload/process")
    assert not is_upload_endpoint("PUT", "/api/v1/upload/process")
    assert not is_upload_endpoint("POST", "/api/v1/upload/chat")
    assert not is_upload_endpoint("POST", "/api/v1/upload/process/extra")
    assert not is_upload_endpoint("POST", "/api/v1/upload/processX")
    assert not is_upload_endpoint("POST", "/api/v1/upload/anything")
    assert not is_upload_endpoint("POST", "/upload/process")


@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_two_mib_pdf_is_not_rejected_by_json_limit(client, make_user, no_jobs, path):
    user = make_user("big_pdf")
    pdf = make_pdf(pad_bytes=2 * 1024 * 1024)
    assert len(pdf) > 2 * 1024 * 1024
    r = _post_file(client, user, path, pdf)
    assert r.status_code == 200, r.status_code
    assert r.json() == {"job_id": "job-test", "status": "queued"}


# --------------------------------------------------------------------------- #
# AC11 — upload size limit counts received bytes, header or not
# --------------------------------------------------------------------------- #
def _limited_app():
    from src.api.upload_limit import MaxUploadSizeMiddleware

    app = FastAPI()

    async def sink(request: Request):
        body = await request.body()
        return {"received": len(body)}

    for path in ("/api/v1/upload/process", "/api/v1/pyq/bank/upload", "/api/v1/other"):
        app.add_api_route(path, sink, methods=["POST"])
    app.add_middleware(MaxUploadSizeMiddleware, max_mb=1)
    return TestClient(app)


def _chunks(total, size=64 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"x" * n


@pytest.mark.parametrize("path", ["/api/v1/upload/process", "/api/v1/pyq/bank/upload"])
def test_upload_over_limit_with_content_length(path):
    c = _limited_app()
    r = c.post(path, content=b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert "Maximum allowed size is 1 MB" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/api/v1/upload/process", "/api/v1/pyq/bank/upload"])
def test_upload_over_limit_without_content_length(path):
    c = _limited_app()
    r = c.post(path, content=_chunks(2 * 1024 * 1024))
    assert r.status_code == 413


def test_upload_under_limit_passes():
    c = _limited_app()
    r = c.post("/api/v1/upload/process", content=_chunks(512 * 1024))
    assert r.status_code == 200
    assert r.json() == {"received": 512 * 1024}


# --------------------------------------------------------------------------- #
# AC12 — PDF validation: signature window + existing parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_non_pdf_renamed_to_pdf_is_rejected(client, make_user, no_jobs, path):
    user = make_user("fake_pdf")
    r = _post_file(client, user, path, b"MZ\x90\x00 this is an exe, not a pdf" * 10)
    assert r.status_code == 400
    assert no_jobs == []


@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_uppercase_extension_is_accepted(client, make_user, no_jobs, path):
    user = make_user("upper_pdf")
    r = _post_file(client, user, path, make_pdf(), name="NOTES.PDF")
    assert r.status_code == 200


@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_wrong_extension_is_rejected(client, make_user, no_jobs, path):
    user = make_user("ext_pdf")
    r = _post_file(client, user, path, make_pdf(), name="notes.txt")
    assert r.status_code == 400


@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_leading_bytes_before_signature_are_tolerated(client, make_user, no_jobs, path):
    user = make_user("lead_pdf")
    data = b"\xef\xbb\xbf" + b" " * 500 + make_pdf()  # header within the first 1,024 bytes
    r = _post_file(client, user, path, data)
    assert r.status_code == 200


@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_signature_beyond_window_is_rejected(client, make_user, no_jobs, path):
    user = make_user("late_pdf")
    data = b" " * 1100 + make_pdf()
    r = _post_file(client, user, path, data)
    assert r.status_code == 400


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.7\nthis is not really a pdf at all\n" * 5,
        make_pdf(password="secret"),
        make_pdf(pages=0),
    ],
    ids=["unparseable", "encrypted", "zero-pages"],
)
@pytest.mark.parametrize("path", UPLOAD_ENDPOINTS)
def test_signature_ok_but_invalid_pdf_is_rejected(client, make_user, no_jobs, path, data):
    user = make_user("bad_pdf")
    r = _post_file(client, user, path, data)
    assert r.status_code == 400
    assert no_jobs == []
