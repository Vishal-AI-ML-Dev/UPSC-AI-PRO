"""Retrieval-quality benchmark for UPSC-AI-PRO (Phase 06, Stage 1: baseline only).

Measures the retrieval paths production actually runs, on a labelled gold
dataset (``src/eval/retrieval_gold.json``), and compares candidate variants on
the SAME dense candidate pool, so that "re-ordering an unchanged candidate set"
and "fetching a larger candidate set before re-ranking" are never confused.

Corpora (each indexed with its workflow's production chunking + ingest code):
  mentor_kb      ``data/mentor_kb/**`` via ``mentor_kb.build_kb`` (topper ingest)
                 -> production search ``mentor_kb.search_kb`` (dense, k=4, 0.25)
  upload_style   synthetic polity notes via ``create_vector_store``
                 -> ``similarity_search`` (dense + RRF re-order, k=5, 0.3)
  lecture_style  synthetic transcript via ``create_vector_store``
                 -> ``similarity_search`` (k=5, 0.3)
  pyq_style      synthetic practice paper, ``build_question_bank`` chunking
                 -> ``similarity_search`` (k=8, 0.3)
  ncert          owner-supplied chapters only (empty until labelled; Q1 = b)

Candidate pools (review Q2): per query ONE exact dense search of POOL_SIZE (16)
candidates is the shared pool. Every ranking below is derived from it. Two
independent searches, at k and at 2k, are what production would issue; they
are VERIFIED equal to the pool prefixes (``topk_matches_pool``,
``overfetch_matches_pool``) and timed for latency.
  dense_k        pool[:k] in vector order (no lexical step)
  production     the workflow's REAL production function (review Q1):
                 mentor_kb -> ``mentor_kb.search_kb`` (dense only);
                 upload / lecture / pyq_bank -> ``similarity_search``;
                 ncert -> ``similarity_search_with_sources``.
                 Its output is compared with the reconstruction from the pool
                 every query (``production_consistent``).
  rrf_same_k     RRF re-order of pool[:k] (= production for the shared pipeline)
  overfetch_2k   RRF over pool[:2k], keep k (a LARGER candidate set)
  dense_10 / rrf_10   pool[:10] orderings for Recall@10 / NDCG@10 / headroom

Comparisons (review Q3): ``overfetch_2k`` vs ``rrf_same_k`` isolates the effect
of the larger pool. ``overfetch_2k`` vs ``production`` is a COMBINED change for
the mentor KB (dense-only production), so the mentor KB and the shared-pipeline
corpora are also summarised separately. Pooled "@k" metrics are means over each
corpus's own production k (mentor 4, upload/lecture 5, PYQ 8) (review Q4).

NDCG (review Q5): the ideal DCG is the best achievable ordering of the corpus's
real chunks (several quotes can share one chunk); ``ndcg_legacy@k`` keeps the
Stage 1 definition (one quote per rank, capped at 1.0) for comparability.

Exit codes: 0 ok / dry run, 2 preflight failed, 3 dataset or corpus path
invalid, 4 plan exceeds budget (nothing sent), 5 budget reached mid-run,
6 provider or runtime error (no retry), 7 cache miss in ``--cache-only`` mode.

Isolation and API guardrails (Phase 06 approval):
  * ``QDRANT_URL`` is blanked and ``QDRANT_PATH`` points at a fresh temp dir
    before any ``src`` import; no production or user collection is reachable.
  * Multi-query, HyDE and the cross-encoder are forced OFF: no LLM generation
    call is possible. Langfuse / LangSmith tracing are off.
  * Every embedding goes through a cache keyed by (model, dim, kind, sha256 of
    the text) and a hard budget: at most ``--max-requests`` (default 500)
    embedding requests and ``--max-tokens`` (default 1,000,000) estimated input
    tokens. Requests are counted conservatively (one per text).
  * A live run first prints the model, the request count, an estimated token
    count and cost, then stops unless ``--confirm-live`` is given.
  * The API key is read by the app settings from the local environment and is
    never printed.

Usage (repo root)::

    uv run python scripts/bench_retrieval.py --fake-embeddings          # offline, no network
    uv run python scripts/bench_retrieval.py                            # live dry run: estimate only
    uv run python scripts/bench_retrieval.py --confirm-live             # live run (Gemini embeddings)
    uv run python scripts/bench_retrieval.py --cache-only               # offline re-run from the cache

``--cache-only`` uses the real model's cached vectors and NEVER calls the API:
the provider client is not created, the API key is blanked, and any uncached
text stops the run with exit code 7. The cache file is not rewritten. Offline
modes also patch ``socket.socket.connect`` so outbound connection attempts
fail; this is a guard, not a complete network sandbox (``connect_ex``, DNS,
UDP and subprocesses are not intercepted).

Corpus paths in the dataset (review S1) must be relative paths inside
``src/eval/corpora/`` or ``data/mentor_kb/``. Each path is resolved (symlinks
and junctions followed) and must stay inside its approved root; secret or
config files and ``data/ncert/`` are refused before anything is read. Every
mentor-KB file the topper-ingest script opens, and every file hashed for the
report, is validated individually against ``data/mentor_kb/`` before it is read
(review SF-1). Hard links cannot be detected.

Cleanup (review S2): the temp work dir (embedded Qdrant) is removed after the
client is closed, on normal completion and on handled exceptions. A hard
process kill can leave it behind under the OS temp dir.

Reports go to the OS temp dir by default (outside the repo, never committed).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "src" / "eval" / "retrieval_gold.json"
DEFAULT_OUT_DIR = Path(tempfile.gettempdir()) / "upsc_retrieval_bench"
DEFAULT_MAX_REQUESTS = 500
DEFAULT_MAX_TOKENS = 1_000_000
CHARS_PER_TOKEN_ESTIMATE = 3.0  # conservative (real English ~4 chars/token)
# Published paid-tier price for gemini-embedding-001 input tokens, USD per 1M
# (assumption; verify the current price - the free tier costs nothing).
USD_PER_MILLION_TOKENS = 0.15
WORK_DIR_PREFIX = "upsc_bench_retrieval_"
POOL_SIZE = 16  # >= 2k for every corpus (pyq k=8) and >= 10 for @10 metrics
FAKE_DIM = 64

# Environment forced BEFORE importing ``src`` (CLI path). Tests use the
# in-process ``isolated_vector_store`` context manager instead.
_FORCED_ENV = {
    "QDRANT_URL": "",
    "LANGFUSE_ENABLED": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "MULTI_QUERY_ENABLED": "false",
    "HYDE_ENABLED": "false",
    "RERANK_ENABLED": "false",
    "SENTRY_DSN": "",
}


class PreflightError(RuntimeError):
    """A required library or configuration is missing; nothing was sent anywhere."""


class BudgetExceeded(RuntimeError):
    """The approved embedding request/token budget would be exceeded."""


class CacheMissError(RuntimeError):
    """``--cache-only``: a text was not in the embedding cache (no API fallback)."""


class CorpusPathError(ValueError):
    """A dataset corpus path is outside the approved roots or names a forbidden file."""


# Review S1: the only places a benchmark corpus may be read from.
ALLOWED_CORPUS_ROOTS = ("src/eval/corpora", "data/mentor_kb")
FORBIDDEN_ROOTS = ("data/ncert",)
_FORBIDDEN_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".der",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".env",
    ".ini",
    ".toml",
    ".cfg",
    ".yaml",
    ".yml",
    ".json",
}
_FORBIDDEN_NAME_PARTS = (
    "secret",
    "credential",
    "password",
    "token",
    "cookie",
    "id_rsa",
    "apikey",
)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_corpus_path(
    rel: str, *, allow_dir: bool = False, roots: tuple[str, ...] = ALLOWED_CORPUS_ROOTS
) -> Path:
    """Resolve a dataset corpus path and refuse anything outside the approved roots.

    Checks happen BEFORE any read: relative path only, no ``..``, the resolved
    path (symlinks and junctions followed) must stay inside one of ``roots``,
    never inside ``data/ncert/``, and secret / config file names are refused.
    Hard links cannot be detected by path resolution.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise CorpusPathError("corpus path is empty")
    raw = Path(rel)
    if raw.is_absolute() or raw.drive or rel.startswith(("/", "\\")):
        raise CorpusPathError(f"corpus path must be relative to the repo: {rel!r}")
    if any(part == ".." for part in raw.parts):
        raise CorpusPathError(f"corpus path must not contain '..': {rel!r}")
    repo = REPO_ROOT.resolve()
    resolved = (repo / raw).resolve()
    for forbidden in FORBIDDEN_ROOTS:
        if _is_within(resolved, (repo / forbidden).resolve()):
            raise CorpusPathError(f"corpus path is inside a protected folder: {rel!r}")
    if not any(_is_within(resolved, (repo / r).resolve()) for r in roots):
        raise CorpusPathError(f"corpus path {rel!r} resolves outside the approved roots {roots}")
    for part in resolved.relative_to(repo).parts:
        low = part.lower()
        if low.startswith(".env") or any(x in low for x in _FORBIDDEN_NAME_PARTS):
            raise CorpusPathError(f"corpus path names a forbidden file: {rel!r}")
    if resolved.suffix.lower() in _FORBIDDEN_SUFFIXES:
        raise CorpusPathError(f"corpus file type is not allowed: {rel!r}")
    if not resolved.exists():
        raise CorpusPathError(f"corpus path does not exist: {rel!r}")
    if resolved.is_dir() and not allow_dir:
        raise CorpusPathError(f"corpus path must be a file: {rel!r}")
    return resolved


MENTOR_KB_ROOT = "data/mentor_kb"


def mentor_kb_file(path: str | os.PathLike) -> Path:
    """Validate ONE mentor-KB file (review SF-1) and return its resolved path.

    The path may be repo-relative (as the topper-ingest script builds it) or an
    absolute path under the repo; symlinks are NOT followed before the check.
    It must resolve inside ``data/mentor_kb/`` and pass the corpus file policy.
    """
    raw = Path(path)
    if raw.is_absolute():
        try:
            raw = raw.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise CorpusPathError(f"mentor KB file is outside the repo: {str(path)!r}") from exc
    return resolve_corpus_path(raw.as_posix(), roots=(MENTOR_KB_ROOT,))


def validate_corpus_paths(dataset: dict) -> list[str]:
    """Check every corpus path in the dataset (all corpora, used or not) before any read."""
    errors: list[str] = []
    for name, spec in (dataset.get("corpora") or {}).items():
        kind = spec.get("kind")
        if kind == "ncert":
            continue  # chapters are read by the app's own NCERT loader (owner-supplied)
        if kind not in ("mentor_kb", "upload", "lecture", "pyq_bank"):
            errors.append(f"corpus {name!r}: unknown kind {kind!r}")
            continue
        try:
            resolved = resolve_corpus_path(spec.get("path", ""), allow_dir=kind == "mentor_kb")
        except CorpusPathError as exc:
            errors.append(f"corpus {name!r}: {exc}")
            continue
        if kind == "mentor_kb" and resolved != (REPO_ROOT / "data" / "mentor_kb").resolve():
            errors.append(f"corpus {name!r}: mentor_kb path must be exactly 'data/mentor_kb'")
    return errors


class CacheOnlyInner:
    """Provider stand-in for ``--cache-only``: any uncached text is a hard stop."""

    def embed_documents(self, texts, **kwargs):
        raise CacheMissError(f"{len(list(texts))} document text(s) not in the embedding cache")

    def embed_query(self, text, **kwargs):
        raise CacheMissError("a query text is not in the embedding cache")


@contextlib.contextmanager
def network_blocked():
    """Make any outbound socket connection fail (offline / cache-only runs)."""
    import socket

    original = socket.socket.connect

    def _refuse(*args, **kwargs):
        raise ConnectionRefusedError("network disabled in offline benchmark mode")

    socket.socket.connect = _refuse
    try:
        yield
    finally:
        socket.socket.connect = original


# --------------------------------------------------------------------------- #
# Embeddings: cache + hard budget (wraps the provider client, inside the pin)
# --------------------------------------------------------------------------- #
def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE))


def _text_key(kind: str, text: str, model_id: str) -> str:
    return f"{model_id}|{kind}|{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


class BudgetedCachingEmbeddings:
    """Cache by unique input text and refuse to exceed the request/token budget.

    The budget is checked BEFORE a provider call: if the uncached texts of a call
    would push the totals over a limit, ``BudgetExceeded`` is raised and nothing
    is sent. Usage is a LEDGER stored in the cache file, so the limits cover every
    live run that shares the cache, not each run separately. A failed provider
    call stays charged (conservative). ``max_texts_per_minute`` paces calls to
    stay under a provider's per-minute quota (Gemini free tier counts each text).
    """

    def __init__(
        self,
        inner: Any,
        *,
        model_id: str,
        cache_path: Path | None,
        max_requests: int,
        max_tokens: int,
        max_texts_per_minute: int | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self._inner = inner
        self.model_id = model_id
        self.cache_path = cache_path
        self.max_requests = max_requests
        self.max_tokens = max_tokens
        self.max_texts_per_minute = max_texts_per_minute
        self._sleep = sleep
        self._clock = clock
        self._window: list[tuple[float, int]] = []
        self.requests = 0  # cumulative ledger (texts sent, all runs on this cache)
        self.tokens = 0
        self.run_requests = 0  # this run only
        self.hits = 0
        self.misses = 0
        self.call_seconds: list[float] = []
        self._cache: dict[str, list[float]] = {}
        if cache_path and cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if "vectors" in data:
                self._cache = data["vectors"]
                self.requests = int(data.get("ledger", {}).get("requests", 0))
                self.tokens = int(data.get("ledger", {}).get("tokens", 0))
            else:  # pre-ledger format: count every cached text as one request
                self._cache = data
                self.requests = len(data)

    def cached(self, kind: str, text: str) -> bool:
        return _text_key(kind, text, self.model_id) in self._cache

    def _charge(self, texts: list[str]) -> None:
        req = self.requests + len(texts)
        tok = self.tokens + sum(estimate_tokens(t) for t in texts)
        if req > self.max_requests or tok > self.max_tokens:
            raise BudgetExceeded(
                f"embedding budget reached: would use {req} requests / ~{tok} tokens "
                f"(limits {self.max_requests} / {self.max_tokens}); stopped before sending"
            )
        self.requests, self.tokens = req, tok
        self.run_requests += len(texts)

    def _pace(self, n: int) -> None:
        """Block until sending ``n`` more texts keeps the last 60 s under the limit."""
        limit = self.max_texts_per_minute
        if not limit:
            return
        while True:
            now = self._clock()
            self._window = [(t, c) for t, c in self._window if now - t < 60.0]
            if sum(c for _, c in self._window) + n <= limit or not self._window:
                self._window.append((now, n))
                return
            self._sleep(max(0.05, 60.0 - (now - self._window[0][0])))

    def _send(self, kind: str, batch: list[str], **kwargs) -> None:
        self._charge(batch)
        self._pace(len(batch))
        start = time.perf_counter()
        if kind == "query":
            vectors = [self._inner.embed_query(t, **kwargs) for t in batch]
        else:
            vectors = self._inner.embed_documents(batch, **kwargs)
        self.call_seconds.append(time.perf_counter() - start)
        for t, v in zip(batch, vectors):
            self._cache[_text_key(kind, t, self.model_id)] = list(v)

    def _embed(self, kind: str, texts: list[str], **kwargs) -> list[list[float]]:
        keys = [_text_key(kind, t, self.model_id) for t in texts]
        missing = list(dict.fromkeys(t for t, k in zip(texts, keys) if k not in self._cache))
        self.hits += len(texts) - len(missing)
        if missing:
            self.misses += len(missing)
            step = self.max_texts_per_minute or len(missing)
            for i in range(0, len(missing), step):
                self._send(kind, missing[i : i + step], **kwargs)
        return [self._cache[k] for k in keys]

    def embed_documents(self, texts, **kwargs):
        return self._embed("document", list(texts), **kwargs)

    def embed_query(self, text, **kwargs):
        return self._embed("query", [text], **kwargs)[0]

    def save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ledger": {"requests": self.requests, "tokens": self.tokens},
                "vectors": self._cache,
            }
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")


class FakeEmbeddings:
    """Offline, deterministic bag-of-words hash vectors (no network)."""

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * FAKE_DIM
        for tok in "".join(c.lower() if c.isalnum() else " " for c in text).split():
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % FAKE_DIM] += 1.0 if (h >> 8) % 2 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts, **kwargs):
        return [self._vec(t) for t in texts]

    def embed_query(self, text, **kwargs):
        return self._vec(text)


# --------------------------------------------------------------------------- #
# Preflight (R9 as approved): fail before any network request
# --------------------------------------------------------------------------- #
def preflight(*, live: bool) -> dict:
    """Check libraries and (for live runs) the embedding configuration.

    Returns a summary with booleans/names only - never the key itself.
    """
    for mod, hint in (
        ("qdrant_client", "qdrant-client"),
        ("langchain_qdrant", "langchain-qdrant"),
    ):
        if importlib.util.find_spec(mod) is None:
            raise PreflightError(
                f"'{mod}' is not importable. It is a project dependency ({hint}); run `uv sync` "
                "in the repo root, then retry. Nothing was sent to any API."
            )
    from src.core.config import settings

    info = {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dim": int(settings.gemini_embedding_dim),
        "api_key_configured": bool(settings.google_api_key),
    }
    if not live:
        return info
    if (settings.embedding_provider or "").strip().lower() != "gemini":
        raise PreflightError(
            f"EMBEDDING_PROVIDER is '{settings.embedding_provider}'. The approved live run covers "
            "Gemini embeddings only; stopping before any request."
        )
    if not settings.embedding_model or int(settings.gemini_embedding_dim) <= 0:
        raise PreflightError(
            "Embedding model or GEMINI_EMBEDDING_DIM is not set; the token/cost estimate would be "
            "unclear, so the run stops before any request."
        )
    if not settings.google_api_key:
        raise PreflightError(
            "GOOGLE_API_KEY is not configured in the local environment (.env). Add it there "
            "yourself; never paste it into chat. Nothing was sent."
        )
    if importlib.util.find_spec("langchain_google_genai") is None:
        raise PreflightError("'langchain_google_genai' is not importable; run `uv sync`.")
    return info


# --------------------------------------------------------------------------- #
# Isolation: point the app's vector layer at a temp embedded Qdrant
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def isolated_vector_store(qdrant_dir: Path, embeddings: Any):
    """Temporarily route ``src.core.vector_store`` to an embedded Qdrant under
    ``qdrant_dir`` with ``embeddings``; restore everything afterwards."""
    import src.core.vector_store as vs
    from src.core.config import settings

    names = (
        "qdrant_url",
        "qdrant_path",
        "multi_query_enabled",
        "hyde_enabled",
        "rerank_enabled",
        "langfuse_enabled",
    )
    saved = {n: getattr(settings, n) for n in names}
    saved_vs = (vs._qdrant_client_instance, vs._embeddings_instance, vs._embed_dim_cache)
    try:
        settings.qdrant_url = ""
        settings.qdrant_path = str(qdrant_dir)
        settings.multi_query_enabled = False
        settings.hyde_enabled = False
        settings.rerank_enabled = False
        settings.langfuse_enabled = False
        vs._qdrant_client_instance = None
        vs._embeddings_instance = embeddings
        vs._embed_dim_cache = None
        if not vs._qdrant_local():
            raise PreflightError("isolation failed: vector store is not in embedded local mode")
        yield vs
    finally:
        client = vs._qdrant_client_instance
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
        for n, v in saved.items():
            setattr(settings, n, v)
        vs._qdrant_client_instance, vs._embeddings_instance, vs._embed_dim_cache = saved_vs


# --------------------------------------------------------------------------- #
# Dataset + corpora (production chunking, computed without any embedding)
# --------------------------------------------------------------------------- #
def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "queries" not in data or "corpora" not in data:
        raise ValueError(f"{path}: expected an object with 'corpora' and 'queries'")
    return data


def _mentor_sources() -> list[dict]:
    """The topper-ingest sources exactly as ``scripts/ingest_topper_md.py`` builds them.

    Review SF-1: the ingest script's ``open`` is replaced, for this call only,
    by a guard that validates every file it opens (``mentor_kb_file``) before
    the file is opened, so no file outside ``data/mentor_kb/`` is ever read.
    """
    import builtins

    spec = importlib.util.spec_from_file_location(
        "_ingest_topper_md", REPO_ROOT / "scripts" / "ingest_topper_md.py"
    )
    module = importlib.util.module_from_spec(spec)

    def _guarded_open(file, *args, **kwargs):
        return builtins.open(mentor_kb_file(file), *args, **kwargs)

    cwd = os.getcwd()
    try:
        os.chdir(REPO_ROOT)  # the ingest script uses repo-relative paths
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
            module.open = _guarded_open  # module global shadows the builtin
            return module.collect_sources()
    finally:
        os.chdir(cwd)


def _read(rel: str) -> str:
    return resolve_corpus_path(rel).read_text(encoding="utf-8")


# Production retrieval parameters per corpus kind (verified in the spec, §3.2).
def _production_params(kind: str) -> dict:
    from src.core.mentor_kb import KB_THRESHOLD
    from src.core.vector_store import SIMILARITY_THRESHOLD

    if kind == "mentor_kb":
        return {"k": 4, "threshold": KB_THRESHOLD, "reorder": False}
    if kind == "pyq_bank":
        return {"k": 8, "threshold": SIMILARITY_THRESHOLD, "reorder": True}
    return {"k": 5, "threshold": SIMILARITY_THRESHOLD, "reorder": True}


def corpus_documents(name: str, spec: dict) -> list:
    """Chunk a corpus the way its workflow's ingest code does (no embedding)."""
    from langchain_core.documents import Document

    from src.core.vector_store import get_text_splitter

    kind = spec["kind"]
    if kind == "mentor_kb":
        from src.core.mentor_kb import _make_docs

        resolve_corpus_path(spec.get("path", ""), allow_dir=True)
        return _make_docs(_mentor_sources())
    if kind in ("upload", "lecture"):
        # Same steps as create_vector_store(text, persist_key, metadata).
        text = _read(spec["path"])
        meta = {"source_type": kind, "source_title": Path(spec["path"]).name}
        docs = get_text_splitter().create_documents([text], metadatas=[meta])
        for i, d in enumerate(docs):
            d.metadata = {"persist_key": f"bench_{name}", "chunk_index": i, **meta, **d.metadata}
        return docs
    if kind == "pyq_bank":
        # Same steps as pyq/graph.py:build_question_bank (no chunk_index there).
        text = _read(spec["path"])
        docs = get_text_splitter().create_documents([text])
        for d in docs:
            d.metadata = {"source_type": "pyq_bank", "source_title": Path(spec["path"]).name}
        return docs
    if kind == "ncert":
        from src.agents.ncert.graph import build_chapter_documents, read_chapter_pages

        docs: list[Document] = []
        for ch in spec.get("chapters", []):
            pages, ref = read_chapter_pages(ch["class_name"], ch["subject"], ch["chapter"])
            docs.extend(
                build_chapter_documents(
                    pages,
                    {
                        "persist_key": f"bench_{name}",
                        "source_type": "ncert",
                        "document": ch["chapter"],
                    },
                )
            )
        return docs
    raise ValueError(f"unknown corpus kind '{kind}' for corpus '{name}'")


def active_corpora(dataset: dict) -> dict[str, dict]:
    """Corpora that have at least one query (an empty NCERT section is skipped)."""
    used = {q["corpus"] for q in dataset["queries"]}
    return {n: s for n, s in dataset["corpora"].items() if n in used}


def validate_dataset(dataset: dict, docs_by_corpus: dict[str, list]) -> list[str]:
    """Every gold quote must be found verbatim (normalised) inside >= 1 chunk."""
    from src.eval.retrieval_eval import span_hits

    errors: list[str] = []
    ids = set()
    for q in dataset["queries"]:
        qid = q.get("id", "?")
        if qid in ids:
            errors.append(f"{qid}: duplicate id")
        ids.add(qid)
        if q.get("corpus") not in dataset["corpora"]:
            errors.append(f"{qid}: unknown corpus '{q.get('corpus')}'")
            continue
        gold = q.get("gold") or []
        if q.get("answerable", True) and not gold:
            errors.append(f"{qid}: answerable query without gold quotes")
        if not q.get("answerable", True) and gold:
            errors.append(f"{qid}: unanswerable query must not have gold quotes")
        texts = [d.page_content for d in docs_by_corpus.get(q["corpus"], [])]
        for i, g in enumerate(gold):
            if not any(span_hits(t, [g["quote"]]) for t in texts):
                errors.append(
                    f"{qid}: gold quote {i} not inside any single chunk: {g['quote'][:60]!r}"
                )
    return errors


def plan_embeddings(dataset: dict, docs_by_corpus: dict[str, list], emb: BudgetedCachingEmbeddings):
    """Uncached texts the run will embed (documents + queries) and their cost."""
    doc_texts = list(
        dict.fromkeys(d.page_content for docs in docs_by_corpus.values() for d in docs)
    )
    query_texts = list(dict.fromkeys(q["question"] for q in dataset["queries"]))
    new_docs = [t for t in doc_texts if not emb.cached("document", t)]
    new_queries = [t for t in query_texts if not emb.cached("query", t)]
    tokens = sum(estimate_tokens(t) for t in new_docs + new_queries)
    return {
        "unique_documents": len(doc_texts),
        "unique_queries": len(query_texts),
        "uncached_documents": len(new_docs),
        "uncached_queries": len(new_queries),
        "requests": len(new_docs) + len(new_queries),
        "estimated_tokens": tokens,
        "estimated_cost_usd": round(tokens / 1_000_000 * USD_PER_MILLION_TOKENS, 6),
    }


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #
def _doc_id(doc) -> str:
    return hashlib.sha256((doc.page_content or "").encode("utf-8")).hexdigest()[:16]


def _timed(fn, repeats: int) -> tuple[Any, float]:
    """Run ``fn`` ``repeats`` times; return the last result and the median ms."""
    times = []
    result = None
    for _ in range(max(1, repeats)):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000.0)
    return result, statistics.median(times)


def _metrics(
    hits: list[set[int]], gold: list[dict], k: int, ideal_hits: list[set[int]] | None
) -> dict:
    from src.eval.retrieval_eval import mrr_at_k, ndcg_at_k, recall_at_k

    grades = [int(g.get("grade", 1)) for g in gold]
    return {
        f"recall@{k}": recall_at_k(hits, len(gold), k),
        f"mrr@{k}": mrr_at_k(hits, k),
        f"ndcg@{k}": ndcg_at_k(hits, grades, k, ideal_hits=ideal_hits),
        f"ndcg_legacy@{k}": ndcg_at_k(hits, grades, k),
    }


# Production function per corpus kind, and whether it keeps None-score chunks.
_KEEPS_NONE = {
    "mentor_kb": True,
    "ncert": True,
    "upload": False,
    "lecture": False,
    "pyq_bank": False,
}


def _production_call(vs, db, q: str, k: int, kind: str):
    """Call the workflow's real production retrieval function."""
    if kind == "mentor_kb":
        from src.core.mentor_kb import search_kb

        return search_kb(q, k=k)
    if kind == "ncert":
        return vs.similarity_search_with_sources(db, q, k=k, label="bench")
    return vs.similarity_search(db, q, k=k, label="bench")


def _production_expected(rows: list[dict], threshold: float, kind: str):
    """What the production function should return, rebuilt from the pool."""
    keep_none = _KEEPS_NONE[kind]
    kept = [
        r
        for r in rows
        if (r["score"] is None and keep_none)
        or (r["score"] is not None and r["score"] >= threshold)
    ]
    if kind == "mentor_kb":
        return "\n\n".join(
            (f"[{r['doc'].metadata.get('source')}] " if r["doc"].metadata.get("source") else "")
            + r["doc"].page_content
            for r in kept
        )
    if kind == "ncert":
        return [r["doc"].page_content for r in kept]
    return "\n\n".join(r["doc"].page_content for r in kept)


def _production_observed(out: Any, kind: str):
    if kind == "mentor_kb":
        return out.get("context", "")
    if kind == "ncert":
        return [c["excerpt"] for c in out.get("chunks", [])]
    return out


def measure_query(
    vs,
    db,
    query: dict,
    params: dict,
    *,
    kind: str,
    repeats: int,
    corpus_texts: list[str] | None = None,
) -> dict:
    """Run every variant for one query; all rankings come from ONE verified pool."""
    from src.core.retrieval import rerank_scored_documents
    from src.eval.retrieval_eval import (
        any_above_threshold,
        gold_dropped_by_threshold,
        ranked_span_hits,
    )

    q = query["question"]
    k, threshold = params["k"], params["threshold"]
    if 2 * k > POOL_SIZE:
        raise ValueError(f"POOL_SIZE {POOL_SIZE} is smaller than 2k={2 * k}")
    quotes = [g["quote"] for g in query.get("gold") or []]
    keep_none = _KEEPS_NONE[kind]
    ideal_hits = [h for h in ranked_span_hits(corpus_texts or [], quotes) if h] or None

    # The shared candidate pool and the two searches production would issue.
    pool, t_pool = _timed(lambda: vs._plain_scored_search(db, q, POOL_SIZE), repeats)
    search_k, t_dense = _timed(lambda: vs._plain_scored_search(db, q, k), repeats)
    pool_ids = [_doc_id(d) for d, _ in pool]
    topk_matches_pool = [_doc_id(d) for d, _ in search_k] == pool_ids[:k]

    def _overfetch_live():
        fetched = vs._plain_scored_search(db, q, 2 * k)
        return fetched, rerank_scored_documents(fetched, q)[:k]

    (fetched_2k, _), t_over = _timed(_overfetch_live, repeats)
    overfetch_matches_pool = [_doc_id(d) for d, _ in fetched_2k] == pool_ids[: 2 * k]
    _, t_rrf_k = _timed(lambda: rerank_scored_documents(pool[:k], q), repeats)

    dense_k = [{"doc": d, "score": sc} for d, sc in pool[:k]]
    rrf_k = rerank_scored_documents(pool[:k], q)
    overfetch = rerank_scored_documents(pool[: 2 * k], q)[:k]
    rrf_10 = rerank_scored_documents(pool[:10], q)
    dense_10 = [{"doc": d, "score": sc} for d, sc in pool[:10]]

    # Production: the real function, timed and checked against the pool rebuild.
    prod_rows = dense_k if kind == "mentor_kb" else rrf_k  # search_kb is dense-only
    prod_out, t_prod = _timed(lambda: _production_call(vs, db, q, k, kind), repeats)
    prod_consistent = _production_observed(prod_out, kind) == _production_expected(
        prod_rows, threshold, kind
    )

    def _texts(rows):
        return [r["doc"].page_content for r in rows]

    def _scores(rows):
        return [r["score"] for r in rows]

    orderings = {
        "dense_k": (dense_k, k),
        "production": (prod_rows, k),
        "rrf_same_k": (rrf_k, k),
        "overfetch_2k": (overfetch, k),
        "dense_10": (dense_10, 10),
        "rrf_10": (rrf_10, 10),
    }
    out: dict[str, Any] = {
        "id": query["id"],
        "corpus": query["corpus"],
        "kind": kind,
        "answerable": bool(query.get("answerable", True)),
        "k": k,
        "threshold": threshold,
        "topk_matches_pool": topk_matches_pool,
        "overfetch_matches_pool": overfetch_matches_pool,
        "production_consistent": prod_consistent,
        "colocated_quotes": any(len(h) > 1 for h in (ideal_hits or [])),
        "top1_score": pool[0][1] if pool else None,
        "latency_ms": {
            "dense_k": t_dense,
            "production": t_prod,
            "rrf_same_k": t_dense + t_rrf_k,
            "overfetch_2k": t_over,
            "pool": t_pool,
        },
        "variants": {},
    }
    for name, (rows, depth) in orderings.items():
        hits = ranked_span_hits(_texts(rows), quotes)
        scores = _scores(rows)
        row: dict[str, Any] = {}
        if out["answerable"]:
            row.update(_metrics(hits, query["gold"], depth, ideal_hits))
            if depth == 10:
                row.update(_metrics(hits, query["gold"], 5, ideal_hits))
                row.update(_metrics(hits, query["gold"], k, ideal_hits))
            row["gold_dropped"] = gold_dropped_by_threshold(
                hits, scores, threshold, depth, none_passes=keep_none
            )
        else:
            row["false_positive"] = any_above_threshold(
                scores, threshold, depth, none_passes=keep_none
            )
        out["variants"][name] = row
    return out


def run_benchmark(
    dataset: dict,
    *,
    embeddings: BudgetedCachingEmbeddings,
    qdrant_dir: Path,
    dim: int,
    repeats: int = 3,
) -> list[dict]:
    """Index each active corpus into the isolated store and measure every query."""
    from src.core.vector_store import _DimPinnedEmbeddings

    corpora = active_corpora(dataset)
    with isolated_vector_store(qdrant_dir, _DimPinnedEmbeddings(embeddings, dim)) as vs:
        rows: list[dict] = []
        for name, spec in corpora.items():
            kind = spec["kind"]
            docs = corpus_documents(name, spec)
            if kind == "mentor_kb":
                from src.core import mentor_kb

                mentor_kb.build_kb(_mentor_sources(), rebuild=True)
                key = mentor_kb.KB_KEY
            else:
                key = f"bench_{name}"
                vs.upsert_documents(key, docs, rebuild=True)
            db = vs.load_vector_store(key)
            from src.core.config import settings

            if settings.qdrant_url or Path(settings.qdrant_path).resolve() != qdrant_dir.resolve():
                raise PreflightError("refusing to search outside the isolated temp store")
            params = _production_params(kind)
            texts = [d.page_content for d in docs]
            for q in (q for q in dataset["queries"] if q["corpus"] == name):
                rows.append(
                    measure_query(vs, db, q, params, kind=kind, repeats=repeats, corpus_texts=texts)
                )
        return rows


# --------------------------------------------------------------------------- #
# Aggregation + report
# --------------------------------------------------------------------------- #
def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)


# (candidate, baseline, metric, label) for the paired bootstrap; "@k" = each
# corpus's own production k (mentor 4, upload/lecture 5, PYQ 8).
COMPARISONS = [
    ("rrf_same_k", "dense_k", "mrr@k", "RRF re-order vs dense (same k candidates)"),
    ("rrf_same_k", "dense_k", "ndcg@k", "RRF re-order vs dense (same k candidates)"),
    ("overfetch_2k", "rrf_same_k", "recall@k", "over-fetch effect only: RRF 2k->k vs RRF k"),
    ("overfetch_2k", "rrf_same_k", "mrr@k", "over-fetch effect only: RRF 2k->k vs RRF k"),
    ("overfetch_2k", "rrf_same_k", "ndcg@k", "over-fetch effect only: RRF 2k->k vs RRF k"),
    ("overfetch_2k", "production", "recall@k", "RRF 2k->k vs production (combined for mentor KB)"),
    ("overfetch_2k", "production", "mrr@k", "RRF 2k->k vs production (combined for mentor KB)"),
    ("overfetch_2k", "production", "ndcg@k", "RRF 2k->k vs production (combined for mentor KB)"),
    ("rrf_10", "dense_10", "ndcg@10", "RRF vs dense, top-10 pool"),
    ("rrf_10", "dense_10", "mrr@10", "RRF vs dense, top-10 pool"),
]
_K_NOTE = "per-corpus production k: mentor_kb 4, upload/lecture 5, pyq 8"


def _metric(row: dict, variant: str, metric: str) -> float:
    name = metric.replace("@k", f"@{row['k']}")
    return float(row["variants"][variant].get(name, 0.0))


def _groups(rows: list[dict]) -> dict[str, list[dict]]:
    """ALL, the shared-pipeline corpora (production = RRF), then each corpus."""
    groups: dict[str, list[dict]] = {"ALL": rows}
    shared = [r for r in rows if r.get("kind") != "mentor_kb"]
    if shared and len(shared) != len(rows):
        groups["SHARED_PIPELINE"] = shared
    for r in rows:
        groups.setdefault(r["corpus"], []).append(r)
    return groups


def summarize(rows: list[dict], *, resamples: int = 10_000) -> dict:
    from src.eval.retrieval_eval import paired_bootstrap_ci

    summary: dict[str, Any] = {}
    for name, grp in _groups(rows).items():
        ans = [r for r in grp if r["answerable"]]
        una = [r for r in grp if not r["answerable"]]
        ks = sorted({r["k"] for r in grp})
        g: dict[str, Any] = {
            "n_answerable": len(ans),
            "n_unanswerable": len(una),
            "k": ks,
            "k_note": "" if len(ks) == 1 else f"mean over different k ({_K_NOTE})",
            "variants": {},
            "comparisons": [],
        }
        for v in ("dense_k", "production", "rrf_same_k", "overfetch_2k"):
            g["variants"][v] = {
                m: _mean([_metric(r, v, m) for r in ans])
                for m in ("recall@k", "mrr@k", "ndcg@k", "ndcg_legacy@k")
            }
            g["variants"][v]["gold_dropped_rate"] = _mean(
                [1.0 if r["variants"][v].get("gold_dropped") else 0.0 for r in ans]
            )
        for v in ("dense_10", "rrf_10"):
            g["variants"][v] = {
                m: _mean([float(r["variants"][v].get(m, 0.0)) for r in ans])
                for m in ("recall@5", "recall@10", "mrr@10", "ndcg@10")
            }
        # Review Q4: headroom against each corpus's own production k, and @5 separately.
        g["headroom_recall10_minus_recall_at_production_k"] = _mean(
            [_metric(r, "dense_10", "recall@10") - _metric(r, "dense_k", "recall@k") for r in ans]
        )
        g["headroom_recall10_minus_recall5"] = round(
            g["variants"]["dense_10"]["recall@10"] - g["variants"]["dense_10"]["recall@5"], 4
        )
        g["no_answer_false_positive_rate"] = _mean(
            [1.0 if r["variants"]["production"].get("false_positive") else 0.0 for r in una]
        )
        g["queries_with_colocated_quotes"] = sum(1 for r in ans if r.get("colocated_quotes"))
        for cand, base, metric, label in COMPARISONS:
            ci = paired_bootstrap_ci(
                [_metric(r, base, metric) for r in ans],
                [_metric(r, cand, metric) for r in ans],
                resamples=resamples,
            )
            g["comparisons"].append({"label": label, "metric": metric, **ci})
        lat = {
            v: [r["latency_ms"][v] for r in grp]
            for v in ("dense_k", "rrf_same_k", "overfetch_2k", "production")
        }
        g["latency_ms"] = {v: {"p50": _pct(x, 0.5), "p95": _pct(x, 0.95)} for v, x in lat.items()}
        top1_ans = [r["top1_score"] for r in ans if r["top1_score"] is not None]
        top1_una = [r["top1_score"] for r in una if r["top1_score"] is not None]
        g["top1_score"] = {
            "answerable": {
                "min": _pct(top1_ans, 0),
                "p50": _pct(top1_ans, 0.5),
                "max": _pct(top1_ans, 1),
            },
            "unanswerable": {
                "min": _pct(top1_una, 0),
                "p50": _pct(top1_una, 0.5),
                "max": _pct(top1_una, 1),
            },
        }
        g["checks"] = {
            "production_consistent": all(r["production_consistent"] for r in grp),
            "topk_matches_pool": all(r["topk_matches_pool"] for r in grp),
            "overfetch_matches_pool": all(r.get("overfetch_matches_pool", False) for r in grp),
        }
        summary[name] = g
    return summary


def render_report(meta: dict, summary: dict) -> str:
    lines = [
        f"# Retrieval benchmark (Phase 06) - {meta.get('run_label', 'run')}",
        "",
        f"- Date (UTC): {meta['date']}; commit `{meta['commit']}`; embeddings: {meta['embeddings']}",
        f"- Dataset: `{meta['dataset']}` sha256 `{meta['dataset_sha256'][:16]}`; "
        f"corpora sha256: {', '.join(f'{k}=`{v[:12]}`' for k, v in meta['corpus_sha256'].items())}",
        f"- Embedding calls: {meta['embedding']}",
        "- Isolated embedded Qdrant in a temp dir; multi-query / HyDE / cross-encoder forced off.",
        "- One exact dense pool of 16 per query; every ranking is derived from it. Independent",
        "  k and 2k searches are checked equal to the pool prefixes (see Checks).",
        "- Relevance = gold evidence quote contained in the chunk. Labels: "
        f"{meta['label_status']}.",
        "- NDCG uses the achievable ideal (real chunks; several quotes may share one chunk);",
        "  `ndcg_legacy` = Stage 1 definition (one quote per rank, capped at 1.0).",
        "",
    ]
    for name, g in summary.items():
        k_label = f"k={g['k'][0]}" if len(g["k"]) == 1 else f"k varies: {g['k_note']}"
        lines += [
            f"## {name}  (answerable n={g['n_answerable']}, unanswerable n={g['n_unanswerable']}, "
            f"{k_label})",
            "",
            "| variant | Recall@k | MRR@k | NDCG@k | NDCG@k legacy | gold dropped by threshold |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for v in ("dense_k", "production", "rrf_same_k", "overfetch_2k"):
            m = g["variants"][v]
            lines.append(
                f"| {v} | {m['recall@k']} | {m['mrr@k']} | {m['ndcg@k']} | {m['ndcg_legacy@k']} "
                f"| {m['gold_dropped_rate']} |"
            )
        lines += [
            "",
            "| top-10 pool | Recall@5 | Recall@10 | MRR@10 | NDCG@10 |",
            "|---|---:|---:|---:|---:|",
        ]
        for v in ("dense_10", "rrf_10"):
            m = g["variants"][v]
            lines.append(
                f"| {v} | {m['recall@5']} | {m['recall@10']} | {m['mrr@10']} | {m['ndcg@10']} |"
            )
        lines += [
            "",
            "- Headroom (dense Recall@10 - Recall@production k): "
            f"{g['headroom_recall10_minus_recall_at_production_k']}; "
            f"(dense Recall@10 - Recall@5): {g['headroom_recall10_minus_recall5']}",
            f"- No-answer false-positive retrieval rate (production threshold): "
            f"{g['no_answer_false_positive_rate']}",
            f"- Answerable queries with several quotes in one chunk: "
            f"{g['queries_with_colocated_quotes']}",
            f"- Top-1 score answerable {g['top1_score']['answerable']} ; unanswerable "
            f"{g['top1_score']['unanswerable']}",
            f"- Checks: {g['checks']}",
            "",
            "| comparison (paired bootstrap 95% CI) | metric | n | delta | CI low | CI high | excludes 0 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for c in g["comparisons"]:
            lines.append(
                f"| {c['label']} | {c['metric']} | {c['n']} | {c['delta']:+.4f} | {c['low']:+.4f} | "
                f"{c['high']:+.4f} | {'yes' if c['significant'] else 'no'} |"
            )
        lines += [
            "",
            "| latency ms (retrieval only, cached embeddings, embedded Qdrant) | p50 | p95 |",
            "|---|---:|---:|",
        ]
        for v, x in g["latency_ms"].items():
            lines.append(f"| {v} | {x['p50']} | {x['p95']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_hashes(dataset: dict) -> dict[str, str]:
    out = {}
    for name, spec in active_corpora(dataset).items():
        if spec["kind"] == "mentor_kb":
            h = hashlib.sha256()
            resolve_corpus_path(spec["path"], allow_dir=True)
            # Review SF-1: validate each file before reading it (no link escapes).
            for p in sorted((REPO_ROOT / spec["path"]).rglob("*.md")):
                h.update(mentor_kb_file(p).read_bytes())
            out[name] = h.hexdigest()
        elif "path" in spec:
            out[name] = _sha256_file(resolve_corpus_path(spec["path"]))
    return out


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _remove_work_dir(work: Path) -> None:
    """Delete ONLY the temp dir this run created (review S2)."""
    tmp_root = Path(tempfile.gettempdir()).resolve()
    resolved = work.resolve()
    if (
        resolved.parent != tmp_root
        or not resolved.name.startswith(WORK_DIR_PREFIX)
        or not resolved.is_dir()
    ):
        return
    shutil.rmtree(resolved, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 06 retrieval benchmark")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--fake-embeddings", action="store_true", help="offline, no network")
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="real model, cached vectors only; never calls the API (exit 7 on a cache miss)",
    )
    parser.add_argument("--confirm-live", action="store_true", help="allow live embedding calls")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--cache", type=Path, default=None, help="embedding cache JSON")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--max-texts-per-minute",
        type=int,
        default=90,
        help="live pacing (Gemini free tier: 100 embed texts/min)",
    )
    parser.add_argument("--repeats", type=int, default=3, help="timing repeats per query")
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--run-label", default="", help="label written into the report title")
    args = parser.parse_args(argv)
    if args.fake_embeddings and args.cache_only:
        parser.error("--fake-embeddings and --cache-only are mutually exclusive")

    # Review S1: corpus paths are checked before src is imported or any file is read.
    try:
        dataset = load_dataset(args.dataset)
    except (OSError, ValueError) as exc:
        print(f"DATASET INVALID: {exc}", file=sys.stderr)
        return 3
    path_errors = validate_corpus_paths(dataset)
    if path_errors:
        print("DATASET INVALID:", *path_errors, sep="\n  ", file=sys.stderr)
        return 3

    # Isolation BEFORE importing src: no production Qdrant, no tracing, no LLM paths.
    os.environ.update(_FORCED_ENV)
    work = Path(tempfile.mkdtemp(prefix=WORK_DIR_PREFIX))
    try:
        os.environ["QDRANT_PATH"] = str(work / "qdrant")
        offline = args.fake_embeddings or args.cache_only
        if offline:
            os.environ["GOOGLE_API_KEY"] = ""
            os.environ["GROQ_API_KEY"] = ""
        os.chdir(REPO_ROOT)  # settings read .env from the working directory
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        guard = network_blocked() if offline else contextlib.nullcontext()
        with guard:
            return _run(args, dataset, work)
    finally:
        # Review S2: the embedded Qdrant client is closed by isolated_vector_store
        # before we get here; remove only the dir this run created.
        _remove_work_dir(work)


def _run(args, dataset: dict, work: Path) -> int:
    live = not (args.fake_embeddings or args.cache_only)
    try:
        info = preflight(live=live)
    except PreflightError as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2

    corpora = active_corpora(dataset)
    try:
        docs_by_corpus = {n: corpus_documents(n, s) for n, s in corpora.items()}
    except (CorpusPathError, ValueError) as exc:
        print(f"DATASET INVALID: {exc}", file=sys.stderr)
        return 3
    errors = validate_dataset(dataset, docs_by_corpus)
    n_ans = sum(1 for q in dataset["queries"] if q.get("answerable", True))
    n_una = len(dataset["queries"]) - n_ans
    print(
        f"Dataset: {n_ans} answerable + {n_una} unanswerable queries; corpora "
        f"{ {n: len(d) for n, d in docs_by_corpus.items()} } chunks"
    )
    if errors:
        print("DATASET INVALID:", *errors, sep="\n  ", file=sys.stderr)
        return 3
    if args.validate_only:
        print("Dataset valid (every gold quote is inside at least one production chunk).")
        return 0

    out_dir = DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.fake_embeddings:
        model_id = f"fake-hash@{FAKE_DIM}"
        inner: Any = FakeEmbeddings()
        cache = args.cache  # offline: in-memory unless asked
    else:
        model_id = f"{info['embedding_model']}@{info['embedding_dim']}"
        inner = CacheOnlyInner() if args.cache_only else None  # live client only after confirm
        cache = args.cache or out_dir / "embedding_cache.json"
    emb = BudgetedCachingEmbeddings(
        inner,
        model_id=model_id,
        cache_path=cache,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        max_texts_per_minute=args.max_texts_per_minute if live else None,
    )
    plan = plan_embeddings(dataset, docs_by_corpus, emb)
    mode = "cache-only" if args.cache_only else ("fake" if args.fake_embeddings else "live")
    print(
        f"Embedding plan ({mode}): model={model_id} requests<={plan['requests']} "
        f"(documents {plan['uncached_documents']}, queries {plan['uncached_queries']}; "
        f"cached {plan['unique_documents'] + plan['unique_queries'] - plan['requests']}) "
        f"estimated_input_tokens~{plan['estimated_tokens']} "
        f"estimated_cost_usd~{plan['estimated_cost_usd']} "
        f"(at ${USD_PER_MILLION_TOKENS}/1M tokens, paid tier; free tier $0) "
        f"limits: {args.max_requests} requests / {args.max_tokens} tokens"
    )
    if args.cache_only and plan["requests"]:
        print(
            f"CACHE MISS: {plan['requests']} text(s) are not in {cache}; --cache-only never "
            "calls the API. Nothing was sent.",
            file=sys.stderr,
        )
        return 7
    print(
        f"Usage ledger so far (this cache): {emb.requests} requests / ~{emb.tokens} tokens; "
        f"after this run at most {emb.requests + plan['requests']} / "
        f"~{emb.tokens + plan['estimated_tokens']}"
    )
    if (
        emb.requests + plan["requests"] > args.max_requests
        or emb.tokens + plan["estimated_tokens"] > args.max_tokens
    ):
        print("STOP: the plan exceeds the approved budget; nothing was sent.", file=sys.stderr)
        return 4
    if live and not args.confirm_live:
        print("Dry run only (no request sent). Re-run with --confirm-live to execute.")
        return 0
    if live:
        from src.core.vector_store import _make_gemini_embeddings

        emb._inner = _make_gemini_embeddings()._inner  # raw client; pinning re-applied below

    dim = FAKE_DIM if args.fake_embeddings else info["embedding_dim"]
    try:
        rows = run_benchmark(
            dataset, embeddings=emb, qdrant_dir=work / "qdrant", dim=dim, repeats=args.repeats
        )
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 5
    except CacheMissError as exc:
        print(f"CACHE MISS: {exc}; --cache-only never calls the API.", file=sys.stderr)
        return 7
    except Exception as exc:  # noqa: BLE001 - provider error: stop, never auto-retry
        from src.core.llm import describe_exception

        print(
            f"STOPPED on provider error ({describe_exception(exc)}). Cache and usage ledger "
            f"saved ({emb.requests} requests / ~{emb.tokens} tokens); no retry was attempted.",
            file=sys.stderr,
        )
        return 6
    finally:
        if not args.cache_only:  # cache-only never changes the cache or its ledger
            emb.save()
    summary = summarize(rows, resamples=args.resamples)
    labels = {q.get("human_verified", False) for q in dataset["queries"]}
    meta = {
        "run_label": args.run_label or mode,
        "mode": mode,
        "date": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
        "commit": _commit(),
        "embeddings": model_id,
        "dataset": args.dataset.relative_to(REPO_ROOT).as_posix()
        if args.dataset.is_relative_to(REPO_ROOT)
        else str(args.dataset),
        "dataset_sha256": _sha256_file(args.dataset),
        "corpus_sha256": _corpus_hashes(dataset),
        "embedding": (
            f"this run {emb.run_requests} texts sent; cumulative ledger {emb.requests} "
            f"requests / ~{emb.tokens} est. tokens; {emb.hits} cache hits; "
            f"call p50 {_pct([s * 1000 for s in emb.call_seconds], 0.5)} ms"
        ),
        "label_status": (
            "human-verified" if labels == {True} else "Claude-authored, NOT yet human-verified"
        ),
    }
    report = render_report(meta, summary)
    report_path = args.report or out_dir / "retrieval_baseline.md"
    json_path = args.json or out_dir / "retrieval_baseline.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=1), encoding="utf-8"
    )
    print(report)
    print(f"Report: {report_path}\nJSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
