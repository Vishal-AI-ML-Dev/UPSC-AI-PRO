"""MCP (Model Context Protocol) server exposing the UPSC KB + tools.

Makes the platform's own capabilities reusable by any MCP client (Claude
Desktop, Cursor, VS Code, or another agent) - the same web-search +
knowledge-base + evaluator + current-affairs tools the app uses internally,
now speakable over the open MCP standard.

Two layers, mirroring ``src/graph/tools.py`` so the useful bits stay testable
without the MCP SDK or any heavy dependency:

1. **Pure spec registry** - ``build_mcp_tool_specs()`` returns lightweight
   ``ToolSpec`` records (name, description, handler). Handlers delegate to the
   already-tested tool functions; heavy imports stay lazy *inside* each handler.
   Importing this module pulls in NO mcp / langchain / vector dependency.
2. **Server wiring** - ``build_server()`` registers each spec on a FastMCP
   instance; ``run()`` starts it over the local stdio transport only. The
   network (SSE) transport and ``mount_mcp(app)`` are disabled because the
   tools are unauthenticated (Phase 02, R12-FC). The ``mcp`` SDK is imported
   lazily so layer 1 (and app boot) never needs it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

SERVER_NAME = "upsc-ai-pro"

_STDIO = "stdio"
_SSE = "sse"  # FastMCP's name for the network (HTTP/SSE) transport


def resolve_transport(value: str | None) -> str:
    """Normalise a transport name to one FastMCP understands.

    Pure + offline-testable. ``http`` is accepted as a friendly alias for the
    network transport (FastMCP names it ``sse``). Anything unknown -> stdio
    (safe local default), so a typo never crashes the server at startup.
    """
    v = (value or _STDIO).strip().lower()
    if v in ("http", "sse", "streamable-http", "streamable_http"):
        return _SSE
    return v if v == _STDIO else _STDIO


def _collect(result) -> str:
    """Flatten a tool result into a single string.

    The evaluator / current-affairs entrypoints are streaming generators that
    yield string (or ``.content``) chunks; the core tool fns return plain
    strings. Handle both so every MCP tool returns clean text.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, Iterable):
        parts = []
        for chunk in result:
            parts.append(chunk if isinstance(chunk, str) else str(getattr(chunk, "content", chunk)))
        return "".join(parts)
    return str(result)


# --------------------------------------------------------------------------- #
# Tool handlers - thin, typed wrappers over already-tested functions.
# Type hints matter: FastMCP derives each tool's input schema from them.
# Heavy imports stay lazy so importing this module is dependency-free.
# --------------------------------------------------------------------------- #
def knowledge_base_search(query: str, persist_key: str = "") -> str:
    """Retrieve grounded, source-labelled passages from the verified UPSC Mentor
    knowledge base. Returns fenced, citeable context text.

    ``persist_key`` is accepted for backward compatibility but IGNORED: an MCP
    caller is unauthenticated, so it may never address a specific (possibly
    user-owned) collection (Phase 02, R12-FC).
    """
    del persist_key  # never honoured - see docstring
    from src.graph.tools import knowledge_base_tool_fn

    return knowledge_base_tool_fn(query)


def web_search(query: str) -> str:
    """Search trusted UPSC/news sources for current, time-sensitive facts
    (exam dates, notifications, results, cut-offs, vacancies, recent news)."""
    from src.graph.tools import web_search_tool_fn

    return web_search_tool_fn(query)


def evaluate_answer(question: str, answer: str) -> str:
    """Evaluate a student's UPSC answer against a question and return feedback
    (score, what went well, gaps, improvements) as markdown."""
    from src.agents.evaluator.graph import evaluate_answer as _evaluate

    return _collect(_evaluate(question, answer))


def current_affairs(selected_date: str = "") -> str:
    """Generate the day's UPSC current-affairs digest, grounded in real news
    feeds (PIB, The Hindu, Indian Express) when available."""
    from src.agents.current_affairs.graph import get_daily_ca

    return _collect(get_daily_ca(selected_date))


# --------------------------------------------------------------------------- #
# 1. Pure spec registry (offline-testable, no MCP import)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSpec:
    """A single MCP tool: the model-facing name/description + its handler."""

    name: str
    description: str
    handler: Callable[..., str]


def build_mcp_tool_specs() -> list[ToolSpec]:
    """Tools this MCP server exposes. Descriptions are the contract the remote
    model sees; handlers delegate to the app's own tested functions."""
    return [
        ToolSpec(
            "knowledge_base_search",
            "Grounded retrieval from the verified UPSC Mentor knowledge base.",
            knowledge_base_search,
        ),
        ToolSpec(
            "web_search",
            "Live web search over trusted UPSC/news sources for current, "
            "time-sensitive facts (dates, notifications, results, cut-offs).",
            web_search,
        ),
        ToolSpec(
            "evaluate_answer",
            "Evaluate a UPSC answer and return structured feedback "
            "(score, strengths, gaps, improvements).",
            evaluate_answer,
        ),
        ToolSpec(
            "current_affairs",
            "The day's UPSC current-affairs digest, grounded in real news feeds when available.",
            current_affairs,
        ),
    ]


# --------------------------------------------------------------------------- #
# 2. Server wiring (mcp SDK imported lazily)
# --------------------------------------------------------------------------- #
def build_server(name: str = SERVER_NAME):
    """Build a FastMCP server with every tool from the spec registry."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name)
    for spec in build_mcp_tool_specs():
        server.add_tool(spec.handler, name=spec.name, description=spec.description)
    return server


def mount_mcp(app, *, path: str | None = None) -> bool:
    """Network (SSE) mounting is DISABLED (Phase 02, R12-FC, fail closed).

    The MCP tools have no authentication, so they must not be reachable over
    HTTP. This always returns False without mounting anything, whatever
    ``settings.mcp_enabled`` says. Use the local stdio transport instead.
    """
    del app, path
    logger.warning("MCP network mount refused: only the local stdio transport is allowed")
    return False


def run(transport: str | None = None) -> None:
    """Run the MCP server standalone (``python -m src.mcp_server``).

    Transport comes from the argument, else ``settings.mcp_transport``, else
    stdio. Only stdio (local desktop clients) is allowed; a network transport
    (sse/http) is refused (Phase 02, R12-FC).
    """
    from src.core.config import settings

    chosen = resolve_transport(transport or getattr(settings, "mcp_transport", _STDIO))
    if chosen != _STDIO:
        # Phase 02, R12-FC: unauthenticated tools must never listen on a network port.
        raise RuntimeError("MCP network transport is disabled; run with the stdio transport.")
    server = build_server()
    logger.info("Starting UPSC MCP server (transport=%s)", chosen)
    server.run(transport=chosen)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
