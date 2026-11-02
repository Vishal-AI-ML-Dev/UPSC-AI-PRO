# Plan — Phase 01 Baseline & safety net

Spec: `.claude/specs/phase-01-baseline-safety-net.md`  ·  Status: DONE (2026-11-02)

## Files to touch (exhaustive — anything else needs a plan amendment)
| File | Change |
|---|---|
| `tests/test_workflow_smoke.py` | new — parametrised offline smoke test: 8 workflows × {401 no token, 422 invalid body}, + route-mounted check |
| `docs/SMOKE_CHECKLIST.md` | new — manual live smoke checklist per workflow |
| `README.md` | factual corrections of the §3 drift claims (lines ~5–7, 12, 25, 72–75, 106, 109, 111, 130, 133, 144–145, 190, 283, 330–335) |
| `ARCHITECTURE.md` | factual corrections (lines ~3–7, 18, 40, 46–48, 60, 73, 108–112) |
| `docs/DECISIONS.md` | append ADR-006 — dead code handling |
| `docs/PROJECT_STATE.md` | phase table, test-suite status, workflow "last verified", Doc-drift → resolved, Backlog additions, handoff |
| `docs/phase-reports/phase-01-report.md` | new — baseline outputs, AC evidence |
| `.claude/specs/phase-01-baseline-safety-net.md`, `.claude/specs/README.md`, this plan | status updates |

Nothing under `src/`, `frontend/`, `migrations/`, `.github/`, or dependency files.

## Steps
1. **Baseline (before any edit):** run the five R1 commands; save raw outputs to the scratchpad; copy summary lines
   into the phase report. If `frontend/node_modules` is missing, stop for those two and ask the user.
2. Record pre-existing failures / ruff findings in PROJECT_STATE Backlog (file, evidence, severity).
3. Read each workflow's request schema in `src/schemas.py` / route signature; write `tests/test_workflow_smoke.py`
   (one primary endpoint per workflow: `/agent/chat/stream`, `/ncert/study`, `/pyq/generate`,
   `/evaluator/evaluate/sync`, `/current-affairs/daily`, `/planner/generate/sync`, `/lecture/process-text`,
   `/upload/chat`). Run it focused.
4. Write `docs/SMOKE_CHECKLIST.md` from the routes + `frontend/src/lib/api.ts` call sites.
5. Edit `README.md` and `ARCHITECTURE.md` claim by claim; keep a claim→evidence table for the report.
6. Append ADR-006 per the user's answer to open question 1.
7. Re-run `uv run pytest -q` and `ruff check`; confirm AC6 with `git diff --stat`.
8. Update PROJECT_STATE + phase report; run `review-changes` skill (no security-reviewer needed — no auth/upload/
   prompt/vector code changes).

## Reused code
- `client`, `make_user` fixtures — `tests/conftest.py`
- 401 / auth-header pattern — `tests/test_security_ownership.py`, `tests/test_smoke_auth.py`

## Verification
```bash
uv run pytest -q
uv run pytest -q tests/test_workflow_smoke.py
uv run ruff check src tests
uv run python -m compileall -q src tests
cd frontend && npm run build
cd frontend && npm run lint
git diff --stat -- src frontend migrations .github   # must be empty
```
Manual / workflow checks: AC4 grep list (e.g. `grep -n "compose_grounded_answer\|tenacity\|Chroma\|10 / 300\|Stateless\|669" README.md ARCHITECTURE.md`).
Live checklist execution needs real keys → UNVERIFIED unless the user runs it.

## Rollback
`git checkout -- README.md ARCHITECTURE.md docs/` and delete the two new files. No code, migration or dependency involved.
