"""Mentor chat specialists that wrap form features: Planner, Evaluator, Current Affairs.

Used only by the Mentor chat supervisor (``src/graph/supervisor.py``). The
Planner, Answer Evaluator and Current Affairs PAGES do not come through here:
their routes call the same feature functions directly with form inputs.

Mentor chat sends only the free-text message, so each node parses its inputs
from ``state['question']`` with a deterministic parser (no model call) and wraps
the existing streaming feature function, collecting the chunks into one answer:
    planner          ``planner_inputs_from_text``  -> goal, hours?, attempt_number?
    evaluator        ``evaluator_inputs_from_text`` -> question, answer
                     ("Question: ... Answer: ..." markers; basic evaluation)
    current_affairs  ``current_affairs_inputs_from_text`` -> daily | editorial | monthly
A required input that is missing is asked for in the reply instead of guessed.
Every reply stores the student's question (once) followed by the answer
(``_reply``), so the mentor's bounded history sees question -> answer pairs.

HITL (``HITL_ENABLED``, off by default) is NOT supported by Mentor chat:
``/agent/chat/stream`` has no resume path for a LangGraph ``interrupt``. Keep it
off in deployments that serve Mentor chat (Phase 05, ADR-014).
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from src.agents.current_affairs.graph import (
    _today_ist,
    get_daily_ca,
    get_editorial,
    get_monthly_summary,
    today_label,
)
from src.agents.evaluator.graph import evaluate_answer
from src.agents.planner.graph import generate_plan
from src.core.config import settings
from src.graph.state import AgentState
from src.graph.tools import _should_seed_question

logger = logging.getLogger(__name__)


def _consume(generator) -> str:
    """Collect a streaming generator of text chunks into a single string."""
    parts = [chunk for chunk in generator if isinstance(chunk, str)]
    return "".join(parts)


def _reply(state: AgentState, text: str) -> dict:
    """State update for a specialist answer: the student's question, then the answer.

    Recording the question keeps the thread a sequence of question -> answer
    turns, so the mentor's bounded history (``tools.bound_history``) pairs every
    earlier answer with its own question. The question is added only when it is
    not already the latest unanswered human message (same rule the mentor agent
    uses), so each question is stored exactly once.
    """
    question = state.get("question", "")
    messages: list = []
    if _should_seed_question(list(state.get("messages") or []), question):
        messages.append(HumanMessage(content=question))
    messages.append(AIMessage(content=text))
    return {"answer": text, "messages": messages}


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
        params = planner_inputs_from_text(state.get("question", ""))
        # HITL: a study plan is useless without a goal + daily hours, so pause
        # to ask for them when missing (no-op unless hitl_enabled).
        params = _maybe_interrupt_for_params(
            params,
            ["goal", "hours"],
            "A study plan needs your target (goal) and daily study hours.",
        )
        # Never assume a number of hours the student did not give.
        if not str(params.get("hours", "")).strip():
            return _reply(state, PLANNER_NEEDS_HOURS)
        text = _consume(
            generate_plan(
                goal=params.get("goal", state.get("question", "")),
                hours=str(params["hours"]),
                optional=params.get("optional", ""),
                weak=params.get("weak", ""),
                attempt_number=str(params.get("attempt_number", "1")),
            )
        )
        return _reply(state, text)

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", END)
    return graph.compile(checkpointer=checkpointer)


# ============================ Evaluator =======================================
def build_evaluator_subgraph(checkpointer=None):
    """Subgraph wrapping basic answer evaluation for Mentor chat.

    Mentor chat only provides "Question: ... Answer: ..." text, so there are no
    marks / word limit and the Mains rubric (``evaluate_mains``) is never used
    here; it stays on the Answer Evaluator page (``src/api/routes/evaluator.py``).
    """

    def evaluator_node(state: AgentState) -> dict:
        params = evaluator_inputs_from_text(state.get("question", ""))
        if not str(params.get("answer", "")).strip():
            # No answer text found: explain the format instead of claiming
            # the student wrote nothing.
            return _reply(state, EVALUATOR_NEEDS_FORMAT)
        text = _consume(evaluate_answer(question=params["question"], answer=params["answer"]))
        return _reply(state, text)

    graph = StateGraph(AgentState)
    graph.add_node("evaluator", evaluator_node)
    graph.add_edge(START, "evaluator")
    graph.add_edge("evaluator", END)
    return graph.compile(checkpointer=checkpointer)


# ============================ Current Affairs =================================
def build_current_affairs_subgraph(checkpointer=None):
    """Subgraph wrapping daily CA, editorials, and the monthly digest."""

    def current_affairs_node(state: AgentState) -> dict:
        params = current_affairs_inputs_from_text(state.get("question", ""))
        mode = (params.get("mode") or "daily").lower()
        if mode == "editorial":
            generator = get_editorial(params.get("topic", ""))
        elif mode == "monthly":
            generator = get_monthly_summary(params.get("month", ""), params.get("year", ""))
        else:
            generator = get_daily_ca(params.get("date") or today_label())
        text = _consume(generator)
        return _reply(state, text)

    graph = StateGraph(AgentState)
    graph.add_node("current_affairs", current_affairs_node)
    graph.add_edge(START, "current_affairs")
    graph.add_edge("current_affairs", END)
    return graph.compile(checkpointer=checkpointer)
