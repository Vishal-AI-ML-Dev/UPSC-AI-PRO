"""Unit tests for the Upstash-backed response cache (src.core.response_cache).

Network is never touched: ResponseCache._redis_command is replaced with an
in-memory dict, so these run fully offline and deterministically.
"""

from __future__ import annotations

from src.core.response_cache import ResponseCache, _cache_key, _normalize


def _mem_cache(scope: str = "thread") -> ResponseCache:
    cache = ResponseCache(
        enabled=True,
        rest_url="https://fake.upstash.io",
        rest_token="token",
        ttl_seconds=100,
        scope=scope,
    )
    store: dict[str, str] = {}

    def fake_cmd(command):
        op = command[0].upper()
        if op == "SET":
            store[command[1]] = command[2]
            return "OK"
        if op == "GET":
            return store.get(command[1])
        return None

    cache._redis_command = fake_cmd  # type: ignore[assignment]
    cache._store = store  # type: ignore[attr-defined]  # for assertions
    return cache


def test_normalize_collapses_variants():
    base = _normalize("What is Article 21?")
    assert base == _normalize("  what   is   article 21   ")
    assert base == _normalize("WHAT IS ARTICLE 21???")


def test_cache_key_scoping():
    t = _cache_key("thread", "u1", "th1", "Q?")
    assert t == _cache_key("thread", "u1", "th1", "q")  # normalized-equal
    assert t != _cache_key("thread", "u1", "th2", "Q?")  # different thread
    assert _cache_key("user", "u1", "t", "Q") != _cache_key("user", "u2", "t", "Q")
    # Same thread id from two users never shares an entry; "global" is per-user.
    assert _cache_key("thread", "u1", "th1", "Q") != _cache_key("thread", "u2", "th1", "Q")
    assert _cache_key("global", "u1", "t1", "Q") != _cache_key("global", "u2", "t2", "Q")
    assert _cache_key("global", "u1", "t1", "Q") == _cache_key("user", "u1", "t2", "Q")


def test_set_then_get_roundtrip():
    cache = _mem_cache()
    assert cache.get(user_id="u1", thread_id="t1", question="Article 21?") is None
    cache.set(
        user_id="u1",
        thread_id="t1",
        question="Article 21?",
        answer="Right to life",
        route="mentor",
    )
    hit = cache.get(user_id="u1", thread_id="t1", question="  article 21  ")
    assert hit is not None
    assert hit["answer"] == "Right to life"
    assert hit["route"] == "mentor"


def test_scope_isolates_threads():
    cache = _mem_cache()
    cache.set(user_id="u1", thread_id="t1", question="Q", answer="A")
    assert cache.get(user_id="u1", thread_id="t2", question="Q") is None


def test_disabled_cache_is_noop():
    cache = ResponseCache(enabled=False, rest_url="", rest_token="")
    cache.set(user_id="u1", thread_id="t1", question="Q", answer="A")
    assert cache.get(user_id="u1", thread_id="t1", question="Q") is None


def test_empty_answer_not_cached():
    cache = _mem_cache()
    cache.set(user_id="u1", thread_id="t1", question="Q", answer="")
    assert cache._store == {}  # type: ignore[attr-defined]
    assert cache.get(user_id="u1", thread_id="t1", question="Q") is None


# --- semantic cache tests (Step 21) ---------------------------------------


def _mem_semantic_cache(threshold: float = 0.8) -> ResponseCache:
    """In-memory ResponseCache in semantic mode with a deterministic embedder."""

    def emb(text: str):
        t = text.lower()
        return [
            1.0 if "article" in t else 0.0,
            1.0 if "21" in t else 0.0,
            1.0 if "gst" in t else 0.0,
        ]

    cache = ResponseCache(
        enabled=True,
        rest_url="https://fake.upstash.io",
        rest_token="token",
        ttl_seconds=100,
        scope="global",
        semantic=True,
        semantic_threshold=threshold,
        embed_fn=emb,
    )
    kv: dict[str, str] = {}
    lists: dict[str, list] = {}

    def fake_cmd(command):
        op = command[0].upper()
        if op == "SET":
            kv[command[1]] = command[2]
            return "OK"
        if op == "GET":
            return kv.get(command[1])
        if op == "LPUSH":
            lst = lists.setdefault(command[1], [])
            for v in command[2:]:
                lst.insert(0, v)
            return len(lst)
        if op == "LTRIM":
            lst = lists.get(command[1], [])
            start, stop = int(command[2]), int(command[3])
            lists[command[1]] = lst[start:] if stop == -1 else lst[start : stop + 1]
            return "OK"
        if op == "LRANGE":
            return list(lists.get(command[1], []))
        return None

    cache._redis_command = fake_cmd  # type: ignore[assignment]
    return cache


def test_semantic_hit_on_paraphrase():
    cache = _mem_semantic_cache()
    cache.set(
        user_id="u1",
        thread_id="t1",
        question="What is Article 21?",
        answer="Right to life",
        route="mentor",
    )
    # Different wording, same meaning -> exact miss, semantic hit.
    hit = cache.get(user_id="u1", thread_id="t1", question="explain article 21")
    assert hit is not None
    assert hit["answer"] == "Right to life"
    assert hit.get("cache") == "semantic"


def test_semantic_miss_on_different_topic():
    cache = _mem_semantic_cache()
    cache.set(
        user_id="u1",
        thread_id="t1",
        question="What is Article 21?",
        answer="Right to life",
        route="mentor",
    )
    # Unrelated question -> no exact key, cosine below threshold -> miss.
    assert cache.get(user_id="u1", thread_id="t1", question="What is GST?") is None


def test_semantic_off_is_exact_only():
    cache = _mem_semantic_cache()
    cache.semantic = False  # disable the semantic fallback
    cache.set(user_id="u1", thread_id="t1", question="What is Article 21?", answer="Right to life")
    # Paraphrase must NOT hit when semantic is off.
    assert cache.get(user_id="u1", thread_id="t1", question="explain article 21") is None
