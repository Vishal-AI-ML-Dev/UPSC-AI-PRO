"""PreToolUse hook: block reads/edits of sensitive paths.

Reads the hook JSON from stdin. Exit 2 (with a reason on stderr) blocks the
tool call; exit 0 allows it. Never prints file contents. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys

# Patterns are matched against the repo-relative, forward-slash path.
BLOCKED = [
    (re.compile(r"(^|/)\.env(\..+)?$"), "environment/secret file"),
    (re.compile(r"\.(pem|key|p12|pfx)$", re.I), "private key/certificate"),
    (re.compile(r"(^|/)cookies[^/]*\.txt$", re.I), "exported browser cookies"),
    (re.compile(r"\.(sqlite|sqlite3|db)$", re.I), "local database file"),
    (re.compile(r"(^|/)data/ncert(/|$)"), "third-party NCERT PDFs"),
]
ALLOWED = [re.compile(r"(^|/)\.env\.example$")]


def _relpath(path: str) -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:  # different drive on Windows
        rel = path
    return rel.replace("\\", "/")


def check(path: str) -> str | None:
    """Return a block reason for ``path``, or None if allowed."""
    if not path:
        return None
    rel = _relpath(path)
    if any(p.search(rel) for p in ALLOWED):
        return None
    for pattern, reason in BLOCKED:
        if pattern.search(rel):
            return f"Blocked by protect_paths hook: '{rel}' is protected ({reason})."
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input: do not block unrelated tools
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    reason = check(path or "")
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
