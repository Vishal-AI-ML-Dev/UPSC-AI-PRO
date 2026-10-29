"""
Upload Agent - PDF processing, book detection, notes, RAG chat
"""

import hashlib
import logging
import re

from pypdf import PdfReader

from src.agents.upload.prompts import BOOK_DETECTION_PROMPT, CHAT_PROMPT, NOTES_PROMPT
from src.core.llm import first_word_yes_no, get_llm, message_text
from src.core.prompt_safety import format_chat_history, harden_untrusted
from src.core.vector_store import create_vector_store, make_persist_key, similarity_search

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# PDF HASH CACHE — avoid reprocessing
# ─────────────────────────────────────────

_processed_cache: dict[str, dict] = {}


def _compute_hash(content: bytes) -> str:
    """Compute MD5 hash of PDF content."""
    return hashlib.md5(content).hexdigest()


# ─────────────────────────────────────────
# PDF READING
# ─────────────────────────────────────────


def extract_pdf_text(file_content: bytes, filename: str) -> tuple[str, str]:
    """Extract text from PDF bytes. Returns (text, hash)."""
    pdf_hash = _compute_hash(file_content)

    # Check cache
    if pdf_hash in _processed_cache:
        logger.info(f"Cache hit for {filename}")
        return _processed_cache[pdf_hash]["text"], pdf_hash

    try:
        import io

        pdf = PdfReader(io.BytesIO(file_content))
        text = ""
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"

        if not text.strip():
            raise ValueError(
                "📄 We couldn’t read any text from this PDF — it looks "
                "like a scanned or image-based document. Please upload a "
                "text-based PDF (one where you can select and copy the text)."
            )

        logger.info(f"PDF extracted: {filename} — {len(text)} chars")
        return text, pdf_hash

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        raise ValueError(
            "📄 We couldn’t open this PDF. The file may be corrupted "
            "or password-protected. Please try a different file."
        )


# ─────────────────────────────────────────
# BOOK DETECTION
# ─────────────────────────────────────────


UNCONFIRMED_SOURCE = "Uploaded document (book not confirmed)"


def _source_name(book_info: dict | None) -> str:
    """Book name to show the model as the source - only when detection is High.

    Detection guesses from the first 2,000 characters against a list of famous
    books, so a Low/Medium guess must not turn a student's own notes into
    "Laxmikanth" in notes or chat.
    """
    info = book_info or {}
    book = str(info.get("book") or "").strip()
    confidence = re.findall(r"[a-z]+", str(info.get("confidence") or "").lower())
    if book and book.lower() != "unknown" and confidence[:1] == ["high"]:
        return book
    return UNCONFIRMED_SOURCE


def detect_book(text: str) -> dict:
    """Detect if PDF is a known UPSC book."""
    try:
        chain = BOOK_DETECTION_PROMPT | get_llm()
        res = message_text(
            chain.invoke({"text": harden_untrusted(text[:2000], label="uploaded document")}).content
        )

        result = {
            "book": "Unknown",
            "author": "Unknown",
            "subject": "General",
            "paper": "Check Syllabus",
            "confidence": "Low",
            "relevant": True,
        }

        for line in res.strip().split("\n"):
            line = line.lstrip("*-#> ").replace("**", "").strip()
            if line.startswith("BOOK:"):
                result["book"] = line.replace("BOOK:", "").strip()
            elif line.startswith("AUTHOR:"):
                result["author"] = line.replace("AUTHOR:", "").strip()
            elif line.startswith("SUBJECT:"):
                result["subject"] = line.replace("SUBJECT:", "").strip()
            elif line.startswith("UPSC_PAPER:"):
                result["paper"] = line.replace("UPSC_PAPER:", "").strip()
            elif line.startswith("CONFIDENCE:"):
                result["confidence"] = line.replace("CONFIDENCE:", "").strip()
            elif line.startswith("RELEVANT:"):
                verdict = first_word_yes_no(line.replace("RELEVANT:", ""))
                if verdict is not None:
                    result["relevant"] = verdict

        return result

    except Exception as e:
        logger.error(f"Book detection failed: {e}")
        return {
            "book": "Unknown",
            "author": "Unknown",
            "subject": "General",
            "paper": "Check Syllabus",
            "confidence": "Low",
            "relevant": True,
        }


# ─────────────────────────────────────────
# MAIN PROCESSING
# ─────────────────────────────────────────


def process_upload(file_content: bytes, filename: str) -> dict:
    """Process uploaded PDF and generate notes."""

    # Extract text
    text, pdf_hash = extract_pdf_text(file_content, filename)

    # Check cache for full processing
    if pdf_hash in _processed_cache and "notes" in _processed_cache[pdf_hash]:
        logger.info(f"Full cache hit for {filename}")
        return _processed_cache[pdf_hash]

    # Detect book
    book_info = detect_book(text)
    logger.info(f"Book detected: {book_info['book']} ({book_info['confidence']})")

    # Relevance gate - refuse non-UPSC documents (resume / invoice / novel / random files)
    if not book_info.get("relevant", True):
        raise ValueError(
            "📄 This document doesn’t look like study material (it appears to be a personal or unrelated file). Please upload exam notes, books, notifications, or other study-related PDFs."
        )

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
        notes = message_text(
            chain.invoke(
                {
                    "text": harden_untrusted(text_for_notes, label="uploaded document"),
                    "book_name": _source_name(book_info),
                    "subject": book_info["subject"],
                    "paper": book_info["paper"],
                }
            ).content
        )
        if not notes.strip():
            raise ValueError("model returned no text")
    except Exception as e:
        logger.error(f"Notes generation failed: {e}")
        notes = "⚠️ Notes generation failed. Please retry."

    if notes and not notes.startswith("⚠️"):
        notes = (
            "> ⚠️ AI-generated notes - cross-check key facts, dates, and names with a standard source."
            + chr(10)
            + chr(10)
            + notes
        )

    # Create vector store for chat
    key = make_persist_key("upload", pdf_hash)
    create_vector_store(
        text,
        persist_key=key,
        metadata={
            "source_type": "upload",
            "source_title": filename,
            "filename": filename,
            "pdf_hash": pdf_hash,
            "book": book_info.get("book"),
            "subject": book_info.get("subject"),
            "paper": book_info.get("paper"),
        },
    )

    # Cache result
    result = {
        "notes": notes,
        "book_info": book_info,
        "text": text,
        "hash": pdf_hash,
        "filename": filename,
        "mindmap_html": "",
        "questions_html": "",
    }
    _processed_cache[pdf_hash] = result

    return result


# ─────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────


def ask_upload(
    question: str,
    pdf_hash: str,
    book_info: dict | None = None,
    chat_history: list | None = None,
):
    """Chat about uploaded PDF (streaming)."""
    if not question or not question.strip():
        yield "Please ask a specific question about the document."
        return

    # Load vector store (Qdrant in prod, local Chroma fallback)
    from src.core.vector_store import load_vector_store

    key = make_persist_key("upload", pdf_hash)
    db = load_vector_store(key)
    if db is None:
        yield "Please upload and process the PDF first."
        return

    ctx = similarity_search(db, question.strip(), k=5, label="upload")
    if not ctx:
        yield "Could not find relevant content. Try rephrasing your question."
        return

    book_name = _source_name(book_info)
    subject = book_info.get("subject", "General") if book_info else "General"
    paper = book_info.get("paper", "Check Syllabus") if book_info else "Check Syllabus"

    try:
        chain = CHAT_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream(
            {
                "context": harden_untrusted(ctx, label="document excerpts"),
                "question": question.strip(),
                "book_name": book_name,
                "subject": subject,
                "paper": paper,
                "history": format_chat_history(chat_history),
            }
        ):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                produced = True
                yield text
        if not produced:
            raise ValueError("model returned no text")
    except Exception as e:
        logger.error(f"Upload chat failed: {e}")
        yield "⚠️ Could not process your question. Please try again."


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────


def get_cached_upload(pdf_hash: str) -> dict | None:
    """Get cached upload data."""
    return _processed_cache.get(pdf_hash)


def clear_cache():
    """Clear upload cache."""
    global _processed_cache
    _processed_cache = {}
    logger.info("Upload cache cleared")
