# Phase 02 report — Security: document ownership & abuse limits

Date: 2026-11-02 · Status: **DONE** · Spec: `.claude/specs/phase-02-security-ownership.md` (rev 4 + review
amendment) · Plan: `.claude/plans/phase-02-security-ownership.md` · ADRs: ADR-007, ADR-008, ADR-009, ADR-010

## Summary
Users can now only read vector collections that are global (mentor KB, NCERT, public YouTube lectures) or that
they created themselves — over HTTP, through the AI Mentor's tools and through the MCP server. The phase also
added an auth-endpoint rate limit, input and request-body bounds, byte-counted upload limits, PDF validation with
the existing parser, a single "safe error" type, and removed emails/query text/exception text from responses,
job records and the touched logs. No migration, no new dependency, no schema change.

**Data note (by design, ADR-007):** PDFs uploaded and transcripts pasted before this phase are stored under the
old global keys (`upload_<hash>`, `lecture_paste_<hash>`). They are kept (not deleted) but never read; users must
re-upload / re-paste to chat with them. A plain rollback to pre-Phase-02 code is **prohibited** without the
mitigation in the plan's *Rollback* section, because it would re-expose those collections across users.

## Deployment check (plan step 1, R12/R13)
- `grep -rn "mount_mcp\|mcp_server" src` → only `src/mcp_server.py` itself; `grep -n mcp Dockerfile render.yaml docker-compose.yml` → no output.
- `render.yaml` `FORWARDED_ALLOW_IPS` = `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` (not `*`); Dockerfile uses uvicorn's default `127.0.0.1` with `--proxy-headers`. No CRITICAL finding.
- Live Render dashboard (Q5): **UNVERIFIED** → fail-closed R12-FC implemented (MCP stdio-only, `mount_mcp` refuses, MCP KB ignores `persist_key`).

## Test-first evidence
New tests were written before the code. Run against the unchanged code:
`89 failed, 55 passed, 3 warnings, 1 error in 124.89s` (the error = `tests/test_error_leaks.py` importing the
not-yet-existing `src/core/errors.py`; the 55 passes were regression/"already true" checks such as PYQ ownership
and the unchanged 6-character password minimum).
Note: `test_cache_is_consistent_under_concurrent_access` is a consistency smoke test — the race it guards is rare
under the GIL, so it may also pass on unlocked code; the lock itself and
`test_cache_lock_not_held_during_extraction_or_vector_work` carry the guarantee.

## Files changed (`git status --porcelain`)
Modified (28): `.claude/specs/phase-02-security-ownership.md`, `.env.example`, `docs/DECISIONS.md`,
`docs/PROJECT_STATE.md`, `frontend/src/features/lecture/Lecture.tsx`, `frontend/src/features/upload/Upload.tsx`,
`src/agents/lecture/graph.py`, `src/agents/upload/graph.py`, `src/api/rate_limit.py`, `src/api/routes/auth.py`,
`src/api/routes/chat.py`, `src/api/routes/lecture.py`, `src/api/routes/pyq.py`, `src/api/routes/upload.py`,
`src/api/upload_limit.py`, `src/core/config.py`, `src/core/job_queue.py`, `src/core/observability.py`,
`src/core/users.py`, `src/graph/tools.py`, `src/mcp_server.py`, `src/schemas.py`, `tests/conftest.py`,
`tests/test_stream_guard.py`, `tests/test_tools.py`, `tests/test_upload_history.py`, `tests/test_upload_prompts.py`,
`tests/test_upload_text.py`.
New (11 incl. this report): `.claude/plans/phase-02-security-ownership.md`, `src/core/errors.py`,
`tests/test_auth_rate_limit.py`, `tests/test_error_leaks.py`, `tests/test_input_limits.py`,
`tests/test_lecture_ownership.py`, `tests/test_mcp_exposure.py`, `tests/test_pyq_ownership.py`,
`tests/test_upload_limits.py`, `tests/test_upload_ownership.py`, `docs/phase-reports/phase-02-report.md`.
Plus `.claude/specs/README.md` (roadmap row). Untouched: `migrations/`, `.github/`, Docker files, `render.yaml`;
no file deleted. Listed but intentionally unchanged: `tests/test_lecture_prompts.py`, `tests/test_lecture_text.py`
(`ask_lecture` keeps `user_id=""` as default and fails closed for paste ids), `tests/test_mcp_server.py`
(`resolve_transport` unchanged; refusal lives in `run()`/`mount_mcp`).

Existing-test changes: mechanical only (`user_id=`, a 32-hex `pdf_hash`, an 11-char `video_id`, `clear_cache()`
instead of patching the removed dict) — plus one **user-approved reversal**:
`tests/test_tools.py::test_tool_exception_becomes_error_not_exception` now asserts exception text does *not*
reach the model (AC4).

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC1 B cannot chat over A's upload; only `upload_<B>_<hash>` read, never legacy key | MET | `test_upload_ownership.py::test_other_user_cannot_chat_over_owner_upload`, `::test_legacy_global_upload_key_is_never_read`, `::test_pdf_hash_must_be_32_hex` |
| AC2 same bytes → two per-user collections | MET | `::test_same_file_two_users_get_independent_collections` |
| AC3 cache ≤ 32 entries / 4 MiB, no text, reprocess when collection gone | MET | `::test_cache_entry_count_is_bounded`, `::test_cache_byte_budget_*`, `::test_cache_is_per_user_and_holds_no_text`, `::test_cache_hit_reprocesses_when_collection_is_gone` |
| AC3b cache thread-safe; lock not held during slow work | MET | `::test_cache_is_consistent_under_concurrent_access`, `::test_cache_lock_not_held_during_extraction_or_vector_work` |
| AC4 model cannot choose a collection; no exception text to the model | MET | `test_tools.py::test_model_chosen_persist_key_is_ignored_by_dispatch`, `::test_structured_kb_tool_has_no_persist_key_argument`, `::test_tool_exception_text_is_not_returned_to_model` |
| AC5 pasted-lecture ownership; forged/invalid `video_id` → 422; YouTube global | MET | `test_lecture_ownership.py` (7 tests) |
| AC6 PYQ: B only touches `pyqbank_<B>` | MET | `test_pyq_ownership.py::test_user_b_cannot_reach_user_a_bank` |
| AC7 six auth endpoints limited, shared budget, others unaffected | MET | `test_auth_rate_limit.py::test_auth_post_is_limited_after_n_requests`, `::test_auth_budget_is_shared_across_auth_endpoints`, `::test_non_auth_routes_unaffected_by_auth_counter` |
| AC7b `/auth/refresh` outside the auth budget, still globally limited (ADR-010) | MET | `::test_refresh_*` (4 tests) |
| AC8 forged `X-Forwarded-For` does not change the key | MET | `::test_forged_forwarded_for_does_not_change_limiter_key` |
| AC9 oversized fields → 422 | MET | `test_input_limits.py::test_oversized_fields_are_rejected`, `::test_oversized_auth_fields_are_rejected` |
| AC9b history truncated/trimmed before validation | MET | `::test_chat_message_truncates_instead_of_rejecting`, `::test_history_keeps_last_twenty_before_validation`, `::test_long_history_is_accepted_and_trimmed_end_to_end` |
| AC10 JSON body > 1 MiB → 413 (CL + chunked); 200k Devanagari transcript accepted | MET | `::test_json_body_over_limit_*`, `::test_max_size_devanagari_transcript_is_accepted` |
| AC10b exact upload classification; `/upload/chat` uses JSON limit | MET | `::test_upload_chat_uses_json_limit_not_file_limit`, `::test_unregistered_path_containing_upload_uses_json_limit`, `test_upload_limits.py::test_is_upload_endpoint_exact_match_only`, `::test_two_mib_pdf_is_not_rejected_by_json_limit` |
| AC11 upload over limit → 413 with/without Content-Length | MET | `test_upload_limits.py::test_upload_over_limit_*` |
| AC12 PDF validation (signature window + parser) | MET | `::test_non_pdf_renamed_to_pdf_is_rejected`, `::test_uppercase_extension_is_accepted`, `::test_leading_bytes_before_signature_are_tolerated`, `::test_signature_beyond_window_is_rejected`, `::test_signature_ok_but_invalid_pdf_is_rejected` |
| AC13 400 `UserFacingError` / 500 generic for ValueError & RuntimeError; jobs; auth | MET | `test_error_leaks.py::test_sync_endpoint_error_contract` (4 endpoints × 3), `::test_job_error_contract`, `::test_register_error_contract`, `::test_reset_error_contract` |
| AC14 `describe_exception` and logs carry no message text | MET | `::test_describe_exception_has_no_message`, log tests in `test_error_leaks.py` |
| AC15 no emails / query text in logs | MET | `::test_user_creation_log_has_no_email`, `::test_email_failure_log_has_no_email`, `::test_forgot_password_failure_log_has_no_email`, `::test_retrieval_metrics_log_has_no_query_text` |
| AC16 new passwords: 6-char min unchanged, ≤ 72 bytes, > 128 chars → 422 | MET | `test_input_limits.py::test_new_password_*`, `::test_existing_minimum_is_unchanged` |
| AC16b login independent (legacy >72-byte works; > 1,024 → 401 without bcrypt) | MET | `::test_legacy_long_password_still_logs_in`, `::test_login_over_1024_chars_is_401_without_bcrypt`, `::test_login_does_not_use_new_password_validator`, `::test_register_and_reset_do_not_use_login_length_check` |
| AC17 web app exposes no MCP | MET | `test_mcp_exposure.py::test_web_app_has_no_mcp_route`, `::test_main_does_not_mount_mcp`, `::test_mount_mcp_is_noop_with_default_settings` |
| AC18 deployment check recorded; R12-FC since Q5 unverified | MET | this report; `::test_mount_mcp_refuses_even_when_enabled`, `::test_run_refuses_network_transport`, `::test_mcp_kb_search_ignores_persist_key` |
| 8 workflows pass offline smoke | MET | `tests/test_workflow_smoke.py` inside the full run below |
| Full suite green, no test deleted/skipped/weakened (one approved reversal) | MET | gate results below |
| `security-reviewer` 0 BLOCKING | MET | review + re-review: 0 BLOCKING, 0 SHOULD-FIX open |

## Gate results (closing run, verbatim)
- `uv run pytest -q` → `1086 passed, 13 warnings in 146.43s (0:02:26)`
- `uv run ruff check src tests` → `All checks passed!`
- `uv run python -m compileall -q src tests` → exit 0
- `cd frontend && npm run build` → `✓ built in 3.73s`
- `cd frontend && npm run lint` → `All matched files use Prettier code style!`
- `git diff --stat -- migrations .github Dockerfile render.yaml docker-compose.yml` → empty; no deleted files.
- Earlier runs this phase: after implementation `1081 passed, 13 warnings in 138.82s`; after review fixes `1086 passed, 13 warnings in 229.42s`.

## Reviews
- Round 1 (`security-reviewer`, `code-quality-reviewer`): 0 BLOCKING; SHOULD-FIX: cache thread safety, refresh in
  the auth budget, duplicated hash pattern, stale dead module → first three fixed (user-approved), fourth recorded
  in the backlog (ADR-006).
- Round 2 (final diff): both 0 BLOCKING / 0 SHOULD-FIX. Remaining NITs → backlog (below).

## 8-workflow status
| Workflow | Effect of Phase 02 | Status |
|---|---|---|
| AI Mentor | KB tool limited to mentor KB; question ≤ 4,000; body ≤ 1 MiB | offline smoke PASS; live UNVERIFIED |
| NCERT Study | field bounds; history trimmed | offline smoke PASS; live UNVERIFIED |
| PYQ + Personal Bank | PDF validation, error contract; bank key unchanged | offline smoke PASS; live UNVERIFIED |
| Answer Evaluator | body limit only | offline smoke PASS; live UNVERIFIED |
| Current Affairs | editorial topic bound | offline smoke PASS; live UNVERIFIED |
| Study Planner | body limit only | offline smoke PASS; live UNVERIFIED |
| Lecture Learning | pasted transcripts per user; `video_id` validated; old pastes must be re-pasted | offline smoke PASS; live UNVERIFIED |
| PDF / Document Chat | per-user keys + cache, PDF validation; old uploads must be re-uploaded | offline smoke PASS; live UNVERIFIED |

## Not verified
- Live Render dashboard settings (Q5) — R12-FC applied instead.
- Whether Render's proxy peer address is inside `FORWARDED_ALLOW_IPS` (rate-limit buckets per real client).
- Live workflow smoke (`docs/SMOKE_CHECKLIST.md`) — needs API keys and Qdrant.

## New backlog items
See `docs/PROJECT_STATE.md` → Backlog (Phase 02 entries): legacy collection cleanup; dead `src/agents/upload/upload.py`
stale signature (Phase 09); refresh/global-limiter proxy check; client `book_info`/`topic_info` unbounded and unfenced
in prompts; `_bank_key` `"anon"` fallback; register enumeration + no dummy bcrypt; `make_persist_key` lower-cases
YouTube ids; upload memory (~2 × 50 MB per request, double parse); `mount_mcp` warns on every call; cache `get`
shallow copy; remaining f-string/filename/topic log lines; `TypeError` text to the model; `UPLOAD_ENDPOINTS`
hard-codes `/api/v1`; auth limiter per worker without Redis.

## Follow-ups
- Run the live smoke checklist on staging, including a fresh upload, an old-PDF re-upload and a long (> 20 turn) chat.
- Confirm Q5 and the proxy peer range on Render.
- Next phase: 03 Request-path performance (not started).
