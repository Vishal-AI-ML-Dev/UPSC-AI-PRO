---
description: Report current phase, workflow health, open risks and uncommitted changes (read-only)
---

Report the current state of UPSC-AI-PRO without changing anything.

1. Read `docs/PROJECT_STATE.md` and the roadmap table in `.claude/specs/README.md`.
2. Run `git status --porcelain` and `git log --oneline -5`.
3. Compare changed files against the active plan's **Files to touch** table; flag anything outside it.
4. Summarise in under 25 lines: active phase and its spec/plan status, acceptance criteria progress,
   8-workflow status, top open risks, out-of-scope changes, and the recommended next step.
