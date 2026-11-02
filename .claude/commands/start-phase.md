---
description: Start phase N — load state, draft or load its spec, and propose a plan (no code changes)
argument-hint: <phase number, e.g. 1>
---

Start phase $ARGUMENTS of UPSC-AI-PRO.

1. Read `CLAUDE.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`, and `.claude/rules/scope-control.md`.
2. Confirm the previous phase is DONE in `docs/PROJECT_STATE.md`. If not, stop and report what's open.
3. Open `.claude/specs/phase-$ARGUMENTS-*.md` (zero-padded, e.g. `phase-01-*`).
   - If it is a STUB: verify every "current behaviour" claim against the source, then expand it into a DRAFT
     using `.claude/specs/_TEMPLATE.md`. Mark anything you could not verify as UNVERIFIED.
4. Draft `.claude/plans/phase-$ARGUMENTS-*.md` from `.claude/plans/_TEMPLATE.md` with an exhaustive
   **Files to touch** table and verification commands.
5. Show the user the spec and plan, list open questions, and wait for approval.
   Do NOT modify application code in this command.
