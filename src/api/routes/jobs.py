"""Job status route - poll a background job by id."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_current_user
from src.core.job_queue import get_job

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("/{job_id}")
async def job_status(job_id: str, current_user: dict = Depends(get_current_user)):
    """Return status/result for a background job owned by the current user."""
    job = get_job(job_id, user_id=current_user.get("id"))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
