"""Offline tests for the circuit breaker."""

from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, d):
        self.t += d


def test_opens_after_fail_max():
    cb = CircuitBreaker(fail_max=3, reset_timeout=10)
    assert cb.allow()
    for _ in range(3):
        cb.record_failure()
    assert cb.state == cb.OPEN
    assert not cb.allow()


def test_half_open_after_reset_then_close():
    clk = Clock()
    cb = CircuitBreaker(fail_max=2, reset_timeout=10, clock=clk)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == cb.OPEN and not cb.allow()
    clk.advance(11)
    assert cb.state == cb.HALF_OPEN
    assert cb.allow()
    cb.record_success()
    assert cb.state == cb.CLOSED and cb.allow()


def test_half_open_failure_reopens():
    clk = Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=5, clock=clk)
    cb.record_failure()
    assert cb.state == cb.OPEN
    clk.advance(6)
    assert cb.state == cb.HALF_OPEN
    cb.record_failure()
    assert cb.state == cb.OPEN and not cb.allow()


def test_call_wraps_and_fastfails():
    cb = CircuitBreaker(fail_max=1, reset_timeout=100)

    def boom():
        raise ValueError("x")

    try:
        cb.call(boom)
    except ValueError:
        pass
    assert cb.state == cb.OPEN
    invoked = {"n": 0}

    def fn():
        invoked["n"] += 1
        return 1

    raised = False
    try:
        cb.call(fn)
    except CircuitBreakerOpen:
        raised = True
    assert raised and invoked["n"] == 0


def test_success_resets_failures():
    cb = CircuitBreaker(fail_max=3, reset_timeout=10)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == cb.CLOSED
