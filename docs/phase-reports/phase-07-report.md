# Phase 07 report — LLM evaluation, safety & quality

Date: 2026-11-03 · Spec: `.claude/specs/phase-07-llm-eval-safety.md` (Revision 3) · Plan:
`.claude/plans/phase-07-llm-eval-safety.md` · ADR: ADR-016 (+ review addenda) · Status: **DONE (2026-11-03)** —
offline T1/T2 evidence only; **T3 (live) NOT RUN by design (AC12 open, allowed by the spec)**

> **Disclaimer.** T1 (prompt construction) and T2 (scripted behaviour) run with fake models and zero network. They show
> that untrusted text reaches prompts only inside fences and that the plumbing and parsing work. **They do not show how a
> real model behaves.** Real-model safety, quality and cost stay UNVERIFIED until the owner validates the token bounds and
> authorises a live (T3) run.

## Summary
- **Prompt-safety fixes F0–F7** (production): every untrusted value that reaches a prompt is now fenced with
  `harden_untrusted()`. That covers upload and lecture metadata, the PYQ bank and parser input, the mentor student
  profile, the evaluator answer and question, and NCERT/lecture study aids. The sanitiser neutralises spoofed fence
  markers, including Unicode, nested, stuck-character, decoy-word and combining-mark forms. F6 keeps evaluator
  scores inside `[0, max]`.
- **Eval harness `evals/`** (new):
  - 60 synthetic cases: workflow cases plus red-team cases;
  - deterministic checks and a fenced, report-only judge;
  - strict offline isolation: network blocked, real-client tripwires, temp Qdrant, and state restored on exit;
  - a request-free dry-run estimate.
- **Live mode is budget-gated.** It needs explicit caps and `--yes`, every model priced and owner-validated, a
  reservation before every send (LLM callback plus an embeddings guard under the cache), and a provider-usage check
  after each call. The shipped `evals/pricing.json` marks every bound `unvalidated`, so live mode refuses before any
  client is built.
- **Review remediation:** rounds 1–5 plus the final budget review (ADR-016 addenda). The final budget review made all
  ledger transactions atomic under one shared lock: reservations, usage validation and the stop latch. It also limited
  one managed reservation to one send.
- **Unchanged:** API paths, response shapes and ownership keys, `src/eval/` and `scripts/bench_retrieval.py`.

## Files changed (`git status --porcelain`, 2026-11-03)
```
 M .claude/specs/README.md
 M .claude/specs/phase-08-evals.md
RM .claude/specs/phase-07-product-gaps.md -> .claude/specs/phase-08-product-gaps.md
 M docs/DECISIONS.md
 M docs/ENGINEERING_METRICS.md
 M docs/PROJECT_STATE.md
 M evals/README.md
 M src/agents/evaluator/graph.py
 M src/agents/lecture/graph.py
 M src/agents/mentor/graph.py
 M src/agents/pyq/graph.py
 M src/agents/upload/graph.py
 M src/core/eval_parse.py
 M src/core/prompt_safety.py
 M src/core/study_aids.py
 M tests/test_eval_parse.py
 M tests/test_evaluator_rubric.py
 M tests/test_lecture_text.py
 M tests/test_prompt_safety.py
 M tests/test_pyq_text.py
 M tests/test_upload_prompts.py
 M tests/test_upload_text.py
?? .claude/plans/phase-07-llm-eval-safety.md
?? .claude/specs/phase-07-llm-eval-safety.md
?? evals/__init__.py
?? evals/datasets/ncert_synthetic_chapter.md
?? evals/datasets/redteam_cases.json
?? evals/datasets/workflow_cases.json
?? evals/evaluators/__init__.py
?? evals/evaluators/checks.py
?? evals/evaluators/judge.py
?? evals/pricing.json
?? evals/reports/.gitignore
?? evals/runners/__init__.py
?? evals/runners/adapters.py
?? evals/runners/budget.py
?? evals/runners/isolation.py
?? evals/runners/run_production_evals.py
?? tests/test_eval_checks.py
?? tests/test_eval_judge.py
?? tests/test_mentor_profile_context.py
?? tests/test_production_evals.py
?? tests/test_study_aids_fencing.py
```
Also present, not part of the phase: `checkpoints.sqlite-shm` / `checkpoints.sqlite-wal`, SQLite side files of the
git-ignored local checkpoint DB. They were not opened by Claude. Add `docs/phase-reports/phase-07-report.md` (this file).

## Acceptance criteria
| AC | Criterion (short) | Status | Evidence |
|---|---|---|---|
| AC1 | 8 workflows exercised through production functions | MET | `tests/test_production_evals.py` (offline run asserts each patched production call was reached); CLI coverage lists all 8 workflows |
| AC2 | Red-team coverage (indirect / metadata injection, marker spoofing, score manipulation, extraction, scope, abstain) | MET | `evals/datasets/redteam_cases.json`; T1 33 pass / 0 fail / 1 known gap |
| AC3 | `fenced_only` FAIL before F0–F7, PASS after | MET | Before (unfixed code): T1 7 pass / **21 fail** / 1 known gap; after: 0 fail. Detail: `docs/ENGINEERING_METRICS.md` → "LLM eval & safety (Phase 07)" → "The 21 'before' failures" |
| AC4 | Regression test per fix F0–F7 with canary + spoofed END marker; `confidence` never rendered | MET | `test_prompt_safety.py` (F0), `test_pyq_text.py` (F1), `test_upload_text.py` / `test_upload_prompts.py` (F2), `test_lecture_text.py` (F3), `test_mentor_profile_context.py` (F4), `test_evaluator_rubric.py` (F5), `test_eval_parse.py` (F6), `test_study_aids_fencing.py` (F7) |
| AC5 | Strict offline: 0 network attempts, real-client construction → exit 2, Qdrant isolation → exit 2, scoped keys, state restored on normal exit and on exception | MET | `tests/test_production_evals.py` (network-zero, tripwire, Qdrant, restoration tests) |
| AC6 | Judge isolation | MET | `tests/test_eval_judge.py` |
| AC7 | Live gating: dry-run, refusals with 0 requests, LLM callback exit 3, embedding guard without the callback, mixed-role cost cap, unvalidated bounds → exit 2, token bound / exit 4, report minimisation | MET | `tests/test_production_evals.py` (92 tests in the file), including the final budget-review concurrency tests |
| AC8 | Parsed scores within `[0, max]`; Mains `max_marks` == requested; pinned test updated with reason | MET | `tests/test_eval_parse.py` |
| AC9 | `src/eval/` and `scripts/bench_retrieval.py` byte-unchanged; Phase 06 tests pass; ADR-016 ownership | MET | `git diff --stat -- src/eval scripts/bench_retrieval.py` → empty; full suite green; ADR-016 |
| AC10 | Full suite green; ruff, compileall, `git diff --check` clean; workflow table checked; Phase 02 ownership tests pass | MET | Gate results below; ownership tests `17 passed`; workflow table updated |
| AC11 | `security-reviewer` and `code-quality-reviewer`: no BLOCKING | MET, with caveat | Review rounds 1–5 and the final targeted budget review: every BLOCKING / SHOULD-FIX item remediated. NITs are in the Backlog. **The last budget-concurrency fix (shared ledger lock, atomic `record_llm_usage`, managed send scope) was verified by tests only; no reviewer agent re-reviewed that delta — UNVERIFIED** |
| AC12 | T3 live run | **NOT RUN / UNVERIFIED (by design)** | Every model in `evals/pricing.json` has `token_bound.status: "unvalidated"`, so live mode refuses. The spec allows closing with AC12 open |

## Final budget review (this close-out session)
- **Ledger atomicity** (`evals/runners/budget.py`):
  - `BudgetLedger._lock` (an `RLock`) covers every reservation's stop check, cap checks (including the shared
    total-cost cap) and counter update.
  - `record_llm_usage` runs detection, the actual-usage counters, the bound comparison and the stop latch in one
    transaction.
  - `summary`, `_halt` and `_check_open` take the same lock.
  - Lock order is callback → ledger; the ledger never takes another lock.
- **Managed send scope** (`budget.py`, `evals/runners/isolation.py:install_send_guard`):
  - `require_reservation` returns a scope held open for the whole send.
  - Nested checks of one send pass: `_agenerate` → `_generate` → `_stream`, including from an executor thread.
  - A later independent send with the same `run_id` is refused and latched.
  - Duplicate `on_chat_model_start` calls for one run are charged once, even when concurrent.
- **Deterministic tests** (event/barrier interleaving, `tests/test_production_evals.py`):
  - two LLM reservations racing the same cap;
  - two embedding reservations racing the same cap;
  - mixed LLM + embedding reservations racing the shared cost cap, in both orders;
  - a reservation racing a halt;
  - a run started twice concurrently;
  - over-bound or unverifiable usage racing a reservation (2 cases);
  - nested sync and async managed checks, a nested check from another thread, and state cleanup.
- **Negative control:** the 6 race tests fail with both locks replaced by no-op locks. The 2 usage-race tests fail on
  the pre-fix `record_llm_usage`. All pass with the fix.
- **Accepted residual:** two sends that overlap in time and share one run manager look like one send with a nested
  check. LangChain gives every invoke its own run manager.

## Gate results (verbatim, 2026-11-03)
- `uv run pytest -q` → `1520 passed, 2 skipped in 163.72s (0:02:43)`. The 2 skips are the file-symlink escape tests
  that need Windows symlink privilege, as in Phase 06.
- `uv run ruff check src tests evals` → `All checks passed!`
- `uv run python -m compileall -q src tests evals` → exit 0
- `git diff --check` → exit 0 (only LF→CRLF working-copy warnings)
- `cd frontend && npm run lint` → `All matched files use Prettier code style!`. Frontend unchanged in Phase 07
  (`git status -- frontend` empty), so `npm run build` was not run.
- `uv run pytest -q tests/test_security_ownership.py tests/test_chat_isolation.py` → `17 passed in 9.57s`
- `uv run python -m evals.runners.run_production_evals` (offline) → exit 0. T1: 33 pass / 0 fail / 0 error /
  1 known gap. T2: 72 pass / 0 fail / 0 error. 60 cases, all 8 workflows covered. The report was written to the
  git-ignored `evals/reports/`.

## 8-workflow status
| Workflow | Phase 07 change | Status |
|---|---|---|
| AI Mentor | Student profile block fenced (F4) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |
| NCERT Study | Study aids fenced (F7) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |
| PYQ + Personal PYQ Bank | Bank context + parser input fenced (F1) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |
| Answer Evaluator | Answer/question fenced (F5); parsed score bounded (F6) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |
| Current Affairs | Eval-only (no app change) | Offline suite + T1/T2 PASS |
| Study Planner | Eval-only (no app change) | Offline suite + T1/T2 PASS |
| Lecture Learning | Chat + notes metadata fenced; `medium` allow-listed (F3, S6); study aids (F7) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |
| PDF / Document Chat | Chat + notes metadata fenced (F2) | Offline suite + T1/T2 PASS; real-model UNVERIFIED |

API paths, response shapes and ownership keys are unchanged. Live workflow smoke (`docs/SMOKE_CHECKLIST.md`) was not
run — UNVERIFIED.

## Backlog items (recorded in `docs/PROJECT_STATE.md` → Backlog)
- Token-bound validation procedure + owner-authorised T3 live run — OPEN.
- Scope gate misses creative-writing requests (`rt-scope-song` known gap) — MED.
- All 60 eval cases are Claude-written (`human_verified: false`) — OPEN.
- Live preflight / streaming-usage behaviour of real clients UNVERIFIED; Groq fallback live runs always refuse — LOW.
- Production usage tracking misses tool-bound / structured-output calls; planner inputs unfenced (self-injection
  only); evaluator `_log_eval` logs the raw score; mentor-KB chunks not captured in eval reports — LOW.
- Review NITs and accepted residuals of the word-bounded marker design — LOW. Round-3 N-3 is now superseded.
- One unexplained offline CLI exit 5 (not reproduced) — LOW.

## Follow-ups
- Owner: review the uncommitted Phase 07 changes and commit if wanted.
- Optional: a `security-reviewer` / `code-quality-reviewer` pass on the final budget-concurrency delta, to close the
  AC11 caveat.
- Owner-approved token-bound validation, then pricing and T3, before any real-model claim.
- Next phase: 08 (product gaps), only when the owner asks.
