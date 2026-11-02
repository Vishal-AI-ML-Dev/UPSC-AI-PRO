"""Phase 02 — uploaded-document ownership (spec AC1, AC2, AC3).

Uploaded PDFs are user-owned: the vector key carries the owner id, the result
cache is per user and bounded, and nothing ever falls back to the legacy global
``upload_<hash>`` collection. Vector store, PDF parsing and LLM are all faked.
"""

import hashlib

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.upload.graph as ug
import src.core.vector_store as vector_store

API = "/api/v1"
NOT_PROCESSED = "Please upload and process the PDF first."
DOC_TEXT = "Panchayati Raj has three tiers: village, block and district. " * 20
BOOK = "BOOK: Unknown\nAUTHOR: Unknown\nSUBJECT: Polity\nUPSC_PAPER: GS2\nCONFIDENCE: Low\nRELEVANT: yes"


def _key(user_id: str, pdf_hash: str) -> str:
    return f"upload_{user_id}_{pdf_hash}".lower()


# --------------------------------------------------------------------------- #
# AC1 — chat only ever reads the caller's own collection
# --------------------------------------------------------------------------- #
@pytest.fixture
def lookups(monkeypatch):
    """Record every vector key the chat path loads; only legacy/owner keys 'exist'."""
    seen: list[str] = []
    existing: set[str] = set()

    def fake_load(key):
        seen.append(key)
        return object() if key in existing else None

    monkeypatch.setattr(vector_store, "load_vector_store", fake_load)
    monkeypatch.setattr(ug, "similarity_search", lambda db, q, k, label: "LEAKED-EXCERPT")
    model = RunnableLambda(lambda _: AIMessage(content="LEAKED-ANSWER"))
    monkeypatch.setattr(ug, "get_llm", lambda: model)
    return seen, existing


def _chat(client, user, pdf_hash):
    return client.post(
        f"{API}/upload/chat?pdf_hash={pdf_hash}",
        json={"request": {"question": "What are the three tiers?"}},
        headers=user.headers,
    )


def test_other_user_cannot_chat_over_owner_upload(client, make_user, lookups):
    seen, existing = lookups
    a, b = make_user("owner"), make_user("intruder")
    pdf_hash = hashlib.md5(b"owner-document").hexdigest()
    existing.add(_key(a.id, pdf_hash))  # A's collection exists

    r = _chat(client, b, pdf_hash)

    assert r.status_code == 200
    assert r.text == NOT_PROCESSED
    assert seen == [_key(b.id, pdf_hash)]
    assert "LEAKED" not in r.text


def test_legacy_global_upload_key_is_never_read(client, make_user, lookups):
    seen, existing = lookups
    b = make_user("legacy")
    pdf_hash = hashlib.md5(b"legacy-document").hexdigest()
    existing.add(f"upload_{pdf_hash}")  # pre-Phase-02 global collection still in Qdrant

    r = _chat(client, b, pdf_hash)

    assert r.text == NOT_PROCESSED
    assert f"upload_{pdf_hash}" not in seen
    assert seen == [_key(b.id, pdf_hash)]


def test_owner_can_chat_over_own_upload(client, make_user, lookups):
    seen, existing = lookups
    a = make_user("self")
    pdf_hash = hashlib.md5(b"self-document").hexdigest()
    existing.add(_key(a.id, pdf_hash))

    r = _chat(client, a, pdf_hash)

    assert r.status_code == 200
    assert r.text == "LEAKED-ANSWER"  # owner gets the answer
    assert seen == [_key(a.id, pdf_hash)]


@pytest.mark.parametrize("bad", ["h", "upload_x", "0" * 31, "Z" * 32, "../" + "0" * 29])
def test_pdf_hash_must_be_32_hex(client, make_user, lookups, bad):
    seen, _ = lookups
    user = make_user("badhash")
    r = _chat(client, user, bad)
    assert r.status_code == 422
    assert seen == []


def test_ask_upload_refuses_without_user(lookups):
    seen, _ = lookups
    pdf_hash = hashlib.md5(b"x").hexdigest()
    out = "".join(ug.ask_upload("q?", pdf_hash, user_id=""))
    assert out == NOT_PROCESSED
    assert seen == []


# --------------------------------------------------------------------------- #
# AC2 / AC3 — independent per-user collections; bounded per-user cache
# --------------------------------------------------------------------------- #
@pytest.fixture
def processing(monkeypatch):
    """Fake extraction, LLM and vector writes; fresh cache."""
    ug.clear_cache()
    created: list[str] = []
    llm_calls: list[int] = []
    monkeypatch.setattr(
        ug,
        "extract_pdf_text",
        lambda content, filename: (DOC_TEXT, hashlib.md5(content).hexdigest()),
    )
    monkeypatch.setattr(
        ug, "create_vector_store", lambda text, persist_key, **kw: created.append(persist_key)
    )
    state = {"exists": True, "notes": "## Notes\n- Three tiers."}

    def fake_llm(prompt):
        llm_calls.append(1)
        return AIMessage(content=BOOK if len(llm_calls) % 2 else state["notes"])

    monkeypatch.setattr(ug, "get_llm", lambda: RunnableLambda(fake_llm))
    monkeypatch.setattr(ug, "vector_store_exists", lambda key: state["exists"], raising=False)
    yield created, llm_calls, state
    ug.clear_cache()


def test_same_file_two_users_get_independent_collections(processing):
    created, _, _ = processing
    content = b"%PDF-1.4 identical bytes"
    h = hashlib.md5(content).hexdigest()

    ra = ug.process_upload(content, "a.pdf", user_id="useraaa")
    rb = ug.process_upload(content, "b.pdf", user_id="userbbb")

    assert created == [_key("useraaa", h), _key("userbbb", h)]
    assert ra["hash"] == rb["hash"] == h


def test_cache_is_per_user_and_holds_no_text(processing):
    created, llm_calls, _ = processing
    content = b"%PDF-1.4 cached"
    h = hashlib.md5(content).hexdigest()

    ug.process_upload(content, "a.pdf", user_id="useraaa")
    calls_after_first = len(llm_calls)
    again = ug.process_upload(content, "a.pdf", user_id="useraaa")  # same user -> hit

    assert len(llm_calls) == calls_after_first  # served from cache
    assert created == [_key("useraaa", h)]  # no second write
    assert "text" not in again
    assert ug.get_cached_upload("useraaa", h) is not None
    assert "text" not in ug.get_cached_upload("useraaa", h)
    assert ug.get_cached_upload("userbbb", h) is None


def test_cache_hit_reprocesses_when_collection_is_gone(processing):
    created, _, state = processing
    content = b"%PDF-1.4 vanished"
    h = hashlib.md5(content).hexdigest()

    ug.process_upload(content, "a.pdf", user_id="useraaa")
    state["exists"] = False  # e.g. Qdrant collection dropped
    ug.process_upload(content, "a.pdf", user_id="useraaa")

    assert created == [_key("useraaa", h), _key("useraaa", h)]


def test_cache_entry_count_is_bounded(processing):
    for i in range(40):
        ug.process_upload(f"%PDF-1.4 doc {i}".encode(), "d.pdf", user_id="useraaa")
    assert len(ug._upload_cache) <= 32
    assert ug._upload_cache.total_bytes <= 4 * 1024 * 1024


def test_cache_byte_budget_skips_oversized_entry(processing):
    _, _, state = processing
    state["notes"] = "x" * (5 * 1024 * 1024)  # one entry larger than the 4 MiB budget
    content = b"%PDF-1.4 huge notes"
    ug.process_upload(content, "big.pdf", user_id="useraaa")
    assert ug.get_cached_upload("useraaa", hashlib.md5(content).hexdigest()) is None
    assert ug._upload_cache.total_bytes <= 4 * 1024 * 1024


def test_cache_byte_budget_evicts_oldest(processing):
    _, _, state = processing
    state["notes"] = "y" * (1024 * 1024)  # ~1 MiB per entry -> at most 4 fit
    hashes = []
    for i in range(6):
        content = f"%PDF-1.4 mb {i}".encode()
        hashes.append(hashlib.md5(content).hexdigest())
        ug.process_upload(content, "m.pdf", user_id="useraaa")
    assert ug._upload_cache.total_bytes <= 4 * 1024 * 1024
    assert ug.get_cached_upload("useraaa", hashes[0]) is None  # oldest evicted
    assert ug.get_cached_upload("useraaa", hashes[-1]) is not None


# --------------------------------------------------------------------------- #
# Review SHOULD-FIX 1 — the cache is safe under concurrent background jobs
# --------------------------------------------------------------------------- #
def test_cache_is_consistent_under_concurrent_access():
    import threading

    cache = ug._UploadCache(max_entries=32, max_bytes=64 * 1024)
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def worker(n: int):
        try:
            start.wait()
            for i in range(400):
                h = f"{(n * 1000 + i) % 50:032x}"  # overlapping keys across threads
                user = f"user{n % 3}"
                cache.put(user, h, {"notes": "n" * (100 + (i % 7) * 900), "hash": h})
                cache.get(user, h)
                if i % 5 == 0:
                    cache.discard(user, h)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    entries, tracked, recomputed = cache.snapshot()
    assert entries <= 32
    assert tracked == recomputed  # byte accounting never drifts
    assert 0 <= tracked <= 64 * 1024


def test_cache_lock_not_held_during_extraction_or_vector_work(processing, monkeypatch):
    created, _, _ = processing
    held: list[bool] = []

    def extract(content, filename):
        held.append(ug._upload_cache._lock.locked())
        return DOC_TEXT, hashlib.md5(content).hexdigest()

    def create(text, persist_key, **kw):
        held.append(ug._upload_cache._lock.locked())
        created.append(persist_key)

    monkeypatch.setattr(ug, "extract_pdf_text", extract)
    monkeypatch.setattr(ug, "create_vector_store", create)
    ug.process_upload(b"%PDF-1.4 lock check", "l.pdf", user_id="useraaa")
    assert held == [False, False]
