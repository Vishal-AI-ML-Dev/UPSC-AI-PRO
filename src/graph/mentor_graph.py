"""Mentor subgraph - thin adapter over the canonical tool-calling agent.

Historically this module hand-rolled a ``router -> retrieve_kb -> web_search ->
generate`` graph that (a) duplicated the mentor logic in
``src/agents/mentor/graph.py`` and (b) decided *for* the model whether to search
(a single ``needs_web_search`` boolean firing one hard-coded search).

As of the P1 dual-stack merge the canonical mentor brain is the real
tool-calling agent in ``src/graph/tools.py`` (``build_tool_agent``): the model
itself decides whether to call ``web_search`` or ``knowledge_base_search``, with
what arguments, and how many times. This module now simply wires that agent with
the mentor system prompt plus per-request student-profile context, preserving
the ``build_mentor_graph`` entry point that ``supervisor.py`` depends on.
"""

from __future__ import annotations

from src.agents.mentor.graph import _build_student_context
from src.graph.tools import DEFAULT_TOOL_SYSTEM, build_tool_agent


def _mentor_context(state) -> str:
    """Per-request context block built from the student profile, if present.

    Folded into the tool agent's system prompt so mentor answers stay
    personalised - the generic tool agent is otherwise profile-agnostic.
    """
    profile = state.get("student_context") if hasattr(state, "get") else None
    if not profile:
        return ""
    return (
        "Student profile (personalise your guidance; do not repeat verbatim):\n"
        + _build_student_context(profile)
    )


def build_mentor_graph(checkpointer=None):
    """Compile the canonical mentor brain (the shared tool-calling agent).

    Args:
        checkpointer: Optional LangGraph checkpointer. Leave ``None`` when nested
            under a checkpointed parent (e.g. the supervisor), which owns
            conversation memory through the shared ``AgentState``.
    """
    return build_tool_agent(
        checkpointer=checkpointer,
        system_prompt=DEFAULT_TOOL_SYSTEM,
        extra_context=_mentor_context,
    )


# ============================ Local smoke test ================================
if __name__ == "__main__":  # pragma: no cover
    from langgraph.checkpoint.memory import InMemorySaver

    app = build_mentor_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "mentor-1"}}
    for q in [
        "Motivate me, I'm feeling low today.",
        "When is the UPSC Prelims 2026 exam scheduled?",
    ]:
        result = app.invoke({"question": q}, cfg)
        print(f"\nQ: {q}\nA: {result.get('answer')}\n")
