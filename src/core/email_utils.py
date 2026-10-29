"""Password-reset / verification email sending via SMTP (stdlib only).

Links carry single-use tokens, so they are NEVER written to logs. Without SMTP:
- production: startup fails (``check_email_config``) and sending raises
  ``EmailNotConfigured``; verification is never silently disabled.
- non-production: nothing is sent and only a link-free warning is logged. For
  local testing, point SMTP_* at a capture stub instead.
"""

import logging
import smtplib
from email.message import EmailMessage

from src.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when an email must be sent but SMTP is not configured."""


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def email_delivery_required() -> bool:
    """True when a missing SMTP config must fail instead of skipping the email."""
    return settings.is_production


def verification_enforced() -> bool:
    """
    Email verification is enforced when requested. Only OUTSIDE production does a
    missing SMTP config auto-disable it (local-dev convenience). In production it
    stays enforced, and a missing SMTP config stops startup instead.
    """
    if not settings.require_email_verification:
        return False
    return smtp_configured() or email_delivery_required()


def check_email_config() -> None:
    """Fail fast in production when verification is required but SMTP is missing."""
    if email_delivery_required() and settings.require_email_verification and not smtp_configured():
        raise RuntimeError(
            "REQUIRE_EMAIL_VERIFICATION is on but SMTP is not configured. Set "
            "SMTP_HOST, SMTP_USER and SMTP_PASSWORD before starting in production."
        )


def _smtp_missing(kind: str) -> None:
    """Handle a send attempt without SMTP. Never logs the link or token."""
    if email_delivery_required():
        raise EmailNotConfigured(f"SMTP is not configured; {kind} email not sent")
    logger.warning("SMTP not configured - %s email NOT sent (dev only; link not logged)", kind)


def send_reset_email(to_email: str, reset_link: str) -> None:
    """Send a password-reset email (see module docstring for the no-SMTP behaviour)."""
    minutes = settings.reset_token_expire_minutes
    subject = "Reset your UPSC AI password"
    text_body = (
        "We received a request to reset your UPSC AI password.\n\n"
        f"Reset it using the link below (valid for {minutes} minutes):\n"
        f"{reset_link}\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    html_body = (
        "<div style='font-family:Arial,sans-serif;color:#1a1a2e;line-height:1.6;'>"
        "<h2 style='color:#4361ee;margin:0 0 12px;'>Reset your UPSC AI password</h2>"
        f"<p>We received a request to reset your password. This link is valid for "
        f"<b>{minutes} minutes</b>.</p>"
        f"<p><a href='{reset_link}' style='display:inline-block;padding:12px 22px;"
        "background:#4361ee;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;'>"
        "Reset Password</a></p>"
        "<p style='color:#6b7490;font-size:13px;'>Or paste this link into your browser:<br>"
        f"{reset_link}</p>"
        "<p style='color:#6b7490;font-size:13px;'>If you didn't request this, you can ignore this email.</p>"
        "</div>"
    )

    if not smtp_configured():
        _smtp_missing("password reset")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    logger.info("Password reset email sent to %s", to_email)


def send_verification_email(to_email: str, verify_link: str) -> None:
    """Send an email-verification link (see module docstring for the no-SMTP behaviour)."""
    minutes = settings.verification_token_expire_minutes
    hours = max(1, minutes // 60)
    subject = "Verify your UPSC AI email"
    text_body = (
        "Welcome to UPSC AI! Please verify your email to activate your account.\n\n"
        f"Verify using the link below (valid for {hours} hour(s)):\n"
        f"{verify_link}\n\n"
        "If you didn't create this account, you can safely ignore this email."
    )
    html_body = (
        "<div style='font-family:Arial,sans-serif;color:#1a1a2e;line-height:1.6;'>"
        "<h2 style='color:#4361ee;margin:0 0 12px;'>Verify your UPSC AI email</h2>"
        "<p>Welcome! Please verify your email to activate your account. This link is valid for "
        f"<b>{hours} hour(s)</b>.</p>"
        f"<p><a href='{verify_link}' style='display:inline-block;padding:12px 22px;"
        "background:#4361ee;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;'>"
        "Verify Email</a></p>"
        "<p style='color:#6b7490;font-size:13px;'>Or paste this link into your browser:<br>"
        f"{verify_link}</p>"
        "<p style='color:#6b7490;font-size:13px;'>If you didn't create this account, you can ignore this email.</p>"
        "</div>"
    )

    if not smtp_configured():
        _smtp_missing("verification")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    logger.info("Verification email sent to %s", to_email)
