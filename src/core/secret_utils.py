"""Secret validation helpers (stdlib-only, import-safe for tests).

Kept dependency-free (no pydantic / no app imports) so the validation logic can
be unit-tested in isolation and reused wherever a secret must be checked.
"""

from __future__ import annotations

import logging
import secrets

logger = logging.getLogger(__name__)

MIN_JWT_SECRET_LEN = 32


def resolve_jwt_secret(
    secret: str, *, is_production: bool, min_len: int = MIN_JWT_SECRET_LEN
) -> str:
    """Return a usable JWT secret, failing fast on a forgeable one in production.

    An empty or short ``jwt_secret`` means access tokens are signed with a
    guessable key, so anyone can forge a valid session. In production we refuse
    to boot; in local/dev we fall back to an ephemeral random secret (tokens
    just won't survive a restart) so the app stays runnable.
    """
    secret = (secret or "").strip()
    if len(secret) >= min_len:
        return secret
    if not is_production:
        logger.warning(
            "JWT_SECRET missing/weak - using an EPHEMERAL dev secret. Set a "
            "strong JWT_SECRET (>= %d chars) before deploying.",
            min_len,
        )
        return secrets.token_urlsafe(48)
    raise ValueError(
        f"JWT_SECRET is missing or too weak (need >= {min_len} chars). Set a "
        "strong random JWT_SECRET (e.g. `openssl rand -hex 32`) before starting "
        "in production - an empty secret makes auth tokens forgeable."
    )
