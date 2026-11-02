# Phase 05 report — Agent architecture truthfulness

Date: 2026-11-02 · Spec: `.claude/specs/phase-05-agent-architecture.md` (DONE) · Plan:
`.claude/plans/phase-05-agent-architecture.md` (DONE, Amendment 1) · ADR-014 (+ addendum) · Status: **DONE**
(AC1–AC17 MET; final independent review: 0 BLOCKING, 0 SHOULD-FIX; open items under "Not verified")

## Summary
Mentor chat's LangGraph supervisor now does what the code and docs say:
- **Per-message routing (bug fix).** Before, the first message's route was saved in the checkpoint and reused as a
  "forced" route, so every later message of a conversation went to the first message's specialist and the router
  ran once per conversation. Now the router runs for every message and a saved route is never read.
- **No unreachable route.** The `rag` route (needed a `persist_key` no API sets) is removed from the supervisor. The
  CRAG subgraph stays for the eval harness. Document-aware Mentor chat is a backlog feature (candidate Phase 07).
- **No dead contract.** `task_inputs` removed; specialists parse the message text, as they always did.
- **Coherent history (review S1).** Planner / evaluator / current-affairs replies store the student's question once
  before the answer, and `bound_history` keeps each earlier turn's own (first) answer, so the mentor sees
  question → answer pairs in mixed conversations.
- **Truthful docs.** README, ARCHITECTURE and docstrings no longer claim forced routes, `task_inputs`, a reachable
  `rag` route, Chroma or a "full multi-agent system". HITL is documented as unsupported in Mentor chat.

## Decisions during the phase
| # | Decision | By |
|---|---|---|
| Q1 | A — remove `rag` from the supervisor; keep CRAG for evals; document-aware Mentor chat → backlog | user |
| Q2 | Remove `task_inputs` | user |
| Q3 | HITL: document as unsupported in Mentor chat; no implementation | user |
| Q4 | Keep `src/api/routes/mentor.py` unchanged (ADR-006, Phase 09) | user |
| Extra ACs | Per-message routing, planner → mentor → CA regression, history preserved, standalone workflows unchanged, routing cost metrics, preserve tests | user |
| Amendment 1 | Apply review S1, S2 and NITs 1-8; `src/graph/tools.py` and `tests/test_mentor_history_bound.py` added to scope | user, after review round 1 |
| "Keep FIRST answer" | `bound_history` pairs each earlier question with its own (first) answer; re-targets two Phase 03 assertions | user |
| Round-2 B1 fix | Fixed in scope (`chat.py`, already in the plan): it was a regression introduced by the S1 change | Claude, reported to the user |

## Test-first evidence (failing before each fix, this session)
1. **Sticky route, offline probe** (scratch script, stub specialists, `InMemorySaver`): router returns planner →
   mentor → current_affairs on one thread; all three answered by **planner**, router called **once**.
2. **`tests/test_supervisor_routing.py` before the fix:** `12 failed, 2 passed`. The endpoint test failed with the
   evaluator message answered by the planner: `'To build your study plan I need your daily study hours. …'`. The two
   passes (CRAG import smoke; document question → mentor) are expected to pass before and after.
3. **Benchmark before the fix** (`scripts/bench_routing.py`): 160 / 200 messages misrouted (80.0 %), 0.1 router
   calls per message, `ALL PASS: False`.
4. **Review S1 tests before the fix:** `9 failed, 31 passed` (Mentor → Planner/Evaluator → Mentor, exactly-once
   storage, specialists record the question, bounded mixed history, two re-targeted Phase 03 tests). The passes are
   guards that hold before and after (no duplicate question; `bound_history` on the new message shape).
5. **Log-safety test** (NIT 7): asserts provider text is absent from the routing-failure log. Not run against the
   pre-fix line; by inspection the old line formatted the raw exception (`%s`, `exc`), so the text would appear.
6. **Echo regression (round 2 B1)** — `test_chat_endpoint_streams_only_the_specialist_answer[planner,evaluator,
   current_affairs]` before the fix: `3 failed` (bodies began with the student's question, e.g.
   `'Make me a UP...2e48)PLAN-OUT' == 'PLAN-OUT'`). Reviewer's stream probe showed `MSG planner HumanMessage …` then
   `MSG planner AIMessage 'PLAN-OUT'`; reproduced in this session.

## Files changed
| File | Change |
|---|---|
| `src/graph/supervisor.py` | Router runs per message (no forced route); `rag` removed (literal, prompt, node, selector); docstring = real topology; failure log uses `describe_exception` |
| `src/graph/agent_subgraphs.py` | `task_inputs` branches removed; `_reply()` stores question once + answer; Mains/HITL branch and `evaluate_mains` import removed from the Mentor evaluator node; docstrings |
| `src/graph/tools.py` | `bound_history` keeps each earlier turn's first answer; docstring. Format hook re-joined one wrapped `logger.warning` line (no behaviour change) |
| `src/graph/state.py` | `task_inputs` removed; comments (route = per-message output; Qdrant; CRAG eval-only) |
| `src/graph/rag_graph.py` | Docstring only (eval-only; Qdrant; ADR-007 guidance for any future HTTP caller) |
| `src/api/routes/chat.py` | Docstring; comment on `"generate"` in `_FINAL_NODES`; `_is_input_message()` so only model output is streamed (round 2 B1) |
| `tests/test_supervisor_routing.py` | New — 17 test functions, more cases with parametrization (routing, stale route, history, rag removal, task_inputs, CRAG import, endpoint, S1, log safety, no question echo) |
| `tests/test_mentor_history_bound.py` | Two Phase 03 tests re-targeted to "first answer" (one renamed `…_its_own_answer`); 2 new mixed-route tests |
| `tests/test_mentor_routes.py` | Two forced-`rag` tests rewritten (not deleted; one renamed `…_is_answered_by_mentor`); overlap comment |
| `scripts/bench_routing.py` | New offline routing benchmark |
| `README.md`, `ARCHITECTURE.md` | Topology corrected; test count 1212 / 76 modules |
| `docs/DECISIONS.md` | ADR-014 + addendum |
| `docs/ENGINEERING_METRICS.md` | Mentor routing benchmark section; suite size |
| `docs/PROJECT_STATE.md`, spec, plan, `.claude/specs/README.md` | Status, facts, backlog, amendment |

Not changed: `src/agents/`, the seven standalone feature routes, `src/api/routes/mentor.py`, `frontend/`, models,
migrations, config, CI/Docker/Render (verified with `git diff --stat`, empty).

## Gate results (this session, verbatim)
- Full suite after the main implementation: `1196 passed, 13 warnings in 146.43s (0:02:26)`; review re-run
  `1196 passed, 13 warnings in 132.85s (0:02:12)`.
- Full suite after review fixes: `1209 passed, 13 warnings in 128.45s (0:02:08)`.
- Focused (routing, history, mentor routes, tool agent, chat isolation, stream citations, planner/evaluator,
  evaluator rubric): `160 passed, 3 warnings in 22.80s`.
- `uv run ruff check src tests scripts/bench_routing.py` → `All checks passed!`; `ruff format --check` on the touched
  Python files → `5 files already formatted`; compileall exit 0; `git diff --check` exit 0.
- `scripts/bench_routing.py` after the fix: 0 / 200 misrouted, 1.0 router calls per message, `ALL PASS: True`
  (3 / 3 runs).
- **Final, after review round 2:** `1212 passed, 13 warnings in 138.61s (0:02:18)`; ruff `All checks passed!`;
  `ruff format --check` on the 10 touched Python files → `10 files already formatted`; compileall exit 0;
  `git diff --check` exit 0; AC11 `git diff --stat` empty; `scripts/bench_routing.py` → 0 misrouted, 1.0 router
  calls per message, `ALL PASS: True`.
- Focused after the B1 fix (routing, history, mentor routes, tool agent, chat isolation, stream citations, stream
  guard, response cache, planner/evaluator, evaluator rubric): `193 passed, 3 warnings in 40.05s`.

## Acceptance criteria
| AC | Status | Evidence |
|---|---|---|
| AC1, AC9 | MET | `test_conversation_planner_then_mentor_then_current_affairs`, `test_router_runs_for_every_message_of_a_long_conversation` |
| AC2 | MET | `test_chat_endpoint_routes_each_message_of_one_thread` |
| AC3 | MET | `test_saved_route_never_forces_the_next_message`, `test_route_in_the_input_does_not_bypass_the_router[*]` |
| AC4 | MET | `test_supervisor_has_no_rag_route` |
| AC5 | MET | `grep -rn task_inputs src --include=*.py` empty; `test_task_inputs_is_not_part_of_the_state_contract` |
| AC6 | MET | `test_eval_harness_still_builds_the_crag_subgraph` |
| AC7 | MET | README / ARCHITECTURE / docstrings corrected |
| AC8 | MET (handoff at close) | ADR-014 + addendum; PROJECT_STATE facts, workflow row, backlog |
| AC10, AC14 | MET | `test_history_and_profile_still_reach_the_specialist`, `test_mentor_then_specialist_then_mentor_sees_coherent_pairs[planner,evaluator]`, `test_each_question_is_stored_exactly_once`, `test_specialist_does_not_duplicate_a_question_already_in_state[*]`, `test_history_stays_bounded_after_mixed_workflows`, `test_mixed_routes_keep_coherent_pairs`, `test_multiple_unanswered_questions_between_mixed_routes` |
| AC11 | MET | `git diff --stat` on `src/agents`, the seven feature routes, `mentor.py`, `frontend` → empty; none imports `src.graph` |
| AC12 | MET | `docs/ENGINEERING_METRICS.md` → Mentor routing benchmark (live latency / $ UNVERIFIED, stated) |
| AC13 | MET | No test deleted; rewritten / re-targeted tests listed above |
| AC15 | MET | `evaluate_mains` no longer imported in `agent_subgraphs.py`; `tests/test_planner_evaluator.py`, `tests/test_evaluator_rubric.py` pass unchanged |
| AC16 | MET | `test_routing_failure_defaults_to_mentor_without_logging_provider_text` |
| AC17 | MET | Specialist response isolation (round 2 B1). `test_chat_endpoint_streams_only_the_specialist_answer[planner,evaluator,current_affairs]`: response body `==` the specialist answer, student text absent (3 failed before the fix). Cache: `cache.set(answer="".join(buffer)…)` uses the same buffer as the asserted body (no direct cache-content test — backlog NIT). Saved assistant message: the frontend saves the response body (`MentorChat.tsx:79-97`). Stored history: question once, then answer (`test_each_question_is_stored_exactly_once`; final-review probe) |

## Metrics (offline; `docs/ENGINEERING_METRICS.md`)
| | Before | After |
|---|---|---|
| Misrouted messages (200) | 160 (80 %) | 0 |
| Router calls per message | 0.1 | 1.0 |
| Router input per call (est. tokens) | ~270 | ~229 |
| Graph overhead per message p50 (no models) | 2.49 ms | 2.47 ms |

## Not verified
- Live Mentor chat with real Gemini / Groq: routing quality with 4 labels, router latency, token usage, $ cost.
- Behaviour with `HITL_ENABLED=true` or `PLAN_EXECUTE_ENABLED=true` over HTTP (both off by default).
- Postgres checkpointer with old checkpoints holding `route` / `task_inputs` (SQLite / in-memory only in tests).
- Frontend build (frontend unchanged).

## Review rounds
- Round 1: security — no CRITICAL/HIGH/MEDIUM; 2 LOW (raw exception log → fixed; router prompt injection →
  accepted, backlog). Code quality — no BLOCKING; S1, S2 + 8 NITs → all applied (Amendment 1).
- Round 2: security — no CRITICAL/HIGH/MEDIUM; 1 LOW pre-existing (`tools.py` `extra_context failed` logs raw
  exception) → backlog. Code quality — **B1 BLOCKING** (question echoed at the start of planner / evaluator / CA
  replies, introduced by S1) → fixed with a regression test; S1 (`plan_execute` does not store the question;
  docstring overstated) → docstring corrected + backlog; S2 (stale plan note) → marked superseded; NITs (private
  `_should_seed_question` import; dedupe test passes on old code by design) → accepted.
- Final independent review (after the B1 fix, read-only, user-requested): **0 BLOCKING, 0 SHOULD-FIX**. Verified the
  `chat.py` fix by reading the LangGraph messages-stream code and a scratch probe of the real supervisor (mentor →
  planner → mentor): stored history alternates question/answer once each; only `AIMessage` output is forwarded;
  focused tests `193 passed, 3 warnings in 38.54s`. NITs (→ backlog): duck-typed filter could be a stricter
  assistant-output allow-list; no direct response-cache content assertion. Found a **pre-existing** limitation (not
  a Phase 05 regression): answers are not shown token by token (nested specialists + `subgraphs=False`; mentor
  `streamed_chunks=0`) → backlog MED; README and `chat.py` docstring corrected at close.

## Closeout (2026-11-02)
- AC17 added to the spec and marked MET; spec and plan `Status: DONE`; roadmap row DONE; ADR-014 addendum extended
  (AC17, streaming note); PROJECT_STATE phase table (05 DONE, 06 NEXT), Mentor workflow row, backlog, handoff.
- Docs only at close: `README.md` (request-flow diagram and "Why streaming" decision no longer claim token-by-token
  display), `src/api/routes/chat.py` handler docstring. **No runtime code changed at close:** hash of the diff of
  every other `src`/`tests`/`scripts` file unchanged (`43816ef7…`), and `chat.py` AST identical before/after with
  docstrings removed (`True`).
- **Closing gates:** `1212 passed, 13 warnings in 130.71s (0:02:10)`; `uv run ruff check src tests` → `All checks
  passed!`; `ruff check scripts/bench_routing.py` → `All checks passed!`; compileall exit 0. Frontend unchanged →
  `npm run build` / `npm run lint` not run.

## 8-workflow status (Phase 05 close)
| Workflow | Touched? | Status |
|---|---|---|
| AI Mentor | Yes | Per-message routing, 4 routes, coherent Q/A history, no question echo; offline tests PASS. Live models UNVERIFIED |
| NCERT Study | No (`src/agents/ncert`, `routes/ncert.py` no diff) | offline smoke PASS (full suite) |
| PYQ + Personal PYQ Bank | No | offline smoke PASS |
| Answer Evaluator (page) | No (Mains intact in `routes/evaluator.py`) | offline smoke PASS; `test_planner_evaluator.py`, `test_evaluator_rubric.py` PASS |
| Current Affairs (page) | No | offline smoke PASS |
| Study Planner (page) | No | offline smoke PASS |
| Lecture Learning | No | offline smoke PASS |
| PDF / Document Chat | No | offline smoke PASS |

## New backlog items (docs/PROJECT_STATE.md)
- FEATURE: document-aware Mentor chat (candidate Phase 07).
- MED: answers not shown token by token (pre-existing; nested specialists + `subgraphs=False`).
- LOW: HITL no resume path; plan_execute delivered via fallback and does not store the question; stale-answer
  snapshot; router prompt injection (accepted); concurrent test runs share one SQLite file; `tools.py`
  `extra_context failed` raw exception log; `_is_input_message` deny-list vs allow-list; no cache-content test.
- RESOLVED: Mentor evaluator Mains branch.

## Follow-ups
- Phase 06 (RAG quality) is next — STUB, not started; wait for the user.
- Live smoke of Mentor chat with keys (`docs/SMOKE_CHECKLIST.md`) to check routing quality, latency and cost
  (Langfuse traces) — UNVERIFIED until run.
- Streaming design (MED backlog) if token-by-token display is wanted.
