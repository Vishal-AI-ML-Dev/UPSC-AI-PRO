"""Per-user conversation history helpers."""

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.core.models import Conversation, Message

logger = logging.getLogger(__name__)


def create_conversation(db: Session, user_id: str, agent: str, title: str = "") -> Conversation:
    convo = Conversation(user_id=user_id, agent=agent, title=(title or "")[:200])
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def get_conversation(db: Session, user_id: str, conversation_id: str) -> Conversation | None:
    """Return the conversation only if it belongs to this user (ownership check)."""
    convo = db.get(Conversation, conversation_id)
    if not convo or convo.user_id != user_id:
        return None
    return convo


def list_conversations(db: Session, user_id: str, agent: str | None = None, limit: int = 50):
    stmt = select(Conversation).where(Conversation.user_id == user_id)
    if agent:
        stmt = stmt.where(Conversation.agent == agent)
    stmt = stmt.order_by(desc(Conversation.updated_at)).limit(limit)
    return list(db.scalars(stmt))


def add_message(
    db: Session,
    user_id: str,
    role: str,
    content: str,
    conversation_id: str | None = None,
    agent: str = "general",
    title: str = "",
) -> Message:
    """Append a message. Creates a conversation if conversation_id is missing/invalid."""
    convo = None
    if conversation_id:
        convo = get_conversation(db, user_id, conversation_id)
    if convo is None:
        convo = create_conversation(db, user_id, agent, title or (content or "")[:60])
    msg = Message(conversation_id=convo.id, role=role, content=content or "")
    db.add(msg)
    convo.updated_at = datetime.now(timezone.utc)
    db.add(convo)
    db.commit()
    db.refresh(msg)
    return msg


def get_messages(db: Session, user_id: str, conversation_id: str):
    """Return messages for a conversation owned by user, or None if not found."""
    convo = get_conversation(db, user_id, conversation_id)
    if convo is None:
        return None
    return list(convo.messages)
