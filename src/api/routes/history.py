"""History routes - per-user conversations & messages."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.deps import get_current_user
from src.core import history as hist
from src.core.db import get_db

router = APIRouter(prefix="/history", tags=["History"])


class SaveMessageRequest(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    agent: str = "general"
    conversation_id: str | None = None
    title: str = ""


def _convo_dict(c) -> dict:
    return {
        "id": c.id,
        "agent": c.agent,
        "title": c.title,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _msg_dict(m) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/conversations")
async def list_conversations(
    agent: str | None = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's conversations (optionally filtered by agent)."""
    convos = hist.list_conversations(db, user["id"], agent=agent)
    return {"conversations": [_convo_dict(c) for c in convos]}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all messages in a conversation owned by the current user."""
    msgs = hist.get_messages(db, user["id"], conversation_id)
    if msgs is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"messages": [_msg_dict(m) for m in msgs]}


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def save_message(
    body: SaveMessageRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a single message; creates a conversation if none is provided."""
    msg = hist.add_message(
        db,
        user["id"],
        role=body.role,
        content=body.content,
        conversation_id=body.conversation_id,
        agent=body.agent,
        title=body.title,
    )
    return {"id": msg.id, "conversation_id": msg.conversation_id}
