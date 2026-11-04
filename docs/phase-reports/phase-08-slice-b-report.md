# Phase 08 Slice B report — study plan persistence (migration 0005)

Date: 2026-11-03 · Spec: `.claude/specs/phase-08-product-gaps.md` (APPROVED rev 4 + Amendment B-1, Slice B R-B1–R-B7) · Plan:
`.claude/plans/phase-08-product-gaps.md` (Slice B) · ADR: ADR-017 · Status: **CLOSED (2026-11-03)** — implemented and
verified offline; security review no CRITICAL/HIGH/MED (LOW #1 fixed); code-quality review no BLOCKER (S1/S2 fixed by
Amendment B-1); check-1 render crash fixed; all 9 owner browser checks PASS; final closure review no BLOCKER /
SHOULD-FIX. C1 / C2 **not started**.

No new dependency, no live LLM call, no production access, no Alembic CLI run, no deploy, no commit or push.
Slice A / Amendment A-1 / Unicode-polish changes are preserved (verified against a pre-Slice-B snapshot of the diff:
every Slice A file identical or with all Slice A lines kept; all 8 untracked Slice A files byte-identical).

## Acceptance criteria
| AC | Status | Evidence (`tests/test_study_plans.py`, 40 tests) |
|---|---|---|
| AC-B1 Saved plan survives a restart | **PASS** | `test_saved_plan_survives_a_restart`: PUT + PATCH, `engine.dispose()` (all pooled connections dropped), then a new `SessionLocal()` and a new `TestClient(app)` return the same id, `plan_md` and progress |
| AC-B2 401 on all 4 endpoints; PUT → GET → PATCH → GET → DELETE → GET null | **PASS** | `test_every_endpoint_requires_auth` (×4); `test_put_get_patch_get_delete_get` (also checks stored inputs, server-computed `meta`, idempotent DELETE, no row left) |
| AC-B3 Isolation | **PASS** | `test_users_cannot_read_or_change_each_others_plan`: U2 GET → null; U2 PATCH with U1's `plan_id` → 404 (U2 has no plan) and 409 (U2 has its own plan); U2 DELETE leaves U1's row; U1's goal / plan / progress unchanged. `test_client_cannot_supply_a_user_id`: a body `user_id` → 422, no row for either user |
| AC-B4 Latest only | **PASS** | `test_second_put_replaces_with_new_id_and_resets_progress`: one row, new id, new content, progress `{}`; PATCH with the old id → 409 with the fixed message, progress unchanged. `test_patch_with_unknown_plan_id_is_409` |
| AC-B5 422 cases | **PASS** | PUT: `plan_md` 100 001 chars / empty / missing, `goal` 201, `hours` 201, `attempt_number` 21, unknown input key (7 cases, no row written). PATCH: 1 001 entries, bad key formats (space, non-digit index, 5-digit index, trailing newline, 160+ chars), int / string / null values (9 cases, progress unchanged); bad `plan_id` formats. Boundaries accepted: 100 000-char plan, 200-char goal, 1 000 entries |
| AC-B6 Concurrency | **PASS** | `test_concurrent_puts_leave_one_complete_row`: 2 threads × `threading.Barrier`, 20 rounds, distinct payloads through the real HTTP stack → every response 200 (503 tolerated with the fixed body; none occurred), never a 500, always exactly 1 row; the row's `goal`, `plan_md` and `inputs.weak` all come from one request, whose response `id` equals the row id; GET agrees. `test_conflict_path_updates_in_place` (same row: `created_at` kept, new id, progress reset). `test_sqlite_supports_on_conflict_and_returning` (local SQLite 3.50.4 ≥ 3.35). `test_upsert_is_one_statement_for_postgresql_and_sqlite` (compiles to `INSERT … ON CONFLICT (user_id) DO UPDATE SET … RETURNING id, updated_at` for both dialects; `created_at` not in the update set) |
| AC-B7 Frontend build + lint; reconciliation checked manually | **PASS** — build + lint PASS; decision logic Node-checked (AC-B8); headless-Chrome check 14/14; all 9 owner browser checks PASS (2026-11-03) |
| AC-B8 (B-1) Node check of `reconcilePlan.ts` | **PASS** — `node --test` (scratchpad): 15/15; with the draft branch removed (pre-fix logic) the 3 S1/adopt cases fail |

Extra coverage beyond the ACs: retry once after an `OperationalError` then succeed (exactly 2 attempts, 1 row);
`OperationalError` / `IntegrityError` on both attempts → 503 `{"detail": "Could not save your study plan right
now. Please try again."}` with no DB text, no row; unsupported dialect fails closed; `get_plan_meta` failure → plan
saved with `meta: null`; migration 0005 upgrade/constraints/downgrade (below).

## Design
- **Table `study_plans`** (`src/core/models.py`, `migrations/versions/0005_study_plans.py`): `id` String(32) PK;
  `user_id` String(32) NOT NULL, FK `users.id` ON DELETE CASCADE, **UNIQUE** (`uq_study_plans_user_id`, which is also
  the upsert conflict target and its index); `goal` String(200); `inputs`, `plan_md`, `progress` Text NOT NULL;
  `meta` Text NULL (optional metadata); `created_at`, `updated_at` timestamptz NOT NULL. JSON stored as Text.
- **PUT** (`src/core/study_plans.py:upsert_plan`): one statement per attempt —
  `INSERT … ON CONFLICT (user_id) DO UPDATE SET id, goal, inputs, plan_md, meta, progress='{}', updated_at
  RETURNING id, updated_at` — built with `sqlalchemy.dialects.postgresql.insert` or `…sqlite.insert` chosen from
  the session's dialect name (anything else → `PlanStoreUnavailable` → 503). No read-before-write. On
  `IntegrityError` / `OperationalError`: rollback, log `study_plan_upsert_failed` (attempt + exception type only),
  retry once with a fresh id, then 503 with a fixed message. `created_at` keeps the first save's time (the update
  set is exactly the spec's list).
- **PATCH** (`update_progress`): `UPDATE … WHERE user_id = :caller AND id = :plan_id RETURNING …`; 0 rows → 404 if the
  caller has no plan, else 409. The progress map is replaced as a whole.
- **GET / DELETE**: by `user_id` only. No endpoint accepts a row id in its path, and no request accepts a user id
  (`extra="forbid"` on every request model).
- **`meta`**: computed server-side at PUT with `get_plan_meta(goal)` (24 h cache); any failure stores `null`.
- **Frontend** (`Planner.tsx`, `api.ts`): on mount `GET /planner/plan` and reconcile per spec R-B6 (table below).
  After a stream that is non-empty and contains no `STREAM_NOTICE_PREFIX` the plan is `PUT`; a failure keeps the
  local copy with "Not saved — kept on this device only. [Retry]". Ticks update the cache, set `planner:dirty` and are
  sent as a debounced (800 ms) PATCH of that plan's ticked entries; a tick counter stops a late PATCH response from
  clearing the dirty flag of newer ticks. A failed PATCH shows "Your last ticks were not saved." (+ "replaced by a
  newer one; reload" on 409). The `streamAgent("/planner/generate", …)` call and its body are unchanged.

| Server | Local cache | Implemented result |
|---|---|---|
| plan P | none, or a different `planId` | cache replaced with P (plan + progress + id), dirty cleared |
| plan P | same id, not dirty | P used |
| plan P | same id, dirty | PATCH re-sent once; success → server response; failure (incl. 409) → P shown + "Your last ticks were not saved." |
| none | a plan without `planId` | shown with "Not saved to your account. [Save]" — explicit action only; a cached failure notice is never offered for saving |
| none | none | empty state |
| GET failed | anything | cached copy shown read-only ("Offline copy — not synced…" + [Retry sync]); ticks, Reset and Generate disabled until a GET succeeds |

Interpretations (not in the table, chosen to keep the server authoritative):
- Server has no plan but the cache has a `planId` → the plan was deleted on the server, so the cache is cleared.
- During the initial GET the view is read-only too, so a slow GET cannot overwrite a newly generated plan.
- A plan generated in this visit is the "pending" plan: `planId` is cleared when generation starts; if the stream
  fails (no draft is created), the server's previous plan is restored on the next visit. Starting a new generation
  is an explicit action that replaces any earlier unsaved draft.
- If the user leaves the page mid-stream, the partial text stays in the cache unsaved and is offered with [Save] on
  return (explicit action).

## Migration 0005 and downgrade verification
- Written by Claude; **the Alembic CLI was never run** and no real database was touched.
- Verified only on throwaway SQLite files through Alembic's Python API, the existing test-suite mechanism
  (`tests/test_migrations.py`; `init_db()` in `tests/conftest.py` also upgrades the suite's throwaway DB to head):
  - `test_migration_0005_upgrade_constraints_and_downgrade`: upgrade to `0005_study_plans` → table, UNIQUE(`user_id`),
    FK → `users` with `ON DELETE CASCADE`; a second row for the same user → `IntegrityError`; **downgrade to
    `0004_single_active_session` → table gone, existing `users` row intact**; upgrade to head again → table back.
  - Existing `tests/test_migrations.py` (5 tests) now also covers 0005: head creates every ORM table, `downgrade base`
    drops them, ORM/migration nullability parity.
- PostgreSQL DDL/upsert: the upsert's PostgreSQL SQL is checked by compilation only. **Running 0005 on PostgreSQL /
  Supabase is UNVERIFIED** (owner: backup first; Render runs `alembic upgrade head` on deploy).
- SQLite does not enforce the FK cascade unless `PRAGMA foreign_keys=ON` (pre-existing for every table); PostgreSQL does.

## Files changed (all in the Slice B plan table)
| File | Change |
|---|---|
| `src/core/models.py` | `StudyPlan` model (+ `UniqueConstraint` import) |
| `migrations/versions/0005_study_plans.py` | new: create table + FK + unique constraint; `downgrade()` drops it |
| `src/schemas.py` | bounds/patterns; `StudyPlanInputs`, `StudyPlanSaveRequest`, `StudyPlanProgressRequest` (`extra="forbid"`, key regex + length, `StrictBool`, ≤ 1000 entries); response models (`ConfigDict`, `StrictBool` imports) |
| `src/core/study_plans.py` | new: `get_plan`, `upsert_statement`, `upsert_plan` (atomic, one retry), `update_progress`, `delete_plan` |
| `src/api/routes/planner.py` | `GET`/`PUT`/`DELETE /planner/plan`, `PATCH /planner/plan/progress` (plain `def`, `get_current_user`, fixed messages); Slice A code untouched |
| `frontend/src/lib/api.ts` | `StudyPlanInputs`, `SavedStudyPlan`, `SavedStudyPlanRef`, `StudyPlanProgress`; `api.getPlan/savePlan/patchPlanProgress/deletePlan` |
| `frontend/src/features/planner/Planner.tsx` | reconciliation, save after a clean stream, debounced PATCH, read-only offline mode, `SyncBanner`; (B-1) persisted `planner:draft`, draft-conflict choices, Generate blocked while saving, latest-only save results |
| `frontend/src/features/planner/reconcilePlan.ts` | (B-1) new, import-free: `decideReconcile`, `createLatestGuard` |
| `.claude/specs/phase-08-product-gaps.md`, `.claude/plans/phase-08-product-gaps.md` | (B-1) narrow amendment: R-B7, AC-B8, plan § Amendment B-1 |
| `tests/test_study_plans.py` | new — 40 tests |
| `docs/phase-reports/phase-08-slice-b-report.md`, `docs/PROJECT_STATE.md` | this report; state update |

## Verification (run in this session, 2026-11-03)
| Command | Result |
|---|---|
| `uv run pytest -q tests/test_study_plans.py tests/test_migrations.py tests/test_planner_evaluator.py tests/test_planner_weak_autofill.py` | `118 passed` |
| `uv run pytest -q` (full suite, run once, before security fix #1) | `1642 passed, 2 skipped, 16 warnings in 246.17s` |
| After security fix #1: `uv run pytest -q tests/test_study_plans.py tests/test_migrations.py` | `45 passed` |
| `uv run ruff check src tests evals` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | exit 0 |
| `git diff --check` | exit 0 |
| `cd frontend && npm run build` | `✓ built in 3.70s` |
| `cd frontend && npm run lint` | `All matched files use Prettier code style!` |
| `node frontend/scripts/check-jsx-escapes.mjs` | `OK: no literal escape sequences …` |

## Security review
`security-reviewer` agent pass (read-only; focused test run by the reviewer: `39 passed`): **no CRITICAL, HIGH or
MEDIUM findings.** Confirmed: auth on all 4 endpoints; every query scoped to the JWT `user["id"]`; no client user id
or row id can reach another user's row; the 404/409 distinction only reveals the caller's own state (no cross-user
oracle; ids are 128-bit random); bounds + `extra="forbid"`; worst-case body (~600 KB plan + ~170 KB progress) under
the 1 MiB limit (ADR-009); fixed error messages; no raw SQL; additive migration with a working downgrade; sanitized
Markdown rendering, no new `dangerouslySetInnerHTML`, per-user namespaced cache keys with no secrets.

| # | Severity | Finding | Action |
|---|---|---|---|
| 1 | LOW | A non-conflict DB error on PUT (e.g. a PostgreSQL `DataError`) reached the global handler, whose `logger.exception` would include the statement parameters (plan text) in server logs; the client still saw a fixed 500 | **FIXED**: `upsert_plan` also catches other `SQLAlchemyError`s → rollback, type-only log, no retry, fixed 503. Regression test `test_put_other_db_error_is_503_without_retry_or_logged_plan_text` (plan text absent from `caplog`, 1 attempt, no row). Focused re-run after the fix: `tests/test_study_plans.py` + `tests/test_migrations.py` → `45 passed`; ruff clean. The full suite was not re-run after this small fix (owner instruction: full suite once) |
| 2 | LOW | DB errors on GET / PATCH / DELETE → fixed-message 500 rather than 503 | Not a spec breach (R-B4 covers PUT only) → backlog |
| — | INFO | `slugify` section ids are unbounded; a heading longer than ~140 chars makes progress keys exceed 160 chars → that section's PATCH is rejected with 422 ("Your last ticks were not saved.") | Backlog (frontend `planMarkdown.ts` is outside the Slice B file list) |

## Code-quality review and Amendment B-1
`code-quality-reviewer` (read-only; its focused run `40 passed`): **no BLOCKER**; 2 SHOULD-FIX; 6 NITs.
- **S1 (fixed, option a, owner-approved):** a generated plan whose save failed was silently replaced by the older
  server plan on the next visit. Now the generated plan is a persisted pending draft (`planner:draft`: goal +
  generating inputs; its ticks stay in `planner:progress`). With a different saved plan, the Planner shows "This
  generated plan is not saved. A different plan is saved to your account." with **Save to replace it** / **Load saved
  plan**. A draft whose save actually landed (same text on the server) is adopted. A failed save or retry keeps the
  draft ("Not saved — kept on this device until you save it." + Retry, + Load saved plan when one exists). "Load saved
  plan" drops the draft only if a saved plan exists.
- **S2 (fixed):** Generate is disabled while `sync === "saving"`; every generation / save / load starts a new
  latest-only token and a save result for an older token is ignored, so an old id can never attach to a different plan.
- NITs → backlog (`docs/PROJECT_STATE.md`): N1 PATCH DB errors → 500 (map to 503); N2 overlapping PATCHes can
  arrive out of order (send one at a time); N3 StrictMode double reconcile in dev (per-effect cancel flag); N4 long
  headings → progress keys > 160 chars (already logged); N5 concurrency test does not assert that a real conflict
  occurred. N6 (Save used current form inputs after a reload) is fixed by the persisted draft inputs, except for
  plans cached before Slice B, which have no recorded inputs.

B-1 verification (2026-11-03): `npm run build` `✓ built in 3.50s`; `npm run lint` clean; JSX escape check OK;
`node --test` of `reconcilePlan.ts` 15/15 (mutation run: 3 expected failures). No backend file changed, so the
backend suite was not re-run (last focused run 45 passed; full suite 1642 passed, 2 skipped).

## Browser check 1 render crash (found 2026-11-03, fixed with owner approval)
- Symptom: after generating, the plan rendered and then the route `ErrorBoundary` replaced the view
  ("Something went wrong"). Saves were unaffected (every `PUT /planner/plan` → 200).
- Root cause (reproduced in headless Chrome against the isolated environment):
  `TypeError: Cannot read properties of null (reading 'open')` in the section `<details>` `onToggle` handler of
  `Planner.tsx`. It read `e.currentTarget` inside the `setCollapsed` updater; React ran the updater later, during
  render, after resetting `currentTarget` to null. The code predates Slice B (identical at HEAD); Slice B's extra
  state updates around plan mount made the deferred path hit (inference — not verified against HEAD).
- Fix (`Planner.tsx`, one handler): read `e.currentTarget.open` synchronously in the handler and pass the boolean
  into the updater. No other logic, backend or database change.
- Verification: build `✓ built in 6.12s`; lint clean; JSX escape check OK; scratchpad headless-Chrome check
  `repro_planner.mjs` (throwaway user) 14/14 — generation without crash, plan saved with matching cached id,
  draft cleared, section collapse/expand, tick synced by PATCH, reload keeps plan + id + tick, a landed draft
  (same text, no cached id) adopted with the same server id and no conflict prompt (only a PATCH of the local tick,
  no PUT), no console errors. Before the fix the same check crashed on the first generation.

## Manual browser checks — all PASS (owner, browser, 2026-11-03, isolated local environment)
| # | Check | Result |
|---|---|---|
| 1 | Generate → saved, no banner; reload → same plan | PASS (after the check-1 render-crash fix) |
| 2 | Ticks survive reload and sign-out / sign-in | PASS |
| 3 | Tick then reload within 0.8 s → ticks re-sent and kept | PASS |
| 4 | Backend down → read-only "Offline copy — not synced"; Retry sync recovers | PASS |
| 5 | Generation failure → notice shown, nothing saved; saved plan returns | PASS |
| 6 | Save failure → draft kept; return → Save to replace it / Load saved plan; Generate disabled while saving | PASS |
| 7 | Tick-sync failure → "Your last ticks were not saved." | PASS |
| 8 | Two tabs: plan replaced in tab 2 → 409 notice in tab 1; reload → new plan | PASS |
| 9 | Pre-Slice-B cached plan, no server plan → "Not saved to your account. [Save]" → synced | PASS |

Check instructions as run:
Environment: backend `http://127.0.0.1:8010` (scratchpad launcher `run_local_backend.py`: CWD without `.env`, all
external services forced off with a startup self-check, scratchpad SQLite + embedded Qdrant, mock planner LLM,
fault toggles `FAIL_PLANNER` / `FAIL_SAVE` / `FAIL_PATCH`); frontend `http://127.0.0.1:5174` with `VITE_API_BASE`
pointing at 8010. The process on port 8000 lacks the Slice B routes and is not used.
1. Generate a plan → "Saving…" then no banner; reload → same plan, no banner.
2. Tick tasks, wait ~1 s, reload → ticks kept. Sign out, sign in → same plan and ticks.
3. Tick, then reload within 0.8 s → ticks re-sent on load and kept.
4. Stop the backend, reload → "Offline copy — not synced" with the cached plan; checkboxes, Reset and Generate
   disabled; restart the backend → [Retry sync] → normal.
5. Generation failure (`FAIL_PLANNER`) → the ⚠️ notice shows and nothing is saved; reload → the saved plan returns.
6. Save failure (`FAIL_SAVE`) with a saved plan P → generate Q → "Not saved — kept on this device until you save it."
   + Retry / Load saved plan; leave Planner and return → Q still shown with **Save to replace it** / **Load saved
   plan**; Generate disabled while "Saving…"; remove `FAIL_SAVE` → Save to replace it → synced; (variant) Load saved
   plan → P.
7. Tick-sync failure (`FAIL_PATCH`) → "Your last ticks were not saved."
8. Two tabs of the same browser (one shared session): tab 1 shows plan A; generate plan B in tab 2; tick in tab 1 →
   409 notice ("replaced by a newer one"); reload tab 1 → plan B. (Two different browsers cannot be used: signing in
   on the second ends the first session — single active session, Phase 02.)
9. A plan cached before Slice B (no `planner:planId`, no draft) with no server plan → "Not saved to your account.
   [Save]"; Save → synced.

## Unresolved / notes
- Supabase: backup before deploying 0005; confirm table grants / RLS for `study_plans` (UNVERIFIED).
- Running 0005 against PostgreSQL is UNVERIFIED (compile-level SQL check only).
- `DELETE /planner/plan` has no UI (spec R-B6 does not define one); `api.deletePlan` exists for later use.
- GET / PATCH / DELETE DB errors fall through to the global fixed-message 500 handler; only PUT has the 503 path
  (as specified) — security LOW #2, backlog.
- Long plan headings can push progress keys past 160 chars (422 on PATCH) — INFO, backlog.
- Code-quality-reviewer pass done (no BLOCKER; S1/S2 fixed via Amendment B-1; NITs in the backlog).
- C1 / C2 **not started**; no `evaluation_attempts`, no migration 0006.

## Final closure review (2026-11-03)
No BLOCKER or SHOULD-FIX findings remain. Reviewed: AC-B1–B8; migration 0005 + downgrade (throwaway SQLite only);
atomic upsert + retry + fixed 503 + concurrency test; ownership and endpoint security (security review); draft
preservation and reconciliation (Amendment B-1); generate/save race guard; all 9 browser checks; Slice A integrity.

Closure gates (run 2026-11-03, full suite not re-run per owner instruction):
| Command | Result |
|---|---|
| `uv run pytest -q tests/test_study_plans.py tests/test_migrations.py tests/test_planner_evaluator.py tests/test_planner_weak_autofill.py tests/test_stream_notice_prefix.py tests/test_progress_overview.py` | `181 passed, 14 warnings in 55.74s` |
| `uv run ruff check src tests evals` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | exit 0 |
| `git diff --check` | exit 0 |
| `cd frontend && npm run build` / `npm run lint` | `✓ built in 4.43s` / Prettier clean |
| `node frontend/scripts/check-jsx-escapes.mjs` | OK |
| `node --test` of `reconcilePlan.ts` (scratchpad) | 15 pass, 0 fail |
| Last full suite (before security fix #1 and B-1; no backend change since except fix #1, covered above) | `1642 passed, 2 skipped` |

Slice A / Amendment A-1 / Unicode-polish integrity vs the pre-Slice-B snapshot: 14 of 19 tracked Slice A files
identical; the other 5 keep every Slice A line except intentional status lines in `docs/PROJECT_STATE.md` and the
`planner.py` import expanded to a multi-line import (still importing `SHORT_TEXT_MAX`); all untracked Slice A files
byte-identical except the plan file, which received the approved Amendment B-1.

Remaining NITs (backlog, LOW): security LOW #2 (GET/PATCH/DELETE DB errors → 500), N1, N2, N3, N4 (long headings),
N5, N6 residual. Owner, before deploying 0005: Supabase backup; table grants / RLS for `study_plans`; Render runs
`alembic upgrade head` on deploy. PostgreSQL execution of 0005 remains UNVERIFIED (compile-level check only).

**Slice C1 and C2: NOT STARTED.** No `evaluation_attempts`, no migration 0006. No commit or push made.
