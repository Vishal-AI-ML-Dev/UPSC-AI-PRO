"""Best-effort cleanup of expired auth tokens (fail-open on boot).

Deletes rows from the three single-purpose token tables once they are past
their ``expires_at``. Safe to run repeatedly; a platform scheduler can also
invoke it periodically (see scripts/cron_cleanup.py).
"""

import logging
from datetime import datetime, timezone

from src.core.db import SessionLocal

logger = logging.getLogger(__name__)


def purge_expired_tokens() -> int:
    """Delete expired password-reset, email-verification and refresh tokens.

    Returns the number of rows removed. The startup caller treats any failure
    as non-fatal, so this stays intentionally simple.
    """
    from src.core.models import (
        EmailVerificationToken,
        PasswordResetToken,
        RefreshToken,
    )

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        deleted = 0
        for model in (PasswordResetToken, EmailVerificationToken, RefreshToken):
            deleted += (
                db.query(model).filter(model.expires_at < now).delete(synchronize_session=False)
            )
        db.commit()
        if deleted:
            logger.info("Token cleanup: purged %d expired auth token(s)", deleted)
        return deleted
    finally:
        db.close()
