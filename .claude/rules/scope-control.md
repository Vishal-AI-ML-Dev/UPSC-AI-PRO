# Scope-control rules

## One phase at a time
- Work only on the phase marked **active** in `docs/PROJECT_STATE.md`.
- Edit only files listed in the approved plan (`.claude/plans/phase-NN-*.md`). If another file must change,
  stop, explain why, and ask to amend the plan.
- No implementation before the spec and plan are approved by the user.

## Preserve the product
- The 8 workflows (AI Mentor, NCERT Study, PYQ + Personal PYQ Bank, Answer Evaluator, Current Affairs,
  Study Planner, Lecture Learning, PDF/Document Chat) must keep working after every phase. Check the workflow
  table in `docs/PROJECT_STATE.md` at phase close.
- No rewrites, no framework swaps, no directory moves (root `src/` stays — ADR-001).
- Preserve public API paths and response shapes unless the spec explicitly changes them, and then update
  `frontend/src/lib/api.ts` in the same phase.

## What to do with findings outside scope
- Do not fix them. Add a line to `docs/PROJECT_STATE.md` → **Backlog** with file, evidence and severity.
- Exception: a CRITICAL security issue — stop and tell the user immediately.

## Changes that always need explicit user approval
- New/removed dependencies (`pyproject.toml`, `uv.lock`, `frontend/package*.json`).
- Database schema or migrations; running migrations is never done by Claude.
- CI (`.github/`), Docker, `render.yaml`, `alembic.ini`, `.claude/settings.json`.
- Deleting files, even dead code.
- Commits, branches, pushes.

## Honesty
- Report what was actually verified. Mark anything not run or not checked as UNVERIFIED.
- Do not mark a phase complete with failing or skipped acceptance criteria; list them instead.
