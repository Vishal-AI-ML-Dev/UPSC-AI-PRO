"""Self-critique reflection loop (reflect -> revise), reusable across graphs.

Pattern (Reflexion / self-refine): after an answer is generated, a critic LLM
scores the DRAFT against UPSC answer-quality criteria. If the answer is weak, a
bounded revise step rewrites it using the critic's concrete suggestions, then it
is re-critiqued. The loop is capped by ``max_revisions`` (cost + latency guard).

Why this is the right shape for this codebase:
  * FAIL-OPEN. Any critic/reviser exception keeps the CURRENT answer untouched -
    reflection can only improve an answer, never break a request. (On any error
    the critic returns a passing verdict, so the loop simply stops.)
  * ZERO new dependency. Uses the existing LLM chains (``src.core.llm``) +
    pydantic. No package to add.
  * OFFLINE-TESTABLE. The stop/again decision is a PURE function
    (``should_revise``) with no LLM/network, unit-tested in
    ``tests/test_reflection.py``.
  * CONFIG-GATED. ``reflection_enabled=false`` restores the exact previous flow.

Only stdlib + pydantic are imported at module top level so the offline verifier
can import this file directly; langchain / langgraph / settings are imported
lazily inside the functions that need them (same discipline as
``src.core.response_cache``).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_SCORE = 1
MAX_SCORE = 10


class Critique(BaseModel):
    """Structured self-critique of a draft answer."""

    passes: bool = Field(
        ...,
        description="True only if the answer is already good enough to send as-is.",
    )
    score: int = Field(
        ...,
        ge=MIN_SCORE,
        le=MAX_SCORE,
        description="Overall quality 1-10 from a UPSC aspirant's perspective.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete problems: missing dimensions, factual errors, irrelevant "
            "content, or poor structure. Empty when the answer is solid."
        ),
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Specific, actionable fixes the reviser should apply.",
    )


_CRITIC_SYS = (
    "You are a demanding UPSC answer reviewer. Judge the DRAFT answer to the "
    "QUESTION on: (1) does it directly address what is asked, (2) factual "
    "accuracy, (3) syllabus relevance and coverage of the key dimensions, "
    "(4) clarity and structure. Give an overall score from 1-10. Set "
    "passes=true only when the answer is genuinely solid and needs no material "
    "improvement (score >= {min_score}). List ONLY concrete, actionable issues "
    "and fixes - never invent facts, and do not demand more length when the "
    "answer is already complete and correct."
)

_REVISE_SYS = (
    "You are revising a UPSC answer using a reviewer's feedback. Produce an "
    "improved answer to the QUESTION that fixes every listed issue while keeping "
    "everything that was already correct. Do NOT invent facts, dates, names, or "
    "article numbers; if the evidence is limited, stay careful and general. "
    "Return ONLY the improved answer text, with no meta commentary about what "
    "you changed."
)


# --------------------------------------------------------------------------- #
# Pure decision (offline-testable; no LLM, no I/O)
# --------------------------------------------------------------------------- #
def should_revise(*, passes, score, min_score, revisions_done, max_revisions):
    """Return True when the reflection loop should revise once more.

    Revise only when we still have budget AND the critic judged the answer weak
    (it did not pass, or its score is below the bar). A missing/garbled score is
    treated as "no score" -> fall back to the ``passes`` flag alone. This keeps
    the loop safe even if a model returns an odd structured payload.
    """
    try:
        if int(revisions_done) >= int(max_revisions):
            return False
    except (TypeError, ValueError):
        return False
    try:
        score_val = int(score)
    except (TypeError, ValueError):
        score_val = None
    if passes and (score_val is None or score_val >= int(min_score)):
        return False
    return True


def _feedback_block(critique) -> str:
    """Render a critic's issues + suggestions into a compact instruction block.

    Pure string formatting so it is unit-testable without an LLM. Accepts a
    ``Critique`` (or any object exposing ``issues``/``suggestions``).
    """
    issues = list(getattr(critique, "issues", None) or [])
    suggestions = list(getattr(critique, "suggestions", None) or [])
    lines: list[str] = []
    if issues:
        lines.append("Issues to fix:")
        lines += [f"- {i}" for i in issues]
    if suggestions:
        lines.append("How to improve:")
        lines += [f"- {s}" for s in suggestions]
    return (
        "\n".join(lines)
        if lines
        else ("Improve overall quality, accuracy, coverage, and structure.")
    )


# --------------------------------------------------------------------------- #
# LLM-backed critic + reviser (lazy heavy imports; fail-open)
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


def critique_answer(question, answer, *, evidence="", min_score=7, tier="lite"):
    """Critique a draft answer. FAIL-OPEN -> a passing critique on any error.

    The critic runs on the cheap ``lite`` tier by default (it is a short
    judgement, not the main answer), preserving flash quota for generation.
    """
    if not (answer or "").strip():
        return Critique(passes=True, score=MAX_SCORE, issues=[], suggestions=[])
    try:
        critic = _structured_llm(Critique, tier)
        human = f"QUESTION:\n{question}\n\n"
        if evidence and evidence.strip():
            human += (
                "EVIDENCE (ground truth; the answer must stay consistent with "
                f"this):\n{evidence[:6000]}\n\n"
            )
        human += f"DRAFT ANSWER:\n{(answer or '')[:6000]}"
        return critic.invoke(
            [("system", _CRITIC_SYS.format(min_score=min_score)), ("human", human)]
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("reflection: critique failed (%s); keeping draft answer", exc)
        return Critique(passes=True, score=MAX_SCORE, issues=[], suggestions=[])


def revise_answer(question, answer, critique, *, evidence="", tier="strong"):
    """Rewrite an answer using the critique. FAIL-OPEN -> original answer.

    Revision uses the ``strong`` tier: fixing a weak answer is exactly the
    quality-critical work the strong model exists for.
    """
    try:
        from src.core.llm import get_llm_for_tier

        llm = get_llm_for_tier(tier)
        human = f"QUESTION:\n{question}\n\n"
        if evidence and evidence.strip():
            human += (
                "EVIDENCE (stay consistent; do not contradict it or go beyond "
                f"it):\n{evidence[:6000]}\n\n"
            )
        human += f"CURRENT ANSWER:\n{answer}\n\nREVIEWER FEEDBACK:\n{_feedback_block(critique)}"
        resp = llm.invoke([("system", _REVISE_SYS), ("human", human)])
        revised = resp.content if hasattr(resp, "content") else str(resp)
        revised = (revised or "").strip()
        return revised or answer
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("reflection: revise failed (%s); keeping current answer", exc)
        return answer


# --------------------------------------------------------------------------- #
# LangGraph node factory (bounded reflect->revise inside one node)
# --------------------------------------------------------------------------- #
def make_reflect_node(
    *,
    evidence_getter: Optional[Callable] = None,
    min_score: int = 7,
    max_revisions: int = 1,
    critic_tier: str = "lite",
    revise_tier: str = "strong",
):
    """Build a graph node that critiques ``state['answer']`` and revises it.

    The loop is kept INSIDE one node (rather than a graph cycle) so it is easy
    to reason about and cannot accidentally spin: it critiques, and while the
    verdict is weak and budget remains, it revises and re-critiques. The node
    reads ``state['question']`` + ``state['answer']`` (and optional evidence via
    ``evidence_getter(state)``) and writes back an improved ``answer`` plus
    telemetry (``critique_score``, ``revision_count``).
    """

    def reflect_node(state):
        question = state.get("question", "")
        answer = state.get("answer", "")
        if not (answer or "").strip():
            return {}
        evidence = ""
        if evidence_getter is not None:
            try:
                evidence = evidence_getter(state) or ""
            except Exception:  # pragma: no cover - defensive
                evidence = ""
        revisions = 0
        last_score = None
        while True:
            critique = critique_answer(
                question,
                answer,
                evidence=evidence,
                min_score=min_score,
                tier=critic_tier,
            )
            last_score = getattr(critique, "score", None)
            if not should_revise(
                passes=getattr(critique, "passes", True),
                score=last_score,
                min_score=min_score,
                revisions_done=revisions,
                max_revisions=max_revisions,
            ):
                break
            answer = revise_answer(
                question,
                answer,
                critique,
                evidence=evidence,
                tier=revise_tier,
            )
            revisions += 1
        return {
            "answer": answer,
            "revision_count": revisions,
            "critique_score": last_score,
        }

    return reflect_node
