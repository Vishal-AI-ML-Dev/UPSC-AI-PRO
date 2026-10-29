"""
Mentor helpers - trusted-source web search + student-context formatting.

The mentor "brain" itself is now the canonical tool-calling agent
(``src/graph/tools.build_tool_agent``, wired via
``src/graph/mentor_graph.build_mentor_graph``). The model decides its own tool
use, so the old hand-rolled intent-detection + ``mentor_reply`` streaming
pipeline was removed in the P1 dual-stack merge.

What remains here are the reusable helpers the rest of the stack still imports:

* ``_fetch_search_context`` - trusted-source web-search digest, used by the
  ``web_search`` tool (``src/graph/tools.py``) and the RAG subgraph.
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
    # Free, keyless fallback when Tavily is unavailable or returns nothing.
    if not _web_search_enabled():
        return ""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return ""
    try:
        lines = []
        with DDGS() as ddgs:
            for r in ddgs.text(question + " UPSC CSE official", max_results=4):
                title = r.get("title", "")
                body = (r.get("body", "") or "")[:300]
                href = r.get("href", "")
                lines.append(f"- {title}: {body} (Source: {href})")
        return chr(10).join(lines)
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return ""


def _web_search_enabled() -> bool:
    from src.core.config import settings as _settings

    return bool(_settings.web_search_enabled)


def _fetch_search_context(question: str) -> str:
    # WEB_SEARCH_ENABLED=false => neither Tavily nor DuckDuckGo is contacted.
    if not _web_search_enabled():
        return ""
    try:
        tool = _get_search_tool()
        if not tool:
            return ""
        raw = tool.invoke(question + " UPSC CSE official 2026")
        results = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
        if not results:
            return _duckduckgo_search(question)
        lines = []
        for r in results:
            title = r.get("title", "")
            content = r.get("content", "")[:300]
            url = r.get("url", "")
            lines.append(f"- {title}: {content} (Source: {url})")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Search failed: {e}")
        return ""


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
