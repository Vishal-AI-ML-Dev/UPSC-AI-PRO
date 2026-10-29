"""Auth dependency - protects routes.

Single active session: the access token's ``sid`` must equal the user's
``users.active_session_id``, checked against the database on every protected
request. Database failures fail CLOSED (the request is refused, never allowed).
"""

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.models import User
from src.core.security import decode_access_token

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Client-facing messages per session error code (no internal details).
SESSION_MESSAGES = {
    "invalid": "Invalid or expired token",
    "session_outdated": "Please sign in again to continue.",
    "session_superseded": "Your account signed in on another device or browser. Please sign in again.",
    "session_ended": "Your session has ended. Please sign in again.",
}

# Postgres SQLSTATEs that mean "try again": serialization failure, deadlock
# detected, lock not available.
_RETRYABLE_SQLSTATES = {"40001", "40P01", "55P03"}
_SQLITE_BUSY_MESSAGES = ("database is locked", "database is busy", "database table is locked")


def unauthorized(code: str) -> HTTPException:
    """401 with an RFC 6750 error description the frontend can act on."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=SESSION_MESSAGES.get(code, SESSION_MESSAGES["invalid"]),
        headers={
            "WWW-Authenticate": f'Bearer error="invalid_token", error_description="{code}"'
        },
    )


def is_retryable_db_error(exc: BaseException) -> bool:
    """True only for recognised lock-contention errors (SQLite busy/locked, Postgres
    serialization/deadlock/lock-not-available). Everything else is not retryable."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    if isinstance(exc, OperationalError) and type(orig).__module__ == "sqlite3":
        return any(m in str(orig).lower() for m in _SQLITE_BUSY_MESSAGES)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code in _RETRYABLE_SQLSTATES


def auth_db_error(exc: BaseException) -> HTTPException:
    """Map a database error in an auth path to a 503 without exposing details."""
    if is_retryable_db_error(exc):
        logger.warning("Auth database busy (%s); asking the client to retry", type(exc).__name__)
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The service is busy. Please retry in a moment.",
            headers={"Retry-After": "1"},
        )
    logger.error("Auth database error (%s)", type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication is temporarily unavailable. Please try again later.",
    )


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Validate the JWT AND its session; return the current user's claims.

    Returns {id, email, name, sid}. Raises 401 for invalid tokens or sessions that
    are superseded/ended/outdated, and 503 if the session cannot be checked.
    """
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise unauthorized("invalid")
    sid = payload.get("sid")
    if not sid:
        raise unauthorized("session_outdated")  # legacy token: no compatibility bypass
    try:
        active = db.scalar(select(User.active_session_id).where(User.id == payload["sub"]))
        db.rollback()  # read-only check: end the transaction, release the connection
    except SQLAlchemyError as exc:
        db.rollback()
        raise auth_db_error(exc) from None
    if active != sid:
        raise unauthorized("session_ended" if active is None else "session_superseded")
    return {
        "id": payload["sub"],
        "email": payload.get("email", ""),
        "name": payload.get("name", ""),
        "sid": sid,
    }
