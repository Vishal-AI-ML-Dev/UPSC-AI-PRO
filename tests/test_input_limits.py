"""Phase 02 — input bounds, chat-history trimming order, JSON body limit and
password length rules (spec AC9, AC9b, AC10, AC10b JSON side, AC16, AC16b).
"""

import uuid

import pytest

API = "/api/v1"
TWO_MIB = 2 * 1024 * 1024


# --------------------------------------------------------------------------- #
# AC9 — oversized free-text fields -> 422
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path, body",
    [
        ("/agent/chat/stream", {"question": "q" * 4001}),
        ("/agent/chat/stream", {"question": "q", "thread_id": "t" * 101}),
        ("/upload/chat?pdf_hash=" + "0" * 32, {"request": {"question": "q" * 4001}}),
        (
            "/ncert/chat",
            {"question": "q" * 4001, "class_name": "6", "subject": "s", "chapter": "c"},
        ),
        ("/lecture/chat", {"question": "q" * 4001, "video_id": "dQw4w9WgXcQ"}),
        ("/lecture/process-text", {"transcript": "t" * 200_001}),
        ("/pyq/generate", {"topic": "t" * 201}),
        ("/pyq/parse", {"text": "t" * 50_001}),
        ("/pyq/bank/generate", {"topic": "t" * 201}),
    ],
)
def test_oversized_fields_are_rejected(client, make_user, path, body):
    user = make_user("limits")
    r = client.post(API + path, json=body, headers=user.headers)
    assert r.status_code == 422, (path, r.status_code)


@pytest.mark.parametrize(
    "path, body",
    [
        ("/auth/register", {"email": "a" * 250 + "@x.io", "password": "secret123"}),
        ("/auth/register", {"email": "ok@example.com", "password": "secret123", "name": "n" * 101}),
        ("/auth/forgot-password", {"email": "a" * 250 + "@x.io"}),
        ("/auth/resend-verification", {"email": "a" * 250 + "@x.io"}),
        ("/auth/verify-email", {"token": "t" * 513}),
        ("/auth/refresh", {"refresh_token": "t" * 513}),
        ("/auth/reset-password", {"token": "t" * 513, "new_password": "secret123"}),
    ],
)
def test_oversized_auth_fields_are_rejected(client, path, body):
    r = client.post(API + path, json=body)
    assert r.status_code == 422, (path, r.status_code)


# --------------------------------------------------------------------------- #
# AC9b — history is trimmed/truncated BEFORE any length validation
# --------------------------------------------------------------------------- #
def _history(n=25, long_at=24):
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"} for i in range(n)
    ]
    msgs[long_at]["content"] = "x" * 10_000
    return msgs


def test_chat_message_truncates_instead_of_rejecting():
    from src.schemas import ChatMessage

    m = ChatMessage(role="user", content="x" * 10_000)
    assert len(m.content) == 4000


def test_history_keeps_last_twenty_before_validation():
    from src.schemas import ChatRequest

    req = ChatRequest(question="q", chat_history=_history())
    assert len(req.chat_history) == 20
    assert req.chat_history[0].content == "message 5"
    assert len(req.chat_history[-1].content) == 4000


def test_non_string_history_content_is_still_a_type_error(client, make_user):
    user = make_user("hist_type")
    r = client.post(
        f"{API}/upload/chat?pdf_hash=" + "0" * 32,
        json={"request": {"question": "q", "chat_history": [{"role": "user", "content": 5}]}},
        headers=user.headers,
    )
    assert r.status_code == 422


@pytest.fixture
def captured_history(monkeypatch):
    import src.api.routes.lecture as lecture_routes
    import src.api.routes.ncert as ncert_routes
    import src.api.routes.upload as upload_routes

    got: dict = {}

    def fake(name):
        def gen(*args, **kwargs):
            got[name] = kwargs.get("chat_history")
            yield "ok"

        return gen

    monkeypatch.setattr(upload_routes, "ask_upload", fake("upload"))
    monkeypatch.setattr(lecture_routes, "ask_lecture", fake("lecture"))
    monkeypatch.setattr(ncert_routes, "ask_ncert", fake("ncert"))
    return got


@pytest.mark.parametrize(
    "name, path, body",
    [
        (
            "upload",
            "/upload/chat?pdf_hash=" + "0" * 32,
            lambda h: {"request": {"question": "q", "chat_history": h}},
        ),
        (
            "lecture",
            "/lecture/chat",
            lambda h: {"question": "q", "video_id": "dQw4w9WgXcQ", "chat_history": h},
        ),
        (
            "ncert",
            "/ncert/chat",
            lambda h: {
                "question": "q",
                "class_name": "6",
                "subject": "s",
                "chapter": "c",
                "chat_history": h,
            },
        ),
    ],
)
def test_long_history_is_accepted_and_trimmed_end_to_end(
    client, make_user, captured_history, name, path, body
):
    user = make_user("hist_" + name)
    r = client.post(API + path, json=body(_history()), headers=user.headers)
    assert r.status_code == 200, r.status_code
    hist = captured_history[name]
    assert len(hist) == 20

    def content(m):
        return m["content"] if isinstance(m, dict) else m.content

    assert content(hist[0]) == "message 5"
    assert len(content(hist[-1])) == 4000


# --------------------------------------------------------------------------- #
# AC10 / AC10b — 1 MiB limit on non-upload bodies (incl. /upload/chat)
# --------------------------------------------------------------------------- #
def _big_json(size=TWO_MIB):
    return ('{"question": "' + "q" * size + '"}').encode()


def test_json_body_over_limit_with_content_length(client, make_user):
    user = make_user("body_cl")
    r = client.post(
        f"{API}/agent/chat/stream",
        content=_big_json(),
        headers={**user.headers, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_json_body_over_limit_chunked(client, make_user):
    user = make_user("body_chunked")
    payload = _big_json()

    def chunks():
        for i in range(0, len(payload), 64 * 1024):
            yield payload[i : i + 64 * 1024]

    r = client.post(
        f"{API}/agent/chat/stream",
        content=chunks(),
        headers={**user.headers, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_upload_chat_uses_json_limit_not_file_limit(client, make_user):
    """/upload/chat contains the word 'upload' but is a JSON endpoint."""
    user = make_user("body_upload_chat")
    body = ('{"request": {"question": "' + "q" * TWO_MIB + '"}}').encode()
    r = client.post(
        f"{API}/upload/chat?pdf_hash=" + "0" * 32,
        content=body,
        headers={**user.headers, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_unregistered_path_containing_upload_uses_json_limit(client, make_user):
    user = make_user("body_upload_other")
    r = client.post(
        f"{API}/upload/anything",
        content=b"x" * TWO_MIB,
        headers={**user.headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 413


def test_max_size_devanagari_transcript_is_accepted(client, make_user, monkeypatch):
    import src.api.routes.lecture as lecture_routes

    monkeypatch.setattr(lecture_routes, "enqueue", lambda *a, **k: "job-test")
    user = make_user("body_transcript")
    transcript = "क" * 200_000  # 600,000 UTF-8 bytes: the largest legitimate body
    r = client.post(
        f"{API}/lecture/process-text", json={"transcript": transcript}, headers=user.headers
    )
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-test", "status": "queued"}


# --------------------------------------------------------------------------- #
# AC16 — new-password path: 6-char minimum (unchanged), 128 chars, 72 bytes
# --------------------------------------------------------------------------- #
LIMIT_MSG = "Password must be at most 72 bytes"
MIN_MSG = "Password must be at least 6 characters"


@pytest.fixture
def register(client, monkeypatch):
    import src.api.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "send_verification_email", lambda to_email, link: None)

    def _do(password):
        email = f"pw_{uuid.uuid4().hex[:10]}@example.com"
        return client.post(
            f"{API}/auth/register", json={"email": email, "password": password, "name": "pw"}
        )

    return _do


@pytest.fixture
def reset(client, make_user):
    from src.core.db import SessionLocal
    from src.core.reset_tokens import create_reset_token

    def _do(new_password):
        user = make_user("reset")
        db = SessionLocal()
        try:
            raw = create_reset_token(db, user.id)
            db.commit()
        finally:
            db.close()
        return client.post(
            f"{API}/auth/reset-password", json={"token": raw, "new_password": new_password}
        )

    return _do


@pytest.mark.parametrize("password", ["€" * 24, "a" * 72], ids=["24-euro-72B", "72-ascii"])
def test_new_password_at_72_bytes_is_accepted(register, reset, password):
    assert len(password.encode("utf-8")) == 72
    assert register(password).status_code == 201
    assert reset(password).status_code == 200


@pytest.mark.parametrize(
    "password", ["€" * 25, "a" * 70 + "€"], ids=["25-euro-75B", "70a+euro-73B"]
)
def test_new_password_over_72_bytes_is_rejected(register, reset, password):
    assert len(password.encode("utf-8")) > 72
    for r in (register(password), reset(password)):
        assert r.status_code == 400
        assert r.json()["detail"].startswith(LIMIT_MSG)


@pytest.mark.parametrize("password", ["abcde", "€" * 5])
def test_existing_minimum_is_unchanged(register, reset, password):
    for r in (register(password), reset(password)):
        assert r.status_code == 400
        assert r.json()["detail"] == MIN_MSG


def test_new_password_over_128_chars_is_422(register, reset):
    assert register("a" * 129).status_code == 422
    assert reset("a" * 129).status_code == 422


# --------------------------------------------------------------------------- #
# AC16b — login is an independent path
# --------------------------------------------------------------------------- #
def _set_password_hash(user_id, plain):
    from src.core.db import SessionLocal
    from src.core.models import User
    from src.core.users import hash_password

    db = SessionLocal()
    try:
        db.get(User, user_id).hashed_password = hash_password(plain)
        db.commit()
    finally:
        db.close()


def _login(client, email, password):
    return client.post(f"{API}/auth/login", data={"username": email, "password": password})


@pytest.mark.parametrize("length", [100, 200])
def test_legacy_long_password_still_logs_in(client, make_user, length):
    user = make_user("legacy_pw")
    legacy = ("Ab1" * 100)[:length]  # > 72 bytes; 200 also exceeds the 128-char new-password bound
    _set_password_hash(user.id, legacy)
    assert _login(client, user.email, legacy).status_code == 200
    wrong = "Zz9" + legacy[3:]
    assert _login(client, user.email, wrong).status_code == 401


def test_login_over_1024_chars_is_401_without_bcrypt(client, make_user, monkeypatch):
    import src.core.users as users

    user = make_user("long_login")
    calls = []
    real = users.verify_password
    monkeypatch.setattr(users, "verify_password", lambda p, h: calls.append(1) or real(p, h))
    r = _login(client, user.email, "a" * 1025)
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"
    assert calls == []


def test_login_does_not_use_new_password_validator(client, make_user, monkeypatch):
    import src.core.users as users

    user = make_user("login_indep")
    called = []
    monkeypatch.setattr(users, "_validate_new_password", lambda p: called.append(p), raising=False)
    assert _login(client, user.email, "secret123").status_code == 200
    assert called == []


def test_register_and_reset_do_not_use_login_length_check(client, register, reset, monkeypatch):
    import src.api.routes.auth as auth_mod

    called = []
    monkeypatch.setattr(
        auth_mod, "_login_input_too_long", lambda *a: called.append(a) or False, raising=False
    )
    assert register("secret123").status_code == 201
    assert reset("secret456").status_code == 200
    assert called == []


def test_login_input_check_bounds():
    from src.api.routes.auth import _login_input_too_long

    assert _login_input_too_long("u@x.io", "a" * 1024) is False
    assert _login_input_too_long("u@x.io", "a" * 1025) is True
    assert _login_input_too_long("u" * 255, "secret123") is True
