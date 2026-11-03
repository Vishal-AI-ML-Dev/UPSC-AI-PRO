"""Run isolation for the workflow evals (Phase 07, spec R4a / R4c).

``offline_isolation()`` fails closed:

1. secrets / service URLs are blanked in ``os.environ`` and ``settings``;
2. a network guard blocks ``socket.connect`` / ``connect_ex`` /
   ``create_connection`` / ``getaddrinfo`` and counts every attempt;
3. tripwires make constructing a real Gemini / Groq / embeddings client raise
   ``OfflineViolation`` (and count it);
4. the vector layer points at a fresh embedded Qdrant in a temp dir with fake
   embeddings; that is asserted before anything is written.

Production code catches broad exceptions and turns them into "⚠️" messages, so a
violation might be swallowed - that is why every attempt is COUNTED and the runner
checks ``ctx.violations`` after each case (any count -> exit 2).

``live_isolation()`` (owner-only T3) pins provider fallback / SDK retries, routes the
budget callback into every model via ``src.core.llm.langchain_callbacks`` and uses
the same temp Qdrant.

Restoration (R4c): every change is registered on one ``ExitStack`` with its undo
BEFORE it is made. The stack unwinds on success and on any exception; the global
caches are reset only after env vars and settings are back (that callback is
registered first, so it runs last).
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
_ABSENT = object()

SECRET_ENV = (
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "TAVILY_API_KEY",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "DATABASE_URL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_API_KEY",
    "COHERE_API_KEY",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_EMBED_API_KEY",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
    "REDIS_URL",
    "SENTRY_DSN",
)
FORCED_ENV = {
    "LANGFUSE_ENABLED": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
}
# settings attribute -> offline value (only attributes that exist are touched)
OFFLINE_SETTINGS = {
    "google_api_key": "",
    "groq_api_key": "",
    "tavily_api_key": "",
    "qdrant_url": "",
    "qdrant_api_key": "",
    "database_url": "",
    "cohere_api_key": "",
    "openai_compat_api_key": "",
    "openai_embed_api_key": "",
    "upstash_redis_rest_url": "",
    "upstash_redis_rest_token": "",
    "redis_url": "",
    "langfuse_enabled": False,
    "langfuse_public_key": "",
    "langfuse_secret_key": "",
    "web_search_enabled": False,
    "multi_query_enabled": False,
    "hyde_enabled": False,
    "rerank_enabled": False,
    "reflection_enabled": False,
    "plan_execute_enabled": False,
    "hitl_enabled": False,
}
# Live (T3): no secrets blanked (the owner's keys are used), but the same
# deterministic feature switches; fallback / retries are pinned by the runner.
LIVE_SETTINGS = {
    k: v
    for k, v in OFFLINE_SETTINGS.items()
    if k
    in (
        "langfuse_enabled",
        "web_search_enabled",
        "multi_query_enabled",
        "hyde_enabled",
        "rerank_enabled",
        "reflection_enabled",
        "plan_execute_enabled",
        "hitl_enabled",
        "qdrant_url",
    )
}


class OfflineViolation(ConnectionError):
    """A network attempt or real-client construction during an offline run."""

    abort_run = True


class IsolationError(RuntimeError):
    """The isolated state could not be established; nothing was written or sent."""


def load_bench():
    """``scripts/bench_retrieval.py`` (Phase 06, unchanged) as a module."""
    name = "_phase07_bench_retrieval"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / "bench_retrieval.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class IsolationContext:
    mode: str
    work_dir: Path
    qdrant_dir: Path
    eval_user_id: str
    network_attempts: list = field(default_factory=list)
    tripwire_hits: list = field(default_factory=list)

    @property
    def violations(self) -> int:
        return len(self.network_attempts) + len(self.tripwire_hits)


def _set_env(stack: contextlib.ExitStack, name: str, value: str) -> None:
    prior = os.environ.get(name, _ABSENT)

    def _undo():
        if prior is _ABSENT:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior

    stack.callback(_undo)
    os.environ[name] = value


def _set_attr(stack: contextlib.ExitStack, obj, name: str, value) -> None:
    prior = getattr(obj, name)
    stack.callback(setattr, obj, name, prior)
    setattr(obj, name, value)


def _client_classes() -> list:
    classes = []
    for mod, names in (
        ("langchain_google_genai", ("ChatGoogleGenerativeAI", "GoogleGenerativeAIEmbeddings")),
        ("langchain_groq", ("ChatGroq",)),
    ):
        with contextlib.suppress(Exception):
            module = __import__(mod, fromlist=list(names))
            classes.extend(getattr(module, n) for n in names if hasattr(module, n))
    return classes


def reset_global_caches() -> None:
    """Drop every cached client / settings- or env-derived value the eval may have
    touched: LLM singletons, the Langfuse switches and LangSmith's env-var cache
    (the run forces LANGCHAIN_TRACING_V2 / LANGSMITH_TRACING)."""
    from src.core import llm, observability

    llm.reset_llm()
    caches = [observability.langfuse_enabled, observability.get_langfuse_handler]
    with contextlib.suppress(Exception):
        from langsmith import utils as ls_utils

        caches.append(ls_utils.get_env_var)
    for fn in caches:
        with contextlib.suppress(Exception):
            fn.cache_clear()


# Module singletons / counters a run could create or change. They are not touched
# by the adapters today (the response cache, Upstash and NCERT storage belong to
# HTTP routes; the usage tracker only sees real clients), but every one is
# snapshotted and put back on exit so no run can leak state (review NIT 7).
PRESERVED_GLOBALS = (
    ("src.core.response_cache", "_cache_singleton"),
    ("src.core.upstash", "_singleton"),
    ("src.core.ncert_storage", "_source"),
    ("src.core.usage_tracker", "_cb_singleton"),
)


def _preserve_globals(stack: contextlib.ExitStack) -> None:
    import importlib

    for module_name, attr in PRESERVED_GLOBALS:
        module = importlib.import_module(module_name)
        stack.callback(setattr, module, attr, getattr(module, attr))
    from src.core import usage_tracker

    counters = {model: dict(row) for model, row in usage_tracker._by_model.items()}

    def _restore_counters():
        usage_tracker._by_model.clear()
        usage_tracker._by_model.update(counters)

    stack.callback(_restore_counters)


def _install_network_guard(stack: contextlib.ExitStack, ctx: IsolationContext) -> None:
    import socket

    def refuse(kind: str):
        def _refuse(*args, **kwargs):
            ctx.network_attempts.append(kind)
            raise OfflineViolation(f"offline isolation violated: network {kind} attempted")

        return _refuse

    stack.enter_context(mock.patch.object(socket.socket, "connect", refuse("connect")))
    stack.enter_context(mock.patch.object(socket.socket, "connect_ex", refuse("connect_ex")))
    stack.enter_context(mock.patch.object(socket, "create_connection", refuse("create_connection")))
    stack.enter_context(mock.patch.object(socket, "getaddrinfo", refuse("getaddrinfo")))
    # UDP has no connect step (review NIT 8). gRPC C-core bypasses Python sockets;
    # the client-constructor tripwires cover the clients that would use it.
    for name in ("sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            stack.enter_context(mock.patch.object(socket.socket, name, refuse(name)))


def _install_tripwires(stack: contextlib.ExitStack, ctx: IsolationContext) -> None:
    import src.core.llm as llm

    def trip(what: str):
        def _trip(*args, **kwargs):
            ctx.tripwire_hits.append(what)
            raise OfflineViolation(f"offline isolation violated: real client {what} constructed")

        return _trip

    stack.enter_context(mock.patch.object(llm, "_make_gemini", trip("_make_gemini")))
    stack.enter_context(mock.patch.object(llm, "_make_groq", trip("_make_groq")))
    for cls in _client_classes():
        stack.enter_context(mock.patch.object(cls, "__init__", trip(cls.__name__)))


def _enter_vector_store(stack: contextlib.ExitStack, ctx: IsolationContext, embeddings) -> None:
    from src.core.config import settings

    if settings.qdrant_url:
        raise IsolationError("offline isolation violated: QDRANT_URL is set")
    vs = stack.enter_context(load_bench().isolated_vector_store(ctx.qdrant_dir, embeddings))
    qdrant_path = Path(settings.qdrant_path).resolve()
    if settings.qdrant_url or not vs._qdrant_local():
        raise IsolationError("offline isolation violated: vector store is not embedded-local")
    if ctx.work_dir.resolve() not in (qdrant_path, *qdrant_path.parents):
        raise IsolationError(
            "offline isolation violated: Qdrant path is outside the run's temp dir"
        )


def _new_context(mode: str, work_root: Path | None) -> IsolationContext:
    root = Path(work_root) if work_root else Path(tempfile.gettempdir())
    work_dir = Path(tempfile.mkdtemp(prefix="upsc_evals_", dir=root))
    return IsolationContext(
        mode=mode,
        work_dir=work_dir,
        qdrant_dir=work_dir / "qdrant",
        eval_user_id=f"eval-{uuid.uuid4().hex}",
    )


@contextlib.contextmanager
def offline_isolation(*, work_root: Path | None = None, embeddings=None):
    """Strict, fail-closed offline isolation (see module docstring)."""
    from src.core.config import settings

    ctx = _new_context("offline", work_root)
    with contextlib.ExitStack() as stack:
        # Registered first -> run last: temp dir removal, then cache reset after
        # env + settings are restored.
        stack.callback(shutil.rmtree, ctx.work_dir, True)
        stack.callback(reset_global_caches)
        _preserve_globals(stack)
        for name in SECRET_ENV:
            _set_env(stack, name, "")
        for name, value in FORCED_ENV.items():
            _set_env(stack, name, value)
        for name, value in OFFLINE_SETTINGS.items():
            if hasattr(settings, name):
                _set_attr(stack, settings, name, value)
        _install_network_guard(stack, ctx)
        _install_tripwires(stack, ctx)
        reset_global_caches()
        _enter_vector_store(stack, ctx, embeddings or load_bench().FakeEmbeddings())
        yield ctx


@contextlib.contextmanager
def live_isolation(
    *,
    embeddings,
    sdk_max_retries: int,
    allow_fallback: bool,
    llm_callbacks: list,
    send_guard=None,
    work_root: Path | None = None,
):
    """Owner-only live isolation: pinned retries/fallback, budget callbacks, temp Qdrant.

    Review S1: callbacks bound with ``with_config`` are dropped by ``bind_tools`` /
    ``with_structured_output``, so the tool-agent and router model factories are
    wrapped to re-attach them, and ``send_guard(run_manager)`` runs inside every
    production client's send methods (``_TimeoutGuard``) - a send without a
    reservation is refused before it leaves the process.
    """
    import src.core.llm as llm
    import src.graph.supervisor as supervisor
    import src.graph.tools as tools_mod
    from src.core.config import settings

    ctx = _new_context("live", work_root)
    with contextlib.ExitStack() as stack:
        stack.callback(shutil.rmtree, ctx.work_dir, True)
        stack.callback(reset_global_caches)
        _preserve_globals(stack)
        for name, value in FORCED_ENV.items():
            _set_env(stack, name, value)
        _set_env(stack, "QDRANT_URL", "")
        for name, value in LIVE_SETTINGS.items():
            if hasattr(settings, name):
                _set_attr(stack, settings, name, value)
        _set_attr(stack, settings, "enable_provider_fallback", bool(allow_fallback))
        _set_attr(stack, settings, "llm_max_retries", int(sdk_max_retries))
        cbs = list(llm_callbacks)
        stack.enter_context(mock.patch.object(llm, "langchain_callbacks", lambda: list(cbs)))
        _install_budget_wrappers(stack, cbs, tools_mod, supervisor)
        if send_guard is not None:
            install_send_guard(stack, send_guard)
        reset_global_caches()
        _enter_vector_store(stack, ctx, embeddings)
        yield ctx


def _install_budget_wrappers(stack: contextlib.ExitStack, cbs: list, tools_mod, supervisor) -> None:
    """Re-attach the budget callbacks to tool-bound and structured-output models."""
    real_bind = tools_mod.bind_tools_with_fallback
    real_structured = supervisor._structured_llm

    def _bind(llm=None, tools=None):
        return real_bind(llm=llm, tools=tools).with_config({"callbacks": list(cbs)})

    def _structured(schema):
        return real_structured(schema).with_config({"callbacks": list(cbs)})

    stack.enter_context(mock.patch.object(tools_mod, "bind_tools_with_fallback", _bind))
    stack.enter_context(mock.patch.object(supervisor, "_structured_llm", _structured))


def install_send_guard(stack: contextlib.ExitStack, guard, cls=None) -> None:
    """Run ``guard(run_manager)`` before every send of ``cls`` (default: the
    ``_TimeoutGuard`` mixin every production Gemini / Groq client inherits)."""
    if cls is None:
        from src.core.llm import _TimeoutGuard as cls

    real = {name: getattr(cls, name) for name in ("_generate", "_stream", "_agenerate", "_astream")}

    def _scope(run_manager):
        # The guard may return a context manager spanning the send (the budget
        # callback does: exiting it closes a managed run's send); None is fine too.
        return guard(run_manager) or contextlib.nullcontext()

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        with _scope(run_manager):
            return real["_generate"](self, messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        with _scope(run_manager):
            yield from real["_stream"](self, messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        with _scope(run_manager):
            return await real["_agenerate"](
                self, messages, stop=stop, run_manager=run_manager, **kwargs
            )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        with _scope(run_manager):
            async for chunk in real["_astream"](
                self, messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                yield chunk

    for name, fn in (
        ("_generate", _generate),
        ("_stream", _stream),
        ("_agenerate", _agenerate),
        ("_astream", _astream),
    ):
        stack.enter_context(mock.patch.object(cls, name, fn))
