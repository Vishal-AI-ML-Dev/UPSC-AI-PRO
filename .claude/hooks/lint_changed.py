"""PostToolUse hook: report ruff lint findings for the edited Python file.

Report-only: findings are returned as ``additionalContext`` JSON for Claude to
see, and the hook always exits 0 so it never blocks. Never installs anything. Stdlib only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

PY_ROOTS = ("src/", "tests/", "scripts/")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    root = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        rel = os.path.relpath(os.path.abspath(path), root).replace("\\", "/")
    except ValueError:
        return 0
    if not (rel.endswith(".py") and rel.startswith(PY_ROOTS)) or not shutil.which("uv"):
        return 0
    try:
        proc = subprocess.run(
            ["uv", "run", "--no-sync", "ruff", "check", "--output-format", "concise", rel],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception:
        return 0
    if proc.returncode != 0 and proc.stdout.strip():
        # Structured output: surfaced to Claude as context without blocking.
        message = f"ruff findings (report-only) for {rel}:\n{proc.stdout.strip()[:2000]}"
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": message,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
