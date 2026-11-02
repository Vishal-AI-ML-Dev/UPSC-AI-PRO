"""Mentor chat supervisor: one LLM routing call per message, one specialist.

Serves ``POST /api/v1/agent/chat/stream`` only; the other feature pages call
their feature functions directly. For EVERY message the router (one structured
fast-model call) picks exactly one specialist, which then answers. There is no
parallel or multi-step orchestration between specialists. All subgraphs share
``AgentState`` and are nested as nodes; only the supervisor is compiled with the
checkpointer/store, so conversation history persists per thread.

Routes:
    mentor           General doubts, concept explanations, advice, motivation, chat
                     (ReAct tool agent with web_search / knowledge_base_search).
    planner          Create / revise a study plan or timetable.
    evaluator        Check, grade, or evaluate a written answer.
    current_affairs  Daily / monthly current affairs, news, editorials.
    plan_execute     Optional upgrade of complex mentor messages
                     (``PLAN_EXECUTE_ENABLED``, off by default).

``state['route']`` is an OUTPUT of the router for the current message. It is
saved in the checkpoint with the rest of the state, so it is never read as an
input: a saved route must not decide the next message (Phase 05, ADR-014).
Planner / evaluator / current_affairs parse their inputs from the message text
(``src/graph/agent_subgraphs.py``). Document questions are answered by the
mentor's knowledge-base tool; the CRAG subgraph (``rag_graph.py``) is used only
by the eval harness (``src/eval/llm_eval.py``).
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from src.core.llm import LLMTimeoutError, describe_exception, get_fast_llm
from src.graph.agent_subgraphs import (
    build_current_affairs_subgraph,
    build_evaluator_subgraph,
    build_planner_subgraph,
)
from src.graph.mentor_graph import _mentor_context, build_mentor_graph
from src.graph.plan_execute import build_plan_execute_graph, is_complex
from src.graph.state import AgentState

logger = logging.getLogger(__name__)

_ROUTES = ("mentor", "planner", "evaluator", "current_affairs")


class RouteDecision(BaseModel):
    """Structured supervisor decision."""

    route: Literal["mentor", "planner", "evaluator", "current_affairs"] = Field(
        ...,
        description=(
            "mentor = doubts/advice/chat; planner = build a study plan; "
            "evaluator = grade a written answer; current_affairs = news/editorials/"
            "monthly digest."
        ),
    )


_SUP_SYS = (
    "You are the router for a UPSC preparation assistant. Read the student's "
    "message and choose exactly one specialist:\n"
    "- mentor: general doubts, concept explanations, study advice, motivation, casual chat.\n"
    "- planner: requests to create or revise a study plan / timetable / schedule.\n"
    "- evaluator: requests to check, grade, or evaluate a written answer.\n"
    "- current_affairs: requests for daily/monthly current affairs, news, or editorials.\n"
    "Default to mentor when unsure."
)


def _structured_llm(schema):
    """Structured-output runnable that tolerates fallback wrappers."""
    base = get_fast_llm()
    if hasattr(base, "with_structured_output"):
        return base.with_structured_output(schema)
    primary = base.runnable.with_structured_output(schema)
    fallbacks = [r.with_structured_output(schema) for r in base.fallbacks]
    return primary.with_fallbacks(fallbacks)


def build_supervisor(checkpointer=None, store=None):
    """Build and compile the top-level supervisor graph.

    Args:
        checkpointer: Short-term per-thread memory (see ``graph.memory``).
        store: Long-term cross-thread memory (see ``graph.memory``).
    """
    # Nested subgraphs share the parent state; they must NOT carry their own
    # checkpointer when nested under a checkpointed parent.
    mentor = build_mentor_graph()
    planner = build_planner_subgraph()
    evaluator = build_evaluator_subgraph()
    current_affairs = build_current_affairs_subgraph()

    # Plan-and-execute (#7): optional upgrade for complex, multi-part mentor
    # queries. Off by default (extra LLM calls); when enabled, complex mentor
    # turns are routed to a decompose -> execute -> synthesize subgraph.
    plan_execute_on = False
    plan_execute_min_words = 30
    try:
        from src.core.config import settings

        plan_execute_on = bool(settings.plan_execute_enabled)
        plan_execute_min_words = int(settings.plan_execute_min_words)
    except Exception:  # pragma: no cover - settings present in app
        plan_execute_on = False
    plan_execute = (
        build_plan_execute_graph(extra_context=_mentor_context) if plan_execute_on else None
    )

    def supervisor_node(state: AgentState) -> dict:
        # Route every message afresh. ``state['route']`` holds the PREVIOUS
        # message's route (restored from the checkpoint), so it is deliberately
        # not read here: reusing it sent every later message of a conversation
        # to the first message's specialist (Phase 05).
        try:
            decision = _structured_llm(RouteDecision).invoke(
                [("system", _SUP_SYS), ("human", state.get("question", ""))]
            )
            route = decision.route
        except LLMTimeoutError:
            # A timed-out routing call ends the request instead of starting
            # another (agent) model chain with its own timeout.
            raise
        except Exception as exc:  # pragma: no cover - defensive
            # Class/status only - never the provider message (ADR-008).
            logger.warning(
                "Supervisor routing failed (%s); defaulting to mentor", describe_exception(exc)
            )
            route = "mentor"
        # Upgrade complex mentor turns to plan-and-execute when enabled.
        if (
            route == "mentor"
            and plan_execute_on
            and is_complex(state.get("question", ""), min_words=plan_execute_min_words)
        ):
            route = "plan_execute"
        return {"route": route}

    def route_selector(state: AgentState) -> str:
        route = state.get("route", "mentor")
        valid = _ROUTES + (("plan_execute",) if plan_execute_on else ())
        return route if route in valid else "mentor"

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("mentor", mentor)
    graph.add_node("planner", planner)
    graph.add_node("evaluator", evaluator)
    graph.add_node("current_affairs", current_affairs)
    if plan_execute is not None:
        graph.add_node("plan_execute", plan_execute)

    routes = _ROUTES + (("plan_execute",) if plan_execute is not None else ())

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_selector,
        {name: name for name in routes},
    )
    for name in routes:
        graph.add_edge(name, END)
    return graph.compile(checkpointer=checkpointer, store=store)


# ============================ Local smoke test =================================
if __name__ == "__main__":
    app = build_supervisor(checkpointer=InMemorySaver())

    samples = [
        "Bhai polity me fundamental rights samjhao",  # -> mentor
        "Mera 2027 ka plan bana do, 8 ghante padh sakta hu",  # -> planner
        "Meri answer check karo aur marks do",  # -> evaluator
        "Aaj ke current affairs do",  # -> current_affairs
    ]

    # Routing-only preview (cheap: just the classifier, no heavy execution).
    print("=== ROUTING PREVIEW ===")
    router = _structured_llm(RouteDecision)
    for q in samples:
        decision = router.invoke([("system", _SUP_SYS), ("human", q)])
        print(f"{decision.route:16s} <- {q}")

    # One full end-to-end run through the mentor path.
    print("\n=== END-TO-END (mentor) ===")
    config = {"configurable": {"thread_id": "sup-test-1"}}
    result = app.invoke({"question": samples[0]}, config)
    print("route:", result.get("route"))
    print("answer:\n", result.get("answer"))
