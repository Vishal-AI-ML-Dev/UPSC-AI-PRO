"""
Observability - Langfuse tracing for every LLM call.

Har LangChain LLM call ka trace Langfuse pe jaata hai: latency, tokens, cost,
prompt/response. Agar Langfuse keys set nahi hain (ya langfuse_enabled=False),
to sab kuch no-op ho jaata hai aur app bilkul pehle jaisa chalta hai.

Usage (llm.py):
    from src.core.observability import langchain_callbacks
    model = ChatGoogleGenerativeAI(...)
    cbs = langchain_callbacks()
    if cbs:
        model = model.with_config({"callbacks": cbs})

Supports both Langfuse v3 (langfuse.langchain) and v2 (langfuse.callback).
"""

import logging
import os
from functools import lru_cache

from src.core.config import settings

logger = logging.getLogger(__name__)


def _export_env() -> None:
    """Push Langfuse creds into env so the SDK/handler can pick them up."""
    if settings.langfuse_public_key:
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    if settings.langfuse_secret_key:
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)


@lru_cache
def langfuse_enabled() -> bool:
    """True only if explicitly enabled AND both keys are present."""
    return bool(
        settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key
    )


@lru_cache
def get_langfuse_handler():
    """Return a cached LangChain CallbackHandler, or None if disabled/unavailable."""
    if not langfuse_enabled():
        logger.info("Langfuse disabled (no keys or langfuse_enabled=False)")
        return None
    _export_env()
    # Langfuse v3 (OpenTelemetry-based)
    try:
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler()
        logger.info("Langfuse tracing enabled (v3)")
        return handler
    except Exception as e_v3:
        logger.debug(f"Langfuse v3 handler unavailable: {e_v3}")
    # Langfuse v2 fallback
    try:
        from langfuse.callback import CallbackHandler as _CBv2

        handler = _CBv2(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        )
        logger.info("Langfuse tracing enabled (v2)")
        return handler
    except Exception as e_v2:
        logger.warning(f"Langfuse handler init failed, tracing off: {e_v2}")
        return None


def langchain_callbacks() -> list:
    """Callbacks list to attach to LangChain runs ( [] if disabled )."""
    handler = get_langfuse_handler()
    return [handler] if handler is not None else []


def trace_config(
    name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list | None = None,
    with_callbacks: bool = False,
    extra: dict | None = None,
) -> dict:
    """Build a LangChain runnable config that makes Langfuse traces readable.

    Adds a human-friendly ``name`` plus optional user/session attribution and
    tags. These travel as the reserved ``langfuse_*`` metadata keys, which are
    inherited by every nested run, so the model-level handler attached in
    ``llm.py`` records them even when the handler itself is not attached here.
    The result: dashboard rows can be filtered by user, grouped by session and
    labelled by tag instead of showing only a bare model-class name.

    Set ``with_callbacks=True`` only when the invoked runnable does NOT already
    carry the Langfuse handler; otherwise the same handler would be registered
    twice and emit duplicate observations.

    Args:
        name: Trace/run name, e.g. "lecture-notes" or "study-aids".
        user_id: Optional end-user id for per-user filtering.
        session_id: Optional id to group related traces (e.g. a thread/video).
        tags: Optional dashboard tags; defaults to ``[name]``.
        with_callbacks: Attach the Langfuse handler at config level too.
        extra: Optional extra metadata to merge in.
    """
    label_tags = list(tags) if tags else [name]
    metadata: dict = {"langfuse_tags": label_tags}
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if session_id:
        metadata["langfuse_session_id"] = session_id
    if extra:
        metadata.update(extra)

    config: dict = {"run_name": name, "tags": label_tags, "metadata": metadata}
    if with_callbacks:
        cbs = langchain_callbacks()
        if cbs:
            config["callbacks"] = cbs
    return config


def flush() -> None:
    """Best-effort flush of pending traces (call on shutdown)."""
    if not langfuse_enabled():
        return
    # v3: flush via global client
    try:
        from langfuse import get_client

        get_client().flush()
        logger.info("Langfuse traces flushed (v3)")
        return
    except Exception:
        pass
    # v2: handler.flush()
    try:
        handler = get_langfuse_handler()
        if handler is not None and hasattr(handler, "flush"):
            handler.flush()
            logger.info("Langfuse traces flushed (v2)")
    except Exception as e:
        logger.debug(f"Langfuse flush skipped: {e}")


# ---------- Eval metrics (Phase 3B) ----------


def _has_active_span() -> bool:
    """True only if we are inside a live OTEL span (Langfuse v3 trace context)."""
    try:
        from opentelemetry import trace as _otel

        span = _otel.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        return bool(ctx and ctx.is_valid)
    except Exception:
        return False


def score(name, value, comment=None):
    """Best-effort: attach a numeric score to the current Langfuse trace.
    Only runs inside an active span (otherwise Langfuse logs noisy context
    warnings and skips anyway). No-op if Langfuse disabled."""
    if not langfuse_enabled() or not _has_active_span():
        return
    try:
        from langfuse import get_client

        get_client().score_current_trace(name=name, value=float(value), comment=comment)
    except Exception as e:
        logger.debug(f"Langfuse score skipped: {e}")


def log_retrieval_metrics(source, query, k, num_relevant, scores):
    """Log retrieval precision@k + grounding for a RAG query.
    Always emits a structured log; sends Langfuse scores when enabled."""
    try:
        k = max(int(k or 0), 1)
        rel = int(num_relevant or 0)
        precision = round(rel / k, 3)
        valid = [s for s in (scores or []) if s is not None]
        avg_score = round(sum(valid) / len(valid), 3) if valid else 0.0
        top_score = round(max(valid), 3) if valid else 0.0
        grounded = rel > 0
        q = (query or "")[:80].replace(chr(10), " ")
        logger.info(
            f"RETRIEVAL | source={source} | precision@{k}={precision} "
            f"| relevant={rel}/{k} | top={top_score} | avg={avg_score} "
            f"| grounded={grounded} | q='{q}'"
        )
        score(f"retrieval_precision@{k}", precision, comment=f"{source}: {rel}/{k} relevant")
        score("grounded", 1.0 if grounded else 0.0, comment=str(source))
    except Exception as e:
        logger.debug(f"log_retrieval_metrics skipped: {e}")


def log_eval_metrics(kind, score_value, max_score):
    """Log evaluation accuracy (model score / max) as Langfuse score + structured log."""
    try:
        if not max_score:
            return
        accuracy = round(float(score_value) / float(max_score), 3)
        logger.info(f"EVAL | kind={kind} | score={score_value}/{max_score} | accuracy={accuracy}")
        score(f"eval_accuracy_{kind}", accuracy, comment=f"{score_value}/{max_score}")
    except Exception as e:
        logger.debug(f"log_eval_metrics skipped: {e}")
