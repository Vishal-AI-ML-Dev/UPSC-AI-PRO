"""Rate-limit decision logic (backend-agnostic, import-safe, unit-testable).

The middleware in ``rate_limit.py`` is a thin wrapper around ``RateLimiter``,
which prefers a distributed Upstash-Redis fixed-window counter and transparently
falls back to an in-process sliding window when Redis is unavailable (creds
unset, breaker OPEN, or a transient error). No app-settings import here.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional, Tuple

from src.core.upstash import get_upstash

# (allowed, retry_after_seconds)
Decision = Tuple[bool, int]


class _InMemoryLimiter:
    """Per-key sliding window using a deque of timestamps. Single process."""

    def __init__(self, max_requests: int, period: int) -> None:
        self.max_requests = max_requests
        self.period = period
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, now: float) -> Decision:
        dq = self._hits[key]
        window_start = now - self.period
        while dq and dq[0] < window_start:
            dq.popleft()
        if len(dq) >= self.max_requests:
            retry_after = int(self.period - (now - dq[0])) + 1
            return (False, retry_after)
        dq.append(now)
        return (True, 0)


class RateLimiter:
    """Distributed (Upstash) fixed-window limiter with in-memory fallback."""

    def __init__(self, max_requests: int, period: int, redis=None) -> None:
        self.max_requests = max_requests
        self.period = period
        self._redis = redis  # inject for tests; None => shared get_upstash()
        self._memory = _InMemoryLimiter(max_requests, period)

    def _get_redis(self):
        return self._redis if self._redis is not None else get_upstash()

    def check(
        self, key: str, *, mono: Optional[float] = None, wall: Optional[float] = None
    ) -> Decision:
        redis = self._get_redis()
        if redis is not None and getattr(redis, "enabled", False):
            decision = self._check_redis(redis, key, wall if wall is not None else time.time())
            if decision is not None:
                return decision
        # Fallback: Redis disabled / breaker open / transient error.
        return self._memory.hit(key, mono if mono is not None else time.monotonic())

    def _check_redis(self, redis, key: str, now: float) -> Optional[Decision]:
        window = int(now // self.period)
        rkey = f"rl:{key}:{window}"
        count = redis.command(["INCR", rkey])
        if count is None:
            return None  # signal caller to fall back to in-memory
        count = int(count)
        if count == 1:
            redis.command(["EXPIRE", rkey, str(self.period)])
        if count > self.max_requests:
            retry_after = int(self.period - (now % self.period)) + 1
            return (False, retry_after)
        return (True, 0)
