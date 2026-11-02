---
name: complete-phase
description: Close the active UPSC-AI-PRO phase — run the quality gates, write the phase report, update PROJECT_STATE and DECISIONS. Use when the user says a phase is done or asks to close/complete it.
---

# Complete a phase

1. Run the gates and capture the exact summary lines:
   - `uv run pytest -q`
   - `uv run ruff check src tests`
   - `uv run python -m compileall -q src tests`
   - `cd frontend && npm run build` (if `frontend/` changed) and `npm run lint`
2. Confirm `review-changes` ran with no BLOCKING findings left open. If any remain, stop.
3. Write `docs/phase-reports/phase-NN-report.md`:
   - Summary; files changed (`git status --porcelain`); acceptance criteria table (MET / NOT MET / UNVERIFIED
     with evidence); gate results verbatim; 8-workflow status; new backlog items; follow-ups.
4. Update `docs/PROJECT_STATE.md`: phase table, workflow table, risks closed/opened, backlog, and a fresh
   **Last handoff** block (use the `/handoff` command format).
5. Add any decisions made during the phase to `docs/DECISIONS.md` as new ADRs.
6. Set the spec and plan `Status: DONE`, and the roadmap row in `.claude/specs/README.md`.
7. Report to the user. Do not commit unless asked; if asked, use the user's message verbatim.

A phase with an unmet acceptance criterion is reported as **INCOMPLETE**, not done.
