# Phase 02 — Security: document ownership & abuse limits

Status: DONE (2026-11-02; revision 4 + review amendment) — report `docs/phase-reports/phase-02-report.md`  ·  Owner: Vishal-AI-ML-Dev  ·  Created: 2026-11-02
Related risks: `docs/PROJECT_STATE.md` open risks #1, #5, #6, #8, #14 · audit sections M (findings 1–8), K

## 1. Goal
A user can only read document collections that are global (mentor KB, NCERT, public YouTube lectures) or that
they created themselves — over HTTP **and** through the AI Mentor's tools. Close the confirmed abuse gaps
(no auth rate limit, unbounded inputs and request bodies, Content-Length-only upload limit, extension-only
PDF check, raw exception text in responses/jobs/logs, emails/query text in logs, silent bcrypt truncation)
without breaking any of the 8 workflows.

## 2. Non-goals
- No new DB table, no schema change, no Alembic migration (ownership is encoded in the vector key — R1, R3).
- **No migration, backfill, fallback or deletion of existing data.** Legacy `upload_<hash>` and
  `lecture_paste_<hash>` collections stay in Qdrant untouched and are no longer read (R1, R3).
- No change to JWT/session handling, `get_current_user`, or token storage in `localStorage` (risk #7).
- Account enumeration via `POST /auth/register` ("Email already registered") — rate-limited here only;
  message unchanged (backlog).
- Blocking work in `async def` handlers (Phase 03), job durability (Phase 04), public `/docs` (Phase 09).
- A repo-wide sweep of `logger.*(f"... {e}")` outside the files this phase touches (backlog).
- MCP server hardening **only while** the verification in R12 shows no network exposure (see R12).

## 3. Current behaviour (verified from source, 2026-11-02)
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Upload vector key | `src/agents/upload/graph.py:process_upload` (l.213) | `make_persist_key("upload", pdf_hash)` — one global collection per file MD5; no user in the key |
| Upload chat | `src/api/routes/upload.py:chat_upload`, `graph.py:ask_upload` (l.262) | Any authenticated user who sends any `pdf_hash` chats over that collection. No owner check; `pdf_hash` not format-validated |
| Upload result cache | `graph.py:_processed_cache` (l.22), `extract_pdf_text` (l.39), `process_upload` (l.156) | Module-level dict keyed by MD5 only, unbounded in entries **and bytes**; each entry holds the full extracted text + notes. User B uploading the same bytes gets A's cached result and **skips** `create_vector_store`, so B has no collection of their own |
| Cached text use | `routes/upload.py:_work` | The route returns only `hash`, `book_info`, `notes` — the cached `text` is never returned to a client |
| PYQ bank key | `src/agents/pyq/graph.py:_bank_key` (l.466) | `pyqbank_<user_id>` built server-side from the JWT user; `/pyq/bank/{upload,generate,status,clear}` all pass `current_user["id"]`. No explicit cross-user regression test exists |
| Pasted lecture key | `src/agents/lecture/graph.py:process_lecture_from_text` (l.485), `build_lecture_chat_index` (l.430), `ask_lecture` (l.536) | `video_id = "paste_" + md5(transcript)[:11]`; key `lecture_paste_…` is global; `/lecture/chat` accepts any `video_id` (no format check). YouTube keys `lecture_<video_id>` are public content |
| Key collision | `src/core/vector_store.py:make_persist_key` | Joins parts with `_`, lower-cases. An unvalidated client id such as `<uid>_paste_<hash>` would produce the same key as a user-scoped key — ids must be format-validated |
| Mentor KB tool | `src/graph/tools.py:knowledge_base_tool_fn` (l.180), `get_structured_tools` (l.419), `run_tool_calls` (l.250) | The **model** may pass any `persist_key`; loaded unchecked. `run_tool_calls` forwards model args via `fn(**args)`, so removing the arg from the schema alone is not enough. Tool exceptions are returned to the model as `ERROR: tool '…' failed: {exc}` |
| MCP server | `src/mcp_server.py` | Exposes `knowledge_base_search(query, persist_key)` with no auth. `mount_mcp(app)` mounts SSE only if `settings.mcp_enabled`; **`mount_mcp` is not called anywhere in `src/`**; `Settings` has no `mcp_enabled`/`mcp_transport` field and uses `extra="ignore"` (`src/core/config.py:22`), so env vars cannot enable it. `run()` defaults to stdio. Not started by `Dockerfile`, `render.yaml` or `docker-compose.yml`. Live Render service settings: UNVERIFIED |
| Auth rate limit | `src/core/config.py:178-179`, `src/api/rate_limit.py` | `auth_rate_limit_requests=10` / `auth_rate_limit_period=300` never read. Only the global per-IP limiter (100/60 s); tests raise it to 100000 (`tests/conftest.py:43`) |
| Redis key | `src/api/rate_limit_core.py:_check_redis` | Key `rl:{key}:{window}` — a second limiter must namespace its key |
| Client IP | `src/api/rate_limit.py:37` | Uses `request.client.host`; the app never parses `X-Forwarded-For` itself. uvicorn `--proxy-headers` (`Dockerfile:76`, `render.yaml:33`) rewrites the client from XFF only for peers in `FORWARDED_ALLOW_IPS` (default `127.0.0.1`; Render: private ranges, `render.yaml:51-52`, comments warn never `*`). No test covers a forged header |
| Free-text bounds | `src/schemas.py` (`ChatMessage.content`, `ChatRequest/MentorRequest/NCERTChatRequest/LectureChatRequest/UploadChatRequest.question`, `LectureChatRequest.video_id`, `LectureTextRequest.transcript`, `QuestionGenRequest.topic`, `ParseRequest.text`, `BankGenRequest.topic`, `EditorialRequest.topic`); `routes/chat.py:AgentChatRequest`; `routes/auth.py` request models | No `max_length`; `chat_history` lists unbounded. Evaluator/Planner already bounded. Prompt use of history already trimmed to 6×600 chars (`src/core/prompt_safety.py:57`) |
| JSON body size | `src/api/upload_limit.py:dispatch` | Classifies by substring: `"upload" in request.url.path`, so **every** path containing "upload" (including the JSON endpoint `/upload/chat`) gets the 50 MB file limit, by `Content-Length` only; all other bodies are unbounded. Only two endpoints actually take binary files (`UploadFile`): `POST /api/v1/upload/process` and `POST /api/v1/pyq/bank/upload` |
| Frontend history | `frontend/src/features/upload/Upload.tsx:73`, `frontend/src/features/lecture/Lecture.tsx:92` | Send the **entire** chat history on every turn, so body size grows without bound in long sessions |
| Upload size | `src/api/upload_limit.py:MaxUploadSizeMiddleware` | `Content-Length` only; chunked or missing header is not limited. Routes `await file.read()` whole files |
| Upload type | `routes/upload.py:29`, `routes/pyq.py:77` | Extension only; no signature or parser check before enqueue. `upload.py` is case-sensitive (`.PDF` rejected), `pyq.py` is not. Parsing happens later in `extract_pdf_text` via `pypdf.PdfReader` |
| Exception leaks (HTTP) | `routes/upload.py:49,51`, `routes/pyq.py:92,94`, `routes/lecture.py:68,70,106,108`, `routes/auth.py:136,254` | `except ValueError → detail=str(e)` (any ValueError, incl. internal ones such as `"yt-dlp not installed. Run: pip install yt-dlp"`, `lecture/graph.py:104`) and `detail=f"... failed: {str(e)}"` on 500 |
| Safe-error precedent | `routes/ncert.py:96-98` | `NcertPdfError` is a dedicated class of fixed user-facing strings — the pattern R10 generalises |
| Exception leak (jobs) | `src/core/job_queue.py:110-111` | `logger.exception` (traceback incl. message) and `error=str(e)[:2000]` for every exception; returned by `GET /jobs/{id}`; shown by the frontend (`api.ts:619`) |
| `describe_exception` | `src/core/llm.py:79` | Emits only class names, numeric status, an UPPER_CASE status token and `file:line:function` — never messages. No test asserts that for a `ValueError` message |
| Raw `{e}` in logs | `upload/graph.py` (l.67,134,201,297), `users.py:34`, `auth.py:121,241`, `tools.py:198,216,289` | Exception text logged verbatim |
| PII in logs | `src/core/users.py:62`, `auth.py:121,241`, `src/core/observability.py:192-198` | Emails on user creation / email failure; first 80 chars of the user's query |
| Password hashing | `src/core/users.py:16-21` | bcrypt; input silently truncated to 72 UTF-8 bytes. Two passwords sharing a 72-byte prefix are equivalent; no maximum is enforced at register/reset |

## 4. Requirements
- **R1 Upload ownership.** Key = `make_persist_key("upload", user_id, pdf_hash)`. `process_upload` and
  `ask_upload` take a required keyword-only `user_id` (empty → refuse). Routes pass `current_user["id"]`.
  `pdf_hash` must match `^[0-9a-f]{32}$` (422). **No fallback to the legacy `upload_<hash>` key**, ever.
  Same file uploaded by two users → two independent collections.
- **R2 Upload cache: per-user, bounded entries and bytes.** Cache key `(user_id, pdf_hash)`; LRU of at most
  32 entries **and** at most 4 MiB total (UTF-8 bytes of notes + serialised book_info). The extracted
  **text is no longer cached** (never returned to clients; a same-user hit means that user's collection was
  already built). On a hit, `vector_store_exists(key)` is checked; if the collection is gone, the upload is
  reprocessed. An entry larger than the budget is not cached. `extract_pdf_text` stops reading the cache
  (signature unchanged — PYQ bank keeps calling it).
- **R3 Pasted-transcript ownership.** `paste_*` ids map to `make_persist_key("lecture", user_id, video_id)`;
  YouTube ids stay `lecture_<video_id>`. `ask_lecture` and `build_lecture_chat_index` take `user_id`; a paste
  id without a user is not indexed/read. `LectureChatRequest.video_id` must match
  `^(paste_[0-9a-f]{11}|[A-Za-z0-9_-]{11})$` (422) to prevent key collisions. **No fallback to legacy
  `lecture_paste_<hash>`**: pasted lectures processed before this phase must be re-pasted to chat with them.
- **R4 Mentor tool cannot choose a collection.** The model-facing `knowledge_base_search` has no
  `persist_key`; the agent registry entry ignores any `persist_key` the model emits and searches the mentor
  KB only. Tool exceptions reach the model as a fixed `ERROR: tool '<name>' failed.` (logged with
  `describe_exception`). `knowledge_base_tool_fn(query, persist_key)` stays for the MCP server (R12).
- **R5 Auth rate limit.** POSTs to `/api/v1/auth/{login,register,forgot-password,reset-password,
  verify-email,resend-verification}` are limited per client IP to `auth_rate_limit_requests` per
  `auth_rate_limit_period` (default 10 / 300 s), separate counter, Redis key `auth:{ip}`, 429 + `Retry-After`.
  The global limiter still applies. **`/auth/refresh` is excluded from this dedicated budget** (policy change
  approved by the user 2026-11-02 after review, ADR-010): refresh tokens are random, rotating and revocable,
  and every open tab refreshes about every 30 min, so students behind one shared IP would otherwise lock each
  other out. Refresh stays under the global per-IP limiter like every other route.
- **R6 Field bounds.** Questions ≤ 4,000 chars; topics ≤ 200; `thread_id` ≤ 100; transcript ≤ 200,000;
  parse text ≤ 50,000; auth email ≤ 254, name ≤ 100, tokens ≤ 512 → 422. `chat_history` is **trimmed, not
  rejected**: last 20 messages kept, each cut to 4,000 chars. **Validation order is mandatory:** trimming and
  truncation run in Pydantic `BeforeValidator`s, *before* any length constraint. `ChatMessage.content` =
  `Annotated[str, BeforeValidator(truncate_to_4000), Field(max_length=4000)]` (the `max_length` is only an
  invariant that can never fire for strings); the history list = `Annotated[list[ChatMessage],
  BeforeValidator(keep_last_20), Field(max_length=20)]`. A 10,000-char message becomes 4,000 chars, never 422.
  Non-string `content` still fails type validation (422) as today.
- **R7 Request-body limit (non-upload).** New setting `max_request_body_kb` (default **1024 KiB = 1 MiB**,
  env `MAX_REQUEST_BODY_KB`). **Upload paths are an explicit allow-list of exact `(method, path)` pairs**:
  only the binary PDF endpoints `("POST", "/api/v1/upload/process")` and `("POST", "/api/v1/pyq/bank/upload")`.
  No substring or prefix matching: `/upload/chat`, and any future path that merely contains "upload", uses the
  JSON limit. Every other `POST/PUT/PATCH` is rejected with 413 when its body exceeds the limit — checked
  by `Content-Length` and by counting bytes actually received (chunked bodies too), before JSON parsing.
  Rationale: the largest legitimate body is a 200,000-char transcript (≤ ~600 KB UTF-8 for Devanagari).
  To keep long chats working, `Upload.tsx` and `Lecture.tsx` send only the last 20 history messages (the
  server keeps 20 anyway), so chat bodies stay roughly constant in size however long the session runs.
- **R8 Upload size by bytes received.** The two allow-listed upload paths (R7), and only those, are limited to `max_upload_mb` by `Content-Length`
  **and** received-byte counting (413), with or without the header.
- **R9 PDF validation.** Before enqueue/processing: extension compared case-insensitively; signature
  `%PDF-` must appear within the **first 1,024 bytes** (the ISO 32000 reader tolerance — not only at offset 0);
  then the file must open with the existing parser `pypdf.PdfReader`, have ≥ 1 page and not be encrypted.
  Parsing runs off the event loop (`starlette.concurrency.run_in_threadpool`). Failures → 400 with a fixed
  message. Applies to `/upload/process` and `/pyq/bank/upload`.
- **R10 Safe errors only.** New `src/core/errors.py: UserFacingError(ValueError)` — the **only** exception
  whose message may be shown to users (plus the existing, already-fixed-string `NcertPdfError`, unchanged).
  - **Conversion, not broad catching:** every intended user-input error is raised as `UserFacingError` at its
    source — the fixed messages in `upload/graph.py` (l.55, 68, 167), `lecture/graph.py` (l.97, 155, 163, 168,
    195, 315, 349, 461, 484), `users.py` (l.49, 51, 53, 81 and the new 72-byte check) and the new
    `read_pdf_upload` checks. No route catches plain `ValueError` any more.
  - **Status contract (HTTP):** `UserFacingError` → **400** with its message as `detail`. Any other exception
    — explicitly including plain `ValueError` and `RuntimeError` — → **500** with
    `detail = GENERIC_ERROR = "Processing failed. Please try again."`. This replaces today's
    `except ValueError → 400 str(e)` in `upload.py`, `pyq.py`, `lecture.py` and `auth.py` (register/reset).
    Pydantic validation errors stay 422 and size limits stay 413 (unchanged framework behaviour).
  - **Jobs:** `error` = the message for `UserFacingError`, otherwise `GENERIC_ERROR`; `status="error"` in both cases.
  - **Logs:** unexpected exceptions are logged with `describe_exception(e)` (class names + `file:line` only);
    `job_queue` stops using `logger.exception`. In every file this phase touches, no log line formats `{e}`,
    `str(e)` or a traceback. **No raw exception text appears in HTTP responses, job errors or logs.**
- **R11 No PII / query text in logs.** User ids instead of emails; retrieval metrics log `q_len`, not text.
- **R12 MCP exposure.** Deferral of MCP `persist_key` hardening to Phase 09 is **conditional** on verified
  non-exposure: (a) no code path mounts or starts the MCP server over a network transport in the deployed
  app, and (b) the user confirms the live Render service start command and environment do not run
  `python -m src.mcp_server` / `run("sse")` or mount `/mcp`. Phase 02 adds regression tests that fail if
  `/mcp` becomes reachable on the app or `mount_mcp` mounts with default settings. **Fail closed:** if (a) or
  (b) fails, **or (b) has not been verified** by the time implementation reaches plan step 12, Phase 02
  implements **R12-FC** before it can close: `mount_mcp` and `run()` refuse the network (`sse`) transport (stdio
  only), and the MCP `knowledge_base_search` ignores `persist_key` (global mentor KB only), with tests.
- **R13 Trusted proxy.** Client IP for both limiters comes only from `request.client.host`; the app never
  reads `X-Forwarded-For`/`Forwarded`. Proxy trust is uvicorn's `--proxy-headers` + `FORWARDED_ALLOW_IPS`
  (never `*`), documented in the phase report and `docs/PROJECT_STATE.md`.
- **R14 Password length vs bcrypt — two independent paths.**
  - **New passwords (register `RegisterRequest.password`, reset `ResetPasswordRequest.new_password`):**
    1. existing minimum, **unchanged**: fewer than 6 characters → 400 "Password must be at least 6 characters"
       (`src/core/users.py:50,80`, now raised as `UserFacingError`);
    2. coarse character bound: Pydantic `Field(max_length=128)` on those two fields only → 422 above 128 chars;
    3. bcrypt bound: more than 72 **UTF-8 bytes** → 400 `UserFacingError("Password must be at most 72 bytes
       (fewer if it uses non-English characters).")`.
    Steps 1 and 3 live in one helper, `_validate_new_password`, used only by `create_user` and `hash_new_password`.
  - **Login (`OAuth2PasswordRequestForm`):** does **not** use `_validate_new_password` and has **no** 128-char
    bound. Inputs up to 1,024 characters are verified exactly as today (`verify_password` → `_to_bytes`, which
    truncates to 72 bytes), so accounts whose original password exceeded 72 bytes keep signing in. A password
    over 1,024 characters (or a username over 254) → the normal 401 "Incorrect email or password" without
    calling bcrypt. No 422 and no distinct message on login.
  - Examples (new passwords): 72 × "a" = 72 B ok; 24 × "€" = 72 B ok; 25 × "€" = 75 B → 400; 70 × "a" + "€" =
    73 B → 400; "abcde" and 5 × "€" (15 B, 5 chars) → 400 by the unchanged minimum; 129 × "a" → 422.
- **R15 PYQ ownership preserved.** `_bank_key` stays server-side; an explicit cross-user regression test is added.

## 5. Affected workflows
| Workflow | Effect | How it stays working |
|---|---|---|
| PDF / Document Chat | Per-user keys; PDF validation; history trimmed client-side | Same API shapes. **PDFs uploaded before this phase must be re-uploaded** (legacy collections kept, not read) |
| PYQ + Personal PYQ Bank | Upload validation/size; error text | `_bank_key` unchanged; regression test added |
| AI Mentor | KB tool loses `persist_key`; body limit | No HTTP flow used it; mentor KB search unchanged |
| Lecture Learning | Paste ids per user; `video_id` validated; history trimmed client-side | YouTube path unchanged. **Transcripts pasted before this phase must be re-pasted to chat** (legacy collections kept, not read) |
| NCERT, Evaluator, Current Affairs, Planner | Field bounds + 1 MiB body limit | Limits far above real use |
| Auth | Rate limit; password ≤ 72 bytes on register/reset | Existing long passwords still log in; tests raise the limit in `conftest.py` |

## 6. Acceptance criteria (testable)
- [ ] AC1 User B calling `/upload/chat` with A's `pdf_hash` gets "Please upload and process the PDF first."; `load_vector_store` is only ever called with `upload_<B id>_<hash>` — never A's key and never the legacy `upload_<hash>`, even when the legacy key exists.
- [ ] AC2 A and B upload identical bytes → `create_vector_store` is called twice with two different per-user keys; B never receives A's cache entry.
- [ ] AC3 Cache never exceeds 32 entries or 4 MiB; an oversized entry is not cached; cached entries contain no extracted text; a same-user hit whose collection no longer exists reprocesses.
- [ ] AC3b (review fix) The cache is thread-safe: 8 threads doing put/get/discard on overlapping keys raise no error, keep ≤ 32 entries and tracked bytes == recomputed bytes; the cache lock is not held during PDF extraction or vector-store writes.
- [ ] AC4 Model call `knowledge_base_search{query, persist_key: "pyqbank_<A id>"}` through `run_tool_calls` / the tools node never calls `load_vector_store` with that key; the structured tool schema has no `persist_key`; a tool raising `RuntimeError("secret-path")` yields content without `secret-path`.
- [ ] AC5 User B cannot chat over A's pasted lecture: lookups use `lecture_<B id>_paste_<hash>` only, never the legacy `lecture_paste_<hash>`; `video_id="<A id>_paste_<hash>"` → 422; YouTube ids still use `lecture_<video_id>`.
- [ ] AC6 PYQ: after A builds a bank, B's `/pyq/bank/status`, `/bank/generate` and `/bank/clear` only touch `pyqbank_<B id>`; A's bank is untouched.
- [ ] AC7 The (N+1)-th POST from one IP within the window to any of the six limited auth endpoints → 429 + `Retry-After`; the six share one budget; non-auth routes are unaffected by the auth counter.
- [ ] AC7b `/auth/refresh` is not in `AUTH_LIMITED_PATHS`; 20 refreshes from one IP do not consume the auth budget (login still gets its full N); refresh still succeeds after the auth budget is exhausted; refresh is still limited by the global limiter.
- [ ] AC8 Forged `X-Forwarded-For` values do not change the limiter key: requests with different forged headers from one peer share one counter.
- [ ] AC9 Oversized fields → 422; a 25-message history (with a 10,000-char message) → 200, trimmed to 20 × ≤ 4,000.
- [ ] AC9b Validation-order regression: `ChatMessage(role="user", content="x" * 10_000)` validates with `len(content) == 4000`; a 25-item list validates to the **last** 20 items; the same payload via `POST /upload/chat`, `/lecture/chat` and `/ncert/chat` returns 200 (not 422) and the handler receives the truncated values.
- [ ] AC10 A non-upload JSON body > 1 MiB → 413, both with `Content-Length` and chunked; a 200,000-char Devanagari transcript body (< 1 MiB) is accepted.
- [ ] AC10b Path classification: a 2 MiB body to `POST /api/v1/upload/chat` → 413 (JSON limit, although the path contains "upload"); a 2 MiB valid PDF to `POST /api/v1/upload/process` and to `POST /api/v1/pyq/bank/upload` → not 413; a 2 MiB body to an unregistered path containing "upload" (e.g. `POST /api/v1/upload/anything`) → 413; unit test: the classifier is an exact `(method, path)` match (`GET` or a trailing-suffix variant of an upload path is not an upload).
- [ ] AC11 A body over `max_upload_mb` to each of the two upload endpoints, with and without `Content-Length` → 413.
- [ ] AC12 PDF validation: non-PDF renamed `.pdf` → 400; `.PDF` accepted; a valid PDF with leading bytes before `%PDF-` (within 1,024) accepted; signature-correct but unparseable/encrypted/zero-page file → 400.
- [ ] AC13 For each of `/upload/process?sync=true`, `/pyq/bank/upload?sync=true`, `/lecture/process?sync=true` and `/lecture/process-text?sync=true` (work function patched to raise):
  - `UserFacingError("Shown message")` → status **400**, `detail == "Shown message"`;
  - plain `ValueError("secret-path")` → status **500**, `detail == "Processing failed. Please try again."`;
  - `RuntimeError("secret-path")` → status **500**, `detail == "Processing failed. Please try again."`;
  - and for a background job with the same three exceptions: `status == "error"` and `error` equals `"Shown message"` / the generic message / the generic message respectively.
  - `/auth/register` and `/auth/reset-password` with `create_user` / `hash_new_password` patched to raise plain `ValueError("secret-path")` → **500** without `secret-path`; with a `UserFacingError` → 400 with its message.
  - `secret-path` appears in no response body, no job `error`, and no captured log record (AC14).
- [ ] AC14 `describe_exception(ValueError("secret-path"))` excludes `secret-path`; captured logs for those failures contain no `secret-path`.
- [ ] AC15 Captured logs for user creation, email-send failure and retrieval metrics contain no email address and no query text.
- [ ] AC16 New-password path (register **and** reset): 24 × "€" (72 B) and 72 × "a" → accepted; 25 × "€" (75 B) and 70 × "a" + "€" (73 B) → 400 with the 72-byte message; "abcde" and 5 × "€" → 400 with the **unchanged** "at least 6 characters" message; 129 × "a" → 422.
- [ ] AC16b Login path is independent: (i) an account whose stored hash was made (directly via `hash_password`) from a 100-ASCII-char / >72-byte password logs in with that full password → 200; (ii) a 200-char password (above the 128 register/reset bound, below 1,024) is processed by `verify_password` — login with the correct 200-char legacy password → 200, wrong → 401, never 422; (iii) a 1,025-char password → 401 "Incorrect email or password" and `verify_password` is **not** called (spy); (iv) login never calls `_validate_new_password` (spy) and register/reset never call the 1,024-char login check.
- [ ] AC17 MCP: `GET /mcp` and `/mcp/sse` on the app → 404; `mount_mcp(app)` with default settings returns `False`; `src/api/main.py` does not reference `mount_mcp`.
- [ ] AC18 Deployment check (R12b) recorded in the phase report. If Q5 is not confirmed, or exposure is found, R12-FC is implemented and its tests pass before close: `run("sse")` refuses, `mount_mcp` returns `False` even with `mcp_enabled=True`, and MCP `knowledge_base_search(query, persist_key="pyqbank_x")` never loads that key.
- [ ] All 8 workflows still pass their offline smoke checks (`tests/test_workflow_smoke.py`).
- [ ] `uv run pytest -q` green with no existing test deleted, skipped or weakened; ruff + compileall clean; `npm run build` and `npm run lint` green.
- [ ] `security-reviewer` agent pass with 0 BLOCKING findings.

## 7. Risks & mitigations
- **Legacy data unreachable** (uploads and pasted lectures) → stated in the phase report and release notes; data kept; cleanup is a backlog item; no fallback by design (a fallback would re-open the cross-user read).
- **Rate limit breaks the suite** (every `make_user` registers + verifies) → `conftest.py` raises `AUTH_RATE_LIMIT_REQUESTS`; the dedicated test lowers it.
- **New 413/422 regressions** → body limit 1 MiB vs ~600 KB worst legitimate body; history trimmed client- and server-side.
- **Pure-ASGI middleware rewrite** → tests with/without `Content-Length`, upload and non-upload paths.
- **Double PDF parse** (validation + job) → only opens the document (no text extraction); off the event loop.
- **Fewer error details / status change for users** (plain `ValueError`s now generic **500** instead of 400) → every intended user-input message is converted to `UserFacingError` at its source (R10 list) and keeps its 400; existing tests asserting those messages and 400s must still pass unchanged. Any existing test that turns out to depend on an unconverted plain `ValueError` → 400 is reported, not silently edited.
- **Rollback re-opens the hole.** The pre-Phase-02 code reads the legacy global keys (`upload_<hash>`,
  `lecture_paste_<hash>`) for any caller and lets the mentor model pick any `persist_key`. Those legacy
  collections are deliberately kept, so a plain rollback re-exposes them across users. Collections created after
  deployment (`upload_<uid>_<hash>`, `lecture_<uid>_paste_<hash>`) are invisible to the old code, so users also
  lose access to their new uploads and pastes. Mitigation: the plan's *Rollback* section **prohibits deploying
  any version that allows arbitrary collection access**; a rollback build must keep the upload and
  pasted-lecture ownership protections and either enforce a server-side mentor-KB-only allow-list or disable the
  Mentor endpoint.
- **Debuggability** (no traceback text in job logs) → `describe_exception` keeps class chain + `file:line`.
- **Test churn** — tests calling `ask_upload("q","h")`, `process_upload(b, name)`, `ask_lecture(q, vid)` or using `pdf_hash=h` / `video_id="v"` get `user_id=` and valid ids. Mechanical only; no assertion weakened.

## 8. Test plan (test-first: each new test must fail on current code, output recorded)
| Test | Covers | Pattern copied |
|---|---|---|
| `tests/test_upload_ownership.py` (new) | AC1, AC2, AC3, AC3b | `make_user`; `monkeypatch.setattr` on `load_vector_store` / `create_vector_store` / `vector_store_exists`; `tests/test_security_ownership.py` |
| `tests/test_lecture_ownership.py` (new) | AC5 | same |
| `tests/test_pyq_ownership.py` (new) | AC6 | same, on `src.agents.pyq.graph` vector helpers |
| `tests/test_tools.py` (extend) | AC4 | existing `run_tool_calls` tests |
| `tests/test_auth_rate_limit.py` (new) | AC7, AC7b, AC8 | `tests/test_rate_limiter.py`, `client` fixture with `headers={"X-Forwarded-For": …}` |
| `tests/test_input_limits.py` (new) | AC9, AC9b, AC10, AC10b (JSON side), AC16, AC16b | `client` + `make_user`; 422/413 assertions as in `tests/test_workflow_smoke.py` |
| `tests/test_upload_limits.py` (new) | AC10b (upload side + classifier unit test), AC11, AC12 | `client` with generator (chunked) body; tiny PDFs built in-test with `pypdf.PdfWriter` |
| `tests/test_error_leaks.py` (new) | AC13 (explicit status + `detail` per exception type, incl. auth), AC14, AC15 | `monkeypatch` failing work fns; `caplog` |
| `tests/test_mcp_exposure.py` (new) | AC17, AC18 (R12-FC) | `client`; `src.mcp_server.mount_mcp` with a throwaway Starlette app |
| Existing upload/lecture/stream-guard tests | regression | call-signature / id updates only |

## 9. Decisions and open questions
Resolved by the user 2026-11-02:
- Q1 re-upload accepted, no migration · Q2 pasted lectures included · Q3 limits as proposed.
- Q4 MCP → Phase 09 **only if** R12 non-exposure is verified; otherwise R12-FC in this phase (fail closed).
- Q6 **approved**: add `MAX_REQUEST_BODY_KB=1024` to `.env.example`.
- Q7 **approved**: 1 MiB request body; 32-entry / 4 MiB upload cache; 1,024-byte PDF signature window;
  maximum password length 72 UTF-8 bytes (existing 6-character minimum unchanged).

Open:
- **Q5 (UNVERIFIED).** Live Render dashboard: the start command equals `render.yaml` (no `src.mcp_server`),
  nothing mounts `/mcp`, and `FORWARDED_ALLOW_IPS` is not `*`. Until confirmed, the plan treats MCP as exposed
  and implements R12-FC. A `*` value would be a CRITICAL finding (rate-limit bypass), reported immediately.
