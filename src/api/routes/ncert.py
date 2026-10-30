"""
NCERT routes - Study sessions and chat
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.agents.ncert.graph import (
    NCERT_UNAVAILABLE_MESSAGE,
    ask_ncert,
    generate_study_session,
    get_chapters,
    get_classes,
    get_subjects,
)
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.schemas import (
    NCERTChatRequest,
    NCERTListResponse,
    NCERTSessionRequest,
    NCERTSessionResponse,
)

router = APIRouter(prefix="/ncert", tags=["NCERT"])


@router.get("/classes", response_model=NCERTListResponse)
async def list_classes():
    """Get available NCERT classes (``available`` is False when no PDFs are installed)."""
    classes = get_classes()
    if not classes:
        return {"items": [], "available": False, "message": NCERT_UNAVAILABLE_MESSAGE}
    return {"items": classes}


@router.get("/subjects/{class_name}", response_model=NCERTListResponse)
async def list_subjects(class_name: str):
    """Get subjects for a class."""
    subjects = get_subjects(class_name)
    if not subjects:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    return {"items": subjects}


@router.get("/chapters/{class_name}/{subject}", response_model=NCERTListResponse)
async def list_chapters(class_name: str, subject: str):
    """Get chapters for a subject."""
    chapters = get_chapters(class_name, subject)
    if not chapters:
        raise HTTPException(status_code=404, detail=f"Subject '{subject}' not found")
    return {"items": chapters}


@router.post("/study", response_model=NCERTSessionResponse)
async def create_study_session(
    request: NCERTSessionRequest, user: dict = Depends(get_current_user)
):
    """Generate study session with notes.

    Requires a valid session: the dependency rejects the request before any
    PDF read, model call, embedding or index write happens.
    """
    try:
        result = generate_study_session(
            class_name=request.class_name,
            subject=request.subject,
            chapter=request.chapter,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")


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
