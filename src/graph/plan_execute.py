"""Plan-and-Execute agent (planner -> executor -> synthesizer) for complex queries.

Answering a genuinely complex, multi-part question in one shot often drops
sub-parts. This subgraph makes the reasoning explicit:

    plan        Decompose the question into 2-N focused, ordered sub-questions.
    execute     Answer each sub-question with the shared tool-calling agent
                (same web/KB grounding tools the mentor uses), collecting a
                step-by-step working transcript.
    synthesize  Compose ONE coherent, exam-ready final answer from the steps.

Design constraints (consistent with the rest of the codebase):
  * FAIL-OPEN. Planning failure -> a single-step plan; a failed step -> empty
    result; synthesis failure -> fall back to one direct tool-agent answer. The
    request always produces an answer.
  * CONFIG-GATED. ``plan_execute_enabled`` defaults to False because this issues
    several extra LLM calls (latency + free-tier quota); callers opt in.
  * OFFLINE-TESTABLE. The decomposition/formatting helpers (``is_complex``,
    ``clamp_steps``, ``format_worklog``) are PURE and unit-tested in
    ``tests/test_plan_execute.py``.

Only stdlib + pydantic at module top level (so the offline verifier can import
this file directly); langchain / langgraph / settings / tools are imported
lazily inside the builder.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_MIN_WORDS = 30
DEFAULT_MAX_STEPS = 5
DEFAULT_MAX_CONCURRENCY = 3  # bounded parallel sub-step fan-out (cost guard)

# Multi-part cues that signal a question is worth decomposing even when short.
_MULTIPART_CUES = (
    "compare",
    "contrast",
    "difference between",
    "differentiate",
    "as well as",
    "along with",
    "and also",
    "advantages and disadvantages",
    "pros and cons",
    "merits and demerits",
    "causes and consequences",
)


class Plan(BaseModel):
    """Ordered decomposition of a complex question into sub-questions."""

    steps: list[str] = Field(
        default_factory=list,
        description=(
            "2-5 focused, self-contained sub-questions that together fully "
            "answer the original question, in the order they should be tackled."
        ),
    )


_PLAN_SYS = (
    "You are a UPSC study strategist. Break the student's question into a short, "
    "ordered list of at most {max_steps} focused sub-questions that together "
    "cover everything asked. Each sub-question must be self-contained and "
    "answerable on its own. Do NOT answer them - only produce the plan. If the "
    "question is already simple and atomic, return it unchanged as a single "
    "step."
)

_SYNTH_SYS = (
    "You are a UPSC mentor composing a final answer. Using the QUESTION and the "
    "WORKING NOTES gathered for each sub-step, write one coherent, well-"
    "structured, exam-ready answer. Integrate the notes - do not list them as "
    "separate Q&A. Do NOT invent facts beyond the notes; if a sub-step returned "
    "nothing useful, answer that part carefully from general knowledge and say "
    "so. Keep it in the student's language."
)


# --------------------------------------------------------------------------- #
# Pure helpers (offline-testable; no LLM, no I/O)
# --------------------------------------------------------------------------- #
def is_complex(question, *, min_words: int = DEFAULT_MIN_WORDS) -> bool:
    """Decide whether a question is worth the plan-execute overhead (pure).

    Complex when it is long (>= ``min_words`` words), asks multiple questions
    (two or more '?'), or contains an explicit multi-part cue such as
    "compare"/"advantages and disadvantages". Everything else is treated as a
    simple, single-shot query.
    """
    q = (question or "").strip()
    if not q:
        return False
    if len(q.split()) >= int(min_words):
        return True
    lowered = q.lower()
    if lowered.count("?") >= 2:
        return True
    return any(cue in lowered for cue in _MULTIPART_CUES)


def clamp_steps(steps, max_steps: int = DEFAULT_MAX_STEPS) -> list[str]:
    """Clean a raw plan: strip, drop empties, de-duplicate, cap at ``max_steps``.

    Pure so the executor never runs on a garbled/oversized plan.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for step in steps or []:
        if step is None:
            continue
        text = str(step).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= int(max_steps):
            break
    return cleaned


def format_worklog(steps, results) -> str:
    """Render executed steps + their results into a synthesis-ready transcript.

    ``results`` is a list of ``{"step", "result"}`` dicts (executor output). Pure
    string formatting -> unit-testable without any model.
    """
    by_step = {}
    for item in results or []:
        if isinstance(item, dict):
            by_step[str(item.get("step", ""))] = str(item.get("result", "") or "")
    blocks: list[str] = []
    for idx, step in enumerate(steps or [], start=1):
        step_text = str(step)
        answer = by_step.get(step_text, "").strip()
        blocks.append(f"Step {idx}: {step_text}\n{answer or '(no result)'}")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# LLM-backed planner (lazy; fail-open)
# --------------------------------------------------------------------------- #
def _structured_llm(schema, tier: str):
    """Structured-output runnable that tolerates the fallback wrapper."""
    from src.core.llm import get_llm_for_tier

    base = get_llm_for_tier(tier)
    if hasattr(base, "with_structured_output"):
        return base.with_structured_output(schema)
    primary = base.runnable.with_structured_output(schema)
    fallbacks = [r.with_structured_output(schema) for r in base.fallbacks]
    return primary.with_fallbacks(fallbacks)


def make_plan(question, *, max_steps: int = DEFAULT_MAX_STEPS, tier: str = "strong"):
    """Decompose a question into sub-questions. FAIL-OPEN -> single-step plan."""
    q = (question or "").strip()
    if not q:
        return []
    try:
        planner = _structured_llm(Plan, tier)
        plan = planner.invoke([("system", _PLAN_SYS.format(max_steps=max_steps)), ("human", q)])
        steps = clamp_steps(getattr(plan, "steps", None) or [], max_steps)
        return steps or [q]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("plan-execute: planning failed (%s); using single-step plan", exc)
        return [q]


# --------------------------------------------------------------------------- #
# Compiled subgraph: plan -> execute -> synthesize
# --------------------------------------------------------------------------- #
def build_plan_execute_graph(
    *,
    checkpointer=None,
    system_prompt: Optional[str] = None,
    extra_context: Optional[Callable] = None,
    max_steps: Optional[int] = None,
    planner_tier: str = "strong",
    synth_tier: str = "strong",
):
    """Compile the plan-and-execute subgraph over the shared ``AgentState``.

    The executor is the canonical tool-calling agent (``build_tool_agent``), so
    each sub-step gets the same web/KB grounding the mentor has. ``extra_context``
    is forwarded to it (e.g. the student-profile block).
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, StateGraph

    from src.core.llm import get_llm_for_tier
    from src.graph.state import AgentState
    from src.graph.tools import DEFAULT_TOOL_SYSTEM, build_tool_agent

    if max_steps is None:
        try:
            from src.core.config import settings

            max_steps = int(settings.plan_execute_max_steps)
        except Exception:  # pragma: no cover - settings present in app
            max_steps = DEFAULT_MAX_STEPS

    sys_prompt = system_prompt or DEFAULT_TOOL_SYSTEM
    # Compile the executor once; invoke it per sub-step with a fresh state so
    # steps stay independent (no cross-step memory bleed).
    executor = build_tool_agent(system_prompt=sys_prompt, extra_context=extra_context)

    def plan_node(state):
        steps = make_plan(state.get("question", ""), max_steps=max_steps, tier=planner_tier)
        return {"plan_steps": steps}

    # #5 Parallel execution. Sub-steps are independent (each runs on a fresh
    # executor state), so they can run concurrently. Read the settings once at
    # build time; FAIL-OPEN -> sequential when settings are unavailable.
    parallel, max_concurrency = False, DEFAULT_MAX_CONCURRENCY
    try:
        from src.core.config import settings

        parallel = bool(settings.plan_execute_parallel)
        max_concurrency = max(1, int(settings.plan_execute_max_concurrency))
    except Exception:  # pragma: no cover - settings present in app
        pass

    def _run_step(step):
        """Answer one sub-step with the shared tool agent. Fail-open -> ''."""
        try:
            out = executor.invoke({"question": step})
            answer = out.get("answer", "") if isinstance(out, dict) else ""
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("plan-execute: step failed (%s)", exc)
            answer = ""
        return {"step": step, "result": answer or ""}

    def execute_node(state):
        question = state.get("question", "")
        steps = state.get("plan_steps") or [question]
        # Concurrent fan-out for multi-step plans (order preserved by map);
        # single-step plans skip the pool overhead. Sequential otherwise.
        if parallel and len(steps) > 1:
            from concurrent.futures import ThreadPoolExecutor

            workers = min(max_concurrency, len(steps))
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(_run_step, steps))
                return {"step_results": results}
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("plan-execute: parallel run failed (%s); retrying sequentially", exc)
        results = [_run_step(step) for step in steps]
        return {"step_results": results}

    def synthesize_node(state):
        question = state.get("question", "")
        steps = state.get("plan_steps") or []
        results = state.get("step_results") or []
        worklog = format_worklog(steps, results)
        answer = ""
        try:
            llm = get_llm_for_tier(synth_tier)
            resp = llm.invoke(
                [
                    ("system", _SYNTH_SYS),
                    (
                        "human",
                        f"QUESTION:\n{question}\n\n"
                        f"WORKING NOTES (from sub-steps):\n{worklog}\n\n"
                        "Write the final, coherent, exam-ready answer.",
                    ),
                ]
            )
            answer = resp.content if hasattr(resp, "content") else str(resp)
            answer = (answer or "").strip()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("plan-execute: synthesis failed (%s); falling back", exc)
            answer = ""
        if not answer:
            # Fail-open: one direct tool-agent answer to the whole question.
            try:
                out = executor.invoke({"question": question})
                answer = (out.get("answer", "") if isinstance(out, dict) else "") or ""
            except Exception:  # pragma: no cover - defensive
                answer = ""
        return {"answer": answer, "messages": [AIMessage(content=answer)]}

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile(checkpointer=checkpointer)
