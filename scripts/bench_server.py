"""Offline benchmark server for the request-path benchmark (Phase 03).

Boots the REAL UPSC-AI-PRO FastAPI app (lifespan, every middleware, every
router) on ONE uvicorn worker, fully offline, with two synthetic dependencies:

* ``SyntheticLLM`` - replaces ``evaluate_answer`` (Answer Evaluator) with a
  generator that blocks for ``--llm-seconds`` (``time.sleep``), like a sync
  provider call, then yields a fixed answer. No API key, no network.
* ``SyntheticUpstash`` - when ``--upstash-rtt`` > 0, replaces the rate
  limiter's Upstash REST client with a fake that blocks for that many seconds
  per command and keeps a thread-safe counter. ``--upstash-rtt 0`` keeps the
  real client with empty credentials, i.e. Upstash disabled -> in-memory.

Safety (never reads ``.env`` or credentials):
* the process ``chdir``s into a fresh temp directory BEFORE importing ``src``;
  pydantic-settings resolves ``env_file=".env"`` relative to the cwd, so the
  project ``.env`` is never read;
* the process drops every environment variable that is not on ``ENV_ALLOW``
  (OS basics only) - however it is started, credentials exported in the shell
  never reach the app - and then sets ``offline_environment()``: throwaway
  SQLite, local Qdrant path, web search off, every key/service setting empty,
  Sentry/Langfuse/LangSmith off.
Auth: ``get_current_user`` is overridden with a fixed user - the benchmark
measures the request path, not JWT/DB session validation.

Not meant to be run by hand normally (``bench_request_path.py`` starts and
stops it), but it can be::

    uv run python scripts/bench_server.py --code-root . --port 8765 --upstash-rtt 0 --llm-seconds 0.5

Every response carries ``X-Bench-Nonce: <--nonce>`` so the driver can tell its
own server from anything else answering on the port. The app runs with auth
bypassed: bind stays ``127.0.0.1`` and data is throwaway.

``--code-root`` is a directory that contains ``src/``, ``migrations/`` and
``alembic.ini`` (the working tree, or a ``git archive`` export of another ref).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time

# OS variables kept for the server (Windows needs the system ones to start Python and
# open sockets). Everything else - including credentials exported in the shell - is
# dropped before ``src`` is imported. Shared with bench_request_path.py.
ENV_ALLOW = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "LANG",
        "LC_ALL",
    }
)

BENCH_JWT_SECRET = "bench-only-secret-not-for-any-real-deployment-0123456789"
BENCH_USER = {"id": "bench-user", "email": "bench@localhost", "sid": "bench-sid"}


def allowed_environment(environ) -> dict[str, str]:
    """Only the OS basics from ``environ`` (case-insensitive names, as on Windows)."""
    return {k: v for k, v in environ.items() if k.upper() in ENV_ALLOW}


def isolate_environment(workdir: str) -> None:
    """Replace this process's environment: OS basics + offline settings only."""
    keep = allowed_environment(os.environ)
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update(offline_environment(workdir))


def with_nonce(app, nonce: str):
    """ASGI wrapper adding ``X-Bench-Nonce`` to every HTTP response."""
    header = (b"x-bench-nonce", nonce.encode("ascii"))

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)

        async def send_with_nonce(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), header]}
            await send(message)

        return await app(scope, receive, send_with_nonce)

    return wrapped


def offline_environment(workdir: str) -> dict[str, str]:
    """Settings that keep the app offline and away from any real credentials."""
    return {
        "DATABASE_URL": f"sqlite:///{workdir.replace(os.sep, '/')}/bench.db",
        "JWT_SECRET": BENCH_JWT_SECRET,
        "GOOGLE_API_KEY": "",
        "GROQ_API_KEY": "",
        "LANGFUSE_ENABLED": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
        "SENTRY_DSN": "",
        "UPSTASH_REDIS_REST_URL": "",
        "UPSTASH_REDIS_REST_TOKEN": "",
        "QDRANT_URL": "",
        "QDRANT_PATH": f"{workdir}/qdrant",
        "R2_ENDPOINT_URL": "",
        "R2_ACCESS_KEY_ID": "",
        "R2_SECRET_ACCESS_KEY": "",
        "WEB_SEARCH_ENABLED": "false",
        "REQUIRE_EMAIL_VERIFICATION": "false",
        "RATE_LIMIT_REQUESTS": "100000000",
        "AUTH_RATE_LIMIT_REQUESTS": "100000000",
        "PYTHONIOENCODING": "utf-8",
    }


class SyntheticLLM:
    """Stand-in for a blocking LLM call: sleeps, then yields a fixed answer."""

    def __init__(self, seconds: float):
        self.seconds = seconds

    def __call__(self, question, answer):
        time.sleep(self.seconds)
        yield "**Score: 7/10**\nSynthetic benchmark answer."


class SyntheticUpstash:
    """Stand-in for the Upstash REST client: each command blocks for ``rtt``."""

    enabled = True

    def __init__(self, rtt: float):
        self.rtt = rtt
        self._n = 0
        self._lock = threading.Lock()

    def command(self, command):
        time.sleep(self.rtt)
        if command[0] == "INCR":
            with self._lock:
                self._n += 1
                return self._n
        return True


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--code-root", required=True, help="dir containing src/, migrations/, alembic.ini"
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--upstash-rtt", type=float, default=0.0, help="seconds; 0 = Upstash disabled")
    ap.add_argument("--llm-seconds", type=float, default=0.5)
    ap.add_argument("--nonce", default="manual", help="echoed in X-Bench-Nonce")
    ap.add_argument("--workdir", help="empty scratch dir (default: a new temp dir)")
    args = ap.parse_args(argv)

    code_root = os.path.abspath(args.code_root)
    workdir = (
        os.path.abspath(args.workdir) if args.workdir else tempfile.mkdtemp(prefix="upsc_bench_")
    )
    os.makedirs(workdir, exist_ok=True)
    if os.listdir(workdir):  # e.g. --workdir . in the repo would make pydantic read .env
        raise SystemExit(f"bench_server: --workdir must be empty: {workdir}")
    isolate_environment(workdir)
    os.chdir(workdir)  # no .env here -> pydantic-settings reads none
    sys.path.insert(0, code_root)

    import uvicorn

    import src.api.main as app_main
    import src.api.rate_limit_core as rate_limit_core
    import src.api.routes.evaluator as evaluator_routes
    from src.api.deps import get_current_user

    loaded = os.path.abspath(app_main.__file__)
    norm = os.path.normcase  # Windows paths are case-insensitive
    if norm(os.path.commonpath([loaded, code_root])) != norm(code_root):
        raise SystemExit(f"bench_server: imported src from {loaded}, expected under {code_root}")
    for module, name in ((evaluator_routes, "evaluate_answer"), (rate_limit_core, "get_upstash")):
        if not hasattr(module, name):  # a renamed seam would silently skip the synthetic fake
            raise SystemExit(
                f"bench_server: {module.__name__}.{name} not found in this code version"
            )

    evaluator_routes.evaluate_answer = SyntheticLLM(args.llm_seconds)
    app_main.app.dependency_overrides[get_current_user] = lambda: dict(BENCH_USER)
    if args.upstash_rtt > 0:
        fake = SyntheticUpstash(args.upstash_rtt)
        rate_limit_core.get_upstash = lambda: fake

    uvicorn.run(
        with_nonce(app_main.app, args.nonce),
        host="127.0.0.1",
        port=args.port,
        workers=1,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
