# Phase 05 — Agent architecture truthfulness

Status: DONE (2026-11-02) — approved by the user (Q1=A, Q2=remove, Q3=document only, Q4=keep); report `docs/phase-reports/phase-05-report.md`
Owner: user  ·  Created: 2026-11-02  ·  Related audit sections: F, G, T  ·  Related risks: 10 (CRAG eval path), 15 (dead code)

## 1. Goal
Make the AI Mentor's LangGraph orchestration do exactly what the code, comments and docs say it does:
every turn is routed on its own, every supervisor route can be reached from the API, and the docs describe
the real topology.

## 2. Non-goals
- No new agents, no multi-agent or parallel orchestration, no "agentic" rebranding.
- NCERT / PYQ / Lecture / Upload stay direct function calls from their routes (not routed through the supervisor).
- No document-scoped questions in Mentor chat (see Q1, option B). Not a new product feature in this phase.
- No change to the mentor ReAct agent (`src/graph/tools.py`), retrieval, prompts of the specialists, or the
  response cache.
- `src/graph/rag_graph.py` is kept (used by `src/eval/llm_eval.py`, ADR-003); only its supervisor wiring changes.
- `src/api/routes/mentor.py` stays unregistered and untouched (ADR-006, revisited in Phase 09).
- Plan-execute, reflection and HITL remain off by default; no behaviour change when off.
- No frontend change, no schema change, no migration, no new dependency.

## 3. Current behaviour (verified from source, 2026-11-02)
| Aspect | File / function | Observed behaviour |
|---|---|---|
| Entry point | `src/api/routes/chat.py:agent_chat_stream` | Streams `agent_graph.stream({"question", "student_context"}, config)` on a checkpointed thread `scoped_thread_id(user_id, thread_id)`. Only these two keys are sent. |
| Thread lifetime | `frontend/src/features/mentor/MentorChat.tsx:30-131` | One `thread_id` per conversation (kept in scoped storage `mentor:threadId`, replaced on "new chat"). |
| **Sticky route (BUG)** | `src/graph/supervisor.py:supervisor_node` | `forced = state.get("route")`; if it is one of the 5 routes the router LLM is skipped. `route` is written to state and **persisted by the checkpointer**, so from turn 2 onward the previous turn's route is treated as "forced". **Reproduced offline** (stub specialists + `InMemorySaver`, one thread): router returns planner → mentor → current_affairs; all three turns answered by **planner**, router called **once**. Effect: in a Mentor conversation every later message goes to the first message's specialist (e.g. after "make me a plan …", "explain DPSP" gets the planner's "I need your daily study hours" reply). |
| Forced-route feature | same | Documented ("a caller may force a route", `README.md:132`), but no production caller sets `route`; only `tests/test_mentor_routes.py:143,148` do. |
| `rag` route | `supervisor.py:route_selector` | Falls back to `mentor` when `persist_key` is missing. No API sets `persist_key` (grep: only `src/eval/llm_eval.py:138,169`, `rag_graph.py` `__main__`). So `rag` is unreachable over HTTP; the router LLM can still pick it (wasted label) and it silently becomes mentor. The `rag` node is still built (`build_rag_subgraph(label="rag")`) at startup. |
| CRAG subgraph | `src/graph/rag_graph.py` | Docstring claims "One factory powers every retrieval agent (NCERT, Lecture, Upload, PYQ)" — false; NCERT/Lecture/Upload/PYQ call their own functions. Real users: supervisor (unreachable) + `src/eval/llm_eval.py`. Docstring says "Chroma" (Qdrant is the store). |
| `task_inputs` | `src/graph/agent_subgraphs.py` planner/evaluator/CA nodes; `state.py` | Each node reads `state.get("task_inputs") or <regex parser>(question)`. No API, test or graph sets `task_inputs` (grep). Real inputs always come from `planner_inputs_from_text` / `evaluator_inputs_from_text` / `current_affairs_inputs_from_text`. Module docstring and `README.md:135` say params "flow through `state['task_inputs']`". |
| HITL | `agent_subgraphs.py:_maybe_interrupt_for_params`; `settings.hitl_enabled=False` | When enabled, calls LangGraph `interrupt()`. `chat.py` has no resume path and does not handle `__interrupt__` output, so with HITL on the turn would end without an answer — **UNVERIFIED** (not run). Off by default. |
| Plan-execute | `supervisor.py`; `settings.plan_execute_enabled=False` | Optional `plan_execute` node; off by default; its `synthesize` node is not in `chat.py:_FINAL_NODES`, so it would reply via the final-state fallback (not token-streamed). Documented as off. No change. |
| Docstrings | `chat.py` module docstring | "Exposes the full multi-agent system (supervisor -> mentor / rag / planner / evaluator / current-affairs)" — overstates (one routing call; rag unreachable). |
| State comment | `src/graph/state.py` | `persist_key: str  # Chroma collection key for the active RAG document.` — Chroma not used. |
| Unregistered mentor router | `src/api/routes/mentor.py`; `tests/test_stream_citations.py:121-137` | Unchanged; ADR-006 defers to Phase 09. |
| Docs | `README.md:116-136`, `ARCHITECTURE.md:4-7,63-75` | Mostly accurate since Phase 01 (says rag unreachable, supervisor = mentor chat only), but still claims forced routes and `task_inputs`/`persist_key` flow (`README.md:132,135`) and draws the `rag` edge. |

## 4. Requirements
- **R1 — Per-turn routing (bug fix).** Every Mentor chat turn is routed by the router LLM for *that* turn's
  question. A route stored in the checkpoint from an earlier turn never decides a later turn. The "forced route"
  input is removed (no production caller).
- **R2 — No unreachable route.** Remove `rag` from the supervisor (route literal, router prompt, node,
  `route_selector` special case). Document-scoped questions in Mentor chat go to the mentor, which already has
  `knowledge_base_search` (mentor KB). `rag_graph.py` stays for the eval harness.
- **R3 — No dead input contract.** Remove `task_inputs` from `AgentState` and the three
  `state.get("task_inputs") or …` branches; the regex parsers are the documented input path.
- **R4 — Truthful comments/docs.** Fix the docstrings/comments listed in §3 in the files this phase touches,
  plus `README.md` (Agent Architecture section, mermaid) and `ARCHITECTURE.md` (`src/graph/` section).
  State that HITL is not supported by `/agent/chat/stream` (no resume path).
- **R5 — Decision record.** ADR-014 records: per-turn routing; `rag` removed from the supervisor (CRAG kept for
  evals); NCERT/PYQ/Lecture/Upload stay direct calls; `task_inputs` removed; mentor.py per ADR-006.

## 5. Affected workflows
- **AI Mentor** — only workflow touched. Behaviour change: later turns in a conversation are routed correctly
  (bug fix). First-turn behaviour is unchanged for mentor/planner/evaluator/current_affairs. A message the
  router would have labelled `rag` now goes straight to the mentor — identical to today's effective behaviour
  (`route_selector` already sent it to mentor).
- NCERT, PYQ, Evaluator page, Current Affairs page, Planner page, Lecture, PDF chat — not touched (they don't
  import `src/graph/supervisor.py` or `agent_subgraphs.py`; verified by grep of `from src.graph`).
- Frontend unchanged; request/response shape of `/api/v1/agent/chat/stream` unchanged.

## 6. Acceptance criteria (testable)
- [x] AC1 — Regression: on one checkpointed thread, three turns whose router decisions are planner → mentor →
  current_affairs are answered by planner, mentor, current_affairs respectively, and the router is called
  3 times. Fails before the fix (verified offline: planner ×3, 1 router call).
- [x] AC2 — Endpoint regression: two `POST /api/v1/agent/chat/stream` calls with the same `thread_id`
  (router stub: planner, then evaluator with a `Question:/Answer:` message) return the planner text then the
  evaluator text.
- [x] AC3 — A `route` value present in the input/checkpoint state does not bypass the router.
- [x] AC4 — The compiled supervisor has no `rag` node; `RouteDecision` rejects `"rag"`; the router system
  prompt does not mention `rag`.
- [x] AC5 — `AgentState` has no `task_inputs`; `grep -rn task_inputs src` returns nothing; the planner,
  evaluator and current-affairs subgraph tests in `tests/test_mentor_routes.py` still pass unchanged.
- [x] AC6 — `src/eval/llm_eval.py` still imports and builds `build_rag_subgraph` (import smoke test).
- [x] AC7 — `README.md` / `ARCHITECTURE.md` / touched docstrings no longer claim forced routes, `task_inputs`,
  a reachable `rag` route, Chroma, or a "full multi-agent system"; topology matches `supervisor.py`.
- [x] AC8 — ADR-014 added; `docs/PROJECT_STATE.md` updated (facts, workflow row, backlog incl. "document-aware
  Mentor chat" as a future product feature, handoff).
- [x] AC9 — Conversation regression (user-specified): one thread, three messages — a planner request gets the
  planner response, then a mentor question gets the mentor response, then a current-affairs question gets the
  current-affairs response; the router is called once **per message** (3 calls, each with that message's text).
- [x] AC10 — History preserved: on message 3 the specialist still receives the thread's earlier messages
  (turn 1 and 2 answers in `messages`) and `student_context`; only the stale `route` is ignored.
- [x] AC11 — Standalone workflows unchanged: `src/api/routes/{ncert,pyq,planner,evaluator,current_affairs,lecture,upload}.py`
  and `src/agents/` are not modified (git diff), none of them imports `src.graph` (grep), and their existing
  tests pass unchanged.
- [x] AC12 — Per-message routing impact recorded in `docs/ENGINEERING_METRICS.md` (Phase 05 section): router
  calls per conversation before/after (offline `scripts/bench_routing.py`, baseline measured BEFORE the fix),
  router input size per call (chars and an estimated token count), offline graph overhead per turn, and the
  cost formula. Live latency and $ cost are UNVERIFIED (no keys) and stated as such.
- [x] AC13 — No existing test deleted. The two tests that assert the removed forced-`rag` behaviour
  (`tests/test_mentor_routes.py:141-149`) are rewritten to assert the new contract (same intent: a
  document-shaped message without a selectable document is answered by the mentor), and this is listed in the
  report.
- [x] All 8 workflows still pass their offline smoke checks (`docs/PROJECT_STATE.md`).
- [x] `uv run pytest -q` green; `uv run ruff check src tests` clean; compileall exit 0. Frontend not touched.

- [x] AC14 — (review S1) Planner / Evaluator / Current Affairs store the student's question once, then the
  answer; Mentor → Planner → Mentor and Mentor → Evaluator → Mentor send coherent question → answer pairs to the
  mentor model; each question is stored exactly once; history stays bounded after mixed workflows; earlier turns
  keep their own (first) answer; unanswered questions are kept and count as turns; no orphan tool call/result.
- [x] AC15 — (review S2) The unreachable Mains/HITL branch and `evaluate_mains` import are gone from the Mentor
  evaluator node; the standalone Answer Evaluator (`src/api/routes/evaluator.py`, `src/agents/evaluator/`) is
  unchanged and its tests pass.
- [x] AC16 — (review NIT 7) A routing failure logs only `describe_exception()` output, never provider text.
- [x] AC17 — Specialist response isolation: student input messages stored in conversation history must never
  be forwarded as assistant-generated response text. The client response, response cache, and saved assistant
  message must contain only the specialist's actual answer. (Added at close for the review round-2 B1 fix.)

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Existing checkpoints hold `route` / `task_inputs` keys | Route is no longer read as input; `TypedDict` keys are not enforced at runtime, so stale keys are ignored. AC1 covers a thread with a stored route. |
| Removing `rag` changes the router's label set (could shift borderline classifications) | Today `rag` already ends in mentor; "default to mentor when unsure" stays. Live routing quality UNVERIFIED (needs keys) — Phase 08 eval. |
| More router calls than today (one per turn instead of one per thread) | This is the documented, intended cost (one fast-LLM call per turn); today's saving was a bug. Noted in report. |
| Removing forced routes removes a "UI can force a target" extension point | No caller uses it; can be re-added later with a non-persisted field if a product need appears. |
| `chat.py:_FINAL_NODES` contains `"generate"` (CRAG) | Keep it: `tests/test_chat_isolation.py:61` uses a fake graph with a `generate` node. Harmless. |

## 8. Test plan
- `tests/test_supervisor_routing.py` (new): AC1, AC3, AC4 using the `stub_supervisor` pattern from
  `tests/test_mentor_routes.py` + `InMemorySaver`; AC6 import smoke for `src.eval.llm_eval`.
- AC2 in the same file: endpoint test copying `test_chat_endpoint_evaluator_route_evaluates_the_answer`
  (`monkeypatch.setattr(supervisor, "_structured_llm", …)`, patched `agent_subgraphs.generate_plan` /
  evaluator `get_llm`), `client` + `make_user` fixtures.
- `tests/test_mentor_routes.py`: rewrite (not delete) the two forced-`rag` tests (AC13): a pre-set
  `route="rag"` / `persist_key` no longer selects a route; the stubbed router decides and a document-shaped
  question routed to mentor is answered by mentor. Drop the stub's `build_rag_subgraph` patch (no such node).
  The planner / evaluator / current-affairs tests stay unchanged.
- AC9/AC10 in `tests/test_supervisor_routing.py` with stub specialists that append an `AIMessage` and record the
  `messages` / `student_context` they received.
- `scripts/bench_routing.py` (AC12): offline, stubbed router and specialists; run on the pre-fix code first.
- Pre-fix failure of AC1/AC2 recorded in the phase report.

## 9. Decisions (user, 2026-11-02)
- **Review round 1 (after `/review-changes`):** apply S1, S2 and NITs 1-8 (plan Amendment 1). For checkpoints
  written before Phase 05, `bound_history` keeps each earlier turn's FIRST answer and drops orphan other-route
  answers ("Keep FIRST answer"), which re-targets two Phase 03 assertions.
- **Q1 → A.** Remove `rag` from the Mentor supervisor; keep the CRAG code for evals. Document-aware Mentor chat
  goes to the product-feature backlog (candidate for Phase 07).
- **Q2 → remove** `task_inputs` (no active workflow uses it).
- **Q3 → document only:** Mentor chat does not support HITL; no HITL implementation in this phase.
- **Q4 → keep** `src/api/routes/mentor.py` unchanged under ADR-006 until Phase 09.
- No commit, no push, no Phase 06.
