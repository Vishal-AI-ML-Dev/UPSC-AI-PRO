"""Shared state object for the Mentor chat graph.

This ``TypedDict`` is the state contract shared by the Mentor chat supervisor
(``src/graph/supervisor.py``) and its specialists (mentor tool agent, planner,
evaluator, current affairs, optional plan-execute), and by the CRAG subgraph used
by the eval harness. NCERT / PYQ / Lecture / Upload do not use it.
``total=False`` marks all keys optional, so each node only populates the
fields it is responsible for and leaves the rest untouched.
"""

from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # --- Conversation ---
    # ``add_messages`` is a reducer that appends new messages instead of
    # overwriting the existing list.
    messages: Annotated[list[BaseMessage], add_messages]
    question: str  # Current user query (raw input).
    student_context: Optional[dict]  # Profile: name, stage, weak_areas, etc.
    chat_history: Optional[list]  # [{"role": "user"|"assistant", "content": str}]

    # --- Routing (supervisor + per-agent routers) ---
    route: str  # Router OUTPUT for the current message; never read as an input (Phase 05).
    intent: str  # "casual" | "vague" | "emotional" | "academic"
    needs_web_search: bool  # Whether live web context is required.

    # --- Retrieval (knowledge base / RAG) ---
    persist_key: str  # Qdrant collection key for the CRAG subgraph (eval harness only).
    kb_context: str  # Retrieved, grounded context text.
    citations: list[str]  # Source labels for user-facing attribution.
    grounded: bool  # True when retrieval cleared the relevance threshold.
    rag_relevant: bool  # CRAG grade: context actually answers the question.
    search_results: str  # Live web search context (fallback).
    grounding_warning: str  # Warning when retrieval grading/checking fails.

    # --- Output ---
    answer: str  # Final generated answer.
    grounding_confidence: str  # high | medium | low post-generation grounding confidence.
    unsupported_claims: list[str]  # Claims not supported by retrieved/web evidence.

    # --- Reflection / self-critique (#7) ---
    critique_score: int  # Last self-critique score (1-10).
    revision_count: int  # How many reflect->revise passes ran.

    # --- Plan-and-execute (#7) ---
    plan_steps: list[str]  # Decomposed sub-questions (planner output).
    step_results: list[dict]  # Per-step {step, result} from the executor.
