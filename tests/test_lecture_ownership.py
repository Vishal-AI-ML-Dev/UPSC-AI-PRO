"""Phase 02 — pasted-transcript lecture ownership (spec AC5).

Pasted transcripts are user-owned (``lecture_<uid>_paste_<hash>``); YouTube
lectures stay global (``lecture_<video_id>``). The legacy global paste key is
never read, and ``video_id`` is format-validated so a client cannot forge a
key that collides with another user's collection.
"""

import pytest

import src.agents.lecture.graph as lg
import src.core.vector_store as vector_store

API = "/api/v1"
PASTE_ID = "paste_0123456789a"
YT_ID = "dQw4w9WgXcQ"
NOT_PROCESSED = "Please process the lecture first before chatting."


@pytest.fixture
def lookups(monkeypatch):
    seen: list[str] = []
    existing: set[str] = set()

    def fake_load(key):
        seen.append(key)
        return object() if key in existing else None

    monkeypatch.setattr(vector_store, "load_vector_store", fake_load)
    monkeypatch.setattr(lg, "similarity_search", lambda db, q, k, label: "LEAKED-EXCERPT")
    return seen, existing


def _chat(client, user, video_id):
    return client.post(
        f"{API}/lecture/chat",
        json={"question": "What is the Gram Sabha?", "video_id": video_id},
        headers=user.headers,
    )


def test_other_user_cannot_chat_over_pasted_lecture(client, make_user, lookups):
    seen, existing = lookups
    a, b = make_user("lec_owner"), make_user("lec_intruder")
    existing.add(f"lecture_{a.id}_{PASTE_ID}".lower())

    r = _chat(client, b, PASTE_ID)

    assert r.status_code == 200
    assert r.text == NOT_PROCESSED
    assert seen == [f"lecture_{b.id}_{PASTE_ID}".lower()]


def test_legacy_global_paste_key_is_never_read(client, make_user, lookups):
    seen, existing = lookups
    b = make_user("lec_legacy")
    existing.add(f"lecture_{PASTE_ID}")  # pre-Phase-02 collection still in Qdrant

    r = _chat(client, b, PASTE_ID)

    assert r.text == NOT_PROCESSED
    assert f"lecture_{PASTE_ID}" not in seen


def test_forged_scoped_video_id_is_rejected(client, make_user, lookups):
    seen, _ = lookups
    a, b = make_user("lec_a"), make_user("lec_b")
    r = _chat(client, b, f"{a.id}_{PASTE_ID}")
    assert r.status_code == 422
    assert seen == []


@pytest.mark.parametrize("bad", ["v", "x" * 12, "paste_XYZ", "paste_0123456789", "../etc/passwd1"])
def test_video_id_format_is_validated(client, make_user, lookups, bad):
    seen, _ = lookups
    user = make_user("lec_fmt")
    r = _chat(client, user, bad)
    assert r.status_code == 422
    assert seen == []


def test_youtube_lecture_stays_global(client, make_user, lookups):
    seen, _ = lookups
    user = make_user("lec_yt")
    r = _chat(client, user, YT_ID)
    assert r.status_code == 200
    assert seen == [f"lecture_{YT_ID}".lower()]


def test_ask_lecture_paste_without_user_reads_nothing(lookups):
    seen, _ = lookups
    out = "".join(lg.ask_lecture("q?", PASTE_ID))
    assert out == NOT_PROCESSED
    assert seen == []


def test_index_builds_user_scoped_key_for_paste(monkeypatch):
    created: list[str] = []
    monkeypatch.setattr(lg, "vector_store_exists", lambda key: False)
    monkeypatch.setattr(
        lg, "create_vector_store", lambda text, persist_key, **kw: created.append(persist_key)
    )
    lg.build_lecture_chat_index(PASTE_ID, "transcript text", user_id="user123")
    lg.build_lecture_chat_index(PASTE_ID, "transcript text")  # no user -> not indexed
    lg.build_lecture_chat_index(YT_ID, "transcript text", user_id="user123")
    assert created == [f"lecture_user123_{PASTE_ID}", f"lecture_{YT_ID}".lower()]
