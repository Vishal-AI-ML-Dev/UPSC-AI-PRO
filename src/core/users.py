"""User store - DB-backed (SQLAlchemy). Multi-user with bcrypt hashing.

Uses the `bcrypt` library directly (no passlib) to avoid the passlib + bcrypt
4.x version-reading bug on Windows.
"""

import logging

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.errors import UserFacingError
from src.core.llm import describe_exception
from src.core.models import User

logger = logging.getLogger(__name__)
_MAX_BYTES = 72  # bcrypt hard 72-byte limit on the password input
_MIN_CHARS = 6
PASSWORD_TOO_SHORT = "Password must be at least 6 characters"
PASSWORD_TOO_LONG = (
    "Password must be at most 72 bytes (fewer if it uses non-English characters)."
)


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
        logger.warning("Password verify failed: %s", describe_exception(e))
        return False


def _validate_new_password(password: str) -> None:
    """Rules for a NEW password (register / reset only - never login).

    The existing 6-character minimum is checked first, unchanged; then bcrypt's
    72-byte input limit, so two different passwords can never collapse into the
    same hash by truncation. Login keeps truncating verification for accounts
    created before this rule existed.
    """
    if not password or len(password) < _MIN_CHARS:
        raise UserFacingError(PASSWORD_TOO_SHORT)
    if len(password.encode("utf-8")) > _MAX_BYTES:
        raise UserFacingError(PASSWORD_TOO_LONG)


def get_user_by_email(db: Session, email: str) -> User | None:
    key = (email or "").lower().strip()
    if not key:
        return None
    return db.scalar(select(User).where(User.email == key))


def create_user(db: Session, email: str, password: str, name: str = "") -> User:
    """Create a new user. Raises UserFacingError if invalid or email already exists."""
    key = (email or "").lower().strip()
    if not key or "@" not in key:
        raise UserFacingError("A valid email is required")
    _validate_new_password(password)
    if get_user_by_email(db, key):
        raise UserFacingError("Email already registered")
    user = User(
        email=key,
        name=(name or "").strip() or key.split("@")[0],
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Created user id %s", user.id)
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
    _validate_new_password(new_password)
    return hash_password(new_password)
