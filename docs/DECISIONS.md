# Architecture decision records

Format: `ADR-NNN — Title` · Date · Status (Accepted / Superseded by ADR-x) · Context · Decision · Consequences.
Append new ADRs; never rewrite accepted ones — supersede them.

---

## ADR-001 — Keep the root `src/` backend layout
2026-11-02 · Accepted
- **Context:** The backend lives in root `src/` (`api/`, `agents/`, `graph/`, `core/`); Dockerfile, Render,
  Alembic (`prepend_sys_path = .`), CI and tests import `src.*`.
- **Decision:** Do not move the backend into a `backend/` directory. New code goes into the existing layers.
- **Consequences:** No import churn; deployment configs stay valid. Layering is enforced by
  `.claude/rules/backend.md` rather than by directory names.

## ADR-002 — Spec-driven, phase-by-phase rebuild
2026-11-02 · Accepted
- **Context:** The audit found the docs overstate the implementation; changes must be incremental and verified.
- **Decision:** Every phase has an approved spec (`.claude/specs/`) and plan (`.claude/plans/`) before code.
  Only plan-listed files change. Each phase closes with a report in `docs/phase-reports/`.
- **Consequences:** Slower start, but every change is traceable, reviewable and preserves the 8 workflows.

## ADR-003 — `src/eval/` stays until the evals phase
2026-11-02 · Accepted
- **Context:** CI runs `python -m src.eval.llm_eval` and `src.eval.retrieval_eval`; moving them now would change CI.
- **Decision:** Create an empty `evals/` skeleton in Phase 00; migrate in Phase 08 together with CI updates.
- **Consequences:** Two eval locations temporarily; `evals/README.md` points to the live one.

## ADR-004 — Version the `.claude/` workspace
2026-11-02 · Accepted
- **Context:** `.gitignore` ignored all of `.claude/`, so rules, specs, skills, agents and hooks would be lost on clone.
- **Decision:** Ignore only `.claude/settings.local.json` and `.claude/**/*.local.*`; commit the rest.
- **Consequences:** Shared, reviewable Claude configuration; personal overrides stay local.

## ADR-005 — Safety hooks are stdlib Python, formatting is best-effort
2026-11-02 · Accepted
- **Context:** Windows dev machine (Git Bash + PowerShell); `uv` and `python` available; no new dependencies allowed.
- **Decision:** `protect_paths.py` blocks sensitive paths (PreToolUse). `format_changed.py` auto-formats only the
  edited file (ruff / prettier if already installed). `lint_changed.py` reports ruff findings without blocking.
- **Consequences:** Legacy files may get reformatted the first time they are edited; keep such diffs in the same
  phase and mention them in the phase report.

## ADR-006 — Keep known dead code until the operations phase
2026-11-02 · Accepted
- **Context:** Phase 01 confirmed three dead/stray files: `src/api/routes/mentor.py` (router not registered in
  `src/api/main.py`, but still imported by `tests/test_stream_citations.py`), `src/agents/upload/upload.py`
  (unmounted duplicate upload router without auth), and `src/index.css` (stray CSS in the backend package).
  Deleting files needs explicit user approval (`.claude/rules/scope-control.md`); the user chose to keep them.
- **Decision:** Preserve all three unchanged in Phase 01 — no deletion or relocation. `ARCHITECTURE.md` labels
  them as dead code. Removal is reconsidered in Phase 09 (operations), together with re-pointing or retiring the
  `test_stream_citations.py` cases that import `mentor.py`.
- **Consequences:** The files stay importable and must not be registered by accident; reviewers treat them as
  out of scope. No runtime change.
