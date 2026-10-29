"""Response cache backed by Upstash Redis (REST).

Skips re-running the full agent graph when the SAME (or a semantically similar)
question is asked again, cutting both latency and LLM cost for repeats/retries.

Design goals
------------
* **Zero new dependency** - talks to Upstash's REST endpoint with ``httpx``
  (already a project dependency). No local Redis server, so it is safe on a
  small (8 GB) box and on free-tier hosting.
* **Fail-open** - any cache error (network, timeout, bad payload, embedding
  failure) is swallowed and treated as a miss, so the cache can never break a
  chat request.
* **No-op when unconfigured** - if ``RESPONSE_CACHE_ENABLED`` is false or the
  Upstash creds are empty, every call is a cheap no-op and behaviour is
  identical to before (so existing tests / deployments are unaffected).
* **Correctness-first scoping** - the cache key includes the conversation
  scope (thread by default), so a cached answer is only ever reused for the
  same question in the same context. This avoids serving one user's
  personalised answer to another, or leaking context across conversations.

Lookup is two-stage:
    1. **Exact match** (fast, always on) - normalised-question SHA-256 key.
    2. **Semantic match** (opt-in via ``RESPONSE_CACHE_SEMANTIC``) - on an
       exact miss, embed the question and cosine-compare it against a small
       per-scope index of recent questions; a hit above the threshold reuses
       that stored answer. This makes "What is Article 21?" and "explain
       article 21" share one cached answer, lifting the hit-rate a lot.

Scope options (``RESPONSE_CACHE_SCOPE``):
    "thread" (default) - key per (user, thread, question); safest, reuses on retries
    "user"             - key per (user, question); reuse across a user's threads
    "global"           - NOT shared across users: answers are personalised, so it
                         is treated as "user" (a warning is logged)

Every key includes the user, so an entry is never served to a different user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Any, Callable, List, Optional

from src.core.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
# Trailing chars stripped during normalisation (ASCII punctuation + Devanagari danda).
_TRAILING = " ?!.\u0964"
# Key-format version. v2 made every scope user-scoped; v1 entries are never read.
_KEY_VERSION = "v2"


def _normalize(question: str) -> str:
    """Normalise a question so trivial variants map to one cache key.

    Lowercases, collapses runs of whitespace, and strips trailing punctuation
    so "What is Article 21?" and "  what is article 21 " collide.
    """
    q = (question or "").strip().lower()
    q = _WS_RE.sub(" ", q)
    return q.rstrip(_TRAILING)


def _digest(*parts: str) -> str:
    """Unambiguous digest of several identifiers (JSON-encoded before hashing)."""
    return hashlib.sha256(json.dumps(list(parts)).encode("utf-8")).hexdigest()[:32]


def _scope_part(scope: str, user_id: str, thread_id: str) -> str:
    """Return the scope discriminator used in both cache and index keys.

    Always includes the user: "global" is treated as "user" so one user's
    personalised answer can never be served to another.
    """
    if scope in ("user", "global"):
        return f"u:{_digest(user_id)}"
    return f"t:{_digest(user_id, thread_id)}"  # "thread" (default / fallback)


def _cache_key(scope: str, user_id: str, thread_id: str, question: str) -> str:
    """Build a stable, collision-resistant cache key for the given scope."""
    digest = hashlib.sha256(_normalize(question).encode("utf-8")).hexdigest()
    return f"respcache:{_KEY_VERSION}:{_scope_part(scope, user_id, thread_id)}:{digest}"


def _index_key(scope: str, user_id: str, thread_id: str) -> str:
    """Redis list key holding the per-scope semantic index."""
    return f"respcache:idx:{_KEY_VERSION}:{_scope_part(scope, user_id, thread_id)}"


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on any degeneracy)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _record_cache(kind: str) -> None:
    """Fail-open bridge to the Cost dashboard usage tracker."""
    try:
        from src.core.usage_tracker import record_cache

        record_cache(kind)
    except Exception:
        pass


_redis_breaker: Optional[CircuitBreaker] = None


def _get_redis_breaker() -> CircuitBreaker:
    """Lazily build the shared cache-Redis circuit breaker from settings."""
    global _redis_breaker
    if _redis_breaker is None:
        from src.core.config import settings

        _redis_breaker = CircuitBreaker(
            fail_max=settings.circuit_breaker_fail_max,
            reset_timeout=settings.circuit_breaker_reset_seconds,
            name="cache-redis",
        )
    return _redis_breaker


class ResponseCache:
    """Thin Upstash-Redis-REST cache for final agent answers."""

    def __init__(
        self,
        *,
        enabled: bool,
        rest_url: str,
        rest_token: str,
        ttl_seconds: int = 86400,
        scope: str = "thread",
        timeout: float = 2.0,
        semantic: bool = False,
        semantic_threshold: float = 0.92,
        semantic_max_index: int = 200,
        embed_fn: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        self.rest_url = (rest_url or "").rstrip("/")
        self.rest_token = rest_token or ""
        # Only truly enabled when switched on AND fully configured.
        self.enabled = bool(enabled and self.rest_url and self.rest_token)
        self.ttl_seconds = ttl_seconds
        if scope == "global":
            logger.warning(
                "RESPONSE_CACHE_SCOPE=global would share answers across users; "
                "using per-user scope instead."
            )
            scope = "user"
        self.scope = scope if scope in ("thread", "user") else "thread"
        self.timeout = timeout
        self.semantic = bool(semantic)
        self.semantic_threshold = float(semantic_threshold)
        self.semantic_max_index = int(semantic_max_index)
        self._embed_fn = embed_fn

    # -- embeddings -------------------------------------------------------
    def _embed(self, text: str) -> Optional[List[float]]:
        """Return an embedding vector, or None on any failure (fail-open).

        The project's Gemini embedder is imported lazily so this module stays
        import-safe and unit-testable without the model/API key loaded. Tests
        inject a deterministic ``embed_fn`` instead.
        """
        try:
            if self._embed_fn is None:
                from src.core.vector_store import get_embeddings

                self._embed_fn = get_embeddings().embed_query
            return self._embed_fn(text)
        except Exception:  # fail-open: no embedding == no semantic match
            logger.debug("response cache embed failed", exc_info=True)
            return None

    # -- public API -------------------------------------------------------
    def get(self, *, user_id: str, thread_id: str, question: str) -> Optional[dict]:
        """Return the cached ``{"answer", "route"}`` dict, or None on miss.

        Tries an exact-match lookup first, then (if enabled) a semantic one.
        """
        if not self.enabled:
            _record_cache("skip")
            return None
        key = _cache_key(self.scope, user_id, thread_id, question)
        hit = self._get_raw(key)
        if hit is not None:
            _record_cache("hit_exact")
            return hit
        if self.semantic:
            result = self._semantic_get(user_id, thread_id, question)
            _record_cache("hit_semantic" if result is not None else "miss")
            return result
        _record_cache("miss")
        return None

    def _get_raw(self, key: str) -> Optional[dict]:
        """GET one cache key and decode it into a valid answer dict, or None."""
        try:
            raw = self._redis_command(["GET", key])
        except Exception:  # fail-open: any error == miss
            logger.debug("response cache GET failed", exc_info=True)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(data, dict) and data.get("answer"):
            return data
        return None

    def _semantic_get(self, user_id: str, thread_id: str, question: str) -> Optional[dict]:
        """Embedding-similarity fallback: reuse the nearest stored answer."""
        try:
            q_emb = self._embed(_normalize(question))
            if not q_emb:
                return None
            idx_key = _index_key(self.scope, user_id, thread_id)
            raw_entries = self._redis_command(["LRANGE", idx_key, "0", "-1"]) or []
            best_key: Optional[str] = None
            best_sim = 0.0
            for raw in raw_entries:
                try:
                    entry = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                sim = _cosine(q_emb, entry.get("emb") or [])
                if sim > best_sim:
                    best_sim, best_key = sim, entry.get("key")
            if best_key and best_sim >= self.semantic_threshold:
                data = self._get_raw(best_key)
                if data is not None:
                    data = dict(data)
                    data["cache"] = "semantic"
                    data["similarity"] = round(best_sim, 4)
                    return data
        except Exception:  # fail-open
            logger.debug("response cache semantic GET failed", exc_info=True)
        return None

    def set(
        self,
        *,
        user_id: str,
        thread_id: str,
        question: str,
        answer: str,
        route: Optional[str] = None,
    ) -> None:
        """Store a final answer under the (scope, question) key with a TTL."""
        if not self.enabled or not answer:
            return
        key = _cache_key(self.scope, user_id, thread_id, question)
        payload = json.dumps({"answer": answer, "route": route})
        try:
            self._redis_command(["SET", key, payload, "EX", str(self.ttl_seconds)])
        except Exception:  # fail-open: never break the request path
            logger.debug("response cache SET failed", exc_info=True)
            return
        if self.semantic:
            self._semantic_index(user_id, thread_id, question, key)

    def _semantic_index(self, user_id: str, thread_id: str, question: str, key: str) -> None:
        """Best-effort: append this question's embedding to the scope index."""
        try:
            emb = self._embed(_normalize(question))
            if not emb:
                return
            idx_key = _index_key(self.scope, user_id, thread_id)
            entry = json.dumps({"key": key, "emb": [round(float(x), 6) for x in emb]})
            self._redis_command(["LPUSH", idx_key, entry])
            # Cap the index so it stays small and cheap to scan.
            self._redis_command(["LTRIM", idx_key, "0", str(self.semantic_max_index - 1)])
        except Exception:  # fail-open: indexing never breaks a request
            logger.debug("response cache semantic index failed", exc_info=True)

    # -- transport --------------------------------------------------------
    def _redis_command(self, command: list) -> Any:
        """Execute one Redis command via the Upstash REST endpoint.

        Upstash accepts a command as a JSON array in the POST body and replies
        with ``{"result": ...}``. httpx is imported lazily so this module stays
        import-safe (and unit-testable) without the dependency loaded.
        """
        import httpx

        breaker = _get_redis_breaker()
        if not breaker.allow():
            raise RuntimeError("Upstash breaker OPEN (cache)")
        try:
            resp = httpx.post(
                self.rest_url,
                json=command,
                headers={"Authorization": f"Bearer {self.rest_token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json().get("result")
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        return result


_cache_singleton: Optional[ResponseCache] = None


def get_response_cache() -> ResponseCache:
    """Return the process-wide cache singleton (built from settings)."""
    global _cache_singleton
    if _cache_singleton is None:
        from src.core.config import settings

        _cache_singleton = ResponseCache(
            enabled=settings.response_cache_enabled,
            rest_url=settings.upstash_redis_rest_url,
            rest_token=settings.upstash_redis_rest_token,
            ttl_seconds=settings.response_cache_ttl_seconds,
            scope=settings.response_cache_scope,
            semantic=settings.response_cache_semantic,
            semantic_threshold=settings.response_cache_semantic_threshold,
            semantic_max_index=settings.response_cache_semantic_max_index,
        )
        if _cache_singleton.enabled:
            logger.info(
                "Response cache ON (scope=%s, ttl=%ss, semantic=%s)",
                _cache_singleton.scope,
                _cache_singleton.ttl_seconds,
                _cache_singleton.semantic,
            )
    return _cache_singleton


def reset_response_cache() -> None:
    """Drop the singleton so the next call rebuilds it from settings."""
    global _cache_singleton, _redis_breaker
    _cache_singleton = None
    _redis_breaker = None
