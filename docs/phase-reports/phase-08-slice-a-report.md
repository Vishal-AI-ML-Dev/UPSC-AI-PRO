# Phase 08 Slice A report — bug fixes + progress tests

Date: 2026-11-03 · Spec: `.claude/specs/phase-08-product-gaps.md` (APPROVED rev 4 + Amendment A-1) · Plan:
`.claude/plans/phase-08-product-gaps.md` (Slice A + Amendment A-1) · ADR: ADR-017 · Status: **CLOSED (2026-11-03)** —
Slice A (A1–A5) and Amendment A-1 verified offline; review findings S1 / NIT 1 / NIT 2 fixed; final review no
BLOCKER / SHOULD-FIX; owner manual browser checks all PASS. **Slice B NOT STARTED**; C1 / C2 not started.

No schema change, no migration, no new dependency, no live LLM call, no production write, no commit or push.

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC-A1 Mentor conversation id persists across reloads; "New chat" resets it | **PASS** (Node check of the reset race + owner browser check 2026-11-03: reload continuity, New Chat, in-flight save race) | `MentorChat.tsx`: `usePersistentState("mentor:conversationId", null)`; `newChat()` bumps `conversationGen` and calls `setConversationId(null)`. Per-user namespacing and sign-out purge come from `lib/session.ts`. `persistExchange.ts` has the generation guard (A-1 / NIT 1), checked with Node. Manual check 1 below. |
| AC-A2 No notice stored as an assistant message | **PASS** (backend + Node check of the save logic + owner browser check: specialist failures not in Session History) | `tests/test_stream_notice_prefix.py` (44 tests). See the next section. |
| AC-A3 Planner autofill: caller's profile only; fail-open; explicit wins | **PASS** | `tests/test_planner_weak_autofill.py` (sync + stream): U1's prompt contains U1's `weak_areas`, never U2's (and vice versa) for omitted / `""` / spaces / "Not specified" (any case); explicit `weak` wins and the store is not read; store raising or `None` → 200 with "Not specified"; the warning logs the exception type only; unusable profiles fall back; the autofilled value is bounded to `SHORT_TEXT_MAX` (200). |
| AC-A4 Labels only; `ProgressOverview` contract unchanged | **PASS** (build + diff + owner browser check of the labels) | `Dashboard.tsx` labels/hints exactly per spec R-A4; heading "Mentor activity (last 30 days)"; `api.ts` `ProgressOverview` untouched; `progress.py` docstring only; data sources unchanged. Planner copy: "Leave blank to use weak areas you've mentioned to the Mentor." |
| AC-A5 `/progress/overview` tests | **PASS** | `tests/test_progress_overview.py` (18 tests): 401 (no / bad token); zeroed shape for a new user; U2 sees none of U1's messages or feedback (written through the real routes); only `user` messages count; streak via the API (current, longest, active today, per-day counts, 30-day window); yesterday anchor; 2-day gap breaks the streak; pure `_streak` table incl. month/year boundaries. |

### AC-A2 evidence (incl. Amendment A-1)
- **Stop notices and Mentor-chat errors:**
  - All 3 `STOP_NOTICES` start with `"\n\n⚠️ "`.
  - The `chat.py` timeout and error notices are checked through the real `/agent/chat/stream`.
  - `api.ts` exports `STREAM_NOTICE_PREFIX` equal to the backend value; every `streamAgent` notice uses it.
- **Specialist failures (S1).** The 7 genuine failure fallbacks now go through `stream_notice()` from the new
  `src/core/stream_notices.py`:
  - planner;
  - basic evaluation, Mains evaluation and model answer;
  - daily, editorial and monthly current affairs.

  For each one, under a provider error, an `LLMTimeoutError` and empty model output (21 cases), the **last chunk is
  exactly** `PREFIX + message`, earlier chunks (headers, disclaimers) never contain the prefix, and no exception text
  leaks.
- **Legitimate output is unchanged:**
  - successful output of all 7 generators;
  - the "Answer too short" / missing-question guidance;
  - "current affairs not available for that date", "live news feed unavailable" and "no monthly archive"
    (`> ⚠️` blockquotes).

  None of these contain the prefix. The NCERT trust note is untouched.
- **Mentor chat end to end:** the planner, evaluator and current-affairs routes with a failing model, through the
  real `/agent/chat/stream`. The streamed text contains the prefix, and the user's Session History stays empty.
- **Save logic** (`persistExchange.ts`, Node check): nothing is saved for an empty answer, a bare notice, a partial
  answer followed by a stop notice, or a specialist failure. A `> ⚠️` disclaimer answer is saved.
- **Pre-fix evidence:** with the three agent files reverted, 23 of the 44 tests fail. One end-to-end case
  (current affairs) passes either way, because the CA header already ends in `"\n\n"`. The chunk-level tests cover
  that path.
- **Slice B risk resolved:** `generate_plan`'s failure (and a failure after partial output) now contains
  `STREAM_NOTICE_PREFIX`, so Slice B's "save only after a successful stream" check can rely on it.

## Files changed (all in the Slice A plan table + Amendment A-1)
| File | Change |
|---|---|
| `frontend/src/lib/api.ts` | `export const STREAM_NOTICE_PREFIX = "\n\n⚠️ "`; the 5 `streamAgent` notices use it; comment narrowed to its real contract (A-1 / NIT 2) |
| `frontend/src/features/mentor/MentorChat.tsx` | persisted `mentor:conversationId`; `newChat()` bumps the generation and resets the id; `persist` delegates to `persistExchange` |
| `frontend/src/features/mentor/persistExchange.ts` | new, import-free: empty/notice skip, generation-token guard, reply joins its question's conversation (A-1) |
| `src/core/stream_notices.py` | new: backend `STREAM_NOTICE_PREFIX` + `stream_notice()` (A-1) |
| `src/agents/planner/graph.py` | plan-generation failure → `stream_notice(...)` (A-1) |
| `src/agents/evaluator/graph.py` | 2 evaluation failures + model-answer failure → `stream_notice(...)` (A-1) |
| `src/agents/current_affairs/graph.py` | daily / editorial / monthly failures → `stream_notice(...)` (A-1) |
| `src/api/routes/planner.py` | both handlers take the user; `_resolve_weak()` (caller's profile `weak_areas`, bounded, fail-open, type-only log). The streaming handler resolves inside the generator, i.e. in the stream guard's worker thread |
| `frontend/src/features/planner/Planner.tsx` | copy only |
| `frontend/src/features/dashboard/Dashboard.tsx` | labels / hints / heading only |
| `src/api/routes/progress.py` | docstring only |
| `tests/test_progress_overview.py` | new — 18 tests |
| `tests/test_stream_notice_prefix.py` | new — 44 tests |
| `tests/test_planner_weak_autofill.py` | new — 21 tests |
| `tests/test_planner_evaluator.py` | 4 exact failure-string assertions updated to the prefixed contract (still exact) (A-1) |

## Verification (run in this session, 2026-11-03)
| Command | Result |
|---|---|
| Pre-fix run of the 3 new files (initial Slice A) | planner autofill + frontend-prefix tests failed as expected (7 failed, 17 errors) |
| Initial Slice A: `uv run pytest -q` | `1566 passed, 2 skipped, 13 warnings in 173.67s` (baseline 1520 + 46 new) |
| A-1 pre-fix (agent files stashed): `uv run pytest -q tests/test_stream_notice_prefix.py` | `23 failed, 21 passed` |
| A-1 post-fix: same file | `44 passed, 3 warnings in 11.16s` |
| A-1 related files: `test_stream_notice_prefix`, `test_planner_evaluator`, `test_current_affairs_text`, `test_current_affairs_dates`, `test_evaluator_rubric`, `test_mcp_server`, `test_mentor_routes`, `test_planner_dates`, `test_production_evals`, `test_stream_guard`, `test_supervisor_routing`, `test_async_routes`, `test_eval_checks`, `test_planner_weak_autofill`, `test_progress_overview` | `402 passed, 3 warnings in 78.55s` |
| `node --test` of `persistExchange.ts` (session scratchpad; not a repo test, the frontend has no test runner) | `tests 10, pass 10, fail 0`. With the guard removed, the two New-chat race cases (null id and existing id) fail, so the check catches the bug |
| `uv run ruff check src tests evals` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | exit 0 |
| `git diff --check` | exit 0 |
| `cd frontend && npm run build` | `✓ built in 4.37s` |
| `cd frontend && npm run lint` | `All matched files use Prettier code style!` |

The full suite was **not** re-run after Amendment A-1, as you instructed. Every test file that imports or drives the
changed generators, or the Mentor chat route, was run (402 passed). The eval failure-sentinel detector
(`evals/evaluators/checks.py:is_error_sentinel`) strips its input first, so prefixed failures are still recognised
(`test_eval_checks` / `test_production_evals` pass).

Workflows touched: Mentor, Planner, Answer Evaluator and Current Affairs (failure text only). The Evaluator and
CA pages render the failure text as before, now after a blank line. Runtime with a real model is UNVERIFIED, as
in earlier phases.

## Security notes (self-review)
- The autofill reads only `("students", user["id"])`. The id comes from the JWT; nothing from the client selects the
  profile. A cross-user test pins this.
- Prompt-safety boundary unchanged: the autofilled text enters the same `weak` slot as the caller's own form input.
  It is the caller's own Mentor text, extracted by `_WEAK_RE` (letters, spaces, `&/-`, ≤ 80 chars, ≤ 12 items), and
  is bounded to the request field's 200 chars. Planner fields stay unfenced, which is the existing Phase 07 backlog
  item (self-injection only, LOW).
- Failures log only `type(exc).__name__`; a test checks that no exception text reaches the log. Notices are fixed
  strings; tests check that no provider text reaches them.

## Manual checks for the owner — all PASS (owner, browser, 2026-11-03)

| Check | Result |
|---|---|
| Mentor reload and conversation continuity | PASS |
| New Chat creates a separate conversation | PASS |
| In-flight save race does not restore the previous conversation | PASS |
| Specialist failures are not saved in Session History | PASS |
| Dashboard labels are correct | PASS |
| Planner weak-area autofill works as expected | PASS |

Original check instructions:
1. Mentor: ask a question, reload, ask another. Session History should show **one** conversation with both
   exchanges. Then "New chat" → ask → a **new** conversation appears. Click "New chat" immediately after an answer
   finishes: the next question still starts a new conversation.
2. Mentor: stop the backend (or force an error), send a question. A ⚠️ notice is shown, and nothing new appears in
   Session History.
3. Dashboard: the cards read "Mentor questions", "Helpful-answer rate", "Mentor streak", "Mentor active days",
   "Due for revision" (hint "available after subject-tagged evaluations"); the strip reads "Mentor activity (last 30 days)".
4. Planner: tell the Mentor "I am weak in Economy", then generate a plan with weak areas blank. The plan should
   target Economy. Production needs the LangGraph store on Postgres (risk #11); with an in-memory store this is lost
   on restart and the planner falls back to "Not specified".

## Behaviour notes
- An exchange whose answer is empty, or contains the notice prefix anywhere (for example, a partial answer followed
  by a stop notice), is not saved at all, including the question. This follows spec R-A2.
- When "New chat" is clicked during an in-flight save, the earlier exchange's reply still goes to the conversation
  its question was saved to; only the id adoption is skipped.
- `MentorChat` reads `conversationId` from the render that started `send()`. If a second message is sent before the
  first exchange's save returns (no "New chat" involved), the second save can still start a new conversation. The
  earlier ref-based code had the same issue; it stays in the backlog (LOW).
- "Live news feed unavailable" and "no monthly archive" are deliberately **not** notices. They are honest,
  designed answers (blockquote warnings), so they are still saved to Mentor history. Prefixing them would be the
  blanket prefixing the amendment rules out.

## Unresolved / backlog
- The response cache stores and replays specialist failure text (`src/api/routes/chat.py` `cache.set`) — MED,
  separate backlog item (no cache change in Slice A).
- The NCERT trust note (`src/agents/ncert/graph.py:404`) starts with the same characters as the prefix. It is a
  legitimate answer and is not routed through Mentor chat; the `api.ts` comment warns against applying the check
  to other features. No change.
- ~~Owner manual checks 1–4~~ — all PASS (2026-11-03).

## Final review (2026-11-03, read-only)
Scope: the full working-tree diff — A1–A5, Amendment A-1, tests, docs and the separate frontend Unicode cleanup.
The full suite was not re-run (owner instruction); the evidence is the verification table above plus the owner's
browser checks.
- Every changed source file is in the Slice A plan table or Amendment A-1, except the Unicode polish files below,
  which were a separate owner-requested frontend task. No migration, schema, dependency, `package.json`, CI or
  backend change outside the plan.
- Security: the planner autofill reads only the JWT user's profile. A client-held `conversation_id` (now kept in
  per-user `localStorage`) cannot address another user's conversation: `add_message` creates a new conversation
  when the caller does not own the id (`src/core/history.py:50-52`). Notices are fixed strings. No new logging of
  user text or exception messages.
- Findings: **no BLOCKER, no SHOULD-FIX.** Two LOW NITs went to the backlog in `docs/PROJECT_STATE.md`: raw
  exception text in the pre-existing Current Affairs failure logs, and `check-jsx-escapes.mjs` path resolution
  (`URL.pathname` rather than `fileURLToPath`).

## Separate frontend polish — literal Unicode escapes (not Slice A scope)
- Cause: JSX text and quoted JSX attribute values do not process JS escapes, so `\u2026` / `\u2014` showed up
  literally in the UI.
- 17 fixes, replaced with the real `…` / `—`:
  - `frontend/index.html` (meta description)
  - `Topbar.tsx` (2)
  - `Lecture.tsx` (7)
  - `Upload.tsx` (4)
  - `Planner.tsx` (2)
  - `AppLayout.tsx` (1)
- 106 escapes inside JS strings / regexes were left as they are, because they render correctly.
- `frontend/README.md`: 31 literal escapes left as is (documentation only), backlog LOW.
- New `frontend/scripts/check-jsx-escapes.mjs`:
  - a TypeScript-AST check that flags escapes only in JSX text / JSX attribute strings;
  - it flagged all 16 TSX bugs before the fix and passes after;
  - it runs manually (not wired into `package.json` / CI).
- Results:
  - `npm run build`: `✓ built`
  - `npm run lint`: clean
  - no U+FFFD characters and no mojibake
  - all 17 fixes browser-verified by the owner

## Closed
Slice A (A1–A5) and Amendment A-1 are **CLOSED** (2026-11-03). Slice B is **NOT STARTED**. It needs explicit owner
approval, and the owner takes a Supabase backup before migration 0005 is deployed.

<!-- superseded pre-close assessment below -->
## Ready to close?
Code, tests and every offline gate are green. Review findings S1, NIT 1 and NIT 2 are fixed under Amendment A-1.
Slice A is **ready to close after the owner's manual checks**. Slice B has not been started.
