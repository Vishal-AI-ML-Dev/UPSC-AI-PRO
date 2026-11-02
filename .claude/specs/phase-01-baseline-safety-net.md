# Phase 01 — Baseline & safety net

Status: STUB  ·  Related audit sections: O, P, T

## Goal
Establish a trustworthy baseline before any behaviour change: real test results, a repeatable smoke check for
each of the 8 workflows, and documentation that matches the code.

## Candidate scope (to be confirmed in DRAFT)
- Run `uv run pytest -q`, `uv run ruff check src tests`, `cd frontend && npm run build`; record actual results.
- Write a smoke checklist per workflow (route, request, expected response shape).
- Correct the doc drift listed in `docs/PROJECT_STATE.md` (README / ARCHITECTURE claims: "8 agents behind a
  supervisor", hybrid retrieval, cross-encoder rerank, `compose_grounded_answer`, tenacity, Chroma fallback,
  auth rate limit, stateless scaling).
- Decide (ADR) what to do with dead code: `src/api/routes/mentor.py`, `src/agents/upload/upload.py`, `src/index.css`.

## Evidence
- 574 `def test_` functions in `tests/`; README claims 669 (unverified).
- CI ruff/prettier steps are `continue-on-error: true` (`.github/workflows/ci.yml`).

## Candidate acceptance criteria
- [ ] Baseline test/lint/build output recorded in the phase report.
- [ ] Smoke checklist exists for all 8 workflows.
- [ ] No README/ARCHITECTURE claim contradicts source.
