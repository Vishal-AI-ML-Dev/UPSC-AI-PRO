# Plan — Phase 00 Engineering workspace setup

Status: DONE (2026-11-02)  ·  Approved by user in session on 2026-11-02.

## Goal
Create the Claude Code / spec-driven workspace so fresh sessions start from verified facts and explicit scope
rules. Documentation and configuration only — no application code, dependencies, migrations or data changes.

## Files touched
| File | Change |
|---|---|
| `.gitignore` | `.claude/` → ignore only `.claude/settings.local.json` and `.claude/**/*.local.*` |
| `CLAUDE.md` | new — concise project guide |
| `.claude/settings.json`, `.claude/settings.local.json` | new — permissions + hooks; personal overrides (gitignored) |
| `.claude/hooks/{protect_paths,format_changed,lint_changed}.py` | new — stdlib-only hooks |
| `.claude/rules/{backend,frontend,testing,security,scope-control}.md` | new |
| `.claude/specs/{README,_TEMPLATE}.md`, `phase-01..09-*.md` | new — templates + roadmap stubs |
| `.claude/plans/{README,_TEMPLATE,phase-00-workspace}.md` | new |
| `.claude/skills/{implement-phase,write-tests,review-changes,complete-phase}/SKILL.md` | new |
| `.claude/agents/{test-engineer,code-quality-reviewer,security-reviewer}.md` | new |
| `.claude/commands/{start-phase,phase-status,handoff}.md` | new |
| `docs/{PROJECT_STATE,DECISIONS}.md`, `docs/audit/2026-11-02-architecture-audit.md`, `docs/phase-reports/phase-00-report.md` | new |
| `evals/README.md`, `evals/{datasets,runners,evaluators,reports}/.gitkeep` | new — skeleton |

## Verification
- `git status --porcelain` shows only the files above.
- `git diff --stat -- src frontend tests migrations` is empty.
- `git check-ignore .claude/settings.json` → not ignored; `.claude/settings.local.json` → ignored.
- `.claude/settings.json` parses as JSON.
- `protect_paths.py` blocks `.env` (exit 2) and allows `src/core/llm.py` (exit 0).
- Every path cited in `CLAUDE.md` exists.

## Rollback
Delete the new files and restore `.gitignore` line `.claude/`.
