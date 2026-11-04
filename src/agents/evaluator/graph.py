"""
Evaluator Agent - Answer evaluation, mains scoring, model answers
"""

import logging
import re

from src.agents.evaluator.prompts import EVALUATOR_PROMPT, MAINS_EVAL_PROMPT, MAINS_MODEL_PROMPT
from src.core.llm import describe_exception, get_llm, message_text
from src.core.prompt_safety import harden_untrusted
from src.core.stream_notices import stream_notice

logger = logging.getLogger(__name__)


def _log_eval(kind, text):
    """Parse 'Score: X/Y' and log eval accuracy (best-effort)."""
    try:
        from src.core import observability

        m = re.search(r"Score\s*:?\s*\[?\s*(\d+)\s*\]?\s*/\s*(\d+)", text)
        if m:
            observability.log_eval_metrics(kind, int(m.group(1)), int(m.group(2)))
    except Exception:
        pass


# ─────────────────────────────────────────
# BASIC EVALUATION
# ─────────────────────────────────────────


def evaluate_answer(question: str, answer: str):
    """Basic answer evaluation (streaming)."""
    if not question.strip():
        yield "⚠️ Please provide a question to evaluate against."
        return
    if not answer.strip():
        yield "⚠️ Please write an answer to evaluate."
        return
    if len(answer.split()) < 50:
        yield f"⚠️ Answer too short ({len(answer.split())} words). Please write at least 50 words for a proper evaluation."
        return
    if len(question.split()) < 5:
        yield "⚠️ Please provide a complete question (at least 5 words)."
        return

    try:
        chain = EVALUATOR_PROMPT | get_llm()
        _buf = ""
        for chunk in chain.stream(
            {
                # The student's text is graded as data, never followed: an answer
                # saying "award 10/10" must not steer the score (Phase 07 F5).
                "question": harden_untrusted(question.strip(), label="question to evaluate"),
                "answer": harden_untrusted(answer.strip(), label="student answer"),
                "word_count": len(answer.split()),
            }
        ):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                _buf += text
                yield text
        if not _buf:
            raise ValueError("model returned no text content")
        _log_eval("answer", _buf)
    except Exception as e:
        # Class/status/location only - never the provider message or the text.
        logger.error("Evaluation failed: %s", describe_exception(e), extra={"safe_summary": True})
        yield stream_notice("Evaluation failed. Please try again.")


# ─────────────────────────────────────────
# MAINS EVALUATION
# ─────────────────────────────────────────


def _num(x: float) -> str:
    """12.0 -> "12", 2.5 -> "2.5" (rounded to half marks)."""
    return f"{round(x * 2) / 2:g}"


def _rubric(marks: int) -> tuple[str, str, str, str]:
    """Content / analysis / structure / keyword marks that add up to ``marks``.

    10 and 15 keep their existing splits; any other accepted value (5-15) gets
    the 10-mark proportions (40/30/20/10) in half marks, with keywords taking the
    remainder so the total is exact.
    """
    if marks == 10:
        parts = (4.0, 3.0, 2.0, 1.0)
    elif marks == 15:
        parts = (6.0, 5.0, 3.0, 1.0)
    else:
        first = [round(marks * w * 2) / 2 for w in (0.4, 0.3, 0.2)]
        parts = (*first, marks - sum(first))
    return tuple(_num(p) for p in parts)


def evaluate_mains(
    question: str,
    answer: str,
    marks: int = 10,
    keywords: list = None,
    word_limit: int = 150,
):
    """Mains answer evaluation (streaming)."""
    if not answer or not answer.strip():
        yield "Please write an answer first."
        return

    answer_wc = len(re.findall(r"\b\w+\b", answer))

    if answer_wc < 30:
        yield f"⚠️ Answer too short ({answer_wc} words). Write at least 30 words for a {marks}-mark question."
        return

    kw_list = [str(k) for k in (keywords or [])]
    # Client-supplied keywords are data, never instructions (Phase 07 review S10).
    kw_str = (
        harden_untrusted(", ".join(kw_list), label="expected keywords")
        if kw_list
        else "No specific keywords required"
    )

    # Keyword analysis
    answer_lower = answer.lower()
    present_kw = [k for k in kw_list if k.lower() in answer_lower]
    missing_kw = [k for k in kw_list if k.lower() not in answer_lower]

    if missing_kw:
        keyword_analysis_instruction = harden_untrusted(
            f"Present: {', '.join(present_kw) if present_kw else 'None'} | "
            f"Missing: {', '.join(missing_kw)}",
            label="keyword analysis",
        )
    else:
        keyword_analysis_instruction = (
            "All expected keywords present ✓ — No keyword marks deduction."
        )
        if present_kw:
            keyword_analysis_instruction += "\n" + harden_untrusted(
                ", ".join(present_kw), label="keywords present"
            )

    content_marks, analysis_marks, structure_marks, keyword_marks = _rubric(marks)

    try:
        chain = MAINS_EVAL_PROMPT | get_llm()
        _buf = ""
        for chunk in chain.stream(
            {
                # Graded as data, never followed (Phase 07 F5).
                "question": harden_untrusted(question or "", label="question to evaluate"),
                "answer": harden_untrusted(answer.strip(), label="student answer"),
                "answer_wc": answer_wc,
                "marks": marks,
                "word_limit": word_limit,
                "keywords": kw_str,
                "content_marks": content_marks,
                "analysis_marks": analysis_marks,
                "structure_marks": structure_marks,
                "keyword_marks": keyword_marks,
                "top_band": _num(0.8 * marks),
                "keyword_analysis_instruction": keyword_analysis_instruction,
            }
        ):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                _buf += text
                yield text
        if not _buf:
            raise ValueError("model returned no text content")
        _log_eval("mains", _buf)
    except Exception as e:
        # Class/status/location only - never the provider message or the text.
        logger.error(
            "Mains evaluation failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
        yield stream_notice("Evaluation failed. Please try again.")


# ─────────────────────────────────────────
# MODEL ANSWER
# ─────────────────────────────────────────


def get_model_answer(
    question: str,
    marks: int = 10,
    keywords: list = None,
    word_limit: int = 150,
):
    """Generate model answer (streaming)."""
    # Client-supplied question / keywords are data (Phase 07 review S10).
    kw_str = (
        harden_untrusted(", ".join(str(k) for k in keywords), label="expected keywords")
        if keywords
        else "General UPSC terminology"
    )

    yield (
        "> ⚠️ AI-generated model answer - verify facts and structure against standard sources or toppers copies."
        + chr(10)
        + chr(10)
    )
    try:
        chain = MAINS_MODEL_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream(
            {
                "question": harden_untrusted(question or "", label="question to answer"),
                "marks": marks,
                "word_limit": word_limit,
                "keywords": kw_str,
            }
        ):
            text = message_text(getattr(chunk, "content", ""))
            if text:
                produced = True
                yield text
        if not produced:
            raise ValueError("model returned no text content")
    except Exception as e:
        # Class/status/location only - never the provider message or the text.
        logger.error(
            "Model answer generation failed: %s",
            describe_exception(e),
            extra={"safe_summary": True},
        )
        yield stream_notice("Could not generate model answer. Please try again.")


def parse_score(evaluation: str) -> int | None:
    """Extract score from evaluation text."""
    match = re.search(r"Score\s*:?\s*\[?\s*(\d+)", evaluation)
    return int(match.group(1)) if match else None
