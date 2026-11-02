# Plan — Phase 02 Security: document ownership & abuse limits

Spec: `.claude/specs/phase-02-security-ownership.md` (revision 4)  ·  Status: DONE (2026-11-02)

No migration, no new dependency (`pypdf`, `starlette`, `bcrypt` are already installed), no CI/Docker/`render.yaml`
change, no deletion of code or data. Test-first: every new test is written and run **before** the code it covers;
the failing output is recorded in the phase report.

## Files to touch (exhaustive — anything else needs a plan amendment)
### Application code
| File | Change |
|---|---|
| `src/core/errors.py` | **New.** `class UserFacingError(ValueError)` — the only exception whose message may be shown to users; `GENERIC_ERROR = "Processing failed. Please try again."` |
| `src/core/config.py` | Add `max_request_body_kb: int = 1024` (R7, env `MAX_REQUEST_BODY_KB`) with a comment; nothing else |
| `src/agents/upload/graph.py` | R1: `_upload_key(user_id, pdf_hash)`; `process_upload(content, filename, *, user_id)`, `ask_upload(..., *, user_id)` (empty user → refuse); no legacy-key fallback. R2: `_UploadCache` (OrderedDict LRU, 32 entries, 4 MiB budget, key `(user_id, hash)`, no text stored, oversized entries skipped); hash computed before extraction; hit re-checked with `vector_store_exists`; `extract_pdf_text` stops reading the cache; `get_cached_upload(user_id, pdf_hash)`; `clear_cache()` resets. R10: user-facing raises → `UserFacingError`; logs via `describe_exception` |
| `src/api/routes/upload.py` | Pass `current_user["id"]`; `pdf_hash: str = Query(..., pattern="^[0-9a-f]{32}$")`; `read_pdf_upload()`; R10 contract: `except UserFacingError` → 400 `detail=str(e)`; `except Exception` (incl. plain `ValueError`/`RuntimeError`) → 500 `detail=GENERIC_ERROR` + `describe_exception` log; no `except ValueError` |
| `src/api/routes/pyq.py` | `bank_upload`: `read_pdf_upload()`; same R10 contract (400 `UserFacingError` / 500 `GENERIC_ERROR`); no `except ValueError` |
| `src/api/routes/lecture.py` | R3: pass `user_id` to `build_lecture_chat_index` / `ask_lecture`; R10 contract (400 `UserFacingError` / 500 `GENERIC_ERROR`) in both `?sync=true` branches; no `except ValueError` |
| `src/agents/lecture/graph.py` | R3: `_lecture_key(video_id, user_id)` (`paste_*` → `lecture_<uid>_paste_…`, else `lecture_<video_id>`; paste without user → `None`); `build_lecture_chat_index(video_id, transcript, user_id="")`; `ask_lecture(..., *, user_id)`; no legacy fallback. R10: user-facing raises (l.97,155,163,168,195,315,349,461,484) → `UserFacingError`; `yt-dlp not installed` (l.104) stays a plain `ValueError` (internal) |
| `src/graph/tools.py` | R4: `mentor_kb_search(query, **_ignored)`; registry uses it; `@tool knowledge_base_search(query)` without `persist_key`; generic tool-failure content + `describe_exception` logs (l.198, 216, 289). `knowledge_base_tool_fn` unchanged |
| `src/mcp_server.py` | **R12-FC — only if Q5 is not confirmed by step 12 (default) or exposure is found:** `resolve_transport`/`run()` refuse `sse` (stdio only); `mount_mcp` always returns `False` without mounting; MCP `knowledge_base_search` ignores `persist_key` (mentor KB only). Untouched if Q5 is confirmed |
| `src/api/rate_limit.py` | R5: second `RateLimiter(auth_rate_limit_requests, auth_rate_limit_period)` for POSTs on the 6 credential/email auth paths (`/auth/refresh` excluded — review amendment, ADR-010), key `auth:{client.host}`; R13: docstring states IP comes only from `request.client.host` (uvicorn proxy headers), never from headers |
| `src/api/upload_limit.py` | R7/R8: rewrite `MaxUploadSizeMiddleware` as pure ASGI (same name/constructor, so `src/api/main.py` is unchanged). `UPLOAD_ENDPOINTS = frozenset({("POST", "/api/v1/upload/process"), ("POST", "/api/v1/pyq/bank/upload")})` and `is_upload_endpoint(method, path)` — **exact match only**, no substring/prefix test. Upload endpoints → `max_upload_mb`; every other `POST/PUT/PATCH` (incl. `/api/v1/upload/chat`) → `max_request_body_kb`. Reject by `Content-Length` and by counting received bytes (413, same JSON body shape). R9: `async read_pdf_upload(file) -> bytes` — case-insensitive `.pdf`, capped chunked read (413), `%PDF-` within first 1,024 bytes, then `run_in_threadpool` → `PdfReader` opens, not encrypted, ≥ 1 page (400 fixed messages) |
| `src/schemas.py` | R6 constants + `max_length` on the fields in spec §3. `ChatMessage.content: Annotated[str, BeforeValidator(_truncate_content), Field(max_length=4000)]`; `ChatHistory = Annotated[list[ChatMessage], BeforeValidator(_keep_last_20), Field(max_length=20)]` used by every `chat_history` — truncation/trimming run **before** length constraints. `LectureChatRequest.video_id` pattern (R3) |
| `src/api/routes/chat.py` | `AgentChatRequest.question` ≤ 4,000, `thread_id` ≤ 100 |
| `src/api/routes/auth.py` | R6 bounds on request models; `max_length=128` **only** on `RegisterRequest.password` and `ResetPasswordRequest.new_password`. R14 login (separate path): before `authenticate`, `len(form_data.username) > 254` or `len(form_data.password) > 1024` → the existing 401; no 128 bound, no `_validate_new_password`. R10: register/reset catch `UserFacingError` → 400; any other exception → 500 `GENERIC_ERROR` + `describe_exception` log (replaces `except ValueError`, l.136, 254). R11 logs (l.121, 241) use user id + `describe_exception` |
| `src/core/users.py` | R14: `_validate_new_password` used **only** by `create_user` and `hash_new_password` — first the **unchanged** `len(p) < 6` → "Password must be at least 6 characters", then the new `len(p.encode("utf-8")) > 72` → 72-byte message. `authenticate` / `verify_password` / `_to_bytes` (login, 72-byte truncation) unchanged. R10: l.49, 51, 53, 81 and the new check raise `UserFacingError`. R11: `Created user id %s`; l.34 log via `describe_exception` |
| `src/core/observability.py` | R11: `log_retrieval_metrics` logs `q_len=` instead of query text |
| `src/core/job_queue.py` | R10: `UserFacingError` → its message; anything else → `GENERIC_ERROR`; `logger.error(..., describe_exception(e))` replaces `logger.exception` |
| `frontend/src/features/upload/Upload.tsx` | R7: send `history.slice(-20)` as `chat_history` |
| `frontend/src/features/lecture/Lecture.tsx` | R7: send `history.slice(-20)` as `chat_history` |
| `.env.example` | Add `MAX_REQUEST_BODY_KB=1024` with a one-line comment (Q6 approved). Edit via a targeted insert; no other line read or changed |

### Tests
| File | Change |
|---|---|
| `tests/conftest.py` | `os.environ["AUTH_RATE_LIMIT_REQUESTS"] = "100000"` next to the global override |
| `tests/test_upload_ownership.py` | **New** — AC1, AC2, AC3 |
| `tests/test_lecture_ownership.py` | **New** — AC5 |
| `tests/test_pyq_ownership.py` | **New** — AC6 |
| `tests/test_auth_rate_limit.py` | **New** — AC7, AC7b (refresh outside the auth budget, still globally limited), AC8 |
| `tests/test_input_limits.py` | **New** — AC9, AC9b (validation order), AC10, AC10b (`/upload/chat` and unregistered "upload" paths get the JSON limit), AC16 (24×€ ok, 25×€ and 70×a+€ → 400, 5-char minimum unchanged, 129 chars → 422), AC16b (login independent: legacy >72-byte and 200-char passwords log in, 1,025 chars → 401 without bcrypt, spies prove no cross-use of validators) |
| `tests/test_upload_limits.py` | **New** — AC10b (upload endpoints get `max_upload_mb`; `is_upload_endpoint` exact-match unit test), AC11, AC12 |
| `tests/test_error_leaks.py` | **New** — AC13 (parametrised over endpoint × {`UserFacingError` → 400 + message, `ValueError` → 500 + `GENERIC_ERROR`, `RuntimeError` → 500 + `GENERIC_ERROR`}, jobs, register/reset), AC14, AC15 |
| `tests/test_mcp_exposure.py` | **New** — AC17; AC18 R12-FC tests (added in step 12 if R12-FC applies) |
| `tests/test_mcp_server.py` | **Only if R12-FC applies:** existing `test_resolve_transport_aliases` expects `sse` aliases — update to the stdio-only contract (behaviour change by design, recorded in the report; no other test changed) |
| `tests/test_tools.py` | Add AC4 tests (no existing test removed) |
| `tests/test_upload_text.py` | Mechanical: `user_id=`; 32-hex `pdf_hash`; `ug.clear_cache()` instead of patching the dict |
| `tests/test_upload_history.py` | Mechanical: `user_id=`; 32-hex `pdf_hash` |
| `tests/test_upload_prompts.py` | Mechanical: `user_id=`; `ug.clear_cache()` |
| `tests/test_lecture_prompts.py` | Mechanical: `user_id=` on `ask_lecture` |
| `tests/test_lecture_text.py` | Mechanical: `user_id=` on `ask_lecture` |
| `tests/test_stream_guard.py` | Mechanical: valid `pdf_hash` and 11-char `video_id` in `ENDPOINTS` |

### Docs
| File | Change |
|---|---|
| `.claude/specs/phase-02-security-ownership.md` | Status updates |
| `.claude/plans/phase-02-security-ownership.md` | Status updates |
| `docs/PROJECT_STATE.md` | Phase table; risks #1/#5/#6/#8/#14; trusted-proxy note; **rollback warning** (see *Rollback*); backlog: MCP `persist_key` (if not fixed by R12-FC), legacy `upload_<hash>` / `lecture_paste_<hash>` cleanup, client `book_info` reaches the prompt unfenced, register enumeration, repo-wide `{e}` log sweep, `make_persist_key` lower-cases case-sensitive YouTube ids |
| `docs/DECISIONS.md` | ADR-007 user-scoped vector keys, no fallback to legacy keys, unsafe-rollback rule · ADR-008 `UserFacingError` as the only displayable error · ADR-009 exact upload-endpoint allow-list, 1 MiB request-body limit, client-side history trimming · ADR-010 `/auth/refresh` outside the dedicated auth rate limit (review amendment) |
| `docs/phase-reports/phase-02-report.md` | **New** at close; failing-first test output, Q5 result / R12-FC decision, legacy-data note, rollback procedure |

## Steps
1. **Deployment check (R12/R13), before any code:** run the *Deployment* commands; ask the user for Q5. A
   `FORWARDED_ALLOW_IPS=*` answer is CRITICAL → stop and report immediately.
2. **Write all new tests and the AC4 additions**; run them; confirm they fail for the expected reason; save the output.
3. R10 foundation: `src/core/errors.py`; `job_queue.py`; convert every listed user-input `ValueError` raise to `UserFacingError` before removing any `except ValueError`.
4. R4 `tools.py`.
5. R1/R2 upload graph + route; mechanical upload-test updates.
6. R3 lecture graph + route; mechanical lecture/stream-guard test updates.
7. R7/R8/R9 `upload_limit.py` (exact allow-list), `config.py`, `.env.example`; `read_pdf_upload` in upload/pyq routes.
8. R6/R14 schemas (BeforeValidator order), chat, auth, users.
9. R5/R13 auth limiter + `conftest.py`.
10. R10/R11 remaining error/log changes (routes, `observability.py`, `users.py`, `auth.py`).
11. Frontend history trimming; `npm run build` + `npm run lint`.
12. **MCP gate (fail closed):** if Q5 is confirmed with no exposure → skip, record the confirmation. Otherwise
    (unconfirmed or exposed) → write R12-FC tests first, then implement R12-FC in `src/mcp_server.py`, update
    `tests/test_mcp_server.py` as listed. The phase cannot close while Q5 is unconfirmed and R12-FC is absent.
13. Full gates; `security-reviewer` and `code-quality-reviewer` agents; fix BLOCKING; `/complete-phase`.

## Reused code
- `make_persist_key`, `load_vector_store`, `create_vector_store`, `vector_store_exists` — `src/core/vector_store.py`
- `RateLimiter` — `src/api/rate_limit_core.py`
- `describe_exception` — `src/core/llm.py:79`
- `format_chat_history` — `src/core/prompt_safety.py:57` (unchanged)
- `PdfReader` (pypdf) as already used in `src/agents/upload/graph.py:extract_pdf_text`
- `run_in_threadpool` — `starlette.concurrency`; `BeforeValidator` — `pydantic` (both installed)
- `settings.auth_rate_limit_*`, `settings.max_upload_mb` — `src/core/config.py`
- Fixtures `client`, `make_user` — `tests/conftest.py`; `NcertPdfError` pattern — `src/api/routes/ncert.py:96`

## Verification
Deployment (step 1 and again at close):
```bash
grep -rn "mount_mcp\|mcp_server" src --include=*.py      # only src/mcp_server.py itself
grep -n "mcp" Dockerfile render.yaml docker-compose.yml   # no output
grep -n "FORWARDED_ALLOW_IPS\|proxy-headers" Dockerfile render.yaml   # no "*"
```
Plus the user's confirmation of the live Render start command / environment (Q5). UNVERIFIED until given; while
UNVERIFIED, R12-FC is mandatory.

Tests and gates:
```bash
uv run pytest -q tests/test_upload_ownership.py tests/test_lecture_ownership.py tests/test_pyq_ownership.py \
  tests/test_auth_rate_limit.py tests/test_input_limits.py tests/test_upload_limits.py \
  tests/test_error_leaks.py tests/test_mcp_exposure.py tests/test_mcp_server.py tests/test_tools.py
uv run pytest -q tests/test_workflow_smoke.py
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
cd frontend && npm run build && npm run lint
git diff --stat -- migrations .github Dockerfile render.yaml docker-compose.yml   # must be empty
git diff --stat --diff-filter=D                                               # no deleted files
```
Manual / workflow checks: live smoke (`docs/SMOKE_CHECKLIST.md`) for PDF chat (fresh upload + re-upload of an old PDF),
PYQ bank, mentor, pasted lecture (fresh paste), long PDF/lecture chat (> 20 turns) — needs keys/Qdrant; UNVERIFIED
unless the user runs it.

## Rollback
**An ordinary rollback is unsafe and is not recommended.**
- The pre-Phase-02 code reads the legacy global collections (`upload_<hash>`, `lecture_paste_<hash>`) for any
  authenticated caller, and its mentor tool lets the model choose any `persist_key` (including `pyqbank_<id>`).
  Because this phase keeps the legacy collections (no deletion), redeploying the old version **re-opens
  cross-user access** to them and to every user's PYQ bank via the mentor.
- Collections created after deployment (`upload_<uid>_<hash>`, `lecture_<uid>_paste_<hash>`) are **not readable**
  by the old version, so users would also lose chat access to uploads/pastes made since deployment.

**Prohibited:** deploying any version (the pre-Phase-02 commit or any partial revert) in which a client or the model
can make the server read an arbitrary vector collection. "Forcing `persist_key` to an empty string" in the old
code is **not** an accepted mitigation (it relies on argument plumbing, not a server-enforced check).

Required procedure if the release must be backed out:
1. **Forward fix first:** revert only the faulty part. R1 (upload ownership), R3 (pasted-lecture ownership) and
   R4 (mentor tool) stay in every rollback build — they are never reverted.
2. If the Mentor change itself (R4) is the problem, the rollback build must do one of the following before it is
   deployed:
   - **(a) server-enforced allow-list:** the mentor tool dispatch accepts only the global mentor KB key
     (`mentor_kb`, `src/core/mentor_kb.py:KB_KEY`) and refuses every other key regardless of tool arguments, with
     the AC4 test passing; **or**
   - **(b) disable the Mentor endpoint:** `POST /api/v1/agent/chat/stream` returns 503 in the rollback build (or is
     blocked at the proxy).
3. If the upload or lecture changes must be reverted, the rollback build must still keep R1/R3 (user-scoped keys,
   validated `pdf_hash` / `video_id`, no legacy-key fallback); otherwise `/api/v1/upload/chat` and
   `/api/v1/lecture/chat` must return 503 (or be blocked at the proxy) in that build.
4. Run `tests/test_upload_ownership.py`, `tests/test_lecture_ownership.py`, `tests/test_pyq_ownership.py` and the
   AC4 tests against the rollback build; deploy only if they pass (or the affected endpoints are disabled).
5. Record the rollback, the mitigation used and any exposure window in `docs/PROJECT_STATE.md`.

No migration or data change is involved; no collection is created, modified or deleted by a rollback.

## Review amendment (2026-11-02, approved by the user)
After `review-changes` (security-reviewer + code-quality-reviewer: 0 BLOCKING), the user approved:
1. `_UploadCache` thread safety (`threading.Lock` around get/put/discard/clear; no lock during extraction or
   vector work) + concurrency regression tests in `tests/test_upload_ownership.py` (AC3b).
2. `/auth/refresh` removed from the dedicated auth limiter; global limiter unchanged (R5 amended, AC7b, ADR-010).
   Files: `src/api/rate_limit.py`, `src/core/config.py` (comment only), `tests/test_auth_rate_limit.py`.
3. `PDF_HASH_PATTERN` defined once in `src/schemas.py`, imported by `src/api/routes/upload.py`.
4. `src/agents/upload/upload.py` stays unchanged (ADR-006); stale call signature recorded in the backlog (Phase 09).
No new files beyond the table above.
