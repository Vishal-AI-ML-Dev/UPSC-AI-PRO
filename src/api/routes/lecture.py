"""
Lecture routes - YouTube lecture processing
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.agents.lecture.graph import (
    ask_lecture,
    build_lecture_chat_index,
    extract_video_id,
    process_lecture,
    process_lecture_from_text,
)
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.core.job_queue import enqueue
from src.schemas import LectureChatRequest, LectureRequest, LectureTextRequest

router = APIRouter(prefix="/lecture", tags=["Lecture"])


def _lecture_payload(result, fallback_video_id=None):
    return {
        "notes": result["notes"],
        "topic_info": result["topic_info"],
        "video_id": result.get("video_id", fallback_video_id),
        "mindmap_html": result.get("mindmap_html", ""),
        "questions_html": result.get("questions_html", ""),
    }


@router.post("/process")
async def process(
    request: LectureRequest,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Process a YouTube lecture into notes.

    Runs as a background job by default (returns a job id to poll via
    GET /jobs/{id}). Pass ``?sync=true`` to run inline and get the notes back.
    """
    video_id = extract_video_id(request.youtube_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    youtube_url = request.youtube_url
    medium = request.medium

    def _work():
        result = process_lecture(youtube_url, medium)
        try:
            build_lecture_chat_index(result["video_id"], result.get("_transcript", ""))
        except Exception:
            pass
        return _lecture_payload(result, video_id)

    if sync:
        try:
            result = process_lecture(youtube_url, medium)
            background_tasks.add_task(
                build_lecture_chat_index, result["video_id"], result.get("_transcript", "")
            )
            return _lecture_payload(result, video_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    job_id = enqueue("lecture_process", _work, user_id=current_user.get("id"))
    return {"job_id": job_id, "status": "queued"}


@router.post("/process-text")
async def process_text(
    request: LectureTextRequest,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Build lecture notes from a pasted transcript (no YouTube fetch).

    Background job by default; ``?sync=true`` runs inline.
    """
    transcript = request.transcript
    medium = request.medium

    def _work():
        result = process_lecture_from_text(transcript, medium)
        try:
            build_lecture_chat_index(result["video_id"], result.get("_transcript", ""))
        except Exception:
            pass
        return _lecture_payload(result)

    if sync:
        try:
            result = process_lecture_from_text(transcript, medium)
            background_tasks.add_task(
                build_lecture_chat_index, result["video_id"], result.get("_transcript", "")
            )
            return _lecture_payload(result)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    job_id = enqueue("lecture_process_text", _work, user_id=current_user.get("id"))
    return {"job_id": job_id, "status": "queued"}


@router.post("/chat")
async def chat(request: LectureChatRequest, user: dict = Depends(get_current_user)):
    """Chat about lecture (streaming)."""

    def generate():
        for chunk in ask_lecture(
            question=request.question,
            video_id=request.video_id,
            topic_info=request.topic_info,
            chat_history=[m.model_dump() for m in request.chat_history]
            if request.chat_history
            else None,
        ):
            yield chunk

    return StreamingResponse(guard_stream(generate(), user), media_type="text/plain")
