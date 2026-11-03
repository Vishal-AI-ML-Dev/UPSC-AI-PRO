# Phase 08 — Product gaps

Status: STUB (renumbered from Phase 07 on 2026-11-03; Phase 07 became LLM eval & safety)  ·  Related audit sections: Q, R

## Goal
Turn placeholder dashboard and planner features into real, persisted, user-scoped features.

## Candidate scope
- Study plan persistence (no table today; `src/api/routes/planner.py` only generates).
- Topic mastery, weak-topic tracking and revision schedule (`src/api/routes/progress.py` returns placeholders).
- Experiments dashboard returns `experiments: []` (`src/api/routes/experiments.py`) — implement or remove.
- Cost/monitoring counters are in-memory per process (`src/core/usage_tracker.py`, `src/api/http_metrics_mw.py`).

## Candidate acceptance criteria
- [ ] Saved plans and weak topics survive restart and are user-scoped (tests).
