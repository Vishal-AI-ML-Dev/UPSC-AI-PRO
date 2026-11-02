"""Phase 02 — MCP server is not reachable over the network (spec AC17, AC18 / R12-FC).

Live Render settings (Q5) are UNVERIFIED, so the approved plan fails closed:
the MCP server is stdio-only, never mounts on the web app, and its
``knowledge_base_search`` cannot be pointed at an arbitrary collection.
"""

from pathlib import Path

import pytest
from starlette.applications import Starlette

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# AC17 — the deployed web app does not expose MCP
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/sse", "/mcp/messages/"])
def test_web_app_has_no_mcp_route(client, path):
    assert client.get(path).status_code == 404


def test_main_does_not_mount_mcp():
    text = (ROOT / "src" / "api" / "main.py").read_text(encoding="utf-8")
    assert "mount_mcp" not in text
    assert "mcp_server" not in text


def test_mount_mcp_is_noop_with_default_settings():
    from src.mcp_server import mount_mcp

    app = Starlette()
    assert mount_mcp(app) is False
    assert not any(getattr(r, "path", "").startswith("/mcp") for r in app.routes)


# --------------------------------------------------------------------------- #
# AC18 — R12-FC fail-closed protection
# --------------------------------------------------------------------------- #
def test_mount_mcp_refuses_even_when_enabled(monkeypatch):
    from types import SimpleNamespace

    import src.core.config as config
    from src.mcp_server import mount_mcp

    # Settings has no mcp_enabled field; simulate an operator enabling it anyway.
    monkeypatch.setattr(config, "settings", SimpleNamespace(mcp_enabled=True, mcp_http_path="/mcp"))
    app = Starlette()
    assert mount_mcp(app) is False
    assert not any(getattr(r, "path", "").startswith("/mcp") for r in app.routes)


@pytest.mark.parametrize("transport", ["sse", "http", "streamable-http"])
def test_run_refuses_network_transport(monkeypatch, transport):
    import src.mcp_server as m

    started = []
    monkeypatch.setattr(m, "build_server", lambda *a, **k: started.append(1))
    with pytest.raises(RuntimeError):
        m.run(transport)
    assert started == []


def test_mcp_kb_search_ignores_persist_key(monkeypatch):
    import src.core.mentor_kb as mentor_kb
    import src.core.vector_store as vector_store
    from src.mcp_server import knowledge_base_search

    loaded = []
    monkeypatch.setattr(vector_store, "load_vector_store", lambda key: loaded.append(key))
    monkeypatch.setattr(
        mentor_kb, "search_kb", lambda q, k=4, **kw: {"context": "MENTOR-KB", "citations": []}
    )
    out = knowledge_base_search("polity", persist_key="pyqbank_someuser")
    assert loaded == []
    assert "MENTOR-KB" in out


def test_mcp_tool_description_does_not_advertise_persist_key():
    from src.mcp_server import build_mcp_tool_specs

    kb = next(s for s in build_mcp_tool_specs() if s.name == "knowledge_base_search")
    assert "persist_key" not in kb.description
