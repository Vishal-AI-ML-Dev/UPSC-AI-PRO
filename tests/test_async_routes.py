"""Phase 03 — no blocking work on the event loop (spec R1–R4, AC1–AC3).

FastAPI runs ``def`` handlers in a threadpool but ``async def`` handlers directly
on the event loop. With a single Uvicorn worker, one ``async def`` handler doing a
sync LLM / DB / Qdrant / HTTP call stalls every other request. These tests:

* statically pin the set of ``async def`` route handlers to an allow-list of
  handlers verified as non-blocking (spec section 3b);
* prove the loop stays responsive while a slow sync endpoint, a ``sync=true``
  upload and an Upstash-backed rate-limit check are in flight.

Fully offline: slow work is simulated with ``time.sleep`` in patched functions.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import pathlib
import threading
import time

import httpx
import pytest
from fastapi import FastAPI

from tests.test_upload_limits import make_pdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROUTES = ROOT / "src" / "api" / "routes"
MAIN = ROOT / "src" / "api" / "main.py"
_VERBS = {"get", "post", "put", "patch", "delete", "api_route"}

# ``async def`` route handlers verified as non-blocking. Anything else that does
# sync DB / LLM / Qdrant / file / SMTP / HTTP work must be a plain ``def``.
ALLOWED_ASYNC = {
    # pure / in-memory
    "auth:me",
    "cost:cost_access",
    "cost:cost_overview",
    "experiments:experiments_access",
    "monitoring:monitoring_access",
    "monitoring:monitoring_overview",
    "current_affairs:topics",
    "current_affairs:dates",
    "current_affairs:months",
    "pyq:topics",
    "main:health_check",
    "main:root",
    # streaming: only build a sync generator; guard_stream runs next() in the threadpool
    "current_affairs:daily_ca",
    "current_affairs:editorial",
    "current_affairs:monthly",
    "ncert:chat_ncert",
    "planner:generate",
    "pyq:generate",
    "pyq:bank_generate",
    "upload:chat_upload",
    "lecture:chat",
    # await the upload read; sync work is offloaded with run_in_threadpool
    "upload:upload_pdf",
    "pyq:bank_upload",
}


def _is_route_decorator(dec: ast.expr) -> bool:
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _VERBS
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id in {"router", "app"}
    )


def _route_handlers() -> dict[str, bool]:
    """``"module:function" -> is_async`` for every registered route handler."""
    files = [p for p in ROUTES.glob("*.py") if p.stem not in {"__init__", "mentor"}]
    files.append(MAIN)  # mentor.py is not registered (dead code, ADR-006)
    out: dict[str, bool] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                _is_route_decorator(d) for d in node.decorator_list
            ):
                out[f"{path.stem}:{node.name}"] = isinstance(node, ast.AsyncFunctionDef)
    return out


def test_async_handlers_are_allow_listed():
    handlers = _route_handlers()
    assert len(handlers) > 40  # sanity: the walker really found the routes
    unexpected = sorted(name for name, is_async in handlers.items() if is_async)
    unexpected = [n for n in unexpected if n not in ALLOWED_ASYNC]
    assert not unexpected, (
        "These `async def` route handlers are not on the non-blocking allow-list: "
        f"{unexpected}. Make them plain `def` (FastAPI runs them in the threadpool), "
        "or offload sync work with run_in_threadpool and add them to ALLOWED_ASYNC."
    )


def test_allow_list_has_no_stale_entries():
    handlers = _route_handlers()
    stale = sorted(n for n in ALLOWED_ASYNC if not handlers.get(n))
    assert not stale, f"ALLOWED_ASYNC lists handlers that are missing or not async: {stale}"


# ---------------------------------------------------------------------------
# Loop responsiveness against the real app
# ---------------------------------------------------------------------------
SLOW = 1.0
FAST_ENOUGH = 0.5


@pytest.fixture
def asgi_app(client):
    """The real app (lifespan already run by ``client``) with auth stubbed out."""
    from src.api.deps import get_current_user

    app = client.app
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "loop-test-user",
        "email": "loop@test.local",
        "sid": "sid",
    }
    yield app
    app.dependency_overrides.pop(get_current_user, None)


class _Started:
    """Set by the patched slow function the moment its blocking work begins."""

    def __init__(self):
        self.at: float | None = None

    def mark(self):
        self.at = time.perf_counter()


async def _slow_then_health(app, slow_request, started: _Started):
    """Run ``slow_request``; once its blocking work has started, hit /health.

    Returns the slow response, its duration, the /health response and how long
    /health took counted from the moment the blocking work started. If the
    blocking work runs on the event loop, /health cannot even be sent until it
    ends, so that number is >= SLOW.

    Never hangs: if the slow request returns without reaching the patched
    function (e.g. an early 4xx/5xx), the probe stops waiting and the test fails
    with that status; the whole exchange is also capped by ``asyncio.wait_for``
    (``ASGITransport`` ignores client timeouts).
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        t0 = time.perf_counter()

        async def slow():
            r = await slow_request(c)
            return r, time.perf_counter() - t0

        slow_task = asyncio.ensure_future(slow())

        async def health():
            while started.at is None and not slow_task.done():
                await asyncio.sleep(0.01)
            if started.at is None:
                return None, None
            r = await c.get("/health")
            return r, time.perf_counter() - started.at

        async def both():
            return await asyncio.gather(slow_task, health())

        (slow_r, slow_s), (health_r, health_s) = await asyncio.wait_for(both(), timeout=15)
    assert started.at is not None, (
        f"slow work never started: {slow_r.status_code} {slow_r.text[:200]}"
    )
    return slow_r, slow_s, health_r, health_s


def test_blocking_sync_endpoint_does_not_stall_loop(asgi_app, monkeypatch):
    import src.api.routes.evaluator as evaluator_routes

    started = _Started()

    def slow_eval(question, answer):
        started.mark()
        time.sleep(SLOW)
        yield "**Score: 7/10**\nGood answer."

    monkeypatch.setattr(evaluator_routes, "evaluate_answer", slow_eval)

    slow_r, slow_s, health_r, health_s = asyncio.run(
        _slow_then_health(
            asgi_app,
            lambda c: c.post(
                "/api/v1/evaluator/evaluate/sync",
                json={
                    "question": "What is federalism?",
                    "answer": "Federalism divides power. " * 4,
                },
            ),
            started,
        )
    )
    assert slow_r.status_code == 200
    body = slow_r.json()
    assert body["response"].startswith("**Score: 7/10**")
    assert "structured" in body  # response shape unchanged
    assert slow_s >= SLOW
    assert health_r.status_code == 200
    assert health_s < FAST_ENOUGH, f"/health waited {health_s:.2f}s behind a blocking handler"


def test_sync_upload_branch_does_not_stall_loop(asgi_app, monkeypatch):
    import src.api.routes.upload as upload_routes

    seen = {}
    started = _Started()

    def slow_process(content, filename, user_id=None):
        seen["user_id"] = user_id
        started.mark()
        time.sleep(SLOW)
        return {"hash": "a" * 32, "book_info": {"title": "T"}, "notes": "notes"}

    monkeypatch.setattr(upload_routes, "process_upload", slow_process)

    slow_r, slow_s, health_r, health_s = asyncio.run(
        _slow_then_health(
            asgi_app,
            lambda c: c.post(
                "/api/v1/upload/process?sync=true",
                files={"file": ("notes.pdf", make_pdf(), "application/pdf")},
            ),
            started,
        )
    )
    assert slow_r.status_code == 200
    assert slow_r.json() == {
        "success": True,
        "filename": "notes.pdf",
        "hash": "a" * 32,
        "book_info": {"title": "T"},
        "notes": "notes",
    }
    assert seen["user_id"] == "loop-test-user"
    assert slow_s >= SLOW
    assert health_r.status_code == 200
    assert health_s < FAST_ENOUGH, f"/health waited {health_s:.2f}s behind a sync upload"


def test_sync_upload_branch_keeps_error_mapping(asgi_app, monkeypatch):
    import src.api.routes.upload as upload_routes
    from src.core.errors import GENERIC_ERROR, UserFacingError

    def user_error(content, filename, user_id=None):
        raise UserFacingError("This PDF has no extractable text.")

    def crash(content, filename, user_id=None):
        raise RuntimeError("secret internal detail")

    async def post(c):
        return await c.post(
            "/api/v1/upload/process?sync=true",
            files={"file": ("notes.pdf", make_pdf(), "application/pdf")},
        )

    async def run():
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            monkeypatch.setattr(upload_routes, "process_upload", user_error)
            r1 = await post(c)
            monkeypatch.setattr(upload_routes, "process_upload", crash)
            r2 = await post(c)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert (r1.status_code, r1.json()["detail"]) == (400, "This PDF has no extractable text.")
    assert (r2.status_code, r2.json()["detail"]) == (500, GENERIC_ERROR)
    assert "secret" not in r2.text


def test_sync_pyq_bank_upload_does_not_stall_loop(asgi_app, monkeypatch):
    import src.api.routes.pyq as pyq_routes

    seen = {}
    started = _Started()

    def slow_bank(content, filename, user_id):
        seen["user_id"] = user_id
        started.mark()
        time.sleep(SLOW)
        return {"filename": filename, "questions_indexed": 3}

    monkeypatch.setattr(pyq_routes, "build_question_bank", slow_bank)

    slow_r, slow_s, health_r, health_s = asyncio.run(
        _slow_then_health(
            asgi_app,
            lambda c: c.post(
                "/api/v1/pyq/bank/upload?sync=true",
                files={"file": ("paper.pdf", make_pdf(), "application/pdf")},
            ),
            started,
        )
    )
    assert slow_r.status_code == 200
    assert slow_r.json() == {"success": True, "filename": "paper.pdf", "questions_indexed": 3}
    assert seen["user_id"] == "loop-test-user"
    assert slow_s >= SLOW
    assert health_r.status_code == 200
    assert health_s < FAST_ENOUGH, f"/health waited {health_s:.2f}s behind a sync bank upload"


# ---------------------------------------------------------------------------
# Rate limiter: Upstash HTTP must not run on the loop (R3 / AC3)
# ---------------------------------------------------------------------------
class _SlowUpstash:
    enabled = True

    def __init__(self):
        self.calls = []

    def command(self, command):
        self.calls.append(command[0])
        time.sleep(0.4)
        return 1 if command[0] == "INCR" else True


def test_rate_limiter_upstash_check_runs_off_loop(monkeypatch):
    from src.api import rate_limit, rate_limit_core
    from src.api.rate_limit import RateLimitMiddleware

    fake = _SlowUpstash()
    monkeypatch.setattr(rate_limit.settings, "rate_limit_redis_enabled", True)
    monkeypatch.setattr(rate_limit_core, "get_upstash", lambda: fake)

    app = FastAPI()

    @app.get("/x")
    def _x():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, max_requests=5, period=60)

    async def run():
        gaps = []
        stop = asyncio.Event()

        async def ticker():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        tick = asyncio.create_task(ticker())
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await asyncio.sleep(0.05)
            r = await c.get("/x")
        stop.set()
        await tick
        return r, max(gaps)

    r, worst_gap = asyncio.run(run())
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert fake.calls == ["INCR", "EXPIRE"]  # Upstash really was consulted
    assert worst_gap < 0.3, f"event loop stalled {worst_gap:.2f}s on the Upstash call"


def test_rate_limiter_upstash_limit_still_enforced(monkeypatch):
    from src.api import rate_limit, rate_limit_core
    from src.api.rate_limit import RateLimitMiddleware

    class _Counting:
        enabled = True

        def __init__(self):
            self.n = 0

        def command(self, command):
            if command[0] == "INCR":
                self.n += 1
                return self.n
            return True

    monkeypatch.setattr(rate_limit.settings, "rate_limit_redis_enabled", True)
    counting = _Counting()
    monkeypatch.setattr(rate_limit_core, "get_upstash", lambda: counting)

    app = FastAPI()

    @app.get("/x")
    def _x():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, max_requests=2, period=60)

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return [await c.get("/x") for _ in range(3)]

    rs = asyncio.run(run())
    assert [r.status_code for r in rs] == [200, 200, 429]
    assert rs[2].headers.get("retry-after")


# ---------------------------------------------------------------------------
# Review fixes S2 / S3 / N1: limiter correctness off the loop, dedicated budget
# ---------------------------------------------------------------------------
class _RacyDeque(collections.deque):
    """A deque that yields the GIL on ``len()``, widening any check-then-append
    race in the sliding window so an unlocked implementation over-admits."""

    def __len__(self):
        size = super().__len__()
        time.sleep(0.001)  # the value is now stale: other threads may append
        return size


class _OutageUpstash:
    """Configured (enabled) but every command fails -> in-memory fallback."""

    enabled = True

    def command(self, command):
        return None


def _hammer(fn, n):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def worker():
        barrier.wait()
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def test_in_memory_window_is_exact_under_concurrent_threads(monkeypatch):
    from src.api import rate_limit_core

    monkeypatch.setattr(rate_limit_core, "deque", _RacyDeque)
    window = rate_limit_core._InMemoryLimiter(max_requests=20, period=60)
    results, errors = _hammer(lambda: window.hit("ip", time.monotonic()), 80)
    assert not errors
    assert sum(1 for ok, _ in results if ok) == 20
    assert all(retry > 0 for ok, retry in results if not ok)


def test_upstash_outage_fallback_is_exact_under_concurrent_threads(monkeypatch):
    from src.api import rate_limit_core

    monkeypatch.setattr(rate_limit_core, "deque", _RacyDeque)
    limiter = rate_limit_core.RateLimiter(10, 60, redis=_OutageUpstash())
    results, errors = _hammer(lambda: limiter.check("auth:203.0.113.5"), 50)
    assert not errors
    assert sum(1 for ok, _ in results if ok) == 10


def test_concurrent_requests_during_upstash_outage_through_middleware(monkeypatch):
    from src.api import rate_limit, rate_limit_core
    from src.api.rate_limit import RateLimitMiddleware

    monkeypatch.setattr(rate_limit_core, "deque", _RacyDeque)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_redis_enabled", True)
    monkeypatch.setattr(rate_limit_core, "get_upstash", lambda: _OutageUpstash())

    app = FastAPI()

    @app.get("/x")
    def _x():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, max_requests=10, period=60)

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.11", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(*(c.get("/x") for _ in range(30)))

    codes = sorted(r.status_code for r in asyncio.run(run()))
    assert codes == [200] * 10 + [429] * 20  # exact budget, never a 500


def test_uses_redis_is_public_and_reflects_backend():
    from src.api.rate_limit import _Disabled
    from src.api.rate_limit_core import RateLimiter

    class _Off:
        enabled = False

    assert RateLimiter(1, 60, redis=_OutageUpstash()).uses_redis() is True
    assert RateLimiter(1, 60, redis=_Off()).uses_redis() is False
    assert RateLimiter(1, 60, redis=_Disabled()).uses_redis() is False


def test_upstash_checks_use_one_dedicated_budget_of_eight(monkeypatch):
    import anyio
    import anyio.to_thread

    from src.api import rate_limit, rate_limit_core
    from src.api.rate_limit import RateLimitMiddleware

    budget = rate_limit._UPSTASH_THREADS
    assert isinstance(budget, anyio.CapacityLimiter)
    assert budget.total_tokens == 8

    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "n": 0}

    class _SlowCounting:
        enabled = True

        def command(self, command):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if command[0] == "INCR":
                    state["n"] += 1
                    n = state["n"]
            time.sleep(0.5)
            with lock:
                state["active"] -= 1
            return n if command[0] == "INCR" else True

    monkeypatch.setattr(rate_limit.settings, "rate_limit_redis_enabled", True)
    monkeypatch.setattr(rate_limit_core, "get_upstash", lambda: _SlowCounting())

    app = FastAPI()

    @app.get("/x")
    def _x():
        return {"ok": True}

    @app.get("/health")
    def _health():  # exempt from rate limiting; runs on the DEFAULT threadpool
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, max_requests=1000, period=60)

    async def run():
        default = anyio.to_thread.current_default_thread_limiter()
        transport = httpx.ASGITransport(app=app, client=("203.0.113.12", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
            flood = asyncio.gather(*(c.get("/x") for _ in range(24)))
            # t=0.1 s: 8 checks are inside the 0.5 s Upstash call, 16 are waiting
            # for the budget, and no /x handler has run yet -> any borrowed token
            # on the default pool could only come from the rate limiter.
            await asyncio.sleep(0.1)
            borrowed = default.borrowed_tokens
            t0 = time.perf_counter()
            health = await c.get("/health")
            health_s = time.perf_counter() - t0
            rs = await asyncio.wait_for(flood, timeout=30)
        return rs, health, health_s, borrowed

    rs, health, health_s, borrowed = asyncio.run(run())
    assert [r.status_code for r in rs] == [200] * 24
    assert 1 < state["peak"] <= 8, state["peak"]  # parallel, but never beyond the budget
    assert borrowed == 0, "Upstash checks must not use the default threadpool"
    assert budget.borrowed_tokens == 0  # budget fully released for later tests
    # Smoke check only (generous for loaded CI): an exempt request is not queued
    # behind the saturated rate-limit budget.
    assert health.status_code == 200
    assert health_s < 1.0, f"unrelated request waited {health_s:.2f}s behind the limiter"
    assert rate_limit._UPSTASH_THREADS is budget  # one shared limiter, not per request
