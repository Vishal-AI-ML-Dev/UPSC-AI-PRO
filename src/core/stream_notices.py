"""Stream notice contract (Phase 08 Slice A, ADR-017).

A *notice* is a fixed, user-facing message that replaces or ends an answer
because generation failed (exception, timeout, empty model output) or the
stream was stopped. Notices start with ``STREAM_NOTICE_PREFIX``, the same value
as ``STREAM_NOTICE_PREFIX`` in ``frontend/src/lib/api.ts``, so the Mentor UI
never saves one to history as an answer.

Use ``stream_notice()`` only for genuine failure fallbacks. Validation guidance
("Answer too short"), ``> ⚠️`` disclaimers and degraded-data answers are normal
output and must not use it.
"""

STREAM_NOTICE_PREFIX = "\n\n⚠️ "


def stream_notice(message: str) -> str:
    """Return ``message`` as a stream notice."""
    return STREAM_NOTICE_PREFIX + message
