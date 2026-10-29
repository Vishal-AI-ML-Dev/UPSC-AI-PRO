"""Error monitoring - Sentry integration.

Captures unhandled exceptions / 500-class errors with a full stack trace plus
request context (method, path, user) and alerts you, so production failures
don't get silently buried in the Render logs.

Design goals (mirrors observability.py):
* **No-op when unconfigured** - empty ``SENTRY_DSN`` (or no sentry-sdk) => every
  call is a cheap no-op and boot is byte-for-byte unchanged.
* **Fail-open** - any init error (bad DSN, import failure) is swallowed and
  logged; a monitoring dependency must NEVER block app boot.
* **Cheap by default** - performance tracing/profiling sample rates default to
  0.0 (only error events are sent), which keeps you inside the free
  Developer plan (5K errors/month).
"""

import logging
from functools import lru_cache

from src.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache
def sentry_enabled() -> bool:
    """True only when a DSN is configured."""
    return bool(settings.sentry_dsn)


def init_sentry() -> bool:
    """Initialise Sentry once at startup. Returns True when monitoring is active.

    Fail-open: unconfigured => no-op (returns False); any error => log a warning
    and continue (returns False) so app boot is never blocked by monitoring.
    """
    if not sentry_enabled():
        logger.info("Sentry disabled (no SENTRY_DSN)")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            profiles_sample_rate=settings.sentry_profiles_sample_rate,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            # PII off by default (don't ship request bodies / headers to Sentry).
            send_default_pii=False,
        )
        logger.info(
            "Sentry error monitoring enabled (env=%s, traces=%.2f)",
            settings.sentry_environment,
            settings.sentry_traces_sample_rate,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never break boot for monitoring
        logger.warning("Sentry init failed, monitoring off: %s", exc)
        return False


def capture_exception(exc: BaseException) -> None:
    """Best-effort manual capture. No-op when Sentry is disabled/unavailable."""
    if not sentry_enabled():
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 - capture must never raise
        logger.debug("Sentry capture_exception skipped", exc_info=True)


def set_request_context(request_id: str) -> None:
    """Tag the current Sentry scope with the request ID (best-effort; no-op if off)."""
    if not sentry_enabled():
        return
    try:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)
    except Exception:  # noqa: BLE001 - tagging must never raise
        logger.debug("Sentry set_tag skipped", exc_info=True)
