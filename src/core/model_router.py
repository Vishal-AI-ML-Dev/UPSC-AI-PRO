"""Query-complexity model router (pure, offline-testable).

Picks a model *tier* per request so trivial turns use the cheap/fast model and
reasoning-heavy turns use the strong model. There is NO LLM / network / heavy
dependency here - the actual chains live in ``src.core.llm``
(``get_llm_for_tier``). This module only decides *which* tier to ask for.

Design principle: **bias toward STRONG when unsure** (quality > cost). LITE is
chosen only for clearly-trivial turns (short, no reasoning/lookup signals), so we
never trade answer quality for savings on a hard question. Over-routing to STRONG
just costs a bit more; under-routing to LITE could hurt an answer - so the safe
mistake is STRONG.

The two tiers map onto the chains that already exist:
    LITE   -> get_fast_llm()  (flash-lite first: fast + cheap, preserves quota)
    STRONG -> get_llm()       (flash first: best quality)
"""

import re

LITE = "lite"
STRONG = "strong"

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Reasoning / long-form signals -> need the strong model. Includes a few common
# Hinglish cues (samjhao/samjha = explain) since users mix languages.
_REASONING_KEYWORDS = frozenset(
    {
        "evaluate",
        "evaluation",
        "assess",
        "assessment",
        "critically",
        "critique",
        "analyse",
        "analyze",
        "analysis",
        "compare",
        "comparison",
        "contrast",
        "discuss",
        "elaborate",
        "explain",
        "essay",
        "mains",
        "differentiate",
        "examine",
        "justify",
        "derive",
        "prove",
        "reason",
        "reasoning",
        "plan",
        "strategy",
        "roadmap",
        "synthesise",
        "synthesize",
        "comprehensive",
        "detailed",
        "why",
        "how",
        "significance",
        "implications",
        "consequences",
        "samjhao",
        "samjha",
        "vistaar",
    }
)

# Volatile-fact lookups -> when tools are available, use the strong model for
# reliable tool orchestration + careful grounding of time-sensitive facts.
_LOOKUP_KEYWORDS = frozenset(
    {
        "latest",
        "current",
        "today",
        "news",
        "date",
        "when",
        "result",
        "results",
        "notification",
        "cutoff",
        "recent",
        "upcoming",
        "deadline",
        "schedule",
    }
)

# A long query is itself a complexity signal, independent of keywords.
STRONG_WORD_THRESHOLD = 30


def _tokens(text):
    return _TOKEN_RE.findall((text or "").lower())


def route_model_tier(query, *, has_tools=False, force=None):
    """Return ``LITE`` or ``STRONG`` for a query.

    ``force`` (``"lite"``/``"strong"``) overrides all heuristics - useful when a
    caller already knows the workload is quality-critical (evaluator/planner) or
    trivial. ``has_tools`` says the caller exposes tools, so volatile-fact
    lookups are routed to STRONG for dependable tool use.
    """
    if force in (LITE, STRONG):
        return force
    tokens = _tokens(query)
    if not tokens:
        return LITE  # empty / greeting-ish -> cheap
    token_set = set(tokens)
    if token_set & _REASONING_KEYWORDS:
        return STRONG
    if len(tokens) >= STRONG_WORD_THRESHOLD:
        return STRONG
    if has_tools and (token_set & _LOOKUP_KEYWORDS):
        return STRONG
    return LITE


def describe_route(query, *, has_tools=False, force=None):
    """Return ``(tier, reason)`` for logging/debugging. Pure, no side effects."""
    if force in (LITE, STRONG):
        return force, "forced"
    tokens = _tokens(query)
    if not tokens:
        return LITE, "empty"
    token_set = set(tokens)
    hit = token_set & _REASONING_KEYWORDS
    if hit:
        return STRONG, "reasoning:" + ",".join(sorted(hit))
    if len(tokens) >= STRONG_WORD_THRESHOLD:
        return STRONG, "long:%d_words" % len(tokens)
    lookup = token_set & _LOOKUP_KEYWORDS
    if has_tools and lookup:
        return STRONG, "lookup:" + ",".join(sorted(lookup))
    return LITE, "trivial"
