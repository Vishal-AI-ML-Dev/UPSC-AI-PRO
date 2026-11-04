# Phase 08 — Product persistence & gaps

Status: APPROVED (revision 4, 2026-11-03) — slices **A → B → C1 → C2**, each implemented, verified, reviewed and
reported separately; the owner approves each slice before it starts. Slice D deferred.
Owner: Vishal-AI-ML-Dev  ·  Created: 2026-11-03 (stub renumbered from 07)  ·  Related audit sections: K, Q, R; risk #11
Plan: `.claude/plans/phase-08-product-gaps.md`  ·  Decision: ADR-017 (`docs/DECISIONS.md`)

Revision history:
- rev 1: audit + roadmap.
- rev 2: owner corrections — dashboard labels; subject (not topic) terminology; server is the source of truth for
  plans; score/threshold/revision definitions; best-effort recording with a redacted warning; migrations 0005/0006;
  single active session preserved.
- rev 3: revision intervals 1/3/7/14 by consecutive passes; "Average Evaluation Score"; latest-plan-only retention;
  Slice C split into C1 and C2.
- rev 4: Slice B PUT is an atomic single-statement upsert (PostgreSQL + SQLite) with a concurrency test.
- rev 4 + Amendment B-1 (2026-11-03, owner-approved): Slice B code-quality review S1 (option a) + S2 — an unsaved
  generated plan is a persisted pending draft that is never silently replaced; Generate is blocked during a save and
  stale save responses are ignored. See R-B7 and plan § Slice B → Amendment B-1.
- rev 4 + Amendment A-1 (2026-11-03, owner-approved): Slice A review S1 + NIT 1–2 (specialist failure notices use
  the prefix; New-chat save race; prefix comment). See plan § Slice A → Amendment A-1.

## 1. Goal
Make the study plan and evaluation results real, persisted, user-scoped server data that is retrievable after
signing in on another device, and stop the Dashboard and Planner from showing placeholder or misleading numbers.

## 2. Non-goals
- **Slice D** (backlog): history delete / clear / pagination; a "my documents" list or delete for uploads and lectures.
- Plan history (only the latest plan is kept); topic-level mastery (only **subject**-level data exists).
- Simultaneous multi-device sessions. The single active session (Phase 02) is unchanged. A user who signs in on
  another device (ending the first session) can **retrieve** their saved plan and evaluation analytics there.
- Server-side Mentor history; Mentor streaming changes; persistence of cost / HTTP counters (Phase 09); the
  experiments dashboard (`experiments: []`, backlog note only).
- Live LLM calls, production writes, running Alembic, deploys, commits or pushes without explicit owner approval.

## 3. Current behaviour (verified from source, 2026-11-03)
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Server tables | `src/core/models.py` | users, conversations, messages, feedback, 3 token tables, jobs. No plans, evaluations, mastery or revision |
| Who writes history | `frontend/src/features/mentor/MentorChat.tsx:79-101` | only the Mentor, from the browser (`POST /history/messages`) |
| Client-only state | `frontend/src/lib/usePersistentState.ts`, `lib/session.ts:85-90` | all other workflows keep state in `localStorage`; `endSession()` purges it at sign-out |
| Planner | `src/api/routes/planner.py`, `agents/planner/graph.py:181` | generates only; blank weak areas become `"Not specified"`; no user passed |
| Planner copy | `features/planner/Planner.tsx:133-134` | claims blank weak areas "are auto-filled from your recent evaluations" — false |
| Evaluator | `src/api/routes/evaluator.py` | parses a score and discards it; requests have no subject field (`src/schemas.py:273-290`) |
| Progress | `src/api/routes/progress.py:170-195` | `questions` = thumbs feedback; `topics`/`weak_topics` = `[]`; `revision` = 0; activity from Mentor messages only; no tests |
| Dashboard | `features/dashboard/Dashboard.tsx:171-201` | "Answer accuracy … correct" shows the thumbs-up rate; "Questions asked … across all practice" is Mentor-only |
| Mentor conversation id | `MentorChat.tsx:24` | `useRef(null)` → after a reload the next exchange creates a new conversation (`core/history.py:48-52`) |
| Error notices | `routes/chat.py:222-229`, `lib/api.ts:969-990`, `api/stream_guard.py` `STOP_NOTICES` | notices are stream text, so `persist()` saves them as assistant answers |
| Profile weak areas | `src/graph/profile.py`, `src/graph/memory.py:183-191` | the store already keeps `weak_areas` (≤ 12) extracted from Mentor chat — reused, not rebuilt |
| Deploy migrations | `render.yaml:33` | `alembic upgrade head` runs on every deploy |

Already solved (do not redo): vector / history ownership (ADR-007), durable job status (ADR-013), checkpoint thread
scoping, per-user browser namespacing and purge, PYQ bank status, profile `weak_areas`.

## 4. Requirements

### Slice A — bug fixes and progress tests (no schema change)
- R-A1 (B2): the Mentor conversation id persists across reloads (`usePersistentState("mentor:conversationId")`) and is
  reset by "New chat".
- R-A2 (B3): a single exported `STREAM_NOTICE_PREFIX = "\n\n⚠️ "` in `lib/api.ts` is used for every notice `streamAgent`
  emits; `MentorChat.persist` saves neither message when the answer is empty or contains the prefix.
  **Amendment A-1 (owner-approved 2026-11-03):**
  - The planner / evaluator / current-affairs agent generators build their exception, timeout and empty-output
    fallbacks with the same prefix, using backend `src/core/stream_notices.py`. Validation guidance, `> ⚠️`
    disclaimers, degraded-data answers and the NCERT trust note are unchanged.
  - "New chat" bumps a conversation-generation token; a save that resolves after the bump never restores the old
    `conversationId`.
- R-A3 (B1): `/planner/generate` and `/planner/generate/sync` take the user. When `weak` is blank or "Not specified",
  it is filled from the caller's profile `weak_areas` (`load_student_profile`); fail-open to "Not specified". An
  explicit `weak` always wins. Copy becomes "Leave blank to use weak areas you've mentioned to the Mentor."
- R-A4 (B4): Dashboard labels only (data unchanged); no metric may imply that local-only workflows contribute:
  | Card | Label | Hint |
  |---|---|---|
  | questions_asked | Mentor questions | Mentor chat only |
  | feedback rate | Helpful-answer rate | `up` of `total` answers rated 👍 / no ratings yet |
  | streak | Mentor streak | days in a row with Mentor chat |
  | active_days | Mentor active days | days with Mentor chat |
  | revision | unchanged until C2 | available after subject-tagged evaluations |
  Activity strip heading: "Mentor activity (last 30 days)"; the `progress.py` docstring says the same.
- R-A5 (B5): tests for `/progress/overview`.

### Slice B — study plan persistence (migration 0005)
- R-B1 Latest plan only: at most one saved plan per user; saving a new plan replaces it. Nothing older is retained.
- R-B2 Table `study_plans`: `id` String(32) PK (new value on every replace); `user_id` FK users.id ON DELETE CASCADE,
  **UNIQUE**; `goal` String(200); `inputs` Text (JSON: hours, optional, weak, attempt_number); `plan_md` Text; `meta`
  Text (JSON from `get_plan_meta`); `progress` Text (JSON map); `created_at`, `updated_at` timestamptz. JSON as Text
  (as `jobs.payload`) for SQLite/PostgreSQL parity.
- R-B3 Endpoints (protected; rows addressed only by `user["id"]`, never a client-sent row id):
  - `GET /planner/plan` → 200 `{"plan": {...}}` or `{"plan": null}`.
  - `PUT /planner/plan` → 200 `{id, updated_at}`; replaces the caller's row (new `id`, progress reset). `goal` /
    `inputs` with `PlannerRequest` bounds; `plan_md` 1–100 000 chars.
  - `PATCH /planner/plan/progress` → body `{plan_id, progress}`; applied only if `plan_id` is the caller's current
    plan (409 if replaced, 404 if none). ≤ 1000 entries; key ≤ 160 chars matching
    `^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+:\d{1,4}$` (format `p<base36>:<n>-<slug>:<i>` from `lib/planMarkdown.ts`
    l.90-95, 179-185); values strictly `bool`; otherwise 422.
  - `DELETE /planner/plan` → 204, idempotent.
- R-B4 **Atomic upsert.** PUT is one statement, `INSERT … ON CONFLICT (user_id) DO UPDATE SET id, goal, inputs,
  plan_md, meta, progress='{}', updated_at … RETURNING id, updated_at`, built with the dialect's `insert`
  (`sqlalchemy.dialects.postgresql` / `sqlalchemy.dialects.sqlite`, chosen from the session's dialect name). No
  read-before-write. An unsupported dialect fails closed (503). A residual `IntegrityError` / `OperationalError`
  (e.g. SQLite `database is locked`) → rollback and one retry; still failing → 503 with a fixed message. Never a
  500, never raw error text, never a duplicate row.
- R-B5 **Deterministic outcome:** last writer wins at statement level. The stored row is exactly one request's
  complete payload (never a field mix); its `id` equals the `id` returned to that same request; `GET` returns it.
- R-B6 Source of truth: for an authenticated user the Planner always calls `GET /planner/plan` on mount. Server data
  is authoritative once a plan exists; `localStorage` (`planner:plan`, `planner:progress`, new `planner:planId`,
  `planner:dirty`) is only a cache and offline fallback.
  | Server | Local cache | Result |
  |---|---|---|
  | plan P | none, or a different id | replace the cache with P (plan + progress) |
  | plan P | same id, not dirty | use P |
  | plan P | same id, dirty | re-send the `PATCH` once; on success use the server response; on failure (incl. 409) show P with "your last ticks were not saved" |
  | none | a plan without `planId` | show it with "Not saved to your account — Save" (explicit user action, no auto-upload) |
  | none | none | empty state |
  | request failed | anything | show the cache read-only with "Offline copy — not synced"; no writes until a successful GET |
  After a successful stream (no `STREAM_NOTICE_PREFIX`) the plan is `PUT`; a failed save keeps it locally with
  "Not saved — Retry". Ticks update the cache, set `dirty`, and are sent as a debounced (~800 ms) `PATCH`. The
  streaming `/planner/generate` is unchanged. One active session ⇒ one writer at a time; last write wins.

- R-B7 **Amendment B-1 (owner-approved 2026-11-03).**
  - A successfully generated plan is cached as a **pending draft** (`planner:draft` = goal + the inputs that produced
    it) before its `PUT`. The draft, its ticks and its inputs stay cached until a save succeeds or the user explicitly
    chooses "Load saved plan" or generates again.
  - Reconciliation (adds to R-B6; rows without a draft are unchanged):
    | Server | Local cache | Result |
    |---|---|---|
    | plan P | pending draft Q ≠ P | show Q with "This generated plan is not saved. A different plan is saved to your account." + **Save to replace it** / **Load saved plan**; never replaced silently |
    | plan P | pending draft with P's exact text | the save landed: adopt P's id, clear the draft, send any local ticks |
    | none | pending draft Q | as R-B6 "a plan without `planId`" (Save), draft inputs used |
  - "Load saved plan" discards the draft only if a saved plan exists. A failed save or retry keeps the draft
    ("Not saved — kept on this device until you save it." + Retry, and Load saved plan when one exists).
  - Generate is disabled while a save is in flight; any save result that resolves after a newer generation, save or
    load is ignored (latest-only guard), so an old id is never attached to a different plan.
  - The decision logic lives in the import-free `features/planner/reconcilePlan.ts` and is checked with Node's
    built-in runner (scratchpad, like Slice A's `persistExchange.ts`). No backend, schema or migration change.

### Slice C1 — evaluation recording + subject dropdown (migration 0006)
- R-C1-1 Optional `subject: Subject | None = None` on `EvaluateRequest` and `MainsEvalRequest`. `Subject` is a
  `Literal` allow-list: Polity & Governance, Economy, History & Culture, Geography, Environment & Ecology, Science &
  Technology, International Relations, Society & Social Justice, Ethics, Internal Security & Disaster Management.
  Other values → 422. `Evaluator.tsx` gets a "Subject (optional)" dropdown; `api.ts` request types gain `subject?`.
- R-C1-2 Table `evaluation_attempts`: `id` PK; `user_id` FK CASCADE, indexed; `mode` String(10) `basic|mains`;
  `subject` String(60) nullable; `score` Float nullable; `max_score` Integer; `created_at` timestamptz; index
  `(user_id, subject, created_at)`. **No question or answer text is stored.** No read endpoint in C1.
- R-C1-3 Score validation (`src/core/evaluation_history.py`): `max_score` = `AnswerEvaluation.max_score` (Basic) or
  `request.marks` (Mains); it must be an integer > 0, otherwise no row. `score` is stored only if finite and
  `0 ≤ score ≤ max_score`, otherwise NULL (unscored, excluded from every percentage). `pct = score / max_score`.
- R-C1-4 Best-effort recording: `evaluate_sync` / `mains_eval_sync` take the user and call `record_attempt(...)`
  after the response is built, inside `try/except Exception`. On failure the response is unchanged and
  `logger.warning("evaluation_attempt_persist_failed", extra={"event": ..., "mode": ..., "error_type": type(exc).__name__})`
  is emitted — no question, answer, score text, email or exception message. `model-answer/sync` and Mentor-chat
  evaluator routing do not record.

### Slice C2 — subject analytics, weak subjects, revision, Dashboard (no migration)
- R-C2-1 Constants (`evaluation_history.py`): `PASS_PCT = 0.60`, `WEAK_MIN_ATTEMPTS = 3`, `MASTERY_WINDOW = 10`,
  `REVISION_INTERVALS_DAYS = (1, 3, 7, 14)`. Computed on read in `progress.py`.
- R-C2-2 **Average Evaluation Score** = mean `pct` over all scored attempts (both modes, tagged and untagged). It is
  the evaluator's own score, never labelled "accuracy". Untagged attempts contribute only here.
- R-C2-3 **Subject mastery** = mean `pct` of a subject's most recent 10 scored attempts (+ count).
- R-C2-4 **Weak subjects** = ≥ 3 scored attempts in the window and mastery < 0.60; sorted by mastery ascending, then
  subject name.
- R-C2-5 **Subject-based revision** (subjects with ≥ 1 scored attempt): `k` = consecutive most-recent scored
  attempts with `pct ≥ 0.60`. `k = 0` (latest failed) → 1 day; `k = 1` → 1; `k = 2` → 3; `k = 3` → 7; `k ≥ 4` → 14.
  `interval = 1 if k == 0 else REVISION_INTERVALS_DAYS[min(k, 4) - 1]`; `next_due = UTC date(latest scored attempt)
  + interval`; `due = next_due ≤ today (UTC)`. Each new scored attempt re-anchors; a pass increments `k`, a fail
  resets it; unscored attempts are ignored.
- R-C2-6 Contract (additive; `api.ts` `ProgressOverview` updated in the same slice): `evaluations: {attempts,
  scored, average_score_pct, by_mode: {basic, mains}}`, `subjects`, `weak_subjects` (`[{subject, mastery,
  attempts}]`), `revision: {due, total, subjects: [{subject, next_due, due}]}`. Legacy `topics` / `weak_topics` stay
  `[]` (deprecated); `questions` keeps its Slice A label.
- R-C2-7 Dashboard: "Average Evaluation Score" card, "Subject mastery" chart (reuse `MasteryChart`), "Weak subjects"
  list, "Due for revision: N subjects". No topic-level wording.
- R-C2-8 Planner autofill: weak subjects first, then profile `weak_areas`.

## 5. Affected workflows
| Workflow | Slice | How it stays working |
|---|---|---|
| AI Mentor | A | client persistence only; `/agent/chat/stream` contract unchanged |
| Study Planner | A, B, C2 | streaming `/planner/generate` unchanged; new endpoints additive; autofill fails open |
| Answer Evaluator | C1 | additive optional field; recording cannot fail the response |
| Dashboard (supporting) | A, C2 | additive contract; legacy keys kept |
| NCERT, PYQ, Current Affairs, Lecture, PDF chat | — | untouched; full suite at every slice close |

## 6. Acceptance criteria (testable)
Slice A
- [ ] AC-A1 After a reload the next Mentor exchange joins the same `conversation_id`; "New chat" starts a new one
  (build + manual; UNVERIFIED-automation — no frontend test runner).
- [ ] AC-A2 No notice is stored as an assistant message; a backend test pins that every `chat.py` error/timeout notice
  and every `STOP_NOTICES` value starts with `"\n\n⚠️ "`. (A-1) Every specialist failure fallback (planner,
  basic/mains evaluation, model answer, daily/editorial/monthly CA) contains the prefix, also when routed through
  Mentor chat. Legitimate warnings do not contain it. "New chat" during an in-flight save keeps the reset, both for
  a null id and for an existing id (Node check of `persistExchange.ts`; UI manual).
- [ ] AC-A3 With `weak` blank, U1's planner prompt contains U1's profile `weak_areas` and never U2's; store
  unavailable → "Not specified" and 200; an explicit `weak` always wins.
- [ ] AC-A4 Label diff only; `ProgressOverview` contract unchanged.
- [ ] AC-A5 `/progress/overview`: 401; empty user → zeroed shape; U2 sees none of U1's messages/feedback; streak math
  (current, longest, yesterday anchor, gaps).

Slice B
- [ ] AC-B1 A saved plan survives a restart (new DB session / new `TestClient`).
- [ ] AC-B2 401 on all 4 endpoints; happy path PUT → GET → PATCH → GET → DELETE → GET null.
- [ ] AC-B3 Isolation: U2 can never read or change U1's row; U2's PATCH with U1's `plan_id` → 404/409, U1 unchanged.
- [ ] AC-B4 Latest-only: a second PUT leaves exactly one row with a new `id` and progress reset; PATCH with the old id → 409.
- [ ] AC-B5 422 for oversize `plan_md` / `goal`, > 1000 entries, bad key format, non-bool values.
- [ ] AC-B6 **Concurrency:** two threads behind a `threading.Barrier` PUT for the same user with distinct payloads,
  repeated 20×: never a 500; never more than one row; the row's `id`, `goal` and `plan_md` all come from the same
  request, whose response `id` matches; `GET` returns it. A direct test proves the conflict path updates in place.
  The SQLite version supports `RETURNING` / `ON CONFLICT` (≥ 3.35), asserted in a test.
- [ ] AC-B7 Frontend build + lint; reconciliation table checked manually (UNVERIFIED-automation).
- [ ] AC-B8 (B-1) Node check of `reconcilePlan.ts`: a pending draft is never resolved to "use server"; a landed
  draft is adopted; the latest-only guard rejects stale saves. Browser: draft conflict choices, Generate disabled while saving.

Slice C1
- [ ] AC-C1-1 Basic and Mains each write one row with correct `user_id`, `mode`, `subject`/NULL, `score`,
  `max_score`; invalid subject → 422, no row; no subject → 200, NULL subject.
- [ ] AC-C1-2 Negative / above-max / NaN score → `score` NULL; `max_score ≤ 0` → no row.
- [ ] AC-C1-3 Regression: `record_attempt` raising → both endpoints 200 with an unchanged body; `caplog` has
  `evaluation_attempt_persist_failed` and not the question or answer text.
- [ ] AC-C1-4 Two users → two rows, correctly attributed; 401; existing response shapes unchanged.
- [ ] AC-C1-5 Frontend build + lint; dropdown checked manually.

Slice C2
- [ ] AC-C2-1 Pure-function tests with a fixed `today`: average includes untagged and both modes, excludes unscored;
  mastery window of 10; weak threshold (2 → not weak, 3 → weak, exactly 0.60 → not weak); deterministic sort incl. ties.
- [ ] AC-C2-2 Revision: pass runs 1/2/3/4/5 → 1/3/7/14/14 days; pass, pass, fail → 1; fail then pass → 1; unscored
  ignored; `next_due == today` → due.
- [ ] AC-C2-3 API: U2's overview never reflects U1's attempts; untagged attempts in no subject list; empty user → zeroed
  shape with legacy keys; 401.
- [ ] AC-C2-4 Planner autofill prefers weak subjects over profile `weak_areas`, caller-only.
- [ ] AC-C2-5 Frontend build + lint; Dashboard checked manually.

Every slice
- [ ] `uv run pytest -q` green (baseline `1520 passed, 2 skipped`); ruff; compileall; frontend build + lint when touched.
- [ ] All 8 workflows still pass their offline smoke checks (`docs/PROJECT_STATE.md`).
- [ ] `security-reviewer` pass for B, C1, C2; `code-quality-reviewer` at each slice close.
- [ ] Claude never runs Alembic, writes to production, calls live LLMs, deploys, commits or pushes without approval.

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| `alembic upgrade head` runs on every Render deploy, so 0005/0006 hit Supabase automatically | additive tables; tested `downgrade()`; owner takes a Supabase backup first; B and C1 deployed separately |
| Production LangGraph store may be in memory (risk #11) → profile `weak_areas` lost on restart | A3 fails open; owner verifies the store is Postgres |
| Supabase RLS / role grants for new tables | owner confirms (UNVERIFIED) |
| Concurrent PUT race | single-statement upsert, one retry, 503 fallback, threaded test |
| Stale deployed frontend vs new backend | additive contracts; legacy keys kept |
| Cache/server divergence | R-B6 table; server wins |
| Evaluation write fails or slows the response | write after the response is built, inside try/except; regression test |
| Optional subject → analytics may stay empty | empty states; untagged → average only |
| Older plans unreachable | latest-only by design; plan history is a backlog feature |
| No frontend test runner | build + lint + manual checks, marked UNVERIFIED-automation |

## 8. Test plan
| New test file | Slice | Pattern copied |
|---|---|---|
| `tests/test_progress_overview.py` | A | `client` + `make_user` (`tests/conftest.py`); ownership as `tests/test_chat_isolation.py` |
| `tests/test_stream_notice_prefix.py` | A | pure constants / monkeypatched graph as `tests/test_stream_guard.py` |
| `tests/test_planner_weak_autofill.py` | A, C2 | fake LLM `RunnableLambda` capturing the prompt (`tests/test_mentor_routes.py`) |
| `tests/test_study_plans.py` | B | `client`/`make_user`; threads + `threading.Barrier` for AC-B6 |
| `tests/test_evaluation_attempts.py` | C1 | monkeypatched evaluator functions; `caplog` |
| `tests/test_evaluation_analytics.py` | C2 | pure helpers as `tests/test_grounding.py`; API via `client` |

## 9. Production verification (owner only, read-only for Claude)
Supabase backup before 0005 and before 0006; confirm the LangGraph store is Postgres; table grants/RLS; after deploy:
save a plan, sign in on a second device and see the same plan; a subject-tagged evaluation appears on the Dashboard.

## 10. Open questions
None. Plan history, Slice D, experiments, UTC streak days and server-side Mentor history are in the backlog.
