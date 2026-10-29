"""
Upload routes - PDF processing and chat
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from src.agents.upload.graph import ask_upload, process_upload
from src.api.deps import get_current_user
from src.api.stream_guard import guard_stream
from src.core.job_queue import enqueue
from src.schemas import ChatRequest

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
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()
    filename = file.filename

    def _work():
        result = process_upload(content, filename)
        return {
            "success": True,
            "filename": filename,
            "hash": result["hash"],
            "book_info": result["book_info"],
            "notes": result["notes"],
        }

    if sync:
        try:
            return _work()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    job_id = enqueue("upload_process", _work, user_id=current_user.get("id"))
    return {"job_id": job_id, "status": "queued"}


@router.post("/chat")
async def chat_upload(
    request: ChatRequest,
    pdf_hash: str,
    book_info: dict = None,
    user: dict = Depends(get_current_user),
):
    """Chat about uploaded PDF."""

    def generate():
        for chunk in ask_upload(
            question=request.question,
            pdf_hash=pdf_hash,
            book_info=book_info,
            chat_history=request.chat_history,
        ):
            yield chunk

    return StreamingResponse(guard_stream(generate(), user), media_type="text/plain")
