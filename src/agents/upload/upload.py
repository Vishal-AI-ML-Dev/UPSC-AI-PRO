"""
Upload routes - PDF processing and chat
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from src.agents.upload.graph import ask_upload, process_upload
from src.schemas import UploadChatRequest

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post("/process")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload and process a PDF file."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()
        result = process_upload(content, file.filename)
        return {
            "success": True,
            "filename": file.filename,
            "hash": result["hash"],
            "book_info": result["book_info"],
            "notes": result["notes"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@router.post("/chat")
async def chat_upload(req: UploadChatRequest):
    """Chat about uploaded PDF."""

    def generate():
        for chunk in ask_upload(
            question=req.question,
            pdf_hash=req.pdf_hash,
            book_info=req.book_info,
            chat_history=req.chat_history,
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain")


@router.post("/chat/sync")
async def chat_upload_sync(req: UploadChatRequest):
    """Chat about uploaded PDF (non-streaming)."""
    response = ""
    for chunk in ask_upload(
        question=req.question,
        pdf_hash=req.pdf_hash,
        book_info=req.book_info,
        chat_history=req.chat_history,
    ):
        response += chunk
    return {"response": response}
