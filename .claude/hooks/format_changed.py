"""PostToolUse hook: auto-format the single file that was just edited.

* ``src/``, ``tests/``, ``scripts/``, ``migrations/`` ``*.py`` -> ``uv run ruff format <file>``
* ``frontend/src/**`` ``*.ts|*.tsx|*.css``           -> ``npx --no-install prettier --write <file>``

Never installs anything; if a tool is missing or fails, it exits 0 silently so
editing is never blocked. Stdlib only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

PY_ROOTS = ("src/", "tests/", "scripts/", "migrations/")
FE_ROOT = "frontend/src/"
FE_EXT = (".ts", ".tsx", ".css")


def _root() -> str:
    return os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(path), root).replace("\\", "/")
    except ValueError:
        return ""


def _run(cmd: list[str], cwd: str) -> None:
    try:
        subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=60, check=False)
    except Exception:
        pass  # formatting is best-effort


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    root = _root()
    rel = _rel(path, root)
    if not rel or rel.startswith(".."):
        return 0
    if rel.endswith(".py") and rel.startswith(PY_ROOTS) and shutil.which("uv"):
        _run(["uv", "run", "--no-sync", "ruff", "format", rel], root)
    elif rel.startswith(FE_ROOT) and rel.endswith(FE_EXT) and shutil.which("npx"):
        frontend = os.path.join(root, "frontend")
        if os.path.isdir(os.path.join(frontend, "node_modules", "prettier")):
            _run(["npx", "--no-install", "prettier", "--write", rel[len("frontend/") :]], frontend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
