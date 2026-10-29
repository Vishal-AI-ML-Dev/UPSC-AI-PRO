"""Refresh tokens + single-active-session identity.

Each account has at most ONE current session, named by ``users.active_session_id``.
Access tokens (short-lived JWTs) carry the ``sid`` they were issued for, and every
protected request checks it against the user's row (``src/api/deps.py``), so a new
login immediately invalidates the previous session's access AND refresh tokens.

A refresh token is an opaque random string; only its SHA-256 hash is stored. Each
refresh ROTATES it (single-use, compare-and-set), keeping the same session id, so
a replayed token is rejected and a token can never rotate twice.

Transactions: these helpers NEVER commit. Each auth flow (``src/api/routes/auth.py``)
runs one write transaction and commits once, so a failure cannot leave a
half-applied state, and tokens are handed out only after that commit succeeds.
Lock order: every write transaction first writes (and so locks) the user's row in
``users`` and only afterwards touches token rows.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.models import RefreshToken, User


class SessionRejected(Exception):
    """A refresh token cannot be used. ``code`` is one of: invalid,
    session_outdated, session_superseded, session_ended."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RefreshTokenInfo(NamedTuple):
    id: str
    user_id: str
    session_id: str | None
    usable: bool  # not revoked and not expired (as of the read)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt  # SQLite: naive


def _write_user(db: Session, user_id: str, **values) -> bool:
    """UPDATE the user's row (taking its write lock). Returns False if it does not exist."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1


def lock_user(db: Session, user_id: str) -> bool:
    """Take the user-row write lock without changing anything."""
    return _write_user(db, user_id, active_session_id=User.active_session_id)


def find_refresh_token(db: Session, raw_token: str) -> RefreshTokenInfo | None:
    """Read-only lookup of a presented refresh token (takes no locks)."""
    if not raw_token:
        return None
    row = db.execute(
        select(
            RefreshToken.id,
            RefreshToken.user_id,
            RefreshToken.session_id,
            RefreshToken.revoked_at,
            RefreshToken.expires_at,
        ).where(RefreshToken.token_hash == _hash_token(raw_token))
    ).one_or_none()
    if row is None:
        return None
    usable = row.revoked_at is None and _aware(row.expires_at) >= datetime.now(timezone.utc)
    return RefreshTokenInfo(row.id, row.user_id, row.session_id, usable)


def stage_refresh_token(db: Session, user_id: str, session_id: str) -> str:
    """Add a new refresh token for ``session_id``; return the raw value (not committed)."""
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(raw),
            session_id=session_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=settings.refresh_token_expire_minutes),
        )
    )
    db.flush()
    return raw


def claim_refresh_token(db: Session, token_id: str) -> bool:
    """Revoke the token only if it is still unrevoked (compare-and-set)."""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1


def revoke_all_refresh_tokens(db: Session, user_id: str) -> int:
    """Revoke every unrevoked refresh token of the user. Lock the user row first."""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount


def start_session(db: Session, user_id: str, **user_values) -> tuple[str, str]:
    """Make a brand-new session the account's only session (login wins).

    Writes the new id to the user's row first (lock), revokes every older refresh
    token, and stages the new session's first refresh token. ``user_values`` are
    applied in the same UPDATE (e.g. ``email_verified=True``). Returns
    ``(session_id, raw_refresh_token)``; raises LookupError if the user is gone.
    """
    sid = uuid.uuid4().hex
    if not _write_user(db, user_id, active_session_id=sid, **user_values):
        raise LookupError("user not found")
    revoke_all_refresh_tokens(db, user_id)
    return sid, stage_refresh_token(db, user_id, sid)


def rotate_session_token(db: Session, raw_token: str) -> tuple[str, str, str]:
    """Rotate a refresh token within its (still current) session.

    Returns ``(user_id, session_id, new_raw_token)``; raises SessionRejected.
    The caller commits on success and rolls back on rejection.
    """
    info = find_refresh_token(db, raw_token)
    if info is None:
        raise SessionRejected("invalid")
    if info.session_id is None:
        raise SessionRejected("session_outdated")  # legacy token: no compatibility path
    if not info.usable:
        # Revoked/expired. Report a superseded/ended session precisely (the token was
        # revoked by a newer login or a logout); a rotated token in the CURRENT
        # session is a replay -> "invalid". Either way it is rejected.
        active = db.scalar(select(User.active_session_id).where(User.id == info.user_id))
        if active != info.session_id:
            raise SessionRejected("session_ended" if active is None else "session_superseded")
        raise SessionRejected("invalid")
    if not lock_user(db, info.user_id):
        raise SessionRejected("invalid")
    active = db.scalar(select(User.active_session_id).where(User.id == info.user_id))
    if active != info.session_id:
        raise SessionRejected("session_ended" if active is None else "session_superseded")
    if not claim_refresh_token(db, info.id):
        raise SessionRejected("invalid")  # already rotated/revoked (replay or race lost)
    return info.user_id, info.session_id, stage_refresh_token(db, info.user_id, info.session_id)


def end_session(db: Session, raw_token: str) -> bool:
    """Logout with a refresh token. Ends the session only if it is still current.

    A revoked, rotated, expired or superseded token never affects a newer session.
    Returns True if the presented token was revoked by this call.
    """
    info = find_refresh_token(db, raw_token)
    if info is None or not info.usable:
        return False
    if not lock_user(db, info.user_id):
        return False
    if not claim_refresh_token(db, info.id):
        return False
    if info.session_id is not None:
        db.execute(
            update(User)
            .where(User.id == info.user_id, User.active_session_id == info.session_id)
            .values(active_session_id=None)
            .execution_options(synchronize_session=False)
        )
    return True


def end_all_sessions(db: Session, user_id: str, **user_values) -> bool:
    """Sign the account out everywhere (e.g. password reset). ``user_values`` are
    applied in the same locking UPDATE. Returns False if the user does not exist."""
    if not _write_user(db, user_id, active_session_id=None, **user_values):
        return False
    revoke_all_refresh_tokens(db, user_id)
    return True
