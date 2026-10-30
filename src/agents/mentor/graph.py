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

# Upper bound for one provider call. A hung Tavily request must not stall the
# answer - on timeout we fall through to the keyless DuckDuckGo fallback.
SEARCH_TIMEOUT_SECONDS = 10.0
MAX_SEARCH_RESULTS = 4

# Appended to every query to bias results towards UPSC/official sources.
# Deliberately carries NO year: a hard-coded year goes stale and skews results.
_QUERY_SUFFIX = " UPSC CSE official"

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


def _call_with_timeout(fn, timeout: float):
    """Run ``fn()`` in a worker thread; raise ``TimeoutError`` past ``timeout``.

    The worker is abandoned (not joined) on timeout so a hung provider can
    never block the request path.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _FutureTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=timeout)
    except _FutureTimeout as exc:
        raise TimeoutError(f"search timed out after {timeout:g}s") from exc
    finally:
        pool.shutdown(wait=False)


def _format_results(results, *, body_key: str, url_key: str) -> str:
    """Render provider results as the "- title: snippet (Source: url)" digest."""
    lines = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        url = (r.get(url_key) or "").strip()
        if not url:
            continue  # never emit an uncitable result
        title = r.get("title", "") or ""
        body = (r.get(body_key, "") or "")[:300]
        lines.append(f"- {title}: {body} (Source: {url})")
    return "\n".join(lines)


def _tavily_search(question: str) -> str:
    """Tavily digest, or "" when no key/tool. Raises on provider errors/timeouts."""
    tool = _get_search_tool()
    if not tool:
        return ""
    raw = _call_with_timeout(lambda: tool.invoke(question + _QUERY_SUFFIX), SEARCH_TIMEOUT_SECONDS)
    results = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
    return _format_results(results, body_key="content", url_key="url")


def _duckduckgo_search(question: str) -> str:
    # Free, keyless fallback when Tavily is missing, fails, times out or
    # returns nothing.
    if not _web_search_enabled():
        return ""
    try:
        from ddgs import DDGS
    except ImportError:
        logger.warning("ddgs not installed; DuckDuckGo fallback disabled. Run: uv add ddgs")
        return ""
    try:

        def _run():
            with DDGS(timeout=int(SEARCH_TIMEOUT_SECONDS)) as ddgs:
                return ddgs.text(question + _QUERY_SUFFIX, max_results=MAX_SEARCH_RESULTS)

        results = _call_with_timeout(_run, SEARCH_TIMEOUT_SECONDS)
        return _format_results(results, body_key="body", url_key="href")
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return ""


def _web_search_enabled() -> bool:
    from src.core.config import settings as _settings

    return bool(_settings.web_search_enabled)


def _fetch_search_context(question: str) -> str:
    """Live search digest: Tavily first, DuckDuckGo as the free fallback.

    DuckDuckGo runs whenever Tavily yields nothing usable - no API key, an
    init/provider error, a timeout, or zero results. Returns "" when every
    provider fails, so the caller can admit it has no live data.
    """
    # WEB_SEARCH_ENABLED=false => neither Tavily nor DuckDuckGo is contacted.
    if not _web_search_enabled():
        return ""
    try:
        result = _tavily_search(question)
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        result = ""
    return result or _duckduckgo_search(question)


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
