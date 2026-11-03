"""Deterministic parsers: evaluator markdown -> validated Pydantic models.

Pure module (stdlib `re` only + Pydantic schemas). No LLM / network / heavy deps
-> offline CI-testable (same philosophy as gates.py / grounding.py / model_router).

The evaluator agents STREAM markdown to the user for a good UX; we do NOT change
that. After the stream completes, the collected text is parsed here into a
structured object for reliable scoring/logging + a machine-readable API field.
This replaces the previous single fragile `Score: X/Y` regex.
"""

import re

from src.schemas import AnswerEvaluation, MainsEvaluation

# Tolerant of `## Score: 6/10`, `## 📊 Score: [7]/10`, `Score: 8.5 / 15`.
_SCORE_RE = re.compile(r"score\s*:?\s*\[?\s*(\d+(?:\.\d+)?)\s*\]?\s*/\s*(\d+)", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_VERDICT_RE = re.compile(r"\*\*\s*verdict\s*:?\s*\*\*\s*(.+)", re.IGNORECASE)


def parse_score(text):
    """Return (score, max_score) as (float, int), or (None, None) if absent."""
    if not text:
        return None, None
    m = _SCORE_RE.search(text)
    if not m:
        return None, None
    return float(m.group(1)), int(m.group(2))


def _section_block(text, keywords):
    """Lines under the FIRST heading containing any keyword, until the next
    heading / horizontal rule / end. `keywords` are lowercase substrings."""
    if not text:
        return ""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line) and any(k in line.lower() for k in keywords):
            start = i + 1
            break
    if start is None:
        return ""
    out = []
    for line in lines[start:]:
        if _HEADING_RE.match(line) or line.strip() == "---":
            break
        out.append(line)
    return "\n".join(out)


def _list_items(block):
    """Bullet/numbered item texts from a block (order preserved, emphasis stripped)."""
    items = []
    for line in block.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            item = m.group(1).strip().strip("*_ ").strip()
            if item:
                items.append(item)
    return items


def _verdict(text):
    if not text:
        return None
    m = _VERDICT_RE.search(text)
    return m.group(1).strip() if m and m.group(1).strip() else None


def bounded_score(score, written_max, target_max):
    """Score on the REQUESTED scale, or None ("unscored") - Phase 07 F6.

    A score written against a different maximum (e.g. "12/15" for a 10-mark
    question) is rescaled onto ``target_max``. Anything still outside
    ``[0, target_max]`` - "12/10", a zero maximum - becomes None, so an
    out-of-range number (e.g. one an answer talked the model into) is never shown.
    """
    if score is None or not target_max or target_max <= 0:
        return None
    if written_max is not None:
        if written_max <= 0 or score > written_max:
            return None
        if written_max != target_max:
            score = score / written_max * target_max
    if not 0 <= score <= target_max:
        return None
    return round(score, 2)


def parse_answer_evaluation(text, *, max_score=10):
    """Parse EVALUATOR_PROMPT (basic) output into AnswerEvaluation (scored out of
    ``max_score``; the basic evaluator always asks for /10)."""
    text = text or ""
    score, written_max = parse_score(text)
    return AnswerEvaluation(
        score=bounded_score(score, written_max, max_score),
        max_score=max_score,
        did_well=_list_items(_section_block(text, ("did well", "what you did", "strength"))),
        missing=_list_items(_section_block(text, ("missing", "what's missing", "gap"))),
        improvements=_list_items(_section_block(text, ("improvement", "priority"))),
    )


def parse_mains_evaluation(text, *, max_marks=10):
    """Parse MAINS_EVAL_PROMPT output into MainsEvaluation."""
    text = text or ""
    score, written_max = parse_score(text)
    return MainsEvaluation(
        score=bounded_score(score, written_max, max_marks),
        max_marks=max_marks,
        verdict=_verdict(text),
        strengths=_list_items(_section_block(text, ("strength",))),
        gaps=_list_items(_section_block(text, ("gap", "missing"))),
        improvements=_list_items(_section_block(text, ("improvement", "top 3"))),
    )
