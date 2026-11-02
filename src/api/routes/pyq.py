"""
PYQ routes - Question generation and practice
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from src.agents.pyq.graph import (
    build_question_bank,
    clear_bank,
    generate_from_bank,
    generate_questions,
    get_bank_status,
    get_topic_suggestions,
    parse_questions,
)
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.api.upload_limit import read_pdf_upload
from src.core.errors import GENERIC_ERROR, UserFacingError
from src.core.job_queue import enqueue
from src.core.llm import describe_exception
from src.schemas import (
    BankGenRequest,
    BankStatusResponse,
    ParseRequest,
    ParseResponse,
    QuestionGenRequest,
    TopicSuggestionsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pyq", tags=["PYQ"])


@router.post("/generate")
async def generate(request: QuestionGenRequest, user: dict = Depends(get_current_user)):
    """Generate practice questions (streaming)."""

    def gen():
        for chunk in generate_questions(
            topic=request.topic,
            question_type=request.question_type,
            difficulty=request.difficulty,
            num_questions=request.num_questions,
            marks=request.marks,
        ):
            yield chunk

    return StreamingResponse(guard_stream(gen(), user), media_type="text/plain")


@router.post("/parse", response_model=ParseResponse)
def parse(request: ParseRequest):
    """Parse pasted question text."""
    questions = parse_questions(request.text)
    return {"questions": questions}


@router.get("/topics/{question_type}", response_model=TopicSuggestionsResponse)
async def topics(question_type: str = "mcq"):
    """Get topic suggestions."""
    return {"topics": get_topic_suggestions(question_type)}


# ─────────────────────────────────────────
# PERSONAL PYQ BANK (per-user, grounded on uploaded papers)
# ─────────────────────────────────────────


@router.post("/bank/upload")
async def bank_upload(
    file: UploadFile = File(...),
    sync: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Upload a PYQ PDF into the user's personal grounded question bank.

    Background job by default (poll GET /jobs/{id}); ``?sync=true`` runs inline.
    """
    content = await read_pdf_upload(file)
    filename = file.filename
    user_id = current_user["id"]

    def _work():
        result = build_question_bank(content, filename, user_id)
        return {"success": True, **result}

    if sync:
        try:
            return await run_in_threadpool(_work)  # keep the event loop free
        except UserFacingError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:  # noqa: BLE001 - never echo internal error text
            logger.error("PYQ bank upload failed: %s", describe_exception(e))
            raise HTTPException(status_code=500, detail=GENERIC_ERROR) from None

    job_id = enqueue("pyq_bank_upload", _work, user_id=user_id)
    return {"job_id": job_id, "status": "queued"}


@router.post("/bank/generate")
async def bank_generate(
    request: BankGenRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate questions grounded on the user's own uploaded papers (streaming)."""

    def gen():
        for chunk in generate_from_bank(
            user_id=current_user["id"],
            topic=request.topic,
            question_type=request.question_type,
            num_questions=request.num_questions,
            marks=request.marks,
            difficulty=request.difficulty,
        ):
            yield chunk

    return StreamingResponse(guard_stream(gen(), current_user), media_type="text/plain")


@router.get("/bank/status", response_model=BankStatusResponse)
def bank_status(current_user: dict = Depends(get_current_user)):
    """Whether the user has a personal PYQ bank yet."""
    return get_bank_status(current_user["id"])


@router.post("/bank/clear")
def bank_clear(current_user: dict = Depends(get_current_user)):
    """Delete the user's personal PYQ bank."""
    return clear_bank(current_user["id"])
