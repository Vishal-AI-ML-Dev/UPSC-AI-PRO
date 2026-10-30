"""
Mentor helpers - trusted-source web search + student-context formatting.

The mentor "brain" itself is now the canonical tool-calling agent
(``src/graph/tools.build_tool_agent``, wired via
``src/graph/mentor_graph.build_mentor_graph``). The model decides its own tool
use, so the old hand-rolled intent-detection + ``mentor_reply`` streaming
pipeline was removed in the P1 dual-stack merge.

What remains here are the reusable helpers the rest of the stack still imports:

* ``_fetch_search_context`` - numbered live-search digest (structured search
  lives in ``src/core/web_search.py``), used by the RAG subgraph.
* ``_get_search_tool`` - the lazily-built Tavily tool used as the first
  provider of that chain.
* ``_build_student_context`` - formats a student profile for the mentor prompt,
  used by ``mentor_graph._mentor_context``.

Only the standard library is imported at module load; all heavy/optional
dependencies (Tavily, DuckDuckGo, Settings) are imported lazily inside the
functions, so importing this module stays cheap and side-effect-free.
"""

import logging

logger = logging.getLogger(__name__)

# Official / trusted sources for live UPSC facts (dates, notifications, etc.)
TRUSTED_DOMAINS = [
    "upsc.gov.in",
    "pib.gov.in",
    "mygov.in",
    "thehindu.com",
    "indianexpress.com",
    "drishtiias.com",
]

_search_tool = None


def _get_search_tool():
    global _search_tool
    if _search_tool is None:
        # Bridge the key from Settings (.env) into os.environ so libraries
        # like langchain_tavily / community Tavily (which read the env var
        # directly, not our Settings object) can find it. setdefault keeps
        # any real exported env var as the source of truth.
        import os as _os

        from src.core.config import settings as _settings

        _tavily_key = (_settings.tavily_api_key or "").strip()
        if _tavily_key:
            _os.environ.setdefault("TAVILY_API_KEY", _tavily_key)
        try:
            from langchain_tavily import TavilySearch

            try:
                _search_tool = TavilySearch(max_results=4, include_domains=TRUSTED_DOMAINS)
            except Exception:
                _search_tool = TavilySearch(max_results=4)
        except ImportError:
            try:
                from langchain_community.tools.tavily_search import TavilySearchResults

                try:
                    _search_tool = TavilySearchResults(
                        max_results=4, include_domains=TRUSTED_DOMAINS
                    )
                except Exception:
                    _search_tool = TavilySearchResults(max_results=4)
            except Exception as e:
                logger.warning(f"Tavily not available: {e}")
                return None
        except Exception as e:
            logger.warning(f"Tavily init failed: {e}")
            return None
    return _search_tool


def _duckduckgo_search(question: str) -> str:
    """Keyless DuckDuckGo digest (text search only); "" on failure or when disabled."""
    from src.core import web_search

    if not question or not web_search.web_search_enabled():
        return ""
    try:
        results = web_search.dedupe(
            web_search._ddgs_text(question, web_search.PROVIDER_TIMEOUT_SECONDS)
        )
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", type(e).__name__)
        return ""
    return web_search.format_digest(results[: web_search.MAX_RESULTS])


def _web_search_enabled() -> bool:
    from src.core.web_search import web_search_enabled

    return web_search_enabled()


def _fetch_search_context(question: str) -> str:
    """Numbered live-search digest from the free-first provider chain.

    See ``src/core/web_search.search_web``: Tavily (if configured) -> DDGS news
    -> DDGS text -> Google News RSS -> PIB RSS, under one time budget. Returns
    "" when search is disabled or every provider fails, so the caller can admit
    it has no live data. Each entry keeps a "(Source: url)" marker.
    """
    from src.core.web_search import format_digest, search_web

    return format_digest(search_web(question))


def _build_student_context(ctx: dict | None) -> str:
    if not ctx:
        return "No profile yet — infer from the question."
    fields = {
        "Name": ctx.get("name"),
        "Optional": ctx.get("optional"),
        "Stage": ctx.get("stage"),
        "Weak Areas": ctx.get("weak_areas"),
        "Strong Areas": ctx.get("strong_areas"),
        "Target Year": ctx.get("target_year"),
        "Daily Hours": ctx.get("study_hours"),
        "Attempts": ctx.get("attempts"),
    }
    lines = [f"- {k}: {v}" for k, v in fields.items() if v]
    return "\n".join(lines) if lines else "No profile yet — infer from the question."
