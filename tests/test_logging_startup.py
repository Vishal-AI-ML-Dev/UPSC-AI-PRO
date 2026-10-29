"""In-process migrations must not reset the app's logging (Alembic fileConfig).

``migrations/env.py`` runs ``logging.config.fileConfig`` only when the Alembic
config's ``configure_logger`` attribute is not False. ``src.core.db`` sets it to
False for the app's in-process ``upgrade head``; the CLI keeps its logging.
Offline: throwaway SQLite only, no provider calls.
"""

import logging
import logging.config
import pathlib

from alembic import command
from alembic.config import Config

from src.core import db
from src.core.log_formatters import RequestIdFilter

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Marker(logging.Handler):
    def emit(self, record):
        pass


def test_in_process_migrations_keep_existing_loggers_and_root_handler(monkeypatch):
    early = logging.getLogger("synthetic.created.before.migrations")
    marker = _Marker()
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [*root.handlers, marker])
    monkeypatch.setattr(root, "level", logging.INFO)
    calls = []
    real_file_config = logging.config.fileConfig
    monkeypatch.setattr(
        logging.config, "fileConfig", lambda *a, **k: calls.append(a) or real_file_config(*a, **k)
    )

    db.run_migrations()

    assert calls == []  # env.py skipped fileConfig
    assert early.disabled is False
    assert marker in root.handlers and root.level == logging.INFO


def test_cli_style_config_still_configures_logging(monkeypatch, tmp_path):
    """Default (CLI) path: env.py still calls fileConfig with alembic.ini."""
    calls = []
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: calls.append(a))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    command.upgrade(cfg, "head")
    assert len(calls) == 1 and str(calls[0][0]).endswith("alembic.ini")


def test_app_startup_leaves_agent_loggers_and_app_handler(client):
    """After the real app lifespan (which runs init_db), app logging is intact."""
    for name in (
        "src.agents.planner.graph",
        "src.agents.evaluator.graph",
        "src.agents.ncert.graph",
        "src.api.main",
    ):
        assert logging.getLogger(name).disabled is False, name
    handlers = logging.getLogger().handlers
    assert any(isinstance(f, RequestIdFilter) for h in handlers for f in h.filters)
