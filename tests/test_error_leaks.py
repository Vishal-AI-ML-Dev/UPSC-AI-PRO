"""Phase 02 — no raw exception text in responses, job errors or logs
(spec AC13, AC14, AC15).

Contract: ``UserFacingError`` -> 400 with its message; any other exception,
including a plain ``ValueError`` -> 500 with the fixed generic message.
"""

import io
import logging
import uuid

import pytest
from pypdf import PdfWriter
from src.core.errors import GENERIC_ERROR, UserFacingError

API = "/api/v1"
SECRET = "secret-path /srv/app/.env"


def _pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _raiser(exc):
    def boom(*args, **kwargs):
        raise exc

    return boom


EXCEPTIONS = [
    (UserFacingError("Shown message"), 400, "Shown message"),
    (ValueError(SECRET), 500, GENERIC_ERROR),
    (RuntimeError(SECRET), 500, GENERIC_ERROR),
]
EXC_IDS = ["user-facing", "plain-valueerror", "runtimeerror"]


def test_generic_message_text():
    assert GENERIC_ERROR == "Processing failed. Please try again."
    assert issubclass(UserFacingError, ValueError)


# --------------------------------------------------------------------------- #
# AC13 — sync endpoints
# --------------------------------------------------------------------------- #
def _call(client, user, endpoint):
    if endpoint == "upload":
        return client.post(
            f"{API}/upload/process?sync=true",
            files={"file": ("n.pdf", _pdf(), "application/pdf")},
            headers=user.headers,
        )
    if endpoint == "pyq":
        return client.post(
            f"{API}/pyq/bank/upload?sync=true",
            files={"file": ("n.pdf", _pdf(), "application/pdf")},
            headers=user.headers,
        )
    if endpoint == "lecture":
        return client.post(
            f"{API}/lecture/process?sync=true",
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers=user.headers,
        )
    return client.post(
        f"{API}/lecture/process-text?sync=true",
        json={"transcript": "Gram Sabha lecture text"},
        headers=user.headers,
    )


def _patch_work(monkeypatch, endpoint, exc):
    import src.api.routes.lecture as lecture_routes
    import src.api.routes.pyq as pyq_routes
    import src.api.routes.upload as upload_routes

    target = {
        "upload": (upload_routes, "process_upload"),
        "pyq": (pyq_routes, "build_question_bank"),
        "lecture": (lecture_routes, "process_lecture"),
        "lecture-text": (lecture_routes, "process_lecture_from_text"),
    }[endpoint]
    monkeypatch.setattr(*target, _raiser(exc))


@pytest.mark.parametrize("exc, status, detail", EXCEPTIONS, ids=EXC_IDS)
@pytest.mark.parametrize("endpoint", ["upload", "pyq", "lecture", "lecture-text"])
def test_sync_endpoint_error_contract(
    client, make_user, monkeypatch, caplog, endpoint, exc, status, detail
):
    user = make_user("err_" + endpoint)
    _patch_work(monkeypatch, endpoint, exc)
    with caplog.at_level(logging.DEBUG):
        r = _call(client, user, endpoint)
    assert r.status_code == status
    assert r.json()["detail"] == detail
    assert "secret-path" not in r.text
    assert "secret-path" not in caplog.text


# --------------------------------------------------------------------------- #
# AC13 — background jobs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc, _status, detail", EXCEPTIONS, ids=EXC_IDS)
def test_job_error_contract(client, monkeypatch, caplog, exc, _status, detail):
    from src.core import job_queue
    from src.core.config import settings

    monkeypatch.setattr(settings, "jobs_backend", "inline")
    with caplog.at_level(logging.DEBUG):
        job_id = job_queue.enqueue("test_job", _raiser(exc), user_id="u-test")
    job = job_queue.get_job(job_id)
    assert job["status"] == "error"
    assert job["error"] == detail
    assert "secret-path" not in caplog.text


# --------------------------------------------------------------------------- #
# AC13 — auth register / reset
# --------------------------------------------------------------------------- #
@pytest.fixture
def quiet_email(monkeypatch):
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "send_verification_email", lambda to_email, link: None)
    return auth_mod


@pytest.mark.parametrize("exc, status, detail", EXCEPTIONS, ids=EXC_IDS)
def test_register_error_contract(client, quiet_email, monkeypatch, caplog, exc, status, detail):
    monkeypatch.setattr(quiet_email, "create_user", _raiser(exc))
    with caplog.at_level(logging.DEBUG):
        r = client.post(
            f"{API}/auth/register",
            json={"email": f"e_{uuid.uuid4().hex[:8]}@example.com", "password": "secret123"},
        )
    assert r.status_code == status
    assert r.json()["detail"] == detail
    assert "secret-path" not in r.text
    assert "secret-path" not in caplog.text


@pytest.mark.parametrize("exc, status, detail", EXCEPTIONS, ids=EXC_IDS)
def test_reset_error_contract(client, quiet_email, monkeypatch, caplog, exc, status, detail):
    monkeypatch.setattr(quiet_email, "hash_new_password", _raiser(exc))
    with caplog.at_level(logging.DEBUG):
        r = client.post(
            f"{API}/auth/reset-password", json={"token": "t" * 40, "new_password": "secret123"}
        )
    assert r.status_code == status
    assert r.json()["detail"] == detail
    assert "secret-path" not in r.text
    assert "secret-path" not in caplog.text


# --------------------------------------------------------------------------- #
# AC14 — describe_exception never carries the message
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exc", [ValueError(SECRET), RuntimeError(SECRET), UserFacingError(SECRET)])
def test_describe_exception_has_no_message(exc):
    from src.core.llm import describe_exception

    try:
        raise exc
    except Exception as caught:  # noqa: BLE001
        out = describe_exception(caught)
    assert "secret-path" not in out
    assert type(exc).__name__ in out


def test_upload_graph_logs_have_no_exception_text(monkeypatch, caplog):
    import src.agents.upload.graph as ug

    monkeypatch.setattr(ug, "get_llm", _raiser(RuntimeError(SECRET)))
    with caplog.at_level(logging.DEBUG):
        ug.detect_book("some text")
    assert "secret-path" not in caplog.text


def test_tool_failure_log_has_no_exception_text(caplog):
    from src.graph.tools import run_tool_calls

    with caplog.at_level(logging.DEBUG):
        run_tool_calls(
            [{"name": "web_search", "args": {"query": "x"}, "id": "1"}],
            registry={"web_search": _raiser(RuntimeError(SECRET))},
        )
    assert "secret-path" not in caplog.text


def test_password_verify_failure_log_has_no_exception_text(caplog):
    from src.core.users import verify_password

    with caplog.at_level(logging.DEBUG):
        assert verify_password("secret123", "not-a-bcrypt-hash secret-path") is False
    assert "secret-path" not in caplog.text


# --------------------------------------------------------------------------- #
# AC15 — no emails / query text in logs
# --------------------------------------------------------------------------- #
def test_user_creation_log_has_no_email(make_user, caplog):
    with caplog.at_level(logging.DEBUG):
        user = make_user("pii")
    assert user.email not in caplog.text
    assert user.email.split("@")[0] not in caplog.text


def test_email_failure_log_has_no_email(client, quiet_email, monkeypatch, caplog):
    monkeypatch.setattr(quiet_email, "send_verification_email", _raiser(RuntimeError(SECRET)))
    email = f"mailfail_{uuid.uuid4().hex[:8]}@example.com"
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"{API}/auth/register", json={"email": email, "password": "secret123"})
    assert r.status_code == 201
    assert email not in caplog.text
    assert "secret-path" not in caplog.text


def test_forgot_password_failure_log_has_no_email(client, make_user, monkeypatch, caplog):
    import src.api.routes.auth as auth_mod

    user = make_user("forgot")
    monkeypatch.setattr(auth_mod, "send_reset_email", _raiser(RuntimeError(SECRET)))
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"{API}/auth/forgot-password", json={"email": user.email})
    assert r.status_code == 200
    assert user.email not in caplog.text
    assert "secret-path" not in caplog.text


def test_retrieval_metrics_log_has_no_query_text(caplog):
    from src.core.observability import log_retrieval_metrics

    with caplog.at_level(logging.DEBUG):
        log_retrieval_metrics("upload", "my private question about my notes", 4, 1, [0.5])
    assert "my private question" not in caplog.text
    assert "q_len=" in caplog.text
