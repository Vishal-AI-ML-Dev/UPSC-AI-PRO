"""Feedback routes - thumbs up/down on agent answers.

Users only click a rating; the data is stored silently for the developer/admin
to review later (which answers were helpful vs not). Nothing is shown back to
other users.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.deps import get_current_user
from src.core.db import get_db
from src.core.models import Feedback

router = APIRouter(prefix="/feedback", tags=["Feedback"])


class FeedbackRequest(BaseModel):
    rating: str  # "up" or "down"
    agent: str = "mentor"
    question: str = ""
    answer: str = ""
    comment: str = ""


@router.post("/submit", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: FeedbackRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record a single thumbs up/down for an agent answer."""
    rating = (body.rating or "").strip().lower()
    if rating not in ("up", "down"):
        raise HTTPException(status_code=422, detail="rating must be 'up' or 'down'")
    fb = Feedback(
        user_id=user["id"],
        agent=(body.agent or "mentor")[:50],
        rating=rating,
        question=(body.question or "")[:4000],
        answer=(body.answer or "")[:8000],
        comment=(body.comment or "")[:2000],
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return {"id": fb.id, "rating": fb.rating}
