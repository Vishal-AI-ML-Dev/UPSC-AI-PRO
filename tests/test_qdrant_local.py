"""QDRANT_PATH embedded local Qdrant: config rules, remote unchanged, no hidden
embedding probes, no stale points after drops, single-process lock.

Uses the installed qdrant-client in local (in-process) mode under pytest's
tmp_path and a counting fake embedding model - no network, no AI calls.
"""

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import ValidationError

import src.core.vector_store as vs
from src.core.config import Settings, settings

DIM = 8


class _CountingEmbeddings(Embeddings):
    """Deterministic 16-dim vectors; records every call (texts only, no network)."""

    def __init__(self):
        self.calls = []

    def _vec(self, text):
        v = [float((hash((text, i)) % 997) + 1) for i in range(16)]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    def embed_documents(self, texts, **kwargs):
        self.calls.append(("documents", list(texts)))
        return [self._vec(t) for t in texts]

    def embed_query(self, text, **kwargs):
        self.calls.append(("query", text))
        return self._vec(text)


@pytest.fixture
def local_qdrant(monkeypatch, tmp_path):
    """Point the app at an embedded local Qdrant folder with pinned fake embeddings."""
    inner = _CountingEmbeddings()
    monkeypatch.setattr(settings, "qdrant_url", "")
    monkeypatch.setattr(settings, "qdrant_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(vs, "_qdrant_client_instance", None)
    monkeypatch.setattr(vs, "_embed_dim_cache", None)
    monkeypatch.setattr(vs, "get_embeddings", lambda: vs._DimPinnedEmbeddings(inner, DIM))
    yield inner
    client = vs._qdrant_client_instance
    if client is not None:
        client.close()  # release the folder lock
    vs._qdrant_client_instance = None


def _docs(n=3, tag="a"):
    return [
        Document(
            page_content=f"{tag} chunk {i} about the Gram Sabha",
            metadata={"pdf_page": i + 1, "chunk_index": i},
        )
        for i in range(n)
    ]


def _count(name):
    return vs.get_qdrant_client().count(vs.collection_for(name), exact=True).count


def _reopen():
    vs._qdrant_client_instance.close()
    vs._qdrant_client_instance = None


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_url_and_path_together_are_rejected():
    with pytest.raises(ValidationError, match="not both"):
        Settings(qdrant_url="http://127.0.0.1:6333", qdrant_path="D:/x/qdrant")


def test_url_or_path_alone_is_accepted():
    assert Settings(qdrant_url="http://127.0.0.1:6333").qdrant_path == ""
    assert Settings(qdrant_path="D:/x/qdrant").qdrant_url == ""


@pytest.mark.parametrize("bad", [-1, 4000])
def test_invalid_embedding_dim_is_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(gemini_embedding_dim=bad)


@pytest.mark.parametrize(
    "url,path,use,local",
    [("", "", False, False), ("http://h:6333", "", True, False), ("", "D:/q", True, True)],
)
def test_backend_selection(monkeypatch, url, path, use, local):
    monkeypatch.setattr(settings, "qdrant_url", url)
    monkeypatch.setattr(settings, "qdrant_path", path)
    assert vs._use_qdrant() is use and vs._qdrant_local() is local


# --------------------------------------------------------------------------- #
# Remote behaviour preserved
# --------------------------------------------------------------------------- #
def test_remote_client_is_built_from_url_as_before(monkeypatch):
    import qdrant_client

    seen = []

    class _Fake:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(qdrant_client, "QdrantClient", _Fake)
    monkeypatch.setattr(settings, "qdrant_url", "http://qdrant.invalid:6333")
    monkeypatch.setattr(settings, "qdrant_path", "")
    monkeypatch.setattr(settings, "qdrant_api_key", "dummy")
    monkeypatch.setattr(vs, "_qdrant_client_instance", None)
    vs.get_qdrant_client()
    assert seen == [
        {
            "url": "http://qdrant.invalid:6333",
            "api_key": "dummy",
            "timeout": settings.qdrant_timeout,
        }
    ]


# --------------------------------------------------------------------------- #
# Local mode end-to-end: exact embedding calls, metadata kept
# --------------------------------------------------------------------------- #
def test_local_ingest_and_search_make_no_hidden_probe_calls(local_qdrant):
    docs = _docs(3)
    assert vs.ensure_vector_storage() is True
    vs.upsert_documents("ncert_v2_t1", docs)
    assert _count("ncert_v2_t1") == 3
    assert vs.vector_store_exists("ncert_v2_t1")
    store = vs.load_vector_store("ncert_v2_t1")
    res = vs.similarity_search_with_sources(store, "Gram Sabha", k=2, min_score=-1.0)
    assert res["chunks"] and all("pdf_page" in c["metadata"] for c in res["chunks"])
    # Exactly one batched document call + one query call; no "dummy_text" /
    # "dimension probe" embeddings from validation or size detection.
    assert [kind for kind, _ in local_qdrant.calls] == ["documents", "query"]
    assert local_qdrant.calls[0][1] == [d.page_content for d in docs]
    info = vs.get_qdrant_client().get_collection("ncert_v2_t1").config.params.vectors
    assert info.size == DIM and info.distance.value == "Cosine"


def test_rebuild_does_not_keep_stale_points_even_after_reopen(local_qdrant):
    vs.upsert_documents("kb_t2", _docs(3, "old"))
    _reopen()  # a restarted process
    vs.upsert_documents("kb_t2", _docs(2, "new"), rebuild=True)
    assert _count("kb_t2") == 2
    _reopen()
    assert _count("kb_t2") == 2
    store = vs.load_vector_store("kb_t2")
    texts = {
        c["excerpt"]
        for c in vs.similarity_search_with_sources(store, "chunk", k=10, min_score=-1.0)["chunks"]
    }
    assert texts and all(t.startswith("new") for t in texts)


def test_dimension_mismatch_is_healed_without_stale_points(local_qdrant):
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = vs.get_qdrant_client()
    client.create_collection("kb_t3", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert(
        "kb_t3", [PointStruct(id=1, vector=[1.0, 0, 0, 0], payload={"page_content": "stale"})]
    )
    assert vs.load_vector_store("kb_t3") is None  # mismatched dim is ignored on load
    assert not vs.vector_store_exists("kb_t3")
    vs.upsert_documents("kb_t3", _docs(2))
    assert vs.get_qdrant_client().get_collection("kb_t3").config.params.vectors.size == DIM
    assert _count("kb_t3") == 2


def test_pyq_clear_bank_leaves_no_points_behind(local_qdrant):
    from src.agents.pyq import graph as pyq

    key = pyq._bank_key("user-1")
    vs.upsert_documents(key, _docs(3))
    assert pyq.clear_bank("user-1") == {"cleared": True}
    assert not vs.get_qdrant_client().collection_exists(vs.collection_for(key))
    vs.upsert_documents(key, _docs(1, "fresh"))
    assert _count(key) == 1  # nothing from the cleared bank came back


def test_local_mode_is_single_process(local_qdrant, tmp_path):
    """Documents the limitation: a second client on the same folder is refused."""
    from qdrant_client import QdrantClient

    vs.get_qdrant_client()
    with pytest.raises(RuntimeError):
        QdrantClient(path=str(tmp_path / "qdrant"))


def test_unpinned_embeddings_are_still_probed_once(monkeypatch):
    """Non-pinned providers keep the previous behaviour (one cached probe)."""
    emb = _CountingEmbeddings()
    monkeypatch.setattr(vs, "_embed_dim_cache", None)
    assert vs._embedding_dim(emb) == 16
    assert vs._embedding_dim(emb) == 16
    assert emb.calls == [("query", "dimension probe")]


def test_pinned_embeddings_report_size_without_any_call():
    emb = _CountingEmbeddings()
    assert vs._embedding_dim(vs._DimPinnedEmbeddings(emb, DIM)) == DIM
    assert emb.calls == []
