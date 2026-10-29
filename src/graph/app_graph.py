"""Production graph entry point.

Builds the supervisor graph wired with persistent memory (checkpointer + store)
and provides a config helper that attaches Langfuse tracing to every run.

The FastAPI layer should import ``build_app`` once at startup and reuse the
returned graph, building a per-request config with ``make_config`` so each run
carries its own ``thread_id`` (and optional ``user_id`` for long-term memory).

Example::

    from src.graph.app_graph import build_app, make_config

    app_graph = build_app()
    config = make_config(thread_id="user-42:session-7", user_id="user-42")
    result = app_graph.invoke({"question": "Explain DPSP"}, config)
"""

from __future__ import annotations

from typing import Optional

from src.core.observability import langchain_callbacks
from src.graph.memory import get_checkpointer, get_store
from src.graph.supervisor import build_supervisor


def build_app():
    """Compile the full supervisor graph with persistent short- and long-term memory."""
    return build_supervisor(checkpointer=get_checkpointer(), store=get_store())


def make_config(
    thread_id: str,
    user_id: Optional[str] = None,
    trace_name: str = "upsc-chat",
    session_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Build an invoke config with Langfuse tracing callbacks attached.

    Attaching the Langfuse handler at the top level lets LangGraph propagate it
    to every nested LLM call, producing a single end-to-end trace per run. It is
    a no-op when Langfuse keys are absent, so this is always safe to call.

    Args:
        thread_id: Conversation/session key for the checkpointer (required).
        user_id: Optional user key for long-term memory and trace attribution.
        trace_name: Langfuse trace name shown in the dashboard.
        session_id: Optional Langfuse session id; defaults to thread_id.
        extra: Optional additional ``configurable`` values to merge in.
    """
    configurable: dict = {"thread_id": thread_id}
    if user_id is not None:
        configurable["user_id"] = user_id
    if extra:
        configurable.update(extra)

    # Langfuse trace attribution: a readable trace name plus user/session
    # grouping so the dashboard shows meaningful rows instead of bare model
    # names. The session defaults to the conversation thread when not given.
    metadata: dict = {"langfuse_tags": ["upsc-ai"]}
    if user_id is not None:
        metadata["langfuse_user_id"] = user_id
    metadata["langfuse_session_id"] = session_id or thread_id

    return {
        "configurable": configurable,
        "callbacks": langchain_callbacks(),
        "run_name": trace_name,
        "tags": ["upsc-ai"],
        "metadata": metadata,
    }


# ============================ Local smoke test =================================
if __name__ == "__main__":
    from src.core.observability import flush, langfuse_enabled
    from src.graph.memory import close_memory

    print("Langfuse enabled:", langfuse_enabled())
    try:
        app = build_app()
        config = make_config(thread_id="trace-test-1", user_id="test-user")
        result = app.invoke({"question": "Bhai polity me fundamental rights samjhao"}, config)
        print("route:", result.get("route"))
        answer = result.get("answer") or ""
        print("answer (first 500 chars):\n", answer[:500])
    finally:
        flush()  # push any pending traces to Langfuse before exit
        close_memory()  # release DB pools cleanly
