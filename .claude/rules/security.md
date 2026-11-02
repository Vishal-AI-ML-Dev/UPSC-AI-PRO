# Security rules

## Secrets
- Never read, print, copy or edit `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key`, cookie exports,
  `*.sqlite` / `*.db`. Enforced by `.claude/settings.json` deny rules and `.claude/hooks/protect_paths.py`.
- New settings go in `src/core/config.py` with an empty/safe default and a placeholder in `.env.example`
  (ask before editing `.env.example`). Never commit a real value.
- Never log tokens, passwords, reset/verification links, API keys, emails or user message text.

## Authentication
- Protected routes use `Depends(get_current_user)` (`src/api/deps.py`); it validates the JWT and the DB
  single-session `sid`. Do not add alternative auth paths.
- Admin-only data uses `Depends(require_admin)` (`src/api/admin_access.py`).
- Auth DB failures fail closed (503), never open.

## Authorization (object level) — at EVERY boundary
- DB: every query for user-owned data filters by `user["id"]` (pattern: `src/core/history.py:get_conversation`,
  `src/core/job_queue.py:get_job`).
- Vector store: a request may only read collections it is entitled to — global corpora (mentor KB, NCERT),
  or collections owned by the caller. Never let the client or the **model** choose an arbitrary `persist_key`.
  Pattern (Phase 02, ADR-007): build owned keys server-side from `user["id"]` — `_upload_key` in
  `src/agents/upload/graph.py`, `_lecture_key` in `src/agents/lecture/graph.py`, `_bank_key` in
  `src/agents/pyq/graph.py`; validate client ids by format (`PDF_HASH_PATTERN`, `VIDEO_ID_PATTERN` in `src/schemas.py`);
  model-facing tools search the mentor KB only (`mentor_kb_search` in `src/graph/tools.py`); never fall back to legacy
  global keys.
- Checkpoints / cache: keys must include the authenticated user (pattern: `scoped_thread_id` in
  `src/api/routes/chat.py`, `_scope_part` in `src/core/response_cache.py`).

## Untrusted input
- Fence uploads, transcripts, web results and KB text with `harden_untrusted()` before they reach a prompt.
- Bound every free-text field (`Field(max_length=...)`) and every uploaded file size; don't rely only on
  `Content-Length`.
- Validate file type by content as well as extension when touching upload code.
- Model output rendered in the browser goes through sanitized Markdown or server-side `html.escape`.
- No user-controlled outbound URLs (SSRF); `src/core/web_search.py` uses fixed endpoints — keep it that way.

## Errors
- HTTP responses carry fixed, user-facing messages only; no stack traces, paths, SQL or provider bodies.

## Reviews
- Any change touching auth, ownership, uploads, prompts or the vector store needs a `security-reviewer`
  agent pass before the phase is closed.
