# Plan — Phase 08 Product persistence & gaps

Spec: `.claude/specs/phase-08-product-gaps.md` (COMPLETE, rev 4)  ·  Status: **COMPLETE (2026-11-04)** — approved 2026-11-03; all slices executed and closed, released to production (owner-reported). The plan below is preserved unchanged as history  ·  ADR-017
Sequence: **A → B → C1 → C2**. The owner approves each slice before it starts. Each slice ends with
verification, review and a slice report (`docs/phase-reports/phase-08-slice-<x>-report.md`), then the work stops.
Hard limits for every slice: Claude does not run Alembic, write to production, call live LLMs or deploy, and does
not commit or push without explicit approval. If a file outside a slice's list must change, stop and amend this plan.

---

## Slice A — bug fixes + progress tests (no schema change)

### Files to touch (exhaustive)
| File | Change |
|---|---|
| `frontend/src/lib/api.ts` | export `STREAM_NOTICE_PREFIX = "\n\n⚠️ "`; build every `streamAgent` notice with it |
| `frontend/src/features/mentor/MentorChat.tsx` | `conversationId` via `usePersistentState("mentor:conversationId", null)`; reset in `newChat()`; `persist` skips both saves when the answer is empty or contains `STREAM_NOTICE_PREFIX` |
| `src/api/routes/planner.py` | both handlers take `user: dict = Depends(get_current_user)`; resolve blank / "Not specified" `weak` from the caller's profile `weak_areas` (fail-open) |
| `features/planner/Planner.tsx` | copy: "Leave blank to use weak areas you've mentioned to the Mentor." |
| `features/dashboard/Dashboard.tsx` | labels per spec R-A4; heading "Mentor activity (last 30 days)" |
| `src/api/routes/progress.py` | docstring only (Mentor-only activity; feedback rate) |
| `tests/test_progress_overview.py` | new (AC-A5) |
| `tests/test_stream_notice_prefix.py` | new (AC-A2) |
| `tests/test_planner_weak_autofill.py` | new (AC-A3) |
`src/agents/planner/graph.py` needs no signature change (the route resolves `weak`). If a change turns out to be
needed, amend the plan first.

### Steps
1. Write the tests first: progress overview (401, empty, isolation, streak math); notice prefix (chat timeout and
   error notices, `STOP_NOTICES`); planner autofill (two users, fake LLM captures the prompt, store unavailable,
   explicit `weak` wins). Confirm the autofill test fails before the fix.
2. Planner route: helper `_resolve_weak(weak, user_id)` → `", ".join(profile["weak_areas"])` when blank or
   "Not specified", using `get_store()` + `load_student_profile()` in try/except. Log failures with the exception
   type only.
3. Frontend: `api.ts` constant + notices; `MentorChat.tsx`; `Planner.tsx` copy; `Dashboard.tsx` labels.
4. Make the `progress.py` docstring honest.

### Reused code
`load_student_profile`, `get_store` (`src/graph/memory.py`); `usePersistentState` (`frontend/src/lib/usePersistentState.ts`);
`STOP_NOTICES` (`src/api/stream_guard.py`); `client`, `make_user` fixtures (`tests/conftest.py`).

### Rollback
Revert the listed files; no data or migration involved.

### Amendment A-1 (owner-approved 2026-11-03, after the Slice A code-quality review)
Scope: review finding S1 and NITs 1–2 only. No schema, no new dependency, no cache change.
- **S1:** Mentor chat's planner / evaluator / current-affairs routes consume the agent generators. Their
  exception, timeout and empty-output fallbacks were plain `"⚠️ …"` text without the `"\n\n"`, so MentorChat saved
  them as answers. That also put the Slice B "save only after a successful stream" check at risk. Fix: those
  fallbacks, and only those, are built with a backend `STREAM_NOTICE_PREFIX` (`"\n\n⚠️ "`). The following keep
  their current text:
  - validation guidance ("Answer too short", "Please provide …");
  - blockquote disclaimers and degraded-data answers (`> ⚠️ …`: date not available, live feed unavailable, no
    monthly archive);
  - the NCERT trust note.
  There is no blanket prefixing of messages that start with `⚠️`.
- **NIT 1:** a conversation-generation token. "New chat" bumps it, and a save that resolves afterwards does not
  restore the old `conversationId`. The assistant reply still joins the conversation its question went to.
- **NIT 2:** the `STREAM_NOTICE_PREFIX` comment covers only `streamAgent` notices and the known Mentor-chat
  error/stop notices.
- The response-cache replay of failure text (`src/api/routes/chat.py` `cache.set`) stays a separate MED backlog item.

| File | Change |
|---|---|
| `src/core/stream_notices.py` | new: `STREAM_NOTICE_PREFIX`, `stream_notice(message)` |
| `src/agents/planner/graph.py` | plan-generation failure → `stream_notice(...)` |
| `src/agents/evaluator/graph.py` | basic / mains evaluation and model-answer failures → `stream_notice(...)` |
| `src/agents/current_affairs/graph.py` | daily / editorial / monthly generation failures → `stream_notice(...)` |
| `frontend/src/features/mentor/persistExchange.ts` | new, import-free: notice/empty skip + generation-token guard (so the shipped code can be checked with Node's built-in runner) |
| `frontend/src/features/mentor/MentorChat.tsx` | uses `persistExchange`; `newChat()` bumps the generation |
| `frontend/src/lib/api.ts` | comment only |
| `tests/test_stream_notice_prefix.py` | specialist failure paths; legitimate warnings untouched; Mentor chat end-to-end |
| `tests/test_planner_evaluator.py` | exact failure strings updated to the prefixed contract (assertions kept exact) |

Verification: focused pytest for the touched agents and routes, ruff, compileall, frontend build + lint, and
an ad-hoc `node --test` of `persistExchange.ts` in the session scratchpad. That last check is not a repo test,
because the frontend has no test runner.

---

## Slice B — study plan persistence (migration 0005)

### Files to touch (exhaustive)
| File | Change |
|---|---|
| `src/core/models.py` | `StudyPlan` model (spec R-B2; `user_id` UNIQUE, FK CASCADE) |
| `migrations/versions/0005_study_plans.py` | create table + unique constraint; `downgrade()` drops it. Written, **never run by Claude** |
| `src/schemas.py` | `StudyPlanSaveRequest`, `StudyPlanProgressRequest` (bounds + key regex + strict bool), `StudyPlanResponse` |
| `src/core/study_plans.py` | new: `get_plan`, `upsert_plan` (atomic), `update_progress`, `delete_plan`; all keyed by `user_id` |
| `src/api/routes/planner.py` | `GET`/`PUT`/`DELETE /planner/plan`, `PATCH /planner/plan/progress` (plain `def` handlers) |
| `frontend/src/lib/api.ts` | `getPlan`, `savePlan`, `patchPlanProgress`, `deletePlan` + types |
| `frontend/src/features/planner/Planner.tsx` | reconciliation (spec R-B6), save after generation, debounced progress `PATCH`, status notices |
| `tests/test_study_plans.py` | new (AC-B1–B6) |

### Steps
1. Model + migration 0005 (additive, portable types; `downgrade` drops the table).
2. `upsert_plan`: pick `sqlalchemy.dialects.postgresql.insert` or `sqlalchemy.dialects.sqlite.insert` from
   `db.get_bind().dialect.name` (other dialect → `RuntimeError` → route 503). One statement:
   `insert(...).values(...).on_conflict_do_update(index_elements=[user_id], set_={id, goal, inputs, plan_md, meta,
   progress: "{}", updated_at}).returning(id, updated_at)`, then commit. On `IntegrityError` / `OperationalError`:
   rollback, retry once, then raise a domain error → 503 with a fixed message. No read-before-write.
3. `update_progress(user_id, plan_id, progress)`: `UPDATE … WHERE user_id = :uid AND id = :plan_id`; 0 rows → 409 if
   the user has a plan, else 404.
4. Routes + schemas; another user's data can never be addressed (no row id in GET/PUT/DELETE paths).
5. Tests: 401 ×4; happy path; isolation; latest-only + stale `plan_id` → 409; 422 cases; restart survival; SQLite
   ≥ 3.35 assertion; **concurrency** (2 threads × `threading.Barrier`, 20 rounds, distinct payloads → no 500, one
   row, row fields and `id` all from one request whose response `id` matches, `GET` agrees); direct conflict-path test.
6. Frontend reconciliation per spec R-B6.
7. `security-reviewer` pass (new user-owned table + endpoints).

### Reused code
`get_current_user` (`src/api/deps.py`); `get_db` (`src/core/db.py`); the ownership-query pattern
(`src/core/history.py:get_conversation`); `get_plan_meta` (`src/agents/planner/graph.py`); `UserFacingError` (ADR-008).

### Rollback
Revert the listed files. If 0005 was deployed: the owner runs `alembic downgrade 0004` after a backup (Claude does
not). The table is additive, so older code ignores it.

### Amendment B-1 (owner-approved 2026-11-03, after the Slice B code-quality review)
Scope: review SHOULD-FIX S1 (option a — preserve the unsaved generated plan) and S2 (no generation during a save).
Frontend only; no backend, schema, migration or dependency change. NITs N1–N6 go to the backlog.

| File | Change |
|---|---|
| `frontend/src/features/planner/reconcilePlan.ts` | new, import-free: `decideReconcile` (R-B6 + R-B7 decisions) and `createLatestGuard` (latest-only save results) |
| `frontend/src/features/planner/Planner.tsx` | persisted `planner:draft`; `draftConflict` state with Save to replace it / Load saved plan; failed save keeps the draft; Generate blocked while saving; save results guarded |

Verification: frontend build + lint + JSX escape check; ad-hoc `node --test` of `reconcilePlan.ts` in the session
scratchpad (not a repo test — no frontend test runner), including a mutation run showing the S1 cases fail on the
pre-fix logic; the 9 (+B-1) browser checks.

---

## Slice C1 — evaluation recording + subject dropdown (migration 0006)

### Files to touch (exhaustive)
| File | Change |
|---|---|
| `src/core/models.py` | `EvaluationAttempt` model (spec R-C1-2) |
| `migrations/versions/0006_evaluation_attempts.py` | create table + index; `downgrade()` drops it. **Never run by Claude** |
| `src/schemas.py` | `Subject` Literal; optional `subject` on `EvaluateRequest`, `MainsEvalRequest` |
| `src/core/evaluation_history.py` | new: `validated_score`, `record_attempt` (no question/answer text) |
| `src/api/routes/evaluator.py` | `evaluate_sync` / `mains_eval_sync` take the user; best-effort record after building the response; redacted warning |
| `frontend/src/lib/api.ts` | `subject?` on evaluator request types; exported subject list |
| `frontend/src/features/evaluator/Evaluator.tsx` | "Subject (optional)" dropdown (persisted with `usePersistentState`) |
| `tests/test_evaluation_attempts.py` | new (AC-C1-1–4) |

### Steps
1. Model + 0006. 2. `Subject` allow-list + request fields. 3. `validated_score` (finite, 0 ≤ score ≤ max, max int > 0).
4. `record_attempt` opens its own short session (`SessionLocal`), so a failure cannot affect the request session.
5. Route wiring inside `try/except Exception` → `logger.warning("evaluation_attempt_persist_failed", extra={...})`.
6. Tests incl. the failed-persistence regression with `caplog` checking that no question/answer text is logged.
7. Dropdown. 8. `security-reviewer` pass.

### Rollback
Revert the files; the owner downgrades to 0005 if 0006 was deployed. The request field is optional, so old clients
keep working.

---

## Slice C2 — subject analytics, weak subjects, revision, Dashboard (no migration)

### Files to touch (exhaustive)
| File | Change |
|---|---|
| `src/core/evaluation_history.py` | constants + pure functions: `average_score`, `subject_mastery`, `weak_subjects`, `revision_schedule` (spec R-C2-1–5) |
| `src/api/routes/progress.py` | add `evaluations`, `subjects`, `weak_subjects`, `revision.subjects`; keep legacy keys `[]`; caller-only query |
| `src/api/routes/planner.py` | autofill: weak subjects first, then profile `weak_areas` |
| `frontend/src/lib/api.ts` | `ProgressOverview` additive fields |
| `frontend/src/features/dashboard/Dashboard.tsx` | "Average Evaluation Score", "Subject mastery" (`MasteryChart`), "Weak subjects", "Due for revision: N subjects" |
| `tests/test_evaluation_analytics.py` | new (AC-C2-1–3) |
| `tests/test_planner_weak_autofill.py` | extend (AC-C2-4) |

### Steps
1. Pure functions with a `today` parameter; `interval = 1 if k == 0 else REVISION_INTERVALS_DAYS[min(k, 4) - 1]`.
2. Progress route: one query for the caller's scored attempts, bounded (latest 500), then the pure functions.
3. Dashboard + types. 4. Planner autofill precedence. 5. Tests. 6. `security-reviewer` pass.

### Rollback
Revert the files (no data change).

### Amendment C2-1 (owner-approved 2026-11-04, before implementation)
- `tests/test_progress_overview.py` (Slice A) asserts the exact pre-C2 shape. Only two expectations change, to the
  additive R-C2-6 contract: `TOP_KEYS` gains `evaluations`, `subjects`, `weak_subjects`; the empty-user `revision`
  becomes `{"due": 0, "total": 0, "subjects": []}`. Every other assertion in that file is unchanged.
- R-C2-8 interpretation: when `weak` is blank / "Not specified", the planner gets the caller's weak subjects
  **first**, then the profile `weak_areas` (case-insensitive de-duplication), bounded to `SHORT_TEXT_MAX`. Each
  source fails open on its own; both unavailable/empty → "Not specified". An explicit `weak` still wins and reads
  neither source.

| File | Change |
|---|---|
| `tests/test_progress_overview.py` | `TOP_KEYS` + empty `revision` expectation only |

---

## Verification (every slice; offline only)
```bash
uv run pytest -q                                  # baseline 1520 passed, 2 skipped
uv run pytest -q tests/<new slice tests>.py
uv run ruff check src tests evals
uv run python -m compileall -q src tests
cd frontend && npm run build && npm run lint      # when the frontend changed
git diff --check
```
Manual checks are listed per AC and marked UNVERIFIED-automation (no frontend test runner). Paste every summary line
into the slice report; anything not run is UNVERIFIED. All 8 workflows must still pass their offline smoke checks.

## Production verification (owner only)
Supabase backup before 0005 and before 0006; confirm the LangGraph store is Postgres; confirm table grants / RLS;
post-deploy smoke: save a plan, sign in on a second device and see it; a subject-tagged evaluation appears on the Dashboard.
