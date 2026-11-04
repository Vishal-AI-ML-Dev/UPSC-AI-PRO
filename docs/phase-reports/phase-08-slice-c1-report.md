# Phase 08 Slice C1 report — evaluation history (migration 0006)

Date: 2026-11-04 · Spec `.claude/specs/phase-08-product-gaps.md` (rev 4, "Slice C1") · Plan
`.claude/plans/phase-08-product-gaps.md` ("Slice C1") · ADR-017.

Status: **CLOSED (2026-11-04).** AC-C1-1–5 PASS. The six manual browser checks are owner-confirmed PASS in an
isolated local environment, and the backend evidence is recorded separately below. C2 is **NOT STARTED**.

No new dependency, no live LLM call, no production or Supabase access, no Alembic CLI run, no deploy, no commit or
push. The working tree from Slice A, A-1, the Unicode polish and Slice B was preserved. Nothing was reset, reverted
or discarded.

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC-C1-1 Basic and Mains each write one row (`user_id`, `mode`, `subject`/NULL, `score`, `max_score`); an invalid subject gives 422 and no row; no subject gives 200 and a NULL subject | **PASS** | `test_basic_records_one_row_with_subject`, `test_mains_records_one_row_with_requested_marks`, `test_no_subject_records_null_subject`, `test_invalid_subject_is_422_and_writes_no_row` (7 bad values × 2 endpoints; the model is never reached), `test_every_allow_listed_subject_is_accepted_and_fits_the_column` |
| AC-C1-2 A negative, above-max or NaN score is stored as NULL; `max_score ≤ 0` writes no row | **PASS** | `test_validated_score_rejects_invalid` (covers −, >max, NaN, ±inf, None, str, bool), `test_validated_score_accepts_bounds`, `test_valid_max_score_rejects`, `test_record_attempt_stores_invalid_score_as_null`, `test_record_attempt_writes_no_row_without_positive_int_max`, `test_unparseable_evaluation_records_unscored_row`, `test_off_scale_mains_score_is_unscored` |
| AC-C1-3 If `record_attempt` raises, both endpoints return 200 with an unchanged body; `caplog` has `evaluation_attempt_persist_failed` and no question or answer text | **PASS** | `test_record_failure_keeps_response_and_logs_redacted_warning` (both endpoints; body equals the baseline body; the record's extra fields are exactly `event`/`mode`/`error_type`; no `exc_info`; no question, answer, email or exception message anywhere in `caplog`; no score text in the C1 record), `test_real_db_failure_keeps_response` (real `SessionLocal` `OperationalError` path) |
| AC-C1-4 Two users get two correctly attributed rows; 401; existing response shapes unchanged | **PASS** | `test_two_users_rows_are_attributed_to_the_caller`, `test_client_cannot_choose_the_owner` (a client-sent `user_id`/`mode`/`score` is ignored), `test_unauthenticated_is_401_and_writes_nothing` (both endpoints), `test_response_shapes_unchanged`; the existing `tests/test_planner_evaluator.py` passes unchanged |
| AC-C1-5 Frontend build + lint; dropdown checked manually | **PASS**: build, lint and JSX escape check PASS (run by Claude); manual browser checks 1–6 **owner-confirmed PASS** | see Verification and Manual browser checks |

Every slice: full suite green; ruff; compileall; `security-reviewer` and `code-quality-reviewer` passes done (below).
The 8 workflows are covered by the full offline suite. Only the Answer Evaluator changed: one optional request field
and a best-effort write after the response is built.

## Design
- **Table `evaluation_attempts`** (`src/core/models.py` `EvaluationAttempt`; migration 0006):
  - `id` String(32) PK; `user_id` String(32) FK → `users.id` ON DELETE CASCADE, indexed (`ix_evaluation_attempts_user_id`).
  - `mode` String(10) (`basic`|`mains`); `subject` String(60) NULL; `score` Float NULL; `max_score` Integer NOT NULL; `created_at` timestamptz NOT NULL.
  - Composite index `ix_evaluation_attempts_user_subject_created (user_id, subject, created_at)`.
  - **No question or answer column.** A test pins the exact column set. There is no read endpoint in C1.
- **Subject allow-list** (`src/schemas.py`): `Subject = Literal[...]` with the 10 spec values, and
  `SUBJECTS = get_args(Subject)`. `subject: Subject | None = None` is on `EvaluateRequest` and `MainsEvalRequest`
  (not `ModelAnswerRequest`). Any other value gives 422 before the handler runs.
- **`src/core/evaluation_history.py`**:
  - `valid_max_score`: an int > 0 (bools rejected), otherwise no row.
  - `validated_score`: a finite value with `0 ≤ score ≤ max`, otherwise NULL (bools and non-numbers rejected).
  - `record_attempt(user_id, mode, subject, score, max_score)` re-checks `mode` and `subject` against the allow-lists and writes the row in its own short `SessionLocal` session, so it cannot affect the request's session.
  - Database errors propagate to the caller.
- **Routes** (`src/api/routes/evaluator.py`):
  - `evaluate_sync` and `mains_eval_sync` take `user = Depends(get_current_user)`. FastAPI caches it per request, so this is the same router-level check, run once.
  - Each builds the response body, then calls `_record_best_effort(...)`, then returns the body unchanged.
  - `max_score` is `structured.max_score` (Basic, always 10) or `request.marks` (Mains, 5–15 by schema).
  - On any exception: `logger.warning("evaluation_attempt_persist_failed", extra={"event", "mode", "error_type"})`. The exception message, question, answer, score text, email and traceback are never logged.
  - `model-answer/sync` does not record. Mentor-chat evaluator routing calls `evaluate_answer` directly, not these routes, so it does not record either.
- **Frontend**:
  - `api.ts`: `EVALUATION_SUBJECTS` (mirrors the backend list), `EvaluationSubject`, `isEvaluationSubject`, and `subject?` on `evaluateAnswer` / `evaluateMains`.
  - `Evaluator.tsx`: a "Subject (optional)" select (default "No subject"), persisted as `eval:subject` via `usePersistentState`, shown in both modes. A stale or edited stored value fails the allow-list check, so it is never sent and the select shows "No subject".

## Migration 0006 and downgrade verification
- Written by Claude. **The Alembic CLI was never run**, and no real database was touched.
- Verified only on throwaway SQLite files through Alembic's Python API, the same mechanism the test suite already uses.
- `test_migration_0006_upgrade_indexes_cascade_and_downgrade` (on a `tmp_path` DB):
  1. Upgrade to `0006_evaluation_attempts`.
  2. Check the exact columns and nullability, both indexes and their columns, and the FK → `users` with `ON DELETE CASCADE`.
  3. With `PRAGMA foreign_keys=ON`, deleting a user removes that user's attempt.
  4. **Downgrade to `0005_study_plans`**: the table is dropped, `study_plans` and the user data remain.
  5. Re-upgrade to head.
- Existing `tests/test_migrations.py` now also covers 0006: head creates every ORM table, `downgrade base` drops them, and ORM and migration nullability match.
- **Applying 0006 to Supabase is UNVERIFIED.**
  - The owner should take a backup first. Render runs `alembic upgrade head` on deploy.
  - A production downgrade deletes all recorded attempts.
  - Deploy C1 separately from B.
  - Table grants / RLS for `evaluation_attempts` need owner confirmation.

## Files changed (all in the Slice C1 plan table)
| File | Change |
|---|---|
| `src/core/models.py` | `EvaluationAttempt` model; import block (`Float`, `Index`, `Integer`) |
| `migrations/versions/0006_evaluation_attempts.py` | new: table + FK CASCADE + 2 indexes; `downgrade()` drops indexes and table |
| `src/schemas.py` | `Subject` Literal, `SUBJECTS`; optional `subject` on `EvaluateRequest`, `MainsEvalRequest` |
| `src/core/evaluation_history.py` | new: `valid_max_score`, `validated_score`, `record_attempt` |
| `src/api/routes/evaluator.py` | user dependency on two handlers; `_record_best_effort` with redacted warning |
| `frontend/src/lib/api.ts` | `EVALUATION_SUBJECTS`, `EvaluationSubject`, `isEvaluationSubject`; `subject?` on two request types |
| `frontend/src/features/evaluator/Evaluator.tsx` | "Subject (optional)" dropdown |
| `tests/test_evaluation_attempts.py` | new: 63 tests (AC-C1-1–4 + migration 0006) |
| `docs/phase-reports/phase-08-slice-c1-report.md` | this report |
| `docs/PROJECT_STATE.md` | status row, backlog, handoff |

## Verification (run in this session, 2026-11-04)
| Command | Result |
|---|---|
| `uv run pytest -q tests/test_evaluation_attempts.py` | `63 passed` |
| `uv run pytest -q tests/test_evaluation_attempts.py tests/test_planner_evaluator.py tests/test_migrations.py tests/test_study_plans.py` | `161 passed` (run before the reviews, and again after the test-only NIT fixes) |
| `uv run pytest -q` (full suite, once) | `1706 passed, 2 skipped, 19 warnings in 361.87s` (baseline before C1: `1642 passed, 2 skipped`) |
| `uv run ruff check src tests` | `All checks passed!` |
| `uv run ruff format --check` (6 C1 Python files) | already formatted |
| `uv run python -m compileall -q src tests migrations` | OK |
| `cd frontend && npm run build` | built OK (tsc + vite) |
| `cd frontend && npm run lint` | `All matched files use Prettier code style!` |
| `node frontend/scripts/check-jsx-escapes.mjs` | `OK: no literal escape sequences …` |

The full suite ran before the two test-only NIT fixes, which changed only `tests/test_evaluation_attempts.py`. The
focused 161 tests were re-run after the fixes and pass.

How the tests were adjusted while writing them (no product assertion was weakened):
- The leak check for score text is limited to the C1 warning record. The pre-existing
  `src.core.observability` INFO line already logs the parsed score (`score=6/10`); that is not C1 code.
- `request_id` is allowed on the record because the app-wide log filter adds it to every record.

## Security review (`security-reviewer`, read-only)
**No CRITICAL, HIGH or MEDIUM findings.**

Confirmed:
- The row owner comes only from the JWT `user["id"]`.
- No question or answer text is stored; FK CASCADE.
- The allow-list is enforced twice.
- The `try/except` covers only the record call, so auth 401/503 is never swallowed.
- The log is redacted.
- The session is self-contained and closes with rollback.
- The migration is additive and correct for Postgres.
- A stale localStorage value is never sent.
- model-answer and Mentor chat do not record.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | LOW | No per-user row cap or retention for `evaluation_attempts`. Each row costs a real LLM call and the per-IP rate limit applies | **Backlog**: decide retention / cap and use bounded queries when C2 adds reads |
| 2 | LOW | Future-change note: `except Exception` would also swallow an unexpected `KeyError` on `user["id"]`. This cannot happen with the current `get_current_user`. Never add `exc_info=True` (SQL bind params) | No change; recorded here |
| 3 | INFO | `ix_evaluation_attempts_user_id` is redundant with the composite index | Kept: the spec (R-C1-2) requires `user_id` indexed **and** the composite index; noted in the backlog for a later review |
| 4 | INFO | A production downgrade deletes all history | Documented in the migration docstring; owner runbook says backup first |
| 5 | INFO | `event` log extra key does not collide with `LogRecord` attributes | None |

## Code-quality review (`code-quality-reviewer`, read-only)
**No BLOCKER or SHOULD-FIX findings.** R-C1-1–4 are complete, the ORM and migration match, the response contract is
unchanged, no file outside the plan was touched and no C2 code is present. The reviewer also confirmed that the
AC-C1-3 regression test would fail without the `try/except`: the TestClient re-raises the error, or the endpoint
returns 500.

| NIT | Disposition |
|---|---|
| 1 `_rows` orders by `created_at`, so the two-row order assertion could flake on equal timestamps | **FIXED**: compare sorted tuples |
| 2 The migration test used `tempfile.mkdtemp()` and never cleaned it up | **FIXED**: `tmp_path` |
| 3 `MODES` is a hand-kept tuple | Not changed (optional; two values) |
| 4 The frontend `EVALUATION_SUBJECTS` duplicates the backend `Subject` list with no automated parity check | **Known manual sync point**: both carry cross-reference comments. Backlog LOW |

## Manual browser checks — all owner-confirmed PASS (2026-11-04, isolated local environment)

### Environment (set up by Claude, scratchpad only; no project file changed)
- **Backend:** `http://127.0.0.1:8011`, launched from the session scratchpad, so the repo `.env` was never loaded.
  - A startup self-check confirmed 17 settings empty or off: database URL = scratchpad SQLite, remote Qdrant, LLM
    keys (Google, Groq, OpenAI-compatible, Cohere), Tavily / web search, SMTP, Sentry, Langfuse, Upstash / Redis,
    and every `r2_*` / `supabase*` setting.
  - Embedded Qdrant and the LangGraph checkpointer files lived in the scratchpad.
  - **The schema was built with `Base.metadata.create_all` on that scratchpad SQLite file; the app's startup
    `init_db` (Alembic) was replaced, so Alembic was not run.**
  - The evaluator LLM was a deterministic mock: Basic always 7/10, Mains 60 % of the marks, Model Answer fixed
    text. No LLM provider was called.
  - A pure ASGI wrapper appended each evaluator request's `subject` key (sent or not, and its value) to a
    scratchpad `requests.log`.
- **Frontend:** Vite dev server at `http://127.0.0.1:5175` (`--strictPort`, loopback only), with
  `VITE_API_BASE=http://127.0.0.1:8011/api/v1` set in the process environment.
  - The served `src/lib/config.ts` was checked to contain that base.
  - A CORS preflight from that origin returned 200.
  - 5175 is a fresh origin, so its localStorage had nothing left over from earlier slices.
- Only processes started for this test were touched. Existing local databases and the working tree were untouched.

### Owner-confirmed results
| # | Check | Owner result | Independent backend evidence (8011 `requests.log`, access log, scratchpad DB) |
|---|---|---|---|
| 1 | Basic and Mains both show the same 10-subject dropdown, default "No subject" | **PASS** | UI only: no backend evidence possible |
| 2 | A selected subject is sent; no selection sends no `subject` | **PASS** | **Verified**: 05:45:05 `basic subject='Geography'` → row `basic / Geography / 7.0 / 10`; 05:41:27 `basic subject=<not sent>` → row with subject NULL |
| 3 | Subject selection survives a page reload | **PASS** | UI only: no backend evidence possible |
| 4 | A tampered stored subject shows "No subject" and is not sent | **PASS** | Partial: no request in the log carried an invalid subject from the browser. The log cannot show which browser request (if any) followed the tampering |
| 5 | Model Answer requests send no `subject` | **PASS** | **Not captured from the browser**: the log has no browser `model-answer` request. Only Claude's API smoke test (05:27:03, `model-answer subject=<not sent>`, no row) is in the log. Unit test `test_model_answer_does_not_record` passes |
| 6 | Optional: inspect the throwaway user's row in the isolated DB | **PASS** | **Verified** (read-only `sqlite3` `mode=ro` on the scratchpad file): table columns are exactly `id, user_id, mode, subject, score, max_score, created_at` (no question or answer text); rows below |

**Basic mock evaluation result: 7/10.** It was recorded as `score 7.0, max_score 10` (the mock's fixed Basic result).

Scratchpad DB rows at closure:
| User | mode | subject | score | max_score |
|---|---|---|---|---|
| `smoke_…@example.com` (Claude API smoke test) | basic | Economy | 7.0 | 10 |
| `smoke_…@example.com` (Claude API smoke test) | mains | NULL | 9.0 | 15 |
| `c1test@example.com` (owner, browser) | basic | NULL | NULL | 10 |
| `c1test@example.com` (owner, browser) | basic | Geography | 7.0 | 10 |

Notes:
- The unscored `c1test` row is expected behaviour. The Basic evaluator rejects answers under 50 **words** before the
  model is called ("Answer too short"), so no score was parsed and C1 stored NULL. The UI's own minimum is 50
  **characters**; that mismatch predates C1 (backlog).
- Claude's API smoke test also showed:
  - an off-list subject (`Astrology`) → 422 and no row
  - Model Answer → 200 and no row
- No browser Mains evaluation appears in the backend log. The Mains path is covered by automated tests (AC-C1-1, 3, 4)
  and the API smoke test.

## Unresolved / notes
- AC-C1-5: PASS. The browser checks are owner-confirmed; the backend evidence for checks 4 and 5 is partial or not
  captured, as shown in the table above.
- **Migration 0006 was never executed against production or any real database.** It ran only on throwaway SQLite
  files through Alembic's Python API in the test suite. The browser environment did not run Alembic at all
  (`create_all`).
- Supabase: applying 0006, grants / RLS, and the backup are UNVERIFIED (owner only).
- The recorded data is not visible anywhere yet. Reads, analytics and Dashboard changes are Slice C2, which is
  **NOT STARTED** and needs explicit owner approval.

## Closure (2026-11-04)
**Slice C1 CLOSED.**
- Gates:
  - full suite `1706 passed, 2 skipped` (not re-run at closure, per owner instruction)
  - focused `161 passed`
  - ruff, compileall, frontend build and lint, and the JSX escape check all clean
- Reviews: security has no CRIT/HIGH/MED; code-quality has no BLOCKER/SHOULD-FIX.
- Manual checks: 1–6 owner-confirmed PASS.
- Closure changed documentation only.
- Slice A + A-1 and Slice B + B-1 remain CLOSED.
  - C1's edits to files shared with Slice B (`src/core/models.py`, `src/schemas.py`, `frontend/src/lib/api.ts`)
    are additive. The only change to an existing line is the `models.py` import block.
  - No Slice A or B logic was altered, and their tests pass in the full suite.
- Slice C2 is **NOT STARTED** and needs explicit owner approval.
