"""
Upload routes - PDF processing and chat
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from src.agents.upload.graph import ask_upload, process_upload
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.api.upload_limit import read_pdf_upload
from src.core.errors import GENERIC_ERROR, UserFacingError
from src.core.job_queue import enqueue
from src.core.llm import describe_exception
from src.schemas import PDF_HASH_PATTERN, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post("/process")
async def upload_pdf(
    file: UploadFile = File(...),
    sync: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Upload and process a PDF.

    The heavy PDF parse + embedding runs as a background job by default and
    returns a job id to poll (GET /jobs/{id}). Pass ``?sync=true`` to run it
    inline and get the notes back directly in the response.
    """
    content = await read_pdf_upload(file)
    filename = file.filename
    user_id = current_user["id"]

    def _work():
        result = process_upload(content, filename, user_id=user_id)
        return {
            "success": True,
            "filename": filename,
            "hash": result["hash"],
            "book_info": result["book_info"],
            "notes": result["notes"],
        }

    if sync:
        try:
            return await run_in_threadpool(_work)  # keep the event loop free
        except UserFacingError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:  # noqa: BLE001 - never echo internal error text
            logger.error("Upload processing failed: %s", describe_exception(e))
            raise HTTPException(status_code=500, detail=GENERIC_ERROR) from None

    job_id = enqueue("upload_process", _work, user_id=user_id)
    return {"job_id": job_id, "status": "queued"}


@router.post("/chat")
async def chat_upload(
    request: ChatRequest,
    pdf_hash: str = Query(..., pattern=PDF_HASH_PATTERN),
    book_info: dict = None,
    user: dict = Depends(get_current_user),
):
    """Chat about the caller's own uploaded PDF."""
    user_id = user["id"]

    def generate():
        for chunk in ask_upload(
            question=request.question,
            pdf_hash=pdf_hash,
            book_info=book_info,
            chat_history=request.chat_history,
            user_id=user_id,
        ):
            yield chunk

    return StreamingResponse(guard_stream(generate(), user), media_type="text/plain")
