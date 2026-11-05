# Security

A high-level description of the security controls in UPSC AI Pro and the risks that remain. Exact secrets, tokens,
connection strings and internal identifiers are deliberately not published. Verified against source on 2026-11-05; "tested"
means covered by the offline test suite, not by a penetration test or an external audit (none has been done).

## Authentication
- **JWT access tokens** (HS256, 30-minute lifetime) validated on every protected request by `get_current_user`
  (`src/api/deps.py`). In production the app refuses to start with a missing or forgeable JWT secret.
- **Single active session:** the token carries a session id (`sid`) that must equal the user's `active_session_id` in the
  database, checked **per request**. A new login supersedes older sessions. If the lookup fails, the request fails closed (`503`).
- **Refresh-token rotation:** refresh tokens are opaque random strings stored only as SHA-256 hashes; each refresh revokes the old
  token and issues a new one, and logout revokes server-side (`src/core/refresh_tokens.py`).
- **Passwords:** bcrypt. Email-verification and password-reset tokens are stored hashed. Outside production, email verification
  is switched off with a warning if SMTP is absent; in production, email flows without SMTP return `503` rather than pretending to succeed.
- **Streams:** `guard_stream` re-checks the session during long streaming responses and stops delivery if it was superseded.

## Authorization (object level)
- **Database:** every query on user-owned data filters by the authenticated user id (conversations, jobs, plans, evaluation
  attempts). Another user's resource returns `404`.
- **Vector collections:** collection keys are built **server-side** from the authenticated user id (`upload_<user>_<md5>`,
  `lecture_<user>_paste_<id>`, `pyqbank_<user>`). Client-sent ids are validated by format. The model-facing
  `knowledge_base_search` tool can only search the mentor KB: it exposes no collection argument and ignores extras
  (ADR-007). Legacy collections without an owner are never read.
- **Checkpoints and cache:** the LangGraph thread id is a hash of the user id and the client label (`scoped_thread_id`);
  response-cache keys include the user. A guessed thread id cannot address another user's state.
- **Admin:** dashboards require `require_admin`.

## Untrusted input and prompt injection
- **Fencing:** uploads, transcripts, web results, KB text, feed text, student answers, profile fields and metadata are wrapped by
  `harden_untrusted()` (`src/core/prompt_safety.py`), which neutralises spoofed fence markers (including nested, Unicode-lookalike,
  stuck-character and decoy variants found during review) before the text reaches a prompt.
- **System prompts** state that instructions inside messages, documents or web results cannot change the UPSC-only scope.
  This is defence in depth; a prompt is not a guarantee.
- **Scope gate:** `src/core/scope_gate.py` refuses clearly off-topic requests and answers greetings without any LLM call. It is
  deterministic, ignores conversation history, and deliberately lets ambiguous messages through. One known gap: "write a song/poem about X"
  is allowed by the gate and relies on the mentor prompt.
- **Allow-lists and bounds:** lecture medium and evaluation subjects are allow-listed; evaluator scores are clamped to the valid range.
- **Evidence:** the Phase 07 offline eval found 21 prompt-construction failures on pre-fix code and 0 afterwards (60 cases, 1 known gap).
  These are **deterministic prompt-construction checks and scripted-model tests**; no live-model safety evaluation has been run
  ([EVALUATION.md](EVALUATION.md)).
- **Router:** the supervisor sees the student's text unfenced, but its output is limited to four allowed values; accepted by design.

## Input and upload limits
- Pydantic `Field(max_length=...)` on free-text fields (e.g. chat question 4,000; transcript 200,000; PYQ paste 50,000; ids 100).
- A 1 MiB request-body limit for JSON; uploads limited to **50 MB** (`MAX_UPLOAD_MB`) enforced on `Content-Length` **and** on bytes
  actually received, returning `413`.
- PDF uploads are validated by content, not only by extension. Allow-listed NCERT paths are resolved against a catalog; object keys
  from clients are never used directly.

## Rate limiting
Global 100 requests / 60 s per client IP and a stricter 10 / 300 s budget for credential and email endpoints; `429` with `Retry-After`.
The limiter reads the real client IP behind Render's proxy via `--proxy-headers` and a restricted `FORWARDED_ALLOW_IPS`. Counters
are per process unless Upstash is configured.

## CORS and security middleware
- CORS is an explicit origin allow-list from `CORS_ORIGINS`; production origins are a Render environment value (not in the repository).
- Response headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS, and a Content-Security-Policy
  (relaxed only on the interactive docs pages).
- Order: `RequestId → HttpMetrics → MaxUploadSize → RateLimit → Timeout(90 s) → SecurityHeaders → CORS`.
- Unhandled errors return a fixed JSON message; exception text, paths and SQL are not returned. Earlier phases moved touched code to class/status
  summaries instead of provider messages (ADR-008). A broader log-privacy sweep and its dedicated tests are **unpublished Phase 09 work (planned, not part of this release)**; a repo-wide guarantee that logs never contain emails or message text is **not claimed**.

## Reliability controls
- **Provider fallback:** Gemini → Groq (`ENABLE_PROVIDER_FALLBACK`). 45-second per-call timeout; a timeout ends the request.
- **Circuit breaker:** `src/core/circuit_breaker.py` (closed → open → half-open) wraps the Upstash REST calls; optional dependencies fail open.
- **Job safety:** heartbeat lease, conditional status transitions and a 900-second deadline (`src/core/job_queue.py`).
- **Secrets handling:** configuration via environment (`pydantic-settings`); `render.yaml` marks secrets `sync: false`; `.env` is git-ignored.

## Error monitoring status
A Sentry integration (`src/core/error_monitoring.py`) is implemented in the published code (unhandled errors only, no default PII, no performance tracing) but is **not active**: no DSN is configured. Additional privacy hardening (never sending exception messages) exists only as **unpublished, owner-pending Phase 09 work** and is not claimed here. Langfuse tracing code exists; activation in production is
UNVERIFIED. There is **no Prometheus/metrics export**; in-process HTTP metrics feed an admin dashboard only.

## Known remaining risks
| Risk | Severity | Note |
|---|---|---|
| Access and refresh tokens stored in browser `localStorage` | Medium | An XSS bug would expose them; sanitized Markdown and escaped HTML reduce, not remove, this |
| Account enumeration: register reports "Email already registered"; login skips bcrypt for unknown emails | Medium | Timing and message differences |
| No production backup has been taken; Supabase native backups not verified | Medium (accepted by owner) | Backup/restore is **planned / deferred** (no runbook in this release) |
| Real-model prompt-injection and scope behaviour never tested live | Medium | T3 not run |
| No per-user active-job cap; upload bytes held in memory per request | Medium | Resource-exhaustion vector for authenticated users |
| 24-hour semantic cache can serve stale time-sensitive answers | Medium | Mentor chat only, if cache is configured |
| Public `/docs` and `/openapi.json` in production | Low | Reveals the API surface, not data |
| Threadpool shared between auth, DB and long LLM calls | Medium | Queueing under load |
| Tests are offline; no external penetration test | — | Do not read "tested" as "audited" |
