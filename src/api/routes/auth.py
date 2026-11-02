"""Auth routes - register + login.

Single active session: every flow that signs a user in (login, verify-email,
register when verification is off) starts a NEW session that supersedes any
previous one; logout ends only the session it belongs to; password reset ends
all sessions. Each flow performs one write transaction and returns tokens only
after it commits (see src/core/refresh_tokens.py for the lock order).

The token routes are plain ``def`` so FastAPI runs them in its threadpool: their
database work is blocking and must not stall the event loop.
"""

import logging
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.api.deps import auth_db_error, get_current_user, unauthorized
from src.core.config import settings
from src.core.db import get_db
from src.core.email_utils import (
    email_delivery_required,
    send_reset_email,
    send_verification_email,
    smtp_configured,
    verification_enforced,
)
from src.core.errors import GENERIC_ERROR, UserFacingError
from src.core.llm import describe_exception
from src.core.models import User
from src.core.refresh_tokens import (
    SessionRejected,
    end_all_sessions,
    end_session,
    rotate_session_token,
    start_session,
)
from src.core.reset_tokens import claim_reset_token, create_reset_token, find_reset_token
from src.core.security import create_access_token
from src.core.users import (
    authenticate,
    create_user,
    get_user_by_email,
    hash_new_password,
)
from src.core.verification_tokens import (
    claim_verification_token,
    create_verification_token,
    find_verification_token,
)

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)


# Input bounds (Phase 02, R6/R14). NEW passwords (register/reset) get a coarse
# 128-char bound here; the 6-char minimum and the 72-byte bcrypt maximum are
# enforced in src/core/users.py. Login is a separate path (_login_input_too_long).
EMAIL_MAX = 254
NAME_MAX = 100
NEW_PASSWORD_MAX_CHARS = 128
TOKEN_MAX = 512
LOGIN_USERNAME_MAX = 254
LOGIN_PASSWORD_MAX = 1024


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=EMAIL_MAX)
    password: str = Field(..., max_length=NEW_PASSWORD_MAX_CHARS)
    name: str = Field("", max_length=NAME_MAX)


def _login_input_too_long(username: str, password: str) -> bool:
    """Login-only bound: legacy passwords up to 1,024 chars are still verified
    (bcrypt truncation, as before); longer input is rejected as a normal 401
    without running bcrypt. Never used by register/reset."""
    return len(username or "") > LOGIN_USERNAME_MAX or len(password or "") > LOGIN_PASSWORD_MAX


def _unexpected(e: Exception, what: str) -> HTTPException:
    """R10: log a secret-safe summary; return the fixed generic message."""
    logger.error("%s failed: %s", what, describe_exception(e))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=GENERIC_ERROR)


@contextmanager
def _write_txn(db: Session):
    """One auth write transaction: commit once on success, roll back on any error.

    Database errors become a 503 without internal details (retry hint only for
    recognised lock contention).
    """
    try:
        yield
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise auth_db_error(exc) from None
    except BaseException:
        db.rollback()
        raise


def _tokens(user_id: str, email: str, name: str, sid: str, refresh_token: str) -> dict:
    """Build the response only from committed values."""
    access = create_access_token({"sub": user_id, "email": email, "name": name, "sid": sid})
    return {"access_token": access, "refresh_token": refresh_token, "token_type": "bearer"}


def _user_claims(db: Session, user_id: str):
    return db.execute(select(User.email, User.name).where(User.id == user_id)).one()


def _frontend_link(param: str, raw_token: str) -> str:
    """Build a frontend URL with one query param (works with bare host or file path)."""
    base = settings.frontend_url.strip().rstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{param}={raw_token}"


_EMAIL_UNAVAILABLE = "Email delivery is not configured on this server. Please try again later."


def _require_email_delivery() -> None:
    """In production, refuse email flows when SMTP is missing instead of claiming success.

    Checked before any account lookup, so the response never reveals whether an
    email address is registered.
    """
    if email_delivery_required() and not smtp_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_EMAIL_UNAVAILABLE
        )


def _send_verification(db: Session, user) -> None:
    """Issue a verification token and email the link (the link is never logged)."""
    try:
        raw = create_verification_token(db, user.id)
        send_verification_email(user.email, _frontend_link("verify_token", raw))
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Verification email failed for user id %s: %s", user.id, describe_exception(e)
        )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new account and email a verification link.

    When REQUIRE_EMAIL_VERIFICATION is on (default), login stays blocked until the
    email is verified, so no JWT is returned here.
    """
    if verification_enforced():
        _require_email_delivery()
    try:
        user = create_user(db, body.email, body.password, body.name)
    except UserFacingError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001 - R10: never echo internal error text
        raise _unexpected(e, "Registration") from None
    if verification_enforced():
        _send_verification(db, user)
        return {
            "verification_required": True,
            "email": user.email,
            "message": "Account created. Please check your email and verify your account before signing in.",
        }
    # Verification disabled (dev): mark verified and auto-login (starts a session).
    user_id, email, name = user.id, user.email, user.name
    with _write_txn(db):
        sid, refresh_token = start_session(db, user_id, email_verified=True)
    return _tokens(user_id, email, name, sid, refresh_token)


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login with email + password (OAuth2 form field 'username' = email).

    A successful login supersedes the account's previous session everywhere.
    """
    if _login_input_too_long(form_data.username, form_data.password):
        user = None
    else:
        user = authenticate(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if verification_enforced() and not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Check your inbox for the verification link, or request a new one.",
        )
    user_id, email, name = user.id, user.email, user.name
    with _write_txn(db):
        sid, refresh_token = start_session(db, user_id)
    return _tokens(user_id, email, name, sid, refresh_token)


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Return the currently logged-in user."""
    return current_user


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., max_length=TOKEN_MAX)


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """Rotate a valid refresh token into a fresh access + refresh pair.

    The presented refresh token is single-use (compare-and-set rotation) and only
    works while its session is still the account's current session.
    """
    try:
        with _write_txn(db):
            user_id, sid, new_refresh = rotate_session_token(db, body.refresh_token)
            claims = _user_claims(db, user_id)
    except SessionRejected as e:
        raise unauthorized(e.code) from None
    return _tokens(user_id, claims.email, claims.name, sid, new_refresh)


@router.post("/logout")
def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    """Revoke a refresh token and end its session if it is still the current one.

    A stale token (rotated, revoked, or from a superseded session) changes nothing,
    so it can never sign out a newer session.
    """
    with _write_txn(db):
        end_session(db, body.refresh_token)
    return {"message": "Logged out."}


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., max_length=EMAIL_MAX)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., max_length=TOKEN_MAX)
    new_password: str = Field(..., max_length=NEW_PASSWORD_MAX_CHARS)


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Email a password-reset link if the address is registered.

    Always returns a generic message so we don't reveal whether an email exists.
    """
    _require_email_delivery()
    user = get_user_by_email(db, body.email)
    if user:
        try:
            raw_token = create_reset_token(db, user.id)
            base = settings.frontend_url.strip().rstrip("/")
            sep = "&" if "?" in base else "?"
            link = f"{base}{sep}reset_token={raw_token}"
            send_reset_email(user.email, link)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Password reset email failed for user id %s: %s", user.id, describe_exception(e)
            )
    return {"message": "If an account exists for that email, a reset link has been sent."}


_BAD_RESET_LINK = "Invalid or expired reset link. Please request a new one."


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Consume a reset token, set the new password and end every session - atomically."""
    try:
        new_hash = hash_new_password(body.new_password)  # validate before using the link
    except UserFacingError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001 - R10: never echo internal error text
        raise _unexpected(e, "Password reset") from None
    found = find_reset_token(db, body.token)
    if not found:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_RESET_LINK)
    token_id, user_id = found
    with _write_txn(db):
        if not end_all_sessions(db, user_id, hashed_password=new_hash):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_RESET_LINK)
        if not claim_reset_token(db, token_id):  # used concurrently -> undo everything
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_RESET_LINK)
    logger.info("Password reset completed; all sessions ended for user id %s", user_id)
    return {"message": "Password updated. You can now sign in with your new password."}


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., max_length=TOKEN_MAX)


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., max_length=EMAIL_MAX)


_BAD_VERIFY_LINK = "Invalid or expired verification link. Please request a new one."


@router.post("/verify-email")
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Consume an email-verification token, mark the user verified, and auto-login."""
    found = find_verification_token(db, body.token)
    if not found:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_VERIFY_LINK)
    token_id, user_id = found
    with _write_txn(db):
        try:
            sid, refresh_token = start_session(db, user_id, email_verified=True)
        except LookupError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_VERIFY_LINK
            ) from None
        if not claim_verification_token(db, token_id):  # used concurrently -> undo everything
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_BAD_VERIFY_LINK)
        claims = _user_claims(db, user_id)
    data = _tokens(user_id, claims.email, claims.name, sid, refresh_token)
    data["message"] = "Email verified successfully. You are now signed in."
    return data


@router.post("/resend-verification")
def resend_verification(body: ResendVerificationRequest, db: Session = Depends(get_db)):
    """Resend the verification email if the account exists and is still unverified.

    Always returns a generic message so we don't reveal whether an email exists.
    """
    _require_email_delivery()
    user = get_user_by_email(db, body.email)
    if user and not user.email_verified:
        _send_verification(db, user)
    return {
        "message": "If an unverified account exists for that email, a new verification link has been sent."
    }
