# Plan — Phase 05 Agent architecture truthfulness

Spec: `.claude/specs/phase-05-agent-architecture.md`  ·  Status: DONE (2026-11-02; Amendment 1 approved by the user) — Q1=A, Q2=remove,
Q3=document only, Q4=keep

## Files to touch (exhaustive — anything else needs a plan amendment)
| File | Change |
|---|---|
| `src/graph/supervisor.py` | `supervisor_node` always calls the router (drop the `forced = state.get("route")` branch). Remove `rag`: `_ROUTES`, `RouteDecision` literal + description, `_SUP_SYS` line, `build_rag_subgraph` import/node, `route_selector` persist_key special case. Module docstring rewritten to the real topology (one routing call per turn; 4 routes + optional plan_execute; no forced routes). |
| `src/graph/agent_subgraphs.py` | Planner / evaluator / CA nodes take params from the `*_inputs_from_text` parsers only (drop `state.get("task_inputs") or`). Module docstring: inputs come from the chat message; HITL is not supported by `/agent/chat/stream` (no resume path). No change to the parsers or messages. |
| `src/graph/state.py` | Remove `task_inputs`. Fix comments: `route` = per-turn supervisor output; `persist_key` = Qdrant collection key used by the CRAG subgraph (eval harness only). Module docstring: drop "NCERT, lecture" subgraph claim. |
| `src/graph/rag_graph.py` | Docstring/comments only: used by `src/eval/llm_eval.py`, not by the supervisor or any HTTP route; Qdrant, not Chroma. No code change. |
| `src/api/routes/chat.py` | Module docstring only: one LLM routing call per turn → mentor / planner / evaluator / current-affairs; not "full multi-agent system". No code change (`_FINAL_NODES` kept, see spec §7). |
| `tests/test_supervisor_routing.py` | New: AC1–AC4, AC6, AC9, AC10 (spec §8). |
| `tests/test_mentor_routes.py` | Rewrite (not delete) the 2 forced-`rag` tests to the new contract (AC13); drop the `build_rag_subgraph` monkeypatch in `stub_supervisor`. |
| `scripts/bench_routing.py` | New offline routing benchmark (AC12): router calls per conversation, router input size, graph overhead per turn. Same isolation style as `scripts/bench_jobs.py`. |
| `docs/ENGINEERING_METRICS.md` | New "Mentor routing (Phase 05)" section: baseline vs after numbers, cost formula, UNVERIFIED live latency/$ (AC12). |
| `README.md` | Agent Architecture: mermaid without the `rag` edge; bullets at lines ~132/135 (no forced route; params parsed from the message; CRAG eval-only); keep the rest. Tech-highlights row "5 routes" → "4 routes". |
| `ARCHITECTURE.md` | Intro (lines 4-7) and `src/graph/` section: 4 routes, per-turn routing, `rag_graph.py` eval-only, HITL unsupported over HTTP. |
| `docs/DECISIONS.md` | ADR-014 (spec R5). |
| `docs/PROJECT_STATE.md` | Phase table, verified architecture facts (supervisor routes, rag), AI Mentor workflow row, backlog (HITL no resume path; plan_execute `synthesize` not token-streamed; ~~non-mentor routes add AI messages without a human message — already handled by `bound_history`~~ superseded by Amendment 1 (S1: specialists now store the question)), handoff. |
| `.claude/specs/phase-05-agent-architecture.md`, this plan, `.claude/specs/README.md` | Status updates. |
| `docs/phase-reports/phase-05-report.md` | New, at close. |

Not touched (except Amendment 1): `src/graph/tools.py`, `mentor_graph.py`, `plan_execute.py`, `reflection.py`, `memory.py`,
`app_graph.py`, `src/api/routes/mentor.py`, `src/eval/`, `src/agents/`, `frontend/`, models/migrations, config,
CI/Docker/Render.

## Steps
0. **Baseline metrics.** Write `scripts/bench_routing.py`; run it on the unchanged code and save the output
   (scratchpad) as the "before" numbers.
1. **Tests first.** Write `tests/test_supervisor_routing.py` (AC1–AC4, AC6). Run it; record the expected
   pre-fix failures (AC1/AC2/AC3 sticky route; AC4 rag present).
2. **Supervisor.** Per-turn routing + `rag` removal in `src/graph/supervisor.py`.
3. **Subgraphs/state.** Remove `task_inputs` (`agent_subgraphs.py`, `state.py`); comment fixes.
4. **Test cleanup.** Rewrite (do not delete) the two forced-`rag` tests in
   `tests/test_mentor_routes.py` to assert the new contract (AC13).
5. **Docstrings.** `rag_graph.py`, `chat.py`.
6. **Run gates** (below); fix only in-scope failures.
7. **Docs.** `README.md`, `ARCHITECTURE.md`, ADR-014; rerun `scripts/bench_routing.py` ("after") and write the
   metrics section.
8. **Reviews.** `code-quality-reviewer` agent (prompts/LLM routing touched → also `security-reviewer`, per
   `.claude/rules/security.md`). Then `/complete-phase`.

## Reused code
- `stub_supervisor` fixture pattern and `test_chat_endpoint_evaluator_route_evaluates_the_answer`
  (`tests/test_mentor_routes.py`); `client` / `make_user` (`tests/conftest.py`).
- `langgraph.checkpoint.memory.InMemorySaver` for multi-turn graph tests.
- Existing parsers `planner_inputs_from_text`, `evaluator_inputs_from_text`, `current_affairs_inputs_from_text`.

## Amendment 1 — review fixes (user-approved 2026-11-02, after `/review-changes`)
User instruction: apply review SHOULD-FIX S1 + S2 and the relevant NITs. Adds one file to the table and widens two.
| File | Change |
|---|---|
| `src/graph/tools.py` | **Added to scope (S1).** `bound_history` keeps each earlier turn's FIRST final answer (its own reply) instead of the last; docstring rewritten. User decision (2026-11-02): "Keep FIRST answer". The format hook also re-joined one pre-existing wrapped `logger.warning` line (no behaviour change). |
| `src/graph/agent_subgraphs.py` | S1: `_reply()` stores `HumanMessage(question)` (only if not already the latest unanswered human message — reuses `tools._should_seed_question`) + `AIMessage(answer)`. S2: remove the unreachable Mains/HITL branch and the `evaluate_mains` import from the Mentor evaluator node. Standalone Answer Evaluator untouched. |
| `src/graph/supervisor.py` | NIT 7: log `describe_exception(exc)` instead of the raw exception. |
| `src/api/routes/chat.py` | NIT 1: comment on why `"generate"` stays in `_FINAL_NODES`. |
| `tests/test_mentor_history_bound.py` | **Added to scope (S1).** Two Phase 03 tests re-targeted to "first answer" (`test_earlier_turn_keeps_only_its_last_answer` renamed `…_its_own_answer`; `test_many_other_route_answers_do_not_bypass_the_bound` expects `a1`) — same strictness, bound still asserted; new mixed-route + multiple-unanswered tests. |
| `tests/test_supervisor_routing.py` | S1 graph tests (Mentor→Planner→Mentor, Mentor→Evaluator→Mentor, exactly-once, no duplicate, bounded after mixed workflows) + log-safety test. |
| `tests/test_mentor_routes.py` | NIT 3: comment on the intentional overlap. |
| `scripts/bench_routing.py` | NIT 4: `ruff format`. |
| `README.md` | NIT 5: offline test count 930 → 1212 (latest verified full run), modules 64 → 76. |
| `docs/phase-reports/phase-05-report.md` | Written now (draft until final review), completed at close. |
| `src/api/routes/chat.py` (round 2, B1) | `_is_input_message()`: stream AI output only. Without it the specialist's stored `HumanMessage` was echoed at the start of every planner / evaluator / CA reply (also into the cache). Regression test `test_chat_endpoint_streams_only_the_specialist_answer[*]`. |
| `src/graph/tools.py` (round 2, S1) | `bound_history` docstring: `plan_execute` does not record the question (backlog). |

## Verification
```bash
uv run pytest -q tests/test_supervisor_routing.py tests/test_mentor_routes.py tests/test_chat_isolation.py \
  tests/test_stream_citations.py tests/test_mentor_history_bound.py tests/test_plan_execute.py tests/test_eval_gate.py
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
grep -rn "task_inputs" src            # expect no output
grep -rn "\"rag\"" src/graph/supervisor.py   # expect no output
git diff --check
git diff --stat -- src/agents src/api/routes/ncert.py src/api/routes/pyq.py src/api/routes/planner.py   src/api/routes/evaluator.py src/api/routes/current_affairs.py src/api/routes/lecture.py   src/api/routes/upload.py src/api/routes/mentor.py     # expect empty (AC11, Q4)
uv run python scripts/bench_routing.py
```
Manual / workflow checks: offline workflow smoke tests (part of the full suite). Live Mentor routing with real
keys — UNVERIFIED unless the user runs it (`docs/SMOKE_CHECKLIST.md`). Frontend build not needed (untouched).

## Rollback
Revert the files listed above (`git checkout -- <files>`; delete `tests/test_supervisor_routing.py`). No
migration, no config, no data change. Existing checkpoints remain readable either way.
