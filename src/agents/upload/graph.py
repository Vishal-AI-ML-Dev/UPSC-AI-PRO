"""
Upload Agent - PDF processing, book detection, notes, RAG chat
"""

import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict

from pypdf import PdfReader

from src.agents.upload.prompts import BOOK_DETECTION_PROMPT, CHAT_PROMPT, NOTES_PROMPT
from src.core.errors import UserFacingError
from src.core.llm import describe_exception, first_word_yes_no, get_llm, message_text
from src.core.prompt_safety import format_chat_history, harden_untrusted
from src.core.vector_store import (
    create_vector_store,
    make_persist_key,
    similarity_search,
    vector_store_exists,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# PDF HASH CACHE — avoid reprocessing
# ─────────────────────────────────────────

_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 4 * 1024 * 1024  # notes + book_info, UTF-8


def _upload_key(user_id: str, pdf_hash: str) -> str:
    """Vector key of a user's uploaded document. The owner id is part of the key,
    so a user can only ever reach collections they created. Legacy global
    ``upload_<hash>`` collections are never read."""
    return make_persist_key("upload", user_id, pdf_hash)


class _UploadCache:
    """Per-user LRU of processed-upload results, bounded by entries AND bytes.

    Keyed by ``(user_id, pdf_hash)`` so one user's upload never satisfies another
    user's request. Extracted text is never stored (it is not returned to clients
    and can be megabytes); an entry larger than the whole budget is not cached.

    Thread-safe: background jobs run in a thread pool, so every read/write of the
    dict and the byte counter happens under one lock. Only these short dict
    operations are locked - never PDF extraction, LLM or vector-store work.
    """

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES, max_bytes: int = _CACHE_MAX_BYTES):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._data: OrderedDict[tuple[str, str], tuple[dict, int]] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @staticmethod
    def _size(entry: dict) -> int:
        return len(json.dumps(entry, ensure_ascii=False, default=str).encode("utf-8"))

    def get(self, user_id: str, pdf_hash: str) -> dict | None:
        key = (user_id, pdf_hash)
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            self._data.move_to_end(key)
            return dict(item[0])

    def put(self, user_id: str, pdf_hash: str, entry: dict) -> None:
        key = (user_id, pdf_hash)
        size = self._size(entry)  # serialisation outside the lock
        copy = dict(entry)
        with self._lock:
            self._discard_locked(key)
            if size > self.max_bytes:
                return
            self._data[key] = (copy, size)
            self._total_bytes += size
            while self._data and (
                len(self._data) > self.max_entries or self._total_bytes > self.max_bytes
            ):
                _, (_, old_size) = self._data.popitem(last=False)
                self._total_bytes -= old_size

    def _discard_locked(self, key: tuple[str, str]) -> None:
        item = self._data.pop(key, None)
        if item is not None:
            self._total_bytes -= item[1]

    def discard(self, user_id: str, pdf_hash: str) -> None:
        with self._lock:
            self._discard_locked((user_id, pdf_hash))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._total_bytes = 0

    def snapshot(self) -> tuple[int, int, int]:
        """(entries, tracked bytes, recomputed bytes) - for consistency checks."""
        with self._lock:
            return len(self._data), self._total_bytes, sum(sz for _, sz in self._data.values())


_upload_cache = _UploadCache()


def _compute_hash(content: bytes) -> str:
    """Compute MD5 hash of PDF content."""
    return hashlib.md5(content).hexdigest()


# ─────────────────────────────────────────
# PDF READING
# ─────────────────────────────────────────


def extract_pdf_text(file_content: bytes, filename: str) -> tuple[str, str]:
    """Extract text from PDF bytes. Returns (text, hash)."""
    pdf_hash = _compute_hash(file_content)

    try:
        import io

        pdf = PdfReader(io.BytesIO(file_content))
        text = ""
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"

        if not text.strip():
            raise UserFacingError(
                "📄 We couldn’t read any text from this PDF — it looks "
                "like a scanned or image-based document. Please upload a "
                "text-based PDF (one where you can select and copy the text)."
            )

        logger.info("PDF extracted: %s chars", len(text))
        return text, pdf_hash

    except UserFacingError:
        raise
    except Exception as e:
        logger.error("PDF extraction failed: %s", describe_exception(e))
        raise UserFacingError(
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
        logger.error("Book detection failed: %s", describe_exception(e))
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


def _collection_exists(key: str) -> bool:
    try:
        return bool(vector_store_exists(key))
    except Exception as e:  # noqa: BLE001 - unknown -> rebuild, never serve stale
        logger.warning("Upload collection check failed: %s", describe_exception(e))
        return False


def process_upload(file_content: bytes, filename: str, *, user_id: str) -> dict:
    """Process an uploaded PDF for ``user_id``: notes + the user's own chat index."""
    if not user_id:
        raise ValueError("process_upload requires the owner's user_id")

    # Same user, same file, collection still there -> reuse the earlier result.
    cached = _upload_cache.get(user_id, _compute_hash(file_content))
    if cached is not None and _collection_exists(_upload_key(user_id, cached["hash"])):
        logger.info("Upload cache hit")
        return cached

    # Extract text
    text, pdf_hash = extract_pdf_text(file_content, filename)

    # Detect book
    book_info = detect_book(text)
    logger.info("Book detected (confidence %s)", book_info.get("confidence"))

    # Relevance gate - refuse non-UPSC documents (resume / invoice / novel / random files)
    if not book_info.get("relevant", True):
        raise UserFacingError(
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
        logger.error("Notes generation failed: %s", describe_exception(e))
        notes = "⚠️ Notes generation failed. Please retry."

    if notes and not notes.startswith("⚠️"):
        notes = (
            "> ⚠️ AI-generated notes - cross-check key facts, dates, and names with a standard source."
            + chr(10)
            + chr(10)
            + notes
        )

    # Create the owner's vector store for chat
    key = _upload_key(user_id, pdf_hash)
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

    # Cache result (per user; no extracted text)
    result = {
        "notes": notes,
        "book_info": book_info,
        "hash": pdf_hash,
        "filename": filename,
        "mindmap_html": "",
        "questions_html": "",
    }
    _upload_cache.put(user_id, pdf_hash, result)

    return result


# ─────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────


def ask_upload(
    question: str,
    pdf_hash: str,
    book_info: dict | None = None,
    chat_history: list | None = None,
    *,
    user_id: str,
):
    """Chat about the caller's own uploaded PDF (streaming)."""
    if not question or not question.strip():
        yield "Please ask a specific question about the document."
        return

    # Fail closed: without an owner there is no collection to read.
    if not user_id or not pdf_hash:
        yield "Please upload and process the PDF first."
        return

    # Load vector store (Qdrant in prod, local Chroma fallback)
    from src.core.vector_store import load_vector_store

    db = load_vector_store(_upload_key(user_id, pdf_hash))
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
        logger.error("Upload chat failed: %s", describe_exception(e))
        yield "⚠️ Could not process your question. Please try again."


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────


def get_cached_upload(user_id: str, pdf_hash: str) -> dict | None:
    """Get a user's cached upload result (never another user's)."""
    return _upload_cache.get(user_id, pdf_hash)


def clear_cache():
    """Clear upload cache."""
    _upload_cache.clear()
    logger.info("Upload cache cleared")
