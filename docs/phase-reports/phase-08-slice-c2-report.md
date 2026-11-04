# Phase 08 Slice C2 report — subject analytics, weak subjects, revision, Dashboard (no migration)

Date: 2026-11-04 · Spec `.claude/specs/phase-08-product-gaps.md` (rev 4, "Slice C2") · Plan
`.claude/plans/phase-08-product-gaps.md` ("Slice C2" + **Amendment C2-1**) · ADR-017.

Status: **CLOSED (2026-11-04).** AC-C2-1–5 PASS. All eight manual browser checks are owner-confirmed PASS in an
isolated local environment; backend evidence is listed separately below. **Phase 08 is COMPLETE** with this slice.

No migration, no new dependency, no live LLM call, no production or Supabase access, no Alembic run, no deploy, no
commit or push. The uncommitted working tree from Slices A (+ A-1), the Unicode polish, B (+ B-1) and C1 was
preserved; nothing was reset, reverted, cleaned or overwritten. **Migrations 0005 and 0006 have never been executed
against production or any real database** (only on throwaway SQLite in tests; the browser environment used
`create_all`).

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC-C2-1 Pure functions with a fixed `today`: average includes untagged and both modes, excludes unscored; mastery window of 10; weak threshold (2 → not weak, 3 → weak, exactly 0.60 → not weak); deterministic sort incl. ties | **PASS** | `tests/test_evaluation_analytics.py`: `test_average_includes_untagged_and_both_modes_excludes_unscored`, `test_average_with_no_scored_attempts_is_zeroed`, `test_invalid_rows_are_never_scored`, `test_mastery_uses_the_latest_ten_scored_attempts`, `test_mastery_window_skips_unscored_attempts`, `test_untagged_attempts_are_in_no_subject_list`, `test_two_low_attempts_are_not_weak_three_are`, `test_mastery_exactly_sixty_percent_is_not_weak` (6/10, 9/15 and float means), `test_just_below_sixty_percent_is_weak`, `test_weak_threshold_uses_the_window_not_old_history`, `test_sort_is_deterministic_with_ties` (10 seeded shuffles) |
| AC-C2-2 Revision: pass runs 1/2/3/4/5 → 1/3/7/14/14 days; pass, pass, fail → 1; fail then pass → 1; unscored ignored; `next_due == today` → due | **PASS** | `test_interval_table`, `test_pass_runs_extend_the_interval`, `test_fail_resets_the_run` (incl. `unscored-between`, `exact-60`), `test_unscored_attempts_are_ignored_and_do_not_reanchor`, `test_next_due_today_is_due_tomorrow_is_not`, `test_each_scored_attempt_reanchors`, `test_anchor_is_the_utc_date`, `test_schedule_counts_and_order` |
| AC-C2-3 API: U2 never sees U1's attempts; untagged in no subject list; empty user → zeroed shape with legacy keys; 401 | **PASS** | `test_overview_requires_auth` (no token / bad token), `test_empty_user_gets_zeroed_analytics_and_legacy_keys`, `test_two_users_are_isolated` (both directions), `test_load_attempts_is_caller_only_bounded_and_newest_first`, `test_analytics_failure_keeps_mentor_stats` (redacted log), `test_evaluation_through_the_api_shows_on_the_overview` (C1 recording → C2 overview, other user unaffected); `tests/test_progress_overview.py` (Slice A) passes with the Amendment C2-1 shape |
| AC-C2-4 Planner autofill prefers weak subjects over profile `weak_areas`, caller-only | **PASS** | `tests/test_planner_weak_autofill.py` (C2 section): `test_weak_subjects_come_before_profile_areas_caller_only` (sync + stream; exact rendered `Weak area(s)` line), `test_weak_subjects_used_without_a_profile`, `test_not_yet_weak_subjects_are_not_used`, `test_duplicates_are_removed_case_insensitively`, `test_explicit_weak_reads_neither_source`, `test_weak_subject_failure_falls_back_to_profile` (sync + stream; redacted log), `test_store_failure_still_uses_weak_subjects`, `test_both_sources_down_fall_back_to_not_specified`; all Slice A autofill tests unchanged and passing |
| AC-C2-5 Frontend build + lint; Dashboard checked manually | **PASS**: build, lint and JSX escape check PASS (run by Claude); manual browser checks 1–8 **owner-confirmed PASS** | see Verification and Manual browser checks |

Every slice: full suite green; ruff; compileall; `security-reviewer` and `code-quality-reviewer` passes done (below).
The 8 workflows are covered by the full offline suite. Only the Study Planner (autofill source order) and the
supporting Dashboard changed; the Answer Evaluator is untouched in C2.

## Implementation summary
- **`src/core/evaluation_history.py`** (pure functions; percentages are fractions in [0, 1]):
  - Constants (R-C2-1): `PASS_PCT = 0.60`, `WEAK_MIN_ATTEMPTS = 3`, `MASTERY_WINDOW = 10`,
    `REVISION_INTERVALS_DAYS = (1, 3, 7, 14)`, plus `ANALYTICS_ATTEMPT_LIMIT = 500` (plan step 2 read bound).
  - `Attempt` (mode, subject, score, max_score, created_at); `attempt_pct` re-validates with the C1 helpers;
    `passes(pct)` = `pct ≥ 0.60` with a 1e-9 float tolerance, so exactly 0.60 passes and is never weak.
  - `average_score` (R-C2-2): `{attempts, scored, average_score_pct, by_mode: {basic, mains}}`, each mode with the
    same three fields; unscored rows count in `attempts` only; 0.0 when nothing is scored.
  - `subject_mastery` (R-C2-3): mean of each subject's latest 10 scored attempts; `attempts` = scored attempts in the
    window (≤ 10). `weak_subjects` (R-C2-4): ≥ 3 in the window and mastery < 0.60. Both sorted by unrounded mastery
    ascending, then subject name; output `[{subject, mastery (4 dp), attempts}]`.
  - `revision_schedule(attempts, today)` (R-C2-5): `k` = consecutive most-recent scored passes;
    `interval = 1 if k == 0 else (1, 3, 7, 14)[min(k, 4) - 1]`; `next_due` = UTC date of the latest scored attempt
    + interval; `due = next_due ≤ today`. Unscored and untagged ignored; naive timestamps treated as UTC. Output
    `{due, total, subjects: [{subject, next_due (YYYY-MM-DD), due}]}` sorted by `next_due`, then subject.
  - `load_attempts(db, user_id, limit=500)`: always `WHERE user_id = :caller`, newest first, empty id → `ValueError`.
    `weak_subject_names(user_id)` uses its own short session.
- **`src/api/routes/progress.py`**: adds `evaluations`, `subjects`, `weak_subjects` and `revision.subjects`
  (R-C2-6, additive). Legacy `topics` / `weak_topics` stay `[]`; `questions` keeps its Slice A meaning. The analytics
  block fails open on its own (zeroed keys, `db.rollback()`, warning with the exception type only), so the Mentor
  stats still render. Docstring updated.
- **`src/api/routes/planner.py`** (R-C2-8 / Amendment C2-1): blank or "Not specified" `weak` → caller's weak subjects
  first, then the Mentor profile `weak_areas`, de-duplicated case-insensitively, bounded to `SHORT_TEXT_MAX`. Each
  source fails open separately (type-only warning); both empty/unavailable → "Not specified". An explicit `weak`
  wins and reads neither source.
- **`frontend/src/lib/api.ts`**: `EvaluationScoreSummary`, `SubjectMastery`, `SubjectRevision`; `ProgressOverview`
  gains `evaluations`, `subjects`, `weak_subjects`, `revision.subjects`; `topics` / `weak_topics` marked deprecated.
- **`frontend/src/features/dashboard/Dashboard.tsx`** (R-C2-7): "Average Evaluation Score" card ("—" until a scored
  evaluation exists), "Due for revision" card (N subjects of M evaluated), "Subject mastery" chart (reuses
  `MasteryChart`; weak bars red), "Weak subjects" list, "Due for revision: N subjects" list ("Due now" / "Next: date").
  Every section has an empty state; no topic-level wording remains.

### Amendment C2-1 (owner-approved 2026-11-04, before implementation)
- `tests/test_progress_overview.py` (Slice A): only `TOP_KEYS` (+ `evaluations`, `subjects`, `weak_subjects`) and the
  empty-user `revision` expectation (`{"due": 0, "total": 0, "subjects": []}`) changed. All other assertions intact.
- Autofill interpretation "merge, weak subjects first" (above).

## Files changed (plan table + Amendment C2-1)
| File | Change |
|---|---|
| `src/core/evaluation_history.py` | C2 constants + pure analytics + `load_attempts` / `weak_subject_names` |
| `src/api/routes/progress.py` | additive analytics keys; isolated fail-open; docstring |
| `src/api/routes/planner.py` | `_resolve_weak` source order; `_weak_subjects`, `_profile_weak_areas` |
| `frontend/src/lib/api.ts` | `ProgressOverview` additive types |
| `frontend/src/features/dashboard/Dashboard.tsx` | C2 cards, chart, lists |
| `tests/test_evaluation_analytics.py` | new (AC-C2-1–3) |
| `tests/test_planner_weak_autofill.py` | C2 section appended (AC-C2-4) |
| `tests/test_progress_overview.py` | Amendment C2-1 shape only |
| `.claude/plans/phase-08-product-gaps.md` | Amendment C2-1 recorded |
| `docs/phase-reports/phase-08-slice-c2-report.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md` | closure docs (this step; documentation only) |

## Verification (run by Claude in this session, 2026-11-04)
| Command | Result |
|---|---|
| `uv run pytest -q` (full suite, run once) | `1765 passed, 2 skipped, 19 warnings in 213.62s` (the 2 skips are the known Windows symlink tests) |
| Focused rerun after the two post-suite edits (security LOW `db.rollback()`, `unscored-between` test case): `tests/test_evaluation_analytics.py tests/test_progress_overview.py tests/test_planner_weak_autofill.py tests/test_evaluation_attempts.py` | `162 passed, 6 warnings in 32.09s` |
| `uv run ruff check src tests evals` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | exit 0 |
| `git diff --check` | exit 0 (only CRLF notices) |
| `cd frontend && npm run build` | built OK (tsc + vite) |
| `cd frontend && npm run lint` | `All matched files use Prettier code style!` |
| `node frontend/scripts/check-jsx-escapes.mjs` | `OK: no literal escape sequences …` |

The full suite was not rerun at closure (owner instruction); the closure step changed documentation only.

## Reviews
- **`security-reviewer`: PASS — no CRITICAL / HIGH / MEDIUM.** Confirmed: every C2 read filtered by the
  authenticated id, no client-supplied ids, no cross-user leakage into the planner prompt, type-only logs, 500-row
  bounded indexed read, subjects from the C1 server allow-list (no new injection surface), React-escaped rendering.
  LOW: add `db.rollback()` in the analytics failure path → **fixed**. LOW/INFO → backlog: the pre-existing outer
  `progress_overview` handler logs with `exc_info=True`; no rate limit on overview/planner (pre-existing).
- **`code-quality-reviewer`: APPROVE — no BLOCKER / SHOULD-FIX.** Every threshold and calculation confirmed against
  R-C2-1–5. NITs: #6 (unscored attempt between passes) → **test added**; #5 (literal "—" vs `—` in a JS string)
  → no change (renders identically); #1, #2, #3, #4 → backlog (below); #7 (test pins the 500 constant) → kept.

## Manual browser checks — all owner-confirmed PASS (2026-11-04, isolated local environment)

### Environment (set up by Claude, scratchpad only; no project file changed)
- **Backend:** `http://127.0.0.1:8012`, launched from the session scratchpad (`c2env/launch_c2_backend.py`), so the
  repo `.env` was never loaded (the launcher refuses to start from any other directory).
  - Startup self-check PASS (16 checks): database URL = scratchpad SQLite; Google, Groq, OpenAI-compatible, Cohere,
    Tavily keys empty; web search off; remote Qdrant empty (embedded, scratchpad); SMTP, Sentry, Langfuse,
    Upstash, Redis, R2 and Supabase settings empty/off; not production; CORS allows the 5176 origin.
  - **Schema built with `Base.metadata.create_all`; the app's Alembic `init_db` was replaced; Alembic never ran.**
  - LangGraph checkpointer file and embedded Qdrant in the scratchpad; profile store in memory.
  - Planner and evaluator LLMs were deterministic mocks (the planner mock echoes the rendered `Weak area(s)` line).
    Every non-loopback socket connection was blocked and logged: **0 blocked attempts** in the session log.
  - Request log + planner autofill log in the scratchpad `requests.log`.
- **Frontend:** Vite dev server `http://127.0.0.1:5176` (`--strictPort`, loopback), `VITE_API_BASE` set in the
  process environment; the served `src/lib/config.ts` contained `http://127.0.0.1:8012/api/v1`; CORS preflight
  200; fresh origin (no stale localStorage). The pre-existing listener on 5175 was left untouched.
- **Seed (deterministic, relative to today's UTC date 2026-11-04):**
  - `c2.analytics@example.com`: 27 attempts (26 scored) — Economy 2/3/4 of 10 + 1 unscored; History & Culture
    4 × 5/10; Geography 1/10, 2/10; Ethics 3 × 9/15 (exactly 60 %); Polity & Governance 2 × 0/10 then 10 × 8/10;
    2 untagged (10/10, 15/15). Mentor profile `weak_areas` = ["Answer writing speed", "economy"].
  - `c2.empty@example.com`: no attempts; Mentor profile `weak_areas` = ["Map work"].
  - Expected values were computed by the real functions and hand-checked (e.g. average 15.0 / 26 = 0.5769); a
    pre-browser API smoke via real login confirmed `/progress/overview` equals them exactly for both users and
    `/planner/generate/sync` resolved "Economy, History & Culture, Answer writing speed" and "Map work".

### Results
The owner confirmed all eight checks as PASS in one message (only check 1 was walked through step by step in the
session).
| # | Check | Owner result | Independent backend evidence (8012 `requests.log`, API smoke) |
|---|---|---|---|
| 1 | Analytics user: "Average Evaluation Score" 58 % ("across 26 scored answers"); "Due for revision" 3 subjects ("of 5 evaluated subjects") | **PASS** | 00:43:56 login + `GET /progress/overview` 200; API smoke: overview equals expected |
| 2 | "Subject mastery" chart: Geography 15, Economy 30, History & Culture 50, Ethics 60, Polity & Governance 80 (%); Economy and History & Culture red | **PASS** | same response (API smoke) |
| 3 | "Weak subjects": Economy 30 % · 3 answers, History & Culture 50 % · 4 answers; Geography (2 attempts) and Ethics (exactly 60 %) absent | **PASS** | same response (API smoke) |
| 4 | "Due for revision: 3 subjects": Geography, Economy, History & Culture "Due now" (History `next_due` = today); Ethics "Next: 2026-11-10"; Polity "Next: 2026-11-17" | **PASS** | same response (API smoke) |
| 5 | No topic-level wording on the Dashboard ("Topic mastery" gone) | **PASS** | UI only; `Dashboard.tsx` has no user-visible "topic" text (only the internal chart data key `topic`) |
| 6 | Analytics user, Planner with Weak areas blank → "Economy, History & Culture, Answer writing speed" (weak subjects first, duplicate "economy" dropped) | **PASS** | **Verified**: 00:48:16 and 00:48:20 `POST /planner/generate` → `planner autofill user=ce5d7388 input='Not specified' -> resolved='Economy, History & Culture, Answer writing speed'`; plans saved (`PUT /planner/plan` 200) |
| 7 | Empty user: Dashboard empty states ("—" / "no scored evaluations yet", "0 subjects", empty mastery / weak / revision messages) | **PASS** | 00:46:36 login + `GET /progress/overview` 200 (user not identifiable from the log); API smoke: zeroed shape |
| 8 | Empty user, Planner with Weak areas blank → "Map work" (profile fallback when no weak subjects) | **PASS** | **No browser request captured** (the log has planner calls for the analytics user only); API smoke resolved "Map work"; covered by `test_weak_subject_failure_falls_back_to_profile` / Slice A tests |

Other log lines: 00:43:47 `POST /auth/register` → 400 (sign-up attempted with a seeded email; expected); 00:48:27 a
generate with the autofilled text typed explicitly (explicit value wins, unchanged); 00:48:32 `PATCH
/planner/plan/progress` 200 (Slice B ticks still work).

## Known limitations
- **Read bound:** analytics use the caller's latest 500 attempts (scored and unscored). Above 500, counts and the
  average cover only those, and a subject not evaluated within them drops out of mastery / weak / revision.
- Subject-level only; untagged evaluations affect only the average (by design).
- Revision days and `next_due` are UTC dates; IST users near midnight may see a day shift (as the UTC streak).
- Mentor profile `weak_areas` come from the LangGraph store; if production's store is in memory (risk #11) they are
  lost on restart (autofill then uses weak subjects only, fail-open).
- No frontend test runner: Dashboard rendering is verified manually only (UNVERIFIED-automation).
- **Production (owner only, UNVERIFIED):** migrations 0005 and 0006 have **not** been executed against production
  or Supabase; Supabase backup before each; grants / RLS for `study_plans` and `evaluation_attempts`; confirm the
  LangGraph store is Postgres; post-deploy smoke (saved plan on a second device; subject-tagged evaluation appears
  on the Dashboard). Render runs `alembic upgrade head` on deploy.

## Backlog added by C2 (all LOW / INFO)
- (C2 code-quality NIT 1) `src/core/evaluation_history.py:load_attempts` 500-row bound counts unscored rows and
  can hide long-unevaluated subjects for very heavy users; option: exact `COUNT` + per-subject window — LOW.
- (C2 code-quality NIT 2) no index serves `WHERE user_id ORDER BY created_at DESC` exactly (composite is
  `(user_id, subject, created_at)`); fine at current scale; would need a migration — INFO.
- (C2 code-quality NIT 3, pre-existing since Slice A) `src/api/routes/planner.py:_resolve_weak` truncation at
  `SHORT_TEXT_MAX` can cut the last item mid-word; option: drop whole items — LOW.
- (C2 code-quality NIT 4) `Dashboard.tsx` overdue revision rows show "Due now" without the due date — LOW.
- (C2 security LOW/INFO, pre-existing) `src/api/routes/progress.py` outer `except` logs with `exc_info=True`
  (driver tracebacks may include SQL / bound user id); `/progress/overview` and planner generation have no
  per-route rate limit beyond the global limiter — LOW.

## Workflows
No workflow regressed (full offline suite green). Changed in C2: Study Planner (autofill source order, fail-open) and
the supporting Dashboard (additive contract). NCERT, PYQ, Current Affairs, Lecture, PDF chat, AI Mentor and the
Answer Evaluator were not touched in C2.
