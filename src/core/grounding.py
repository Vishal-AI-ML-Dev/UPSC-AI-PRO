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

Structured web citations (Phase C): the mentor's ``web_search`` tool attaches
``{"sources": [...]}`` as the ``ToolMessage.artifact``. Citations are built from
that artifact - never parsed out of the untrusted text - numbered per user
question, and rendered as a grouped "Sources used" block whose inline ``[n]``
numbers match the answer. Messages without an artifact (older threads, plain
string tools) fall back to the legacy "(Source: url)" extraction.

Everything here is pure Python (only ``re`` and the stdlib-only
``src.core.web_search`` helpers): no network, no LLM, no heavy imports, so it
runs in CI with no API keys.
"""

from __future__ import annotations

import os as _os
import re

from src.core.web_search import SearchResult, display_date, url_key

# Keep at most this many citations on any answer, so long tool runs do not
# produce an unreadable wall of sources.
MAX_CITATIONS = 5

# Opening line of the block ``format_sources`` appends to an answer.
SOURCES_HEADER = "\n\nSources used:"

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
    lines = [SOURCES_HEADER]
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
            if _has_structured_sources(message):
                # Structured provenance is handled by web_sources_from_tool_messages;
                # never mine (untrusted) text when an artifact exists.
                continue
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


def unstreamed_tail(final_answer, streamed) -> str:
    """Part of ``final_answer`` the client has NOT yet received via streaming.

    Answer nodes stream the model's tokens, then append deterministic text
    (the "Sources used" block, a trust note) to ``state['answer']`` without
    streaming it. Streaming routes send this tail after the token stream so
    citations reach the client and are saved with the answer.

    * ``final_answer`` extends the streamed text -> the remainder.
    * otherwise (e.g. the model streamed a preamble before a tool call) ->
      the sources block, if the stream does not already contain one.
    * nothing to add -> "".
    """
    final_answer = final_answer or ""
    streamed = streamed or ""
    if not final_answer:
        return ""
    if final_answer.startswith(streamed):
        return final_answer[len(streamed) :]
    idx = final_answer.rfind(SOURCES_HEADER)
    if idx != -1 and SOURCES_HEADER.strip() not in streamed:
        return final_answer[idx:]
    return ""


# --- Structured web citations (ToolMessage artifacts) ----------------------
def _structured_sources(message):
    """The ``sources`` list from a web_search ToolMessage artifact, or None."""
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("sources"), list):
        return artifact["sources"]
    return None


def _has_structured_sources(message) -> bool:
    return _structured_sources(message) is not None


def _numbered(source) -> dict | None:
    """Validate one artifact entry -> ``SearchResult`` dict plus integer ``n``."""
    if not isinstance(source, dict):
        return None
    n = source.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        return None
    result = SearchResult.from_dict(source)
    if result is None:
        return None
    return {**result.to_dict(), "n": n}


def web_sources_from_tool_messages(messages) -> list:
    """Numbered web sources from web_search artifacts, ordered by number.

    Duck-typed (``.type`` / ``.name`` / ``.artifact``), offline-testable. Only
    artifacts count - text inside a tool result can never create a citation.
    """
    by_n: dict = {}
    for message in messages or []:
        if getattr(message, "type", "") != "tool":
            continue
        if (getattr(message, "name", "") or "") != "web_search":
            continue
        for source in _structured_sources(message) or []:
            entry = _numbered(source)
            if entry and entry["n"] not in by_n:
                by_n[entry["n"]] = entry
    return [by_n[n] for n in sorted(by_n)]


def number_web_sources(existing, sources) -> list:
    """Give new web results numbers that are stable for the whole question.

    ``existing`` are sources already numbered earlier in this user question; a
    URL seen before keeps its number, a new URL gets the next free one.
    Duplicates inside ``sources`` collapse to one entry.
    """
    by_key = {}
    for s in existing or []:
        key = url_key(s.get("url", "")) if isinstance(s, dict) else ""
        if key and isinstance(s.get("n"), int):
            by_key.setdefault(key, s["n"])
    next_n = max(by_key.values(), default=0) + 1
    out, used = [], set()
    for source in sources or []:
        result = SearchResult.from_dict(source)
        if result is None:
            continue
        key = url_key(result.url)
        n = by_key.get(key)
        if n is None:
            n, next_n = next_n, next_n + 1
            by_key[key] = n
        if n in used:
            continue
        used.add(n)
        out.append({**result.to_dict(), "n": n})
    return out


_CITE_RE = re.compile(r"\[(\d{1,2}(?:\s*[,–-]\s*\d{1,2})*)\](?!\()")


def cited_numbers(answer) -> list:
    """Bracketed reference numbers used inline, e.g. "[1]", "[2, 3]", "[1-3]".

    Markdown links ("[1](...)") are ignored. Returns sorted unique numbers.
    """
    found = set()
    for match in _CITE_RE.finditer(answer or ""):
        for part in match.group(1).split(","):
            bounds = re.split(r"\s*[–-]\s*", part.strip())
            try:
                nums = [int(b) for b in bounds if b]
            except ValueError:
                continue
            if len(nums) == 2 and 0 < nums[1] - nums[0] <= 10:
                found.update(range(nums[0], nums[1] + 1))
            else:
                found.update(nums)
    return sorted(n for n in found if n >= 1)


_MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]<>#|~])")


def _md(text) -> str:
    """Escape Markdown control characters in untrusted display text."""
    return _MD_SPECIAL_RE.sub(r"\\\1", str(text or ""))


def web_source_line(source: dict) -> str:
    """One clickable Markdown line: number, title link, publisher, date."""
    when = display_date(source.get("published", ""))
    return (
        f"- **[{source['n']}]** [{_md(source.get('title'))}](<{source['url']}>)"
        f" — {_md(source.get('publisher'))} · {when}"
    )


def web_citation_label(source: dict) -> str:
    """Plain-text label for ``state['citations']``."""
    return f"web [{source['n']}] — {source['url']}"


def _grouped(sources, official_label: str, secondary_label: str) -> list:
    lines = []
    official = [s for s in sources if s.get("official")]
    secondary = [s for s in sources if not s.get("official")]
    for label, group in ((official_label, official), (secondary_label, secondary)):
        if group:
            lines += ["", label] + [web_source_line(s) for s in group]
    return lines


def format_sources_block(answer, web_sources, other_citations=None) -> str:
    """Grouped "Sources used" block for an answer built on structured web results.

    * The answer cites ``[n]`` that match retrieved sources -> only those are
      listed (original numbers kept), split into official / secondary.
    * The answer cites none -> every retrieved source is listed under a clearly
      labelled "Retrieved sources (not cited inline)" section, explicitly NOT
      linked to specific statements.
    * Inline numbers that match no retrieved source are flagged, never mapped.
    * Knowledge-base / legacy citations follow under "Study material".
    """
    # Re-validate: publisher/official are recomputed from the URL, bad entries dropped.
    web_sources = [e for e in (_numbered(s) for s in web_sources or []) if e]
    other = [c for c in (other_citations or []) if c]
    available = {s["n"]: s for s in web_sources}
    cited = cited_numbers(answer)
    matched = [available[n] for n in cited if n in available]
    dangling = [n for n in cited if n not in available]
    if not (web_sources or other or dangling):
        return ""
    lines = [SOURCES_HEADER]
    if matched:
        lines += _grouped(matched, "**Official sources**", "**Secondary sources**")
    elif web_sources:
        lines += [
            "",
            "**Retrieved sources (not cited inline)**",
            "_These sources were retrieved for this question but are not linked "
            "to specific statements above._",
        ]
        lines += _grouped(web_sources, "*Official*", "*Secondary*")
    if dangling:
        refs = ", ".join(f"[{n}]" for n in dangling)
        verb = "does" if len(dangling) == 1 else "do"
        lines += ["", f"_Note: {refs} {verb} not match any retrieved source._"]
    if other:
        lines += ["", "**Study material**"] + [f"- {_md(c)}" for c in other[:MAX_CITATIONS]]
    return "\n".join(lines)


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
