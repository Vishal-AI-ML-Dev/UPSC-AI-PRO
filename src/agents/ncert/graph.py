"""
NCERT Agent - PDF reading, notes generation, RAG chat
"""

import logging
import os
import re
from functools import lru_cache

from langchain_core.documents import Document
from pypdf import PdfReader

from src.agents.ncert.prompts import CHAT_PROMPT, NOTES_PROMPT
from src.core.config import settings
from src.core.llm import describe_exception, get_llm, message_text
from src.core.vector_store import (
    get_text_splitter,
    load_vector_store,
    make_persist_key,
    similarity_search_with_sources,
    upsert_documents,
    vector_store_exists,
)

logger = logging.getLogger(__name__)

BASE = settings.ncert_data_dir

# ─────────────────────────────────────────
# SUBJECT DETECTION
# ─────────────────────────────────────────

SUBJECT_MAP = {
    "history": ("History / Indian Heritage & Culture", "GS Paper 1"),
    "geography": ("Geography", "GS Paper 1"),
    "polity": ("Indian Polity & Constitution", "GS Paper 2"),
    "civics": ("Indian Polity & Governance", "GS Paper 2"),
    "economy": ("Indian Economy", "GS Paper 3"),
    "economics": ("Indian Economy", "GS Paper 3"),
    "science": ("Science & Technology", "GS Paper 3"),
    "biology": ("Science & Technology / Environment", "GS Paper 3"),
    "physics": ("Science & Technology", "GS Paper 3"),
    "chemistry": ("Science & Technology", "GS Paper 3"),
    "environment": ("Environment & Ecology", "GS Paper 3"),
    "sociology": ("Indian Society", "GS Paper 1"),
    "political": ("Indian Polity & Governance", "GS Paper 2"),
    "social": ("Indian Society & Social Issues", "GS Paper 1"),
}


GENERAL_AREA = ("General Studies", "Check UPSC Syllabus")
SOCIAL_SCIENCE_AREA = (
    "Social Science (History, Geography, Polity, Economy, Society)",
    "GS Papers 1-3, depending on the topic",
)

# Social Science books mix several UPSC areas, so the chapter title decides.
# First matching rule wins; patterns match whole words (or word prefixes).
SOCIAL_SCIENCE_CHAPTER_RULES = [
    (
        r"\b(democracy|governance|govern\w*|constitution|panchayat\w*|rulers?|state)\b",
        ("Indian Polity & Governance", "GS Paper 2"),
    ),
    (
        r"\b(econom\w*|money|barter|markets?|banks?|financ\w*|infrastructure|work)\b",
        ("Indian Economy", "GS Paper 3"),
    ),
    (
        r"\b(oceans?|continents?|landforms?|geograph\w*|weather|climates?)\b",
        ("Geography", "GS Paper 1"),
    ),
    (
        r"\b(history|civili[sz]ation|cultur\w*|empires?|kingdoms?|centuries|gupta"
        r"|reorganisation|sacred|bharat|cities)\b",
        ("History / Indian Heritage & Culture", "GS Paper 1"),
    ),
    (
        r"\b(family|community|diversity|home to many)\b",
        ("Indian Society & Social Issues", "GS Paper 1"),
    ),
]


def detect_subject_area(subject: str, class_name: str = "", chapter: str = "") -> tuple[str, str]:
    """Detect UPSC subject area from the NCERT subject folder (and chapter title).

    Subject names are compared as whole words, so ``science`` never matches
    inside ``socialscience``. Social Science chapters are classified by their
    title; unrecognised ones get a broad Social Science label.
    """
    key = re.sub(r"\(.*?\)", " ", subject.lower())  # drop "(Part-1)" etc.
    if re.sub(r"[^a-z]", "", key) == "socialscience":
        title = re.sub(r"^\d+\s*-\s*|\.pdf$", "", chapter.lower().strip())
        for pattern, area in SOCIAL_SCIENCE_CHAPTER_RULES:
            if re.search(pattern, title):
                return area
        return SOCIAL_SCIENCE_AREA

    best_match = None
    best_len = 0
    for word in re.findall(r"[a-z]+", key):
        if word in SUBJECT_MAP and len(word) > best_len:
            best_match = SUBJECT_MAP[word]
            best_len = len(word)
    return best_match or GENERAL_AREA


# ─────────────────────────────────────────
# DROPDOWN HELPERS
# ─────────────────────────────────────────


@lru_cache(maxsize=1)
def get_classes() -> list[str]:
    """Get available NCERT classes."""
    if not os.path.exists(BASE):
        logger.error(f"NCERT base directory not found: {BASE}")
        return []
    return sorted(os.listdir(BASE))


@lru_cache(maxsize=50)
def get_subjects(class_name: str) -> list[str]:
    """Get subjects for a class."""
    path = f"{BASE}/{class_name}"
    if not os.path.exists(path):
        return []
    return sorted(os.listdir(path))


@lru_cache(maxsize=200)
def get_chapters(class_name: str, subject: str) -> list[str]:
    """Get chapters for a subject."""
    path = f"{BASE}/{class_name}/{subject}"
    if not os.path.exists(path):
        return []
    return sorted(os.listdir(path))


# ─────────────────────────────────────────
# PDF READING
# ─────────────────────────────────────────


def read_chapter_pages(
    class_name: str, subject: str, chapter: str
) -> tuple[list[tuple[int, str]], str]:
    """Read a chapter PDF page by page.

    Returns ``([(pdf_page, text), ...], path)``. ``pdf_page`` is the 1-based
    physical page index inside the PDF file - NOT the printed book page number,
    which is never guessed. Pages without extractable text are skipped.
    """
    path = f"{BASE}/{class_name}/{subject}/{chapter}"
    if not os.path.exists(path):
        raise FileNotFoundError(f"PDF not found: {path}")

    pdf = PdfReader(path)
    pages = []
    for number, page in enumerate(pdf.pages, start=1):
        extracted = page.extract_text()
        if extracted and extracted.strip():
            pages.append((number, extracted))

    if not pages:
        raise ValueError(
            "Could not extract text from this PDF.\n"
            "The file may be scanned/image-based. Only text-based PDFs are supported."
        )

    logger.info(f"PDF read: {class_name}/{subject}/{chapter} — {len(pages)} pages")
    return pages, path


def read_chapter_pdf(class_name: str, subject: str, chapter: str) -> tuple[str, str]:
    """Read chapter PDF and return text + path."""
    pages, path = read_chapter_pages(class_name, subject, chapter)
    return "".join(text + "\n" for _, text in pages), path


def ncert_persist_key(class_name: str, subject: str, chapter: str) -> str:
    """Index key for one chapter. ``v2`` = page-aware chunks; older page-less
    indexes (plain ``ncert_...`` keys) are never reused."""
    return make_persist_key("ncert", "v2", class_name, subject, chapter)


def build_chapter_documents(pages: list[tuple[int, str]], metadata: dict) -> list[Document]:
    """Split each page separately so every chunk keeps its document/page/chunk metadata."""
    splitter = get_text_splitter()
    docs: list[Document] = []
    for pdf_page, text in pages:
        for piece in splitter.split_text(text):
            docs.append(
                Document(
                    page_content=piece,
                    metadata={**metadata, "pdf_page": pdf_page, "chunk_index": len(docs)},
                )
            )
    return docs


# ─────────────────────────────────────────
# STUDY SESSION
# ─────────────────────────────────────────


def generate_study_session(class_name: str, subject: str, chapter: str) -> dict:
    """Generate complete study session with notes, mindmap, questions."""

    # Read PDF (page by page, so chunks can keep their page reference)
    pages, path = read_chapter_pages(class_name, subject, chapter)
    text = "".join(page_text + "\n" for _, page_text in pages)

    # Detect subject area
    subject_area, paper = detect_subject_area(subject, class_name, chapter)

    # Smart truncation for notes
    max_chars = 14000
    if len(text) > max_chars:
        mid_start = len(text) // 2 - 1000
        text_for_notes = (
            text[:8000]
            + "\n\n[...]\n\n"
            + text[mid_start : mid_start + 2000]
            + "\n\n[...]\n\n"
            + text[-2000:]
        )
    else:
        text_for_notes = text

    # Generate notes
    try:
        chain = NOTES_PROMPT | get_llm()
        res = chain.invoke(
            {
                "text": text_for_notes,
                "class_name": class_name,
                "subject": subject,
                "subject_area": subject_area,
                "paper": paper,
            }
        )
        notes = message_text(getattr(res, "content", res)).strip()
        if not notes:
            raise ValueError("model returned no text content")
    except Exception as e:
        # Class/status/location only - never the provider message or the text.
        logger.error(
            "Notes generation failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
        notes = "⚠️ Notes generation failed. Please retry."

    # Create the page-aware vector store for chat (idempotent: reuse if built)
    key = ncert_persist_key(class_name, subject, chapter)
    if not vector_store_exists(key):
        docs = build_chapter_documents(
            pages,
            {
                "persist_key": key,
                "source_type": "ncert",
                "class_name": class_name,
                "subject": subject,
                "chapter": chapter,
                "document": os.path.basename(path),
                "source_title": f"NCERT {class_name} {subject} - {chapter}",
                "source_path": path,
            },
        )
        upsert_documents(key, docs, rebuild=False)

    # Generate study aids (skip when notes failed)
    if notes and "Notes generation failed" not in notes:
        from src.core.study_aids import generate_study_aids

        mindmap_html, questions_html = generate_study_aids(text_for_notes, subject, paper)
    else:
        mindmap_html, questions_html = "", ""

    return {
        "notes": notes,
        "mindmap_html": mindmap_html,
        "questions_html": questions_html,
        "chapter_path": path,
    }


# ─────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────


def ask_ncert(
    question: str,
    class_name: str,
    subject: str,
    chapter: str,
    chat_history: list | None = None,
):
    """Chat about NCERT chapter (streaming)."""
    if not question or not question.strip():
        yield "Please ask a specific question about the chapter."
        return

    # Load the page-aware vector store (Qdrant in prod, local Chroma fallback)
    key = ncert_persist_key(class_name, subject, chapter)
    db = load_vector_store(key)
    if db is None:
        yield "Please generate study session first before chatting."
        return

    found = similarity_search_with_sources(db, question.strip(), k=5, label="ncert")
    sources = number_sources(found["chunks"])
    if not sources:
        yield NOT_IN_EXCERPTS
        return

    subject_area, paper = detect_subject_area(subject, class_name, chapter)

    answer = ""
    try:
        chain = CHAT_PROMPT | get_llm()
        for chunk in chain.stream(
            {
                "ctx": format_source_context(sources),
                "q": question.strip(),
                "class_name": class_name,
                "subject": subject,
                "subject_area": subject_area,
                "paper": paper,
            }
        ):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                answer += text
                yield text
    except Exception as e:
        logger.error("NCERT chat failed: %s", describe_exception(e), extra={"safe_summary": True})
        yield "⚠️ Could not process your question. Please try again."
        return
    footer = source_footer(answer, sources)
    if footer:
        yield footer


# ─────────────────────────────────────────
# EVIDENCE / CITATIONS (deterministic, no model involved)
# ─────────────────────────────────────────

NOT_IN_EXCERPTS = "The retrieved chapter excerpts do not contain this information."
_CITATION = re.compile(r"\[S(\d+)\]")


def number_sources(chunks: list[dict]) -> list[dict]:
    """Label retrieved chunks S1..Sn with references taken only from their metadata."""
    sources = []
    for i, chunk in enumerate(chunks or [], start=1):
        md = chunk.get("metadata") or {}
        document = md.get("document") or md.get("source_title") or md.get("chapter") or "chapter"
        page = md.get("pdf_page")
        where = f"PDF p.{page}" if page is not None else "page not recorded"
        sources.append(
            {"label": f"S{i}", "ref": f"{document}, {where}", "excerpt": chunk.get("excerpt", "")}
        )
    return sources


def format_source_context(sources: list[dict]) -> str:
    return "\n\n".join(f"[{s['label']}] ({s['ref']})\n{s['excerpt']}" for s in sources)


def source_footer(answer: str, sources: list[dict]) -> str:
    """List the sources actually cited; flag answers with no valid citation.

    This checks citation FORM only (a [S#] tag that matches a retrieved source);
    it cannot prove the cited text supports the sentence.
    """
    valid = {s["label"] for s in sources}
    cited = [f"S{n}" for n in dict.fromkeys(_CITATION.findall(answer)) if f"S{n}" in valid]
    lines = []
    if cited:
        by_label = {s["label"]: s["ref"] for s in sources}
        lines.append("\n\nSources (retrieved chapter excerpts):")
        lines.extend(f"- [{label}] {by_label[label]}" for label in cited)
    elif NOT_IN_EXCERPTS.lower() not in answer.lower():
        lines.append(
            "\n\n⚠️ This answer cites none of the retrieved chapter excerpts; "
            "verify it against the chapter before relying on it."
        )
    return "\n".join(lines)
