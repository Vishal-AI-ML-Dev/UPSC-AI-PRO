"""Offline tests for the MCP server (src/mcp_server.py).

No mcp SDK, no API keys, no vector store required - we test the pure spec
registry, transport resolution, the _collect flattener, and that the core
handlers really delegate to the app's tool functions (empty-query path stays
dependency-free).
"""

import importlib
import sys


def _load():
    sys.modules.pop("src.mcp_server", None)
    return importlib.import_module("src.mcp_server")


def test_registry_exposes_expected_tools():
    m = _load()
    specs = m.build_mcp_tool_specs()
    names = {s.name for s in specs}
    assert names == {
        "knowledge_base_search",
        "web_search",
        "evaluate_answer",
        "current_affairs",
    }
    for s in specs:
        assert s.description.strip()
        assert callable(s.handler)


def test_resolve_transport_aliases():
    m = _load()
    assert m.resolve_transport("stdio") == "stdio"
    assert m.resolve_transport("http") == "sse"
    assert m.resolve_transport("sse") == "sse"
    assert m.resolve_transport("streamable-http") == "sse"
    assert m.resolve_transport(None) == "stdio"
    assert m.resolve_transport("garbage") == "stdio"  # unknown -> safe default
    assert m.resolve_transport("  HTTP ") == "sse"  # trim + case-insensitive


def test_collect_flattens_strings_and_streams():
    m = _load()
    assert m._collect("hello") == "hello"
    assert m._collect(["a", "b", "c"]) == "abc"  # list of str
    assert m._collect(iter(["x", "y"])) == "xy"  # generator of str

    class _Chunk:
        def __init__(self, content):
            self.content = content

    assert m._collect([_Chunk("p"), _Chunk("q")]) == "pq"  # objects with .content


def test_core_handlers_delegate_offline():
    # Empty query short-circuits inside the underlying tool fns without any
    # heavy import, so this exercises real delegation with zero deps.
    m = _load()
    assert m.web_search("") == "No query provided."
    assert m.knowledge_base_search("") == "No query provided."


def test_import_pulls_no_heavy_deps():
    for mod in ("mcp", "langchain", "langgraph", "langchain_core"):
        sys.modules.pop(mod, None)
    _load()
    for mod in ("mcp", "langchain", "langgraph"):
        assert mod not in sys.modules, f"{mod} must not import at module load"
