"""
NCERT routes - Study sessions and chat

Listing and study handlers are plain ``def`` so FastAPI runs them in its
threadpool: they do blocking work (bucket/file I/O, PDF parsing, model calls)
that must not stall the event loop. Error responses never include server paths,
storage keys or internal exception text.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.agents.ncert.graph import (
    NCERT_TEMPORARILY_UNAVAILABLE_MESSAGE,
    NCERT_UNAVAILABLE_MESSAGE,
    ask_ncert,
    generate_study_session,
    get_chapters,
    get_classes,
    get_subjects,
)
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.core.ncert_storage import NcertNotFound, NcertPdfError, NcertUnavailable
from src.schemas import (
    NCERTChatRequest,
    NCERTListResponse,
    NCERTSessionRequest,
    NCERTSessionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ncert", tags=["NCERT"])


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail=NCERT_TEMPORARILY_UNAVAILABLE_MESSAGE)


@router.get("/classes", response_model=NCERTListResponse)
def list_classes():
    """Get available NCERT classes (``available`` is False when the library is not usable)."""
    try:
        classes = get_classes()
    except NcertUnavailable:
        return {"items": [], "available": False, "message": NCERT_TEMPORARILY_UNAVAILABLE_MESSAGE}
    if not classes:
        return {"items": [], "available": False, "message": NCERT_UNAVAILABLE_MESSAGE}
    return {"items": classes}


@router.get("/subjects/{class_name}", response_model=NCERTListResponse)
def list_subjects(class_name: str):
    """Get subjects for a class."""
    try:
        subjects = get_subjects(class_name)
    except NcertUnavailable:
        raise _unavailable() from None
    if not subjects:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"items": subjects}


@router.get("/chapters/{class_name}/{subject}", response_model=NCERTListResponse)
def list_chapters(class_name: str, subject: str):
    """Get chapters for a subject."""
    try:
        chapters = get_chapters(class_name, subject)
    except NcertUnavailable:
        raise _unavailable() from None
    if not chapters:
        raise HTTPException(status_code=404, detail="Subject not found")
    return {"items": chapters}


@router.post("/study", response_model=NCERTSessionResponse)
def create_study_session(request: NCERTSessionRequest, user: dict = Depends(get_current_user)):
    """Generate study session with notes.

    Requires a valid session: the dependency rejects the request before any
    PDF read, model call, embedding or index write happens.
    """
    try:
        return generate_study_session(
            class_name=request.class_name,
            subject=request.subject,
            chapter=request.chapter,
        )
    except NcertNotFound:
        raise HTTPException(status_code=404, detail="Chapter not found") from None
    except NcertUnavailable:
        raise _unavailable() from None
    except NcertPdfError as e:
        # Messages of this type are fixed, user-facing strings (no paths/internals).
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        logger.error("NCERT study session failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=500, detail="Could not generate the study session. Please try again."
        ) from None


@router.post("/chat")
async def chat_ncert(request: NCERTChatRequest, user: dict = Depends(get_current_user)):
    """Chat about NCERT chapter (streaming)."""

    def generate():
        for chunk in ask_ncert(
            question=request.question,
            class_name=request.class_name,
            subject=request.subject,
            chapter=request.chapter,
            chat_history=[m.model_dump() for m in request.chat_history]
            if request.chat_history
            else None,
        ):
            yield chunk

    return StreamingResponse(guard_stream(generate(), user), media_type="text/plain")
