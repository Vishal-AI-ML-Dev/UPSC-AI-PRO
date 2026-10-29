"""
Planner Agent - Study plan generation with live UPSC dates
"""

import logging
import re
import time as _time
from datetime import datetime

from src.agents.planner.constants import (
    get_attempt_guidance,
    get_optional_context,
    get_weak_context,
    get_weak_plan_instruction,
)
from src.agents.planner.prompts import PLANNER_PROMPT
from src.core import mentor_kb
from src.core.llm import describe_exception, get_llm, message_text

# Official / trusted domains for live UPSC date lookups
TRUSTED_DOMAINS = [
    "upsc.gov.in",
    "pib.gov.in",
    "mygov.in",
    "thehindu.com",
    "indianexpress.com",
    "drishtiias.com",
]

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# TAVILY SEARCH — lazy init + cached UPSC dates
# ─────────────────────────────────────────

_search_tool = None
# One cached date, valid only for the attempt year it was fetched for.
_upsc_date_cache = {"date": None, "timestamp": 0, "year": None}

_PRELIMS_DATE_RE = re.compile(
    r"(prelims|preliminary).*?(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE
)


def _prelims_date_for_year(text: str, attempt_year: int) -> str:
    """First "prelims ... <date>" in ``text`` whose date is in ``attempt_year``."""
    for match in _PRELIMS_DATE_RE.finditer(text or ""):
        if match.group(2).rstrip().endswith(str(attempt_year)):
            return match.group(2)
    return ""


def _get_search_tool():
    global _search_tool
    if _search_tool is None:
        try:
            from langchain_community.tools.tavily_search import TavilySearchResults

            try:
                _search_tool = TavilySearchResults(max_results=4, include_domains=TRUSTED_DOMAINS)
            except Exception:
                _search_tool = TavilySearchResults(max_results=4)
        except Exception as e:
            logger.warning(
                "Tavily init failed: %s", describe_exception(e), extra={"safe_summary": True}
            )
            return None
    return _search_tool


def _duckduckgo_date_search(attempt_year: int) -> str:
    """Fallback: search DuckDuckGo for the prelims date when Tavily is unavailable."""
    try:
        from duckduckgo_search import DDGS
    except Exception:
        return ""
    try:
        query = f"UPSC CSE Prelims {attempt_year} exam date official notification upsc.gov.in"
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                body = (r.get("body") or "") + " " + (r.get("title") or "")
                found = _prelims_date_for_year(body, attempt_year)
                if found:
                    return found
    except Exception as e:
        logger.warning(
            "DuckDuckGo date fetch failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
    return ""


def _fetch_upsc_prelims_date(attempt_year: int) -> str:
    """Fetch real UPSC prelims date from web. Cached for 24 hours."""
    cache_age = _time.time() - _upsc_date_cache["timestamp"]
    if (
        _upsc_date_cache["date"]
        and _upsc_date_cache.get("year") == attempt_year
        and cache_age < 86400
    ):
        return _upsc_date_cache["date"]

    from src.core.config import settings

    # WEB_SEARCH_ENABLED=false => neither Tavily nor DuckDuckGo is contacted.
    if not settings.web_search_enabled:
        return ""

    date_str = ""
    try:
        tool = _get_search_tool()
        if tool:
            results = tool.invoke(
                f"UPSC CSE Prelims {attempt_year} exam date official notification"
            )
            for r in results or []:
                content = r.get("content", "") if isinstance(r, dict) else str(r)
                date_str = _prelims_date_for_year(content, attempt_year)
                if date_str:
                    break
    except Exception as e:
        logger.warning(
            "UPSC date fetch failed: %s", describe_exception(e), extra={"safe_summary": True}
        )

    if not date_str:
        date_str = _duckduckgo_date_search(attempt_year)

    if date_str:
        _upsc_date_cache["date"] = date_str
        _upsc_date_cache["timestamp"] = _time.time()
        _upsc_date_cache["year"] = attempt_year
        logger.info(f"Live UPSC date fetched: {date_str}")
    return date_str


# ─────────────────────────────────────────
# MAIN FUNCTION — streaming
# ─────────────────────────────────────────


def generate_plan(
    goal: str,
    hours: str,
    optional: str,
    weak: str,
    attempt_number: str,
):
    """
    Streaming generator — yields plan chunks.
    """
    from src.core.plan_timeline import compute_plan_timeline, parse_attempt_year

    today = datetime.now()

    # Timeline (same rules as get_plan_meta): an already-held Prelims rolls the
    # plan to the next one instead of saying "Prelims is this month".
    live_date = _fetch_upsc_prelims_date(parse_attempt_year(goal, today))
    meta = compute_plan_timeline(goal, today=today, live_date=live_date or None)
    attempt_year, months_left, timeline_msg = (
        meta.attempt_year,
        meta.months_left,
        meta.timeline_msg,
    )

    # A regex hit from web search is not an official date - label it as such.
    live_upsc_date = (
        f"Prelims {attempt_year} date: {meta.prelims_date} (from a web search result, "
        "not verified - confirm at upsc.gov.in)"
        if meta.prelims_date
        else f"No date for Prelims {attempt_year} was found - verify at upsc.gov.in"
    )

    # Get contexts
    attempt_num = attempt_number.strip() if attempt_number else "1"
    attempt_guidance = get_attempt_guidance(attempt_num)

    optional_clean = optional.strip() if optional and optional.strip() else "Not decided"
    weak_clean = weak.strip() if weak and weak.strip() else "Not specified"

    optional_context, optional_plan_instruction = get_optional_context(optional_clean)
    weak_context = get_weak_context(weak_clean)
    weak_plan_instruction = get_weak_plan_instruction(weak_clean, months_left)

    # Knowledge base grounding (verified facts + topper strategies)
    try:
        if mentor_kb.kb_exists():
            kb_query = f"UPSC strategy {optional_clean} {weak_clean} prelims preparation"
            kb_res = mentor_kb.search_kb(kb_query, k=4)
            kb_context = kb_res.get("context") or "No extra knowledge-base context available."
        else:
            kb_context = "No extra knowledge-base context available."
    except Exception as e:
        logger.warning(
            "Planner KB search failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
        kb_context = "No extra knowledge-base context available."

    try:
        chain = PLANNER_PROMPT | get_llm()
        produced = False
        for chunk in chain.stream(
            {
                "today": today.strftime("%d %B %Y"),
                "attempt_year": attempt_year,
                "months_left": months_left,
                "timeline_msg": timeline_msg,
                "hours": hours,
                "optional": optional_clean,
                "weak": weak_clean,
                "attempt_number": attempt_num,
                "live_upsc_date": live_upsc_date,
                "attempt_guidance": attempt_guidance,
                "optional_context": optional_context,
                "optional_plan_instruction": optional_plan_instruction,
                "weak_context": weak_context,
                "weak_plan_instruction": weak_plan_instruction,
                "kb_context": kb_context,
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
            "Plan generation failed: %s", describe_exception(e), extra={"safe_summary": True}
        )
        yield "⚠️ Plan generation failed. Please try again."


def get_plan_meta(goal: str):
    """Deterministic, code-derived study-plan metadata (StudyPlanMeta).

    Uses the 24h-cached live prelims date when available; pure timeline math
    lives in src.core.plan_timeline (offline-testable). No LLM call.
    """
    from src.core.plan_timeline import compute_plan_timeline, parse_attempt_year

    today = datetime.now()
    attempt_year = parse_attempt_year(goal, today)
    live_date = _fetch_upsc_prelims_date(attempt_year)
    return compute_plan_timeline(goal, today=today, live_date=live_date or None)
