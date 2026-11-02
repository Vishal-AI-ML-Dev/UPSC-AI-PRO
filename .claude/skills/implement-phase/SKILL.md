---
name: implement-phase
description: Implement the active UPSC-AI-PRO phase from its APPROVED spec and plan, editing only the files the plan lists. Use when the user says to implement / start coding / execute a phase plan.
---

# Implement a phase

## Preconditions (stop and ask if any fails)
1. `docs/PROJECT_STATE.md` names the active phase NN.
2. `.claude/specs/phase-NN-*.md` has `Status: APPROVED`.
3. `.claude/plans/phase-NN-*.md` has `Status: APPROVED`.

## Steps
1. Read `CLAUDE.md`, the spec, the plan, and the rules relevant to the touched areas
   (`.claude/rules/backend.md`, `frontend.md`, `testing.md`, `security.md`, `scope-control.md`).
2. Re-read every file in the plan's **Files to touch** table before editing it. Confirm the "current behaviour"
   in the spec still matches the code; if it doesn't, stop and report the difference.
3. Implement step by step, in plan order. Reuse the functions named in the plan's **Reused code** section.
4. Edit ONLY files in the table. If another file must change, stop and propose a plan amendment.
5. Write or update tests with the `write-tests` skill as you go (regression test before each fix where possible).
6. Run the plan's verification commands. At minimum: `uv run pytest -q`; plus `cd frontend && npm run build`
   if any `frontend/` file changed. Record the exact summary lines.
7. Out-of-scope findings: append to `docs/PROJECT_STATE.md` → Backlog (file, evidence, severity). Don't fix them.
8. Report: files changed, tests added, command results (verbatim summary), anything UNVERIFIED.
   Then suggest `review-changes` and, after review, `complete-phase`.

## Never
- Run migrations, install packages, push, or commit unless the user asks.
- Weaken or skip existing tests to make the suite pass.
- Claim a check passed that you did not run.
