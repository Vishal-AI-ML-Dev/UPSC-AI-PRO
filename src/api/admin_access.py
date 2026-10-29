"""Admin allowlist helpers.

An "admin" is any user whose email is listed in ADMIN_EMAILS (settings.admin_emails,
a JSON list of emails). These users - and only these - can open the Cost,
Monitoring, and Experiments dashboards.

- `is_admin(email)` -> bool, used by the `/access` endpoints so the UI can hide
  a tab WITHOUT triggering a 403.
- `require_admin` -> FastAPI dependency that raises 403 for non-admins, used to
  guard the actual `/overview` data endpoints.
"""

from fastapi import Depends, HTTPException, status

from src.api.deps import get_current_user
from src.core.config import settings


def is_admin(email: str) -> bool:
    """True if `email` is in the ADMIN_EMAILS allowlist (case-insensitive)."""
    if not email:
        return False
    allow = [
        e.strip().lower() for e in (settings.admin_emails or []) if isinstance(e, str) and e.strip()
    ]
    return email.strip().lower() in allow


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: allow only allowlisted admins; otherwise 403."""
    if not is_admin(user.get("email", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
