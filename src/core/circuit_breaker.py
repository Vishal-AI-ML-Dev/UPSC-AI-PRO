"""A minimal, dependency-free circuit breaker.

Wraps a flaky external dependency (e.g. the Upstash Redis REST endpoint) so that
once it starts failing we stop hammering it. After ``fail_max`` consecutive
failures the breaker OPENS and every call fails fast (no network round-trip) for
``reset_timeout`` seconds; it then goes HALF_OPEN and lets a single trial call
through -- success CLOSES it, failure re-OPENS it. Thread-safe.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitBreakerOpen(RuntimeError):
    """Raised by ``call`` when the breaker is OPEN (fail fast)."""


class CircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        *,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
        name: str = "circuit",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fail_max = max(1, int(fail_max))
        self.reset_timeout = max(0.0, float(reset_timeout))
        self.name = name
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._state = self.CLOSED
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        with self._lock:
            return self._resolve()

    def _resolve(self) -> str:
        # Lock must be held. OPEN -> HALF_OPEN once the cooldown elapses.
        if self._state == self.OPEN and (self._clock() - self._opened_at) >= self.reset_timeout:
            self._state = self.HALF_OPEN
        return self._state

    def allow(self) -> bool:
        """True if a call may proceed (CLOSED, or a HALF_OPEN trial)."""
        with self._lock:
            return self._resolve() != self.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == self.HALF_OPEN or self._failures >= self.fail_max:
                self._state = self.OPEN
                self._opened_at = self._clock()

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Run ``fn`` through the breaker; raise CircuitBreakerOpen when OPEN."""
        if not self.allow():
            raise CircuitBreakerOpen(f"{self.name} is OPEN")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result
