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

## ADR-007 — Ownership encoded in user-scoped vector keys; no legacy fallback
2026-11-02 · Accepted (Phase 02)
- **Context:** Uploads (`upload_<md5>`) and pasted lectures (`lecture_paste_<md5>`) were global collections readable
  by any user who knew the id; the mentor model could pick any `persist_key`.
- **Decision:** Owner id is part of the key (`upload_<uid>_<md5>`, `lecture_<uid>_paste_<hex11>`), built server-side
  from the JWT; ids are format-validated; the mentor tool and MCP server cannot choose a collection. Legacy
  collections are kept but never read (no fallback, no migration). A rollback build must keep these protections or
  disable the affected endpoints (see the Phase 02 plan, *Rollback*).
- **Consequences:** No schema change. Users re-upload PDFs / re-paste transcripts processed before Phase 02.
  Legacy collections need a later cleanup (backlog).

## ADR-008 — `UserFacingError` is the only displayable error
2026-11-02 · Accepted (Phase 02)
- **Context:** Routes returned `str(e)` for any `ValueError` and `f"... failed: {e}"` on 500; jobs stored `str(e)`.
- **Decision:** `src/core/errors.py:UserFacingError` (fixed strings only) → HTTP 400 / job error text. Every other
  exception, including plain `ValueError`, → 500 / `GENERIC_ERROR`, logged with `describe_exception` only.
- **Consequences:** Unexpected failures show a generic message; intended user messages are raised as
  `UserFacingError` at their source.

## ADR-009 — Exact upload allow-list and a 1 MiB body limit for everything else
2026-11-02 · Accepted (Phase 02)
- **Context:** The upload-size middleware matched any path containing "upload" and trusted `Content-Length`.
- **Decision:** Only `POST /api/v1/upload/process` and `POST /api/v1/pyq/bank/upload` get `max_upload_mb`; every
  other write body gets `max_request_body_kb` (1 MiB). Limits count received bytes. Chat history is trimmed to 20
  messages server-side (before validation) and client-side.
- **Consequences:** Long chats keep working; oversized JSON bodies get 413. A new upload endpoint must be added to
  `UPLOAD_ENDPOINTS` explicitly.

## ADR-010 — `/auth/refresh` is outside the dedicated auth rate limit
2026-11-02 · Accepted (Phase 02 review amendment, approved by the user)
- **Context:** Phase 02 R5 originally put `/auth/refresh` in the 10-per-5-minute per-IP auth budget. Review found
  that every open tab refreshes about every 30 minutes, so students behind one shared IP (coaching-centre Wi-Fi,
  colleges, mobile CGNAT) would exhaust the budget for each other; the frontend treats a 429 on refresh as transient.
- **Decision:** The dedicated budget covers only login, register, forgot-password, reset-password, verify-email and
  resend-verification. `/auth/refresh` stays under the global per-IP limiter only. Refresh tokens are random,
  rotating and revocable, so they cannot be guessed.
- **Consequences:** Refresh abuse is bounded by the global limiter (100/60 s per IP by default). Credential guessing
  and email-sending endpoints keep the strict budget.
