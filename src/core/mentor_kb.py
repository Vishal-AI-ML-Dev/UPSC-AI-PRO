"""
Mentor Knowledge Base - grounded, citeable knowledge for the Mentor agent.

One persistent vector store ("mentor_kb") holding DURABLE, slow-changing knowledge:
  - Verified UPSC facts (exam pattern, eligibility, syllabus) from a curated, DATED
    markdown file.
  - Topper strategies from YouTube interview transcripts (attributed to topper + year).
  - Optional official PDFs (notification / syllabus).

VOLATILE facts (exact dates, cutoffs, vacancy numbers) are deliberately NOT stored
here - the Mentor agent fetches those live from official sources. This KB only holds
knowledge that stays valid for a long time, and every chunk carries a 'source' label
so the Mentor can cite it to the user.

Backend follows src.core.vector_store (Qdrant in prod, local Chroma fallback).

Rebuild whenever the facts file changes or topper videos are added:
    python scripts/ingest_mentor_kb.py
"""

import logging
import os
from typing import Optional

from src.core.vector_store import (
    _use_qdrant,
    collection_for,
    get_text_splitter,
    load_vector_store,
    make_persist_key,
    persist_dir_for,
    upsert_documents,
    vector_store_exists,
)

logger = logging.getLogger(__name__)

KB_KEY = make_persist_key("mentor", "kb")
# Separate, tunable threshold for the mentor KB (general knowledge needs a slightly
# looser bar than tight chapter RAG).
KB_THRESHOLD = float(os.getenv("MENTOR_KB_THRESHOLD", "0.25"))


def kb_location() -> str:
    """Human-readable location of the mentor KB (Qdrant collection or on-disk dir)."""
    return f"qdrant:{collection_for(KB_KEY)}" if _use_qdrant() else persist_dir_for(KB_KEY)


# Backwards-compatible alias kept for the ingest script's logging.
def kb_dir() -> str:
    """Deprecated name for kb_location(); retained so callers do not break."""
    return kb_location()


def kb_exists() -> bool:
    """True if the mentor KB has been built and holds at least one chunk."""
    return vector_store_exists(KB_KEY)


def _make_docs(sources):
    """sources: list of {"text": str, "metadata": dict}. Returns split LC documents."""
    splitter = get_text_splitter()
    docs = []
    for s in sources:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        md = dict(s.get("metadata") or {})
        # Keep metadata values primitive (str/int/float/bool); drop None.
        md = {k: v for k, v in md.items() if v is not None}
        docs.extend(splitter.create_documents([text], metadatas=[md]))
    return docs


def build_kb(sources, rebuild: bool = True) -> int:
    """
    Build (or rebuild) the Mentor KB from a list of sources.
    Each source: {"text": <str>, "metadata": {"source": <citation label>, ...}}.
    Returns the number of chunks indexed.
    """
    docs = _make_docs(sources)
    if not docs:
        logger.warning("Mentor KB: no documents to index (nothing ingested)")
        return 0
    upsert_documents(KB_KEY, docs, rebuild=rebuild)
    logger.info(f"Mentor KB built: {len(docs)} chunks -> {kb_location()}")
    return len(docs)


def search_kb(query: str, k: int = 4, min_score: Optional[float] = None) -> dict:
    """
    Search the Mentor KB.

    Returns:
        {"context": str, "citations": [str], "grounded": bool}
    where each context block is prefixed with its source label, and 'citations'
    is the de-duplicated list of source labels for user-facing attribution.
    """
    q = (query or "").strip()
    if not q or not kb_exists():
        return {"context": "", "citations": [], "grounded": False}
    threshold = KB_THRESHOLD if min_score is None else min_score
    db = load_vector_store(KB_KEY)
    if db is None:
        return {"context": "", "citations": [], "grounded": False}
    try:
        scored = db.similarity_search_with_relevance_scores(q, k=k)
    except Exception as e:
        logger.warning(f"Mentor KB search failed: {e}")
        return {"context": "", "citations": [], "grounded": False}

    parts, citations = [], []
    for doc, score in scored:
        if score is None or score >= threshold:
            src = (doc.metadata or {}).get("source")
            prefix = f"[{src}] " if src else ""
            parts.append(f"{prefix}{doc.page_content}")
            if src and src not in citations:
                citations.append(src)

    try:
        from src.core import observability

        observability.log_retrieval_metrics("mentor_kb", q, k, len(parts), [s for _, s in scored])
    except Exception:
        pass

    return {
        "context": "\n\n".join(parts),
        "citations": citations,
        "grounded": bool(parts),
    }
