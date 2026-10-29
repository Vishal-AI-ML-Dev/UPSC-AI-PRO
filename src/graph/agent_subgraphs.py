"""Form-style agent subgraphs: Planner, Evaluator, Current Affairs.

Unlike the mentor and RAG agents (free-text question in, answer out), these
three are driven by structured form inputs in the original app. To fit them
into the shared graph contract without rewriting their business logic, each
subgraph reads its parameters from ``state['task_inputs']`` and wraps the
existing streaming functions, collecting the streamed chunks into a single
answer string.

Expected ``task_inputs`` shapes:
    planner          {goal, hours, optional, weak, attempt_number}
    evaluator        {question, answer, marks?, keywords?, word_limit?}
                     (marks present -> Mains rubric; otherwise basic evaluation)
    current_affairs  {mode: "daily"|"editorial"|"monthly", date|topic|month+year}
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from src.agents.current_affairs.graph import (
    _today_ist,
    get_daily_ca,
    get_editorial,
    get_monthly_summary,
    today_label,
)
from src.agents.evaluator.graph import evaluate_answer, evaluate_mains
from src.agents.planner.graph import generate_plan
from src.core.config import settings
from src.graph.state import AgentState

logger = logging.getLogger(__name__)


def _consume(generator) -> str:
    """Collect a streaming generator of text chunks into a single string."""
    parts = [chunk for chunk in generator if isinstance(chunk, str)]
    return "".join(parts)


# ---------------------------------------------------------------------------- #
# Inputs from a free-text chat message (Mentor chat sends only ``question``).
# Deterministic, no model call. A required input that is not in the message is
# asked for instead of guessed.
# ---------------------------------------------------------------------------- #
_HOURS_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?|ghante|ghanta|h)\b", re.I)
_ATTEMPT_RE = re.compile(
    r"\b(\d)\s*(?:st|nd|rd|th)?\s+attempt\b|\battempt\s*(?:no\.?|#)?\s*(\d)\b", re.I
)
_QA_RE = re.compile(
    r"\b(?:question|ques|q)\s*[:\-]\s*(?P<q>.+?)\s*\b(?:answer|ans)\s*[:\-]\s*(?P<a>.+)\Z",
    re.I | re.S,
)
_MONTHS = (
    "January February March April May June July August September October November December"
).split()
# Case-insensitive month names, except "May" (capitalised only: "may I ..." is not a month).
_MONTH_RE = re.compile(r"\b((?i:" + "|".join(m for m in _MONTHS if m != "May") + r")|May)\b")
_YEAR_RE = re.compile(r"\b(20\d\d)\b")
_EDITORIAL_TOPIC_RE = re.compile(r"\b(?:on|about|regarding|for)\s+(.+?)[\s.?!]*\Z", re.I | re.S)

PLANNER_NEEDS_HOURS = (
    "To build your study plan I need your daily study hours. Please send them with "
    'your target, e.g. "Plan for UPSC 2027, 6 hours a day, 1st attempt" - or use '
    "the Study Planner page."
)
EVALUATOR_NEEDS_FORMAT = (
    "To evaluate an answer here, send it in this format:\n\n"
    "Question: <the question>\nAnswer: <your answer>\n\n"
    "For Mains marks and word limits, use the Answer Evaluator page."
)


def planner_inputs_from_text(text: str) -> dict:
    """goal = the message; hours / attempt number when stated."""
    text = text or ""
    params = {"goal": text}
    hours = _HOURS_RE.search(text)
    if hours:
        params["hours"] = hours.group(1)
    attempt = _ATTEMPT_RE.search(text)
    if attempt:
        params["attempt_number"] = attempt.group(1) or attempt.group(2)
    return params


def evaluator_inputs_from_text(text: str) -> dict:
    """question / answer from "Question: ... Answer: ..." markers, else {}."""
    m = _QA_RE.search(text or "")
    if not m:
        return {}
    return {"question": m.group("q").strip(), "answer": m.group("a").strip()}


def current_affairs_inputs_from_text(text: str) -> dict:
    """editorial (topic) / monthly (month, year) / daily (today)."""
    text = text or ""
    lowered = text.lower()
    if "editorial" in lowered:
        topic = _EDITORIAL_TOPIC_RE.search(text)
        return {"mode": "editorial", "topic": (topic.group(1) if topic else text).strip()}
    month = _MONTH_RE.search(text)
    if "monthly" in lowered or month:
        today = _today_ist()
        year = _YEAR_RE.search(text)
        return {
            "mode": "monthly",
            "month": month.group(1).capitalize() if month else _MONTHS[today.month - 1],
            "year": year.group(1) if year else str(today.year),
        }
    return {"mode": "daily", "date": today_label()}


def _maybe_interrupt_for_params(params: dict, required: list[str], reason: str) -> dict:
    """Human-in-the-loop (#4): pause to collect missing params, then resume.

    When ``hitl_enabled`` is on and any ``required`` param is missing/blank, the
    graph is paused via LangGraph ``interrupt`` with a small payload describing
    what is needed. The resume-capable client answers with a dict of values,
    which is merged back into ``params``. FAIL-OPEN: if HITL is off, interrupts
    are unsupported (no checkpointer), or anything else goes wrong, the original
    params are returned unchanged so the node still runs.

    NOTE: ``interrupt`` raises a special control-flow signal (GraphInterrupt)
    that MUST propagate for pause/resume to work, so it is intentionally NOT
    swallowed here; only the import/guard path is defensive.
    """
    if not settings.hitl_enabled:
        return params
    missing = [f for f in required if not str(params.get(f, "")).strip()]
    if not missing:
        return params
    from langgraph.types import interrupt  # lazy: only needed when HITL is on

    provided = interrupt(
        {
            "type": "missing_params",
            "reason": reason,
            "missing": missing,
            "have": {k: v for k, v in params.items() if v not in (None, "")},
        }
    )
    if isinstance(provided, dict):
        return {**params, **provided}
    return params


# ============================ Planner =========================================
def build_planner_subgraph(checkpointer=None):
    """Subgraph wrapping the study-plan generator."""

    def planner_node(state: AgentState) -> dict:
        params = state.get("task_inputs") or planner_inputs_from_text(state.get("question", ""))
        # HITL: a study plan is useless without a goal + daily hours, so pause
        # to ask for them when missing (no-op unless hitl_enabled).
        params = _maybe_interrupt_for_params(
            params,
            ["goal", "hours"],
            "A study plan needs your target (goal) and daily study hours.",
        )
        # Never assume a number of hours the student did not give.
        if not str(params.get("hours", "")).strip():
            text = PLANNER_NEEDS_HOURS
            return {"answer": text, "messages": [AIMessage(content=text)]}
        text = _consume(
            generate_plan(
                goal=params.get("goal", state.get("question", "")),
                hours=str(params["hours"]),
                optional=params.get("optional", ""),
                weak=params.get("weak", ""),
                attempt_number=str(params.get("attempt_number", "1")),
            )
        )
        return {"answer": text, "messages": [AIMessage(content=text)]}

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", END)
    return graph.compile(checkpointer=checkpointer)


# ============================ Evaluator =======================================
def build_evaluator_subgraph(checkpointer=None):
    """Subgraph wrapping answer evaluation (basic + Mains rubric)."""

    def evaluator_node(state: AgentState) -> dict:
        params = state.get("task_inputs") or evaluator_inputs_from_text(state.get("question", ""))
        if not str(params.get("answer", "")).strip():
            # No answer text found: explain the format instead of claiming
            # the student wrote nothing.
            text = EVALUATOR_NEEDS_FORMAT
            return {"answer": text, "messages": [AIMessage(content=text)]}
        # HITL: Mains rubric scoring depends on the marks + word limit, so
        # confirm them before grading (no-op unless hitl_enabled). Only prompt
        # when this looks like a Mains request (marks hint present).
        if params.get("marks") or params.get("word_limit"):
            params = _maybe_interrupt_for_params(
                params,
                ["marks", "word_limit"],
                "Mains evaluation needs the marks (e.g. 10/15) and word limit.",
            )
        question = params.get("question", state.get("question", ""))
        answer = params.get("answer", "")
        if params.get("marks"):
            generator = evaluate_mains(
                question=question,
                answer=answer,
                marks=int(params.get("marks", 10)),
                keywords=params.get("keywords"),
                word_limit=int(params.get("word_limit", 150)),
            )
        else:
            generator = evaluate_answer(question=question, answer=answer)
        text = _consume(generator)
        return {"answer": text, "messages": [AIMessage(content=text)]}

    graph = StateGraph(AgentState)
    graph.add_node("evaluator", evaluator_node)
    graph.add_edge(START, "evaluator")
    graph.add_edge("evaluator", END)
    return graph.compile(checkpointer=checkpointer)


# ============================ Current Affairs =================================
def build_current_affairs_subgraph(checkpointer=None):
    """Subgraph wrapping daily CA, editorials, and the monthly digest."""

    def current_affairs_node(state: AgentState) -> dict:
        params = state.get("task_inputs") or current_affairs_inputs_from_text(
            state.get("question", "")
        )
        mode = (params.get("mode") or "daily").lower()
        if mode == "editorial":
            generator = get_editorial(params.get("topic", ""))
        elif mode == "monthly":
            generator = get_monthly_summary(params.get("month", ""), params.get("year", ""))
        else:
            generator = get_daily_ca(params.get("date") or today_label())
        text = _consume(generator)
        return {"answer": text, "messages": [AIMessage(content=text)]}

    graph = StateGraph(AgentState)
    graph.add_node("current_affairs", current_affairs_node)
    graph.add_edge(START, "current_affairs")
    graph.add_edge("current_affairs", END)
    return graph.compile(checkpointer=checkpointer)
