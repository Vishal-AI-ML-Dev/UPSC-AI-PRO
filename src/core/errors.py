"""Errors whose message is safe to show to a user.

``UserFacingError`` is the ONLY exception type whose text may reach an HTTP
response or a job record (the NCERT route's ``NcertPdfError`` is the one older
equivalent). Raise it with a fixed, friendly sentence - never with exception
text, paths, provider bodies or user data. Every other exception, including a
plain ``ValueError``, is reported to the user as ``GENERIC_ERROR`` and logged
with ``describe_exception``.

It subclasses ``ValueError`` so existing ``except ValueError`` handlers inside
feature code keep treating it as invalid input.
"""

GENERIC_ERROR = "Processing failed. Please try again."


class UserFacingError(ValueError):
    """A fixed, user-facing message (shown verbatim with HTTP 400)."""
