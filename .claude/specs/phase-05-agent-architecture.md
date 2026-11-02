# Phase 05 — Agent architecture truthfulness

Status: STUB  ·  Related audit sections: F, G, T

## Goal
Make the orchestration do what it claims, or make the claims match the orchestration — the user decides which.

## Candidate scope
- The `rag` supervisor route is unreachable (`src/graph/supervisor.py:route_selector`; no API sets
  `persist_key`). Either wire document-scoped questions to the CRAG subgraph (with Phase 02 ownership) or
  remove the route.
- `task_inputs` is never set by the API; planner/evaluator/current-affairs subgraphs rely on regex parsing
  (`src/graph/agent_subgraphs.py`).
- Decide whether NCERT / PYQ / Lecture / Upload go through the graph or stay direct function calls.
- Unregistered `src/api/routes/mentor.py` (still exercised by `tests/test_stream_citations.py`) — keep,
  register or delete (ADR).
- Jev / TypeSafe is not present; add only via a dedicated spec if requested.

## Candidate acceptance criteria
- [ ] Every supervisor route is reachable from the API or removed.
- [ ] Docs describe the real topology.
