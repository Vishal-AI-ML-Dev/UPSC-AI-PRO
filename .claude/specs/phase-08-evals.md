# Phase 08 — Evals

Status: SUPERSEDED (2026-11-03) by `phase-07-llm-eval-safety.md` — kept for history. The `src/eval/` → `evals/` move is deferred to a separately approved change (ADR-016).  ·  Related audit sections: O, U#10

## Goal
Evals that measure the production paths, organised under `evals/`.

## Candidate scope
- Move or wrap `src/eval/` (`llm_eval.py`, `retrieval_eval.py`, `gates.py`, `eval_dataset.json`) into
  `evals/{runners,evaluators,datasets,reports}` (ADR-003 keeps `src/eval/` until this phase); update CI paths.
- The current LLM eval exercises the CRAG graph that production never reaches; add evals for the mentor tool
  agent, NCERT chat, PDF chat and PYQ bank.
- Keep generated reports out of the Python package (`evals/reports/`).

## Candidate acceptance criteria
- [ ] Each of the 8 workflows has at least one eval case, or an explicit reason why not.
