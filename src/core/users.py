"""User store - DB-backed (SQLAlchemy). Multi-user with bcrypt hashing.

Uses the `bcrypt` library directly (no passlib) to avoid the passlib + bcrypt
4.x version-reading bug on Windows.
"""

import logging

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import User

logger = logging.getLogger(__name__)
_MAX_BYTES = 72  # bcrypt hard 72-byte limit on the password input


def _to_bytes(plain: str) -> bytes:
    """Encode and safely truncate to bcrypt's 72-byte limit."""
    return plain.encode("utf-8")[:_MAX_BYTES]


def hash_password(plain: str) -> str:
    """Hash a plain password with bcrypt."""
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except Exception as e:
        logger.warning(f"Password verify failed: {e}")
        return False


def get_user_by_email(db: Session, email: str) -> User | None:
    key = (email or "").lower().strip()
    if not key:
        return None
    return db.scalar(select(User).where(User.email == key))


def create_user(db: Session, email: str, password: str, name: str = "") -> User:
    """Create a new user. Raises ValueError if invalid or email already exists."""
    key = (email or "").lower().strip()
    if not key or "@" not in key:
        raise ValueError("A valid email is required")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if get_user_by_email(db, key):
        raise ValueError("Email already registered")
    user = User(
        email=key,
        name=(name or "").strip() or key.split("@")[0],
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"Created user: {key}")
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Return the User if credentials are valid, else None."""
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def hash_new_password(new_password: str) -> str:
    """Validate and hash a replacement password (used by password reset).

    Does not touch the database: the reset route writes the hash in the same
    transaction that consumes the reset token and ends every session.
    """
    if not new_password or len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters")
    return hash_password(new_password)
