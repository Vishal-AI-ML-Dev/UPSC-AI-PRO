"""Password reset tokens: hashed, single-use, time-limited.

The raw token is returned only once (emailed in the reset link). Only its
SHA-256 hash is stored, so a DB leak cannot be used to reset passwords.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.models import PasswordResetToken


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_reset_token(db: Session, user_id: str) -> str:
    """Invalidate the user's old unused tokens, then issue a new raw token."""
    now = datetime.now(timezone.utc)
    old = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for row in old:
        row.used_at = now

    raw = secrets.token_urlsafe(32)
    token = PasswordResetToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        expires_at=now + timedelta(minutes=settings.reset_token_expire_minutes),
    )
    db.add(token)
    db.commit()
    return raw


def find_reset_token(db: Session, raw_token: str) -> tuple[str, str] | None:
    """Read-only: ``(token_id, user_id)`` if the token is unused and unexpired, else None.

    Consumption happens separately via ``claim_reset_token`` inside the caller's
    single write transaction (together with the password change).
    """
    if not raw_token:
        return None
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(raw_token))
    )
    if not row or row.used_at is not None:
        return None

    expires_at = row.expires_at
    if expires_at.tzinfo is None:  # SQLite may return naive datetimes
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return row.id, row.user_id


def claim_reset_token(db: Session, token_id: str) -> bool:
    """Mark the token used only if it is still unused (compare-and-set). Does not commit."""
    stmt = (
        update(PasswordResetToken)
        .where(PasswordResetToken.id == token_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1
