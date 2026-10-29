"""Pure, dependency-free grounding + citation helpers.

This centralises the citation / trust-note formatting that used to live inside
``src/graph/rag_graph.py`` so the *logic* is importable and unit-testable
WITHOUT langchain / langgraph / any vector backend (same offline-first pattern
as ``src/eval/gates.py``).

Two consumers share this module:

* the **RAG subgraph** (``rag_graph.py``) - already had a full
  ``verify_grounding`` node; it now imports these helpers instead of defining
  private copies, so there is exactly one source of truth.
* the **mentor tool-calling agent** (``src/graph/tools.py``) - previously had
  NO citations at all; it now reuses ``citations_from_tool_messages`` +
  ``format_sources`` to append deterministic "Sources used" notes to every
  answer that was grounded in a tool call.

Everything here is pure Python (only ``re``): no network, no LLM, no heavy
imports, so it runs in CI with no API keys.
"""

from __future__ import annotations

import os as _os
import re

# Keep at most this many citations on any answer, so long tool runs do not
# produce an unreadable wall of sources.
MAX_CITATIONS = 5

# "(Source: https://...)" markers emitted by the web-search digest
# (_fetch_search_context / _duckduckgo_search in src/agents/mentor/graph.py).
_SOURCE_URL_RE = re.compile(r"\(Source:\s*(.*?)\)")


def source_label(chunk: dict) -> str:
    """Format one retrieved chunk as a user-facing citation label.

    Reads only metadata that actually exists on the chunk; never invents a
    title, page, or score.
    """
    metadata = (chunk.get("metadata") or {}) if isinstance(chunk, dict) else {}
    source_type = metadata.get("source_type") or "retrieved context"
    title = (
        metadata.get("source_title")
        or metadata.get("filename")
        or metadata.get("chapter")
        or "source"
    )
    chunk_index = metadata.get("chunk_index")
    pdf_page = metadata.get("pdf_page")
    score = chunk.get("score") if isinstance(chunk, dict) else None
    parts = [str(title)]
    if source_type:
        parts.append(f"type={source_type}")
    if pdf_page is not None:
        parts.append(f"PDF p.{pdf_page}")
    if chunk_index is not None:
        parts.append(f"chunk={chunk_index}")
    if score is not None:
        parts.append(f"score={score}")
    return " \u2014 ".join(parts)


def format_sources(citations, *, max_items: int = MAX_CITATIONS) -> str:
    """Create compact, user-facing source notes without inventing metadata.

    Accepts a mixed list of chunk dicts (from retrieval) and/or plain string
    labels (e.g. web/tool citations). Returns "" when there is nothing to cite.
    """
    cleaned = [c for c in (citations or []) if c]
    if not cleaned:
        return ""
    lines = ["\n\nSources used:"]
    for i, citation in enumerate(cleaned[:max_items], start=1):
        label = source_label(citation) if isinstance(citation, dict) else str(citation)
        lines.append(f"- {i}. {label}")
    return "\n".join(lines)


def format_trust_note(confidence: str, unsupported_claims) -> str:
    """Append a short reliability note only when the answer needs caution."""
    claims = list(unsupported_claims or [])
    if confidence == "high" and not claims:
        return ""
    note = f"\n\nReliability note: grounding confidence is {confidence}."
    if claims:
        note += " Potentially unsupported claim(s): " + "; ".join(claims[:3])
    return note


def derive_confidence(*, grounded: bool, relevant: bool, has_evidence: bool) -> str:
    """Heuristic pre-LLM confidence used before (or instead of) an LLM verifier.

    * no evidence at all              -> low
    * grounded retrieval + relevant   -> high
    * anything else (partial support) -> medium
    """
    if not has_evidence:
        return "low"
    if grounded and relevant:
        return "high"
    return "medium"


def _is_fence_or_guard(line: str) -> bool:
    """True for prompt-safety scaffolding lines that are NOT real content.

    ``harden_untrusted`` wraps untrusted text in ``<<<...>>>`` fence markers and
    a leading ``[UNTRUSTED ...]`` guard line. When we later mine a (hardened)
    tool result for citations we must skip that scaffolding, otherwise the guard
    banner would be mis-read as a source.
    """
    s = line.strip()
    if not s:
        return True
    if s.startswith("<<<") and s.endswith(">>>"):
        return True
    if s.startswith("[UNTRUSTED"):
        return True
    return False


def extract_web_citations(text: str, *, max_items: int = MAX_CITATIONS) -> list:
    """Pull "(Source: URL)" markers out of a web-search digest into labels.

    De-duplicates while preserving order and caps the count.
    """
    if not text:
        return []
    seen = set()
    out = []
    for match in _SOURCE_URL_RE.finditer(text):
        url = match.group(1).strip()
        if url and url not in seen:
            seen.add(url)
            out.append(f"web \u2014 {url}")
        if len(out) >= max_items:
            break
    return out


def citations_from_tool_messages(messages, *, max_items: int = MAX_CITATIONS) -> list:
    """Duck-typed scan of tool messages for citeable sources (web + KB).

    Works on any object exposing ``.type`` / ``.name`` / ``.content`` (the shape
    of a LangChain ``ToolMessage``), so it stays offline-testable with light
    fakes and never imports langchain.
    """
    out = []
    seen = set()
    for message in messages or []:
        if getattr(message, "type", "") != "tool":
            continue
        name = getattr(message, "name", "") or ""
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        if name == "web_search":
            for citation in extract_web_citations(content, max_items=max_items):
                if citation not in seen:
                    seen.add(citation)
                    out.append(citation)
        elif name == "knowledge_base_search":
            # knowledge_base_tool_fn prefixes passages with "[<source>] ".
            for line in content.splitlines():
                if _is_fence_or_guard(line):
                    continue
                stripped = line.strip()
                if stripped.startswith("[") and "]" in stripped:
                    src = stripped[1 : stripped.index("]")].strip()
                    if src:
                        citation = f"knowledge base \u2014 {src}"
                        if citation not in seen:
                            seen.add(citation)
                            out.append(citation)
        if len(out) >= max_items:
            break
    return out[:max_items]


def compose_grounded_answer(
    answer,
    citations=None,
    *,
    confidence: str | None = None,
    unsupported_claims=None,
) -> str:
    """Single entry point: answer + "Sources used" + optional reliability note."""
    text = answer or ""
    text += format_sources(citations or [])
    if confidence is not None:
        text += format_trust_note(confidence, unsupported_claims or [])
    return text


# --- Citation honesty guard -------------------------------------------------
# A retrieved chunk is NOT proof the answer used it. If a KB chunk clears the
# vector threshold but the model actually answered from its own knowledge, we
# must NOT staple that source onto the answer (that produced false "Sources
# used" like a topper's strategy note under a constitutional-law answer).
# These helpers verify real content overlap before a KB source is cited.

# Fraction of the ANSWER's distinctive words that must also appear in a KB
# chunk before we trust that the answer was actually drawn from it. Tunable.
try:
    _MIN_KB_OVERLAP = float(_os.getenv("MENTOR_CITATION_MIN_OVERLAP", "0.25"))
except (TypeError, ValueError):
    _MIN_KB_OVERLAP = 0.25

_KB_CITATION_MARKER = "knowledge base \u2014 "

# Latin + Devanagari word characters, so Hindi answers are handled too.
_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u0900-\u097F]+")

# Very common words that carry no topical signal (kept small + bilingual).
_STOP_TOKENS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "into",
    "over",
    "under",
    "which",
    "while",
    "their",
    "they",
    "them",
    "then",
    "than",
    "there",
    "these",
    "those",
    "such",
    "also",
    "been",
    "being",
    "about",
    "would",
    "could",
    "should",
    "will",
    "shall",
    "your",
    "you",
    "our",
    "can",
    "not",
    "but",
    "any",
    "all",
    "each",
    "more",
    "most",
    "some",
    "other",
    "only",
    "very",
    "much",
    "many",
    "jaise",
    "aur",
    "hai",
    "hain",
    "tha",
    "the",
    "kya",
    "kar",
    "karo",
    "liye",
    "mein",
    "apne",
}


def _content_tokens(text: str) -> set:
    """Distinctive (>=4 char, non-stopword) lowercase tokens for overlap tests."""
    return {
        t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 4 and t not in _STOP_TOKENS
    }


def kb_chunks_from_tool_messages(messages) -> dict:
    """Map each KB source label -> the retrieved chunk text it contributed.

    ``knowledge_base_tool_fn`` prefixes each passage with ``[<source>] ``; we
    stitch every line back to its owning source so we can later check whether
    the final answer actually reflects that source's content.
    """
    chunks: dict = {}
    for message in messages or []:
        if getattr(message, "type", "") != "tool":
            continue
        if (getattr(message, "name", "") or "") != "knowledge_base_search":
            continue
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        current = None
        for line in content.splitlines():
            if _is_fence_or_guard(line):
                continue
            stripped = line.strip()
            if stripped.startswith("[") and "]" in stripped:
                src = stripped[1 : stripped.index("]")].strip()
                rest = stripped[stripped.index("]") + 1 :]
                current = src or None
                if current:
                    chunks[current] = chunks.get(current, "") + " " + rest
            elif current:
                chunks[current] = chunks.get(current, "") + " " + stripped
    return chunks


def filter_kb_citations_by_overlap(
    answer, messages, citations, *, min_overlap: float | None = None
) -> list:
    """Drop KB citations the answer did not actually use.

    For every ``knowledge base \u2014 <src>`` citation we measure how much of the
    answer's distinctive vocabulary appears in that source's retrieved chunk.
    Below ``min_overlap`` the source was retrieved but not used, so we drop it.
    Web / non-KB citations pass through untouched.
    """
    if not citations:
        return citations
    threshold = _MIN_KB_OVERLAP if min_overlap is None else min_overlap
    chunks = kb_chunks_from_tool_messages(messages)
    if not chunks:
        return citations
    answer_tokens = _content_tokens(answer)
    if len(answer_tokens) < 8:
        # Answer too short to judge overlap reliably; leave citations as-is.
        return citations
    kept = []
    for citation in citations:
        label = str(citation)
        if not label.startswith(_KB_CITATION_MARKER):
            kept.append(citation)  # web / other sources unaffected
            continue
        src = label[len(_KB_CITATION_MARKER) :].strip()
        chunk_tokens = _content_tokens(chunks.get(src, ""))
        if not chunk_tokens:
            continue  # cannot verify -> do not cite
        overlap = len(answer_tokens & chunk_tokens) / len(answer_tokens)
        if overlap >= threshold:
            kept.append(citation)
        # else: retrieved but not reflected in the answer -> drop silently
    return kept
