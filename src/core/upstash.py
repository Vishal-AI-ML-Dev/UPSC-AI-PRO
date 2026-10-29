"""Shared Upstash Redis REST client, guarded by a circuit breaker.

One tiny HTTP client that both the response cache and the rate limiter can use
to talk to Upstash's serverless Redis over REST. The circuit breaker means a
Redis outage degrades gracefully (fail fast -> caller falls back) instead of
adding a full HTTP timeout to every request on the hot path. httpx is imported
lazily so this module stays import-safe (and unit-testable) without it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

logger = logging.getLogger(__name__)


class UpstashRest:
    def __init__(
        self,
        *,
        rest_url: str,
        rest_token: str,
        timeout: float = 2.0,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
    ) -> None:
        self.rest_url = (rest_url or "").rstrip("/")
        self.rest_token = rest_token or ""
        self.timeout = timeout
        self.enabled = bool(self.rest_url and self.rest_token)
        self._breaker = CircuitBreaker(
            fail_max=fail_max, reset_timeout=reset_timeout, name="upstash"
        )

    @property
    def breaker_state(self) -> str:
        return self._breaker.state

    def command(self, command: list) -> Any:
        """Run one Redis command; return its ``result`` or None on any failure.

        Never raises: transport errors or an OPEN breaker degrade to None so the
        caller can fall back (cache miss, in-memory rate limit, ...).
        """
        if not self.enabled:
            return None
        try:
            return self._breaker.call(self._post, command)
        except CircuitBreakerOpen:
            logger.debug("Upstash breaker OPEN; skipping command")
            return None
        except Exception:
            logger.debug("Upstash command failed", exc_info=True)
            return None

    def _post(self, command: list) -> Any:
        import httpx

        resp = httpx.post(
            self.rest_url,
            json=command,
            headers={"Authorization": f"Bearer {self.rest_token}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("result")


_singleton: Optional[UpstashRest] = None


def get_upstash() -> UpstashRest:
    """Process-wide Upstash client built from settings."""
    global _singleton
    if _singleton is None:
        from src.core.config import settings

        _singleton = UpstashRest(
            rest_url=settings.upstash_redis_rest_url,
            rest_token=settings.upstash_redis_rest_token,
            timeout=settings.redis_rest_timeout_seconds,
            fail_max=settings.circuit_breaker_fail_max,
            reset_timeout=settings.circuit_breaker_reset_seconds,
        )
    return _singleton


def reset_upstash() -> None:
    global _singleton
    _singleton = None
