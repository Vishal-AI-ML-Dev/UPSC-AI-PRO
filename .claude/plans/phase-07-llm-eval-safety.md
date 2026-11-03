# PLAN — Phase 07 LLM Evaluation, Safety & Quality
Spec: `.claude/specs/phase-07-llm-eval-safety.md` · Status: DONE (2026-11-03) · Approved 2026-11-03

## Files to touch (exhaustive; anything else needs a plan amendment)
| File | Change |
|---|---|
| `.claude/specs/phase-07-llm-eval-safety.md`, `.claude/plans/phase-07-llm-eval-safety.md` | NEW: this spec and plan |
| `.claude/specs/phase-07-product-gaps.md` → `phase-08-product-gaps.md` | `git mv` + retitle (**rename; your approval of this plan = approval of the rename**) |
| `.claude/specs/phase-08-evals.md` | Header marked SUPERSEDED by phase-07 (content kept) |
| `.claude/specs/README.md` | Roadmap renumbered |
| `evals/__init__.py`, `evals/runners/__init__.py`, `evals/evaluators/__init__.py` | NEW: package markers |
| `evals/datasets/workflow_cases.json`, `evals/datasets/redteam_cases.json`, `evals/datasets/ncert_synthetic_chapter.md` | NEW: synthetic data |
| `evals/pricing.json` | NEW: for every model in every role (workflow chain, judge, embedding): prices `null`, `verified_on: null`, `token_bound` {`method: "utf8_bytes"`, `status: "unvalidated"`, `validated_on: null`, `evidence: null`, `provider_usage_check`} |
| `evals/evaluators/checks.py` | NEW: R2 |
| `evals/evaluators/judge.py` | NEW: R3 (imports `JudgeScore`, `_FAITHFULNESS_SYS`, `_RELEVANCY_SYS` from `src/eval/llm_eval.py`) |
| `evals/runners/isolation.py` | NEW: R4a network guard, client tripwires, env blanking, Qdrant isolation assertions; R4c `ExitStack`-based restoration + cache reset |
| `evals/runners/budget.py` | NEW: R4b `BudgetLedger` (per-role + total cost), LLM pre-send callback (`raise_error=True`) with post-call usage validation, `BudgetedEmbeddingsGuard`, `utf8_bytes` bound, retry/output-limit preflight, pricing loader |
| `evals/runners/adapters.py` | NEW: one adapter per workflow (seed isolated state → call the production function → `{output, prompts, sources}`) |
| `evals/runners/run_production_evals.py` | NEW: CLI, dry-run, confirmation, three-tier report writer |
| `evals/reports/.gitignore` | NEW: ignore everything except itself and `.gitkeep` |
| `evals/README.md` | Rewrite: purpose, tiers, offline/live usage, budget, limitations, directory ownership |
| `src/core/prompt_safety.py` | F0 variant-tolerant marker strip; NEW `clean_label`, `fence_label` |
| `src/agents/pyq/graph.py` | F1 |
| `src/agents/upload/graph.py` | F2: `fence_label` for `book_name` / `subject` / `paper` in `ask_upload` and `process_upload` notes |
| `src/agents/lecture/graph.py` | F3: `fence_label` for `topic` / `paper` (chat + notes), `syllabus`, `medium` (notes) |
| `src/agents/mentor/graph.py` | F4 `_build_student_context` |
| `src/agents/evaluator/graph.py` | F5 |
| `src/core/eval_parse.py` | F6 |
| `src/core/study_aids.py` | F7 |
| `tests/test_eval_checks.py`, `tests/test_eval_judge.py`, `tests/test_production_evals.py`, `tests/test_mentor_profile_context.py`, `tests/test_study_aids_fencing.py` | NEW |
| `tests/test_prompt_safety.py`, `tests/test_pyq_text.py`, `tests/test_upload_text.py`, `tests/test_lecture_text.py`, `tests/test_evaluator_rubric.py`, `tests/test_eval_parse.py` | Add regression tests; update `test_eval_parse.py:89-91` (F6) |
| `docs/DECISIONS.md` | ADR-016: directory ownership (amends ADR-003), evidence tiers, judge pinning + fencing, metadata fencing rule, live budget policy |
| `docs/ENGINEERING_METRICS.md` | "LLM eval & safety" section: T1/T2 results; T3 UNVERIFIED |
| `docs/PROJECT_STATE.md` | Phase table; Backlog (deferred items + "token-bound validation procedure; T3 live run"); handoff (at close) |
| `docs/phase-reports/phase-07-report.md` | NEW at close (T1/T2/T3 separately; "before" evidence) |

**Explicitly unchanged:**
- `src/eval/**`, `scripts/bench_retrieval.py`
- `.github/**`, `pyproject.toml`, `uv.lock`
- `frontend/**`
- `*/prompts.py`
- `migrations/**`

## Steps
0. Write the spec and plan files (APPROVED 2026-11-03). Rename/supersede the stubs and renumber the
   roadmap.
1. **Stage A: harness, no app change.**
   - Write `isolation.py` + tests first (AC5).
   - Then `checks.py` + tests, then `judge.py` + tests (AC6), then `budget.py` + tests (AC7).
   - Then the datasets, `adapters.py`, and the runner + `test_production_evals.py`.
2. Run the offline evals on the **unfixed** code. Save the T1 failing `fenced_only` results as AC3 "before" evidence.
3. **Stage B: fixes.** F0 → F7, one at a time. Write each regression test first, watch it fail, fix, then run the
   focused tests.
4. Run the offline evals again: T1 all pass, T2 recorded.
5. Full gates. Check `git diff --stat src/eval scripts/bench_retrieval.py` is empty.
6. Docs: ADR-016, `evals/README.md`, the metrics section.
7. T3 hand-off note only. Document in the report and README:
   - the dry-run command, which is request-free and shows estimates labelled experimental/unvalidated;
   - that live mode is disabled until the owner validates the bounds and authorises a run.

   Add the Backlog item "define an owner-approved token-bound validation procedure". Claude runs **no** live command.
   AC12 = NOT RUN.
8. `security-reviewer` + `code-quality-reviewer`; remediate if approved; then `/complete-phase`.

## Reused code
- `harden_untrusted`, `sanitize_untrusted`, `format_chat_history`: `src/core/prompt_safety.py`.
- `JudgeScore`, `_FAITHFULNESS_SYS`, `_RELEVANCY_SYS`: `src/eval/llm_eval.py:75-97` (imported, not edited).
- `_make_gemini`: `src/core/llm.py:255` (pinned judge).
- `_with_tracing` `:240` + `reset_llm` `:388`: budget-callback seam.
- `settings.enable_provider_fallback`, `llm_max_retries`, `llm_max_output_tokens`: `src/core/config.py`.
- From `scripts/bench_retrieval.py`, via `importlib` as in `tests/test_bench_retrieval.py:24`, unchanged:
  - `BudgetedCachingEmbeddings`, `FakeEmbeddings`, `isolated_vector_store`, `preflight` (its chars/4
    `estimate_tokens` is **not** used for caps);
  - the corpus path guards.
- `upsert_documents`, `make_persist_key`: `src/core/vector_store.py`.
- `ncert_persist_key`: `ncert/graph.py:194`.
- `_upload_key` / `_lecture_key` / `_bank_key`.
- `supervisor._structured_llm`, `RouteDecision`, `_SUP_SYS`; `check_scope`; `parse_answer_evaluation`,
  `parse_mains_evaluation`.
- Test fakes:
  - `tests/test_upload_text.py:45` (`RunnableLambda`);
  - `tests/test_evaluator_rubric.py:37` (`RunnableGenerator`);
  - router fake `tests/test_supervisor_routing.py:37-81`;
  - tool-agent fake `tests/test_tool_agent_turns.py:25-56`.

## Verification (Claude runs offline only)
```bash
uv run pytest -q tests/test_eval_checks.py tests/test_eval_judge.py tests/test_production_evals.py
uv run pytest -q tests/test_prompt_safety.py tests/test_pyq_text.py tests/test_upload_text.py \
  tests/test_lecture_text.py tests/test_evaluator_rubric.py tests/test_eval_parse.py \
  tests/test_mentor_profile_context.py tests/test_study_aids_fencing.py
uv run pytest -q tests/test_security_ownership.py tests/test_chat_isolation.py \
  tests/test_bench_retrieval.py tests/test_retrieval_eval.py tests/test_eval_gate.py
uv run python -m evals.runners.run_production_evals --mode offline     # T1+T2, exit 0 after fixes
uv run python -m evals.runners.run_production_evals --mode live        # dry-run only: no --yes, 0 requests
uv run pytest -q
uv run ruff check src tests evals
uv run python -m compileall -q src tests evals
git diff --stat -- src/eval scripts/bench_retrieval.py                 # must be empty
git diff --check
```
Owner only, **disabled in this phase**. It refuses (exit 2) until every bound used by the run is owner-validated with
evidence, prices are filled, and the owner has explicitly authorised T3:
```bash
uv run python -m evals.runners.run_production_evals --mode live --yes \
  --max-llm-sends <N> --max-llm-input-tokens <T> \
  --max-embedding-requests <E> --max-embedding-tokens <ET> --max-cost-usd <USD>
```
Manual: the 8-workflow table in `docs/PROJECT_STATE.md`. The frontend is not touched.

## Rollback
- Revert the listed `src/` files (no migration, schema or dependency change).
- Remove the new `evals/*` and `tests/*` files.
- `git mv` the spec stub back and revert the docs.

## Amendment 1 (2026-11-03, owner-approved)
- **File added:** `tests/test_upload_prompts.py`, the assertion of `test_high_confidence_book_is_named` only.
- **Why:** fix F2 fences each upload metadata value on its own lines, so the old exact substring
  `"Book/Source: Indian Polity"` no longer occurs.
- **Still protected:** the test checks that the high-confidence book name is used, now inside the fenced block that
  follows `Book/Source:`. `test_chat_does_not_name_unconfirmed_book` still covers the low-confidence path.

## Amendment 2 (2026-11-03, owner-approved): review remediation
- **Scope:** fix review findings B1, S1–S10 and the owner-listed NITs 1–12, all inside files this plan already lists
  (no new files outside the table).
  - `src/core/prompt_safety.py`: B1 / S5.
  - `src/agents/lecture/graph.py`: S6.
  - `src/agents/evaluator/graph.py`: S10, including `get_model_answer` keywords / question.
  - `evals/**`, the listed `tests/**` and the docs: everything else.
- **S1 without editing `src/graph/tools.py` or `src/graph/supervisor.py`:** `live_isolation` wraps the outputs of
  `bind_tools_with_fallback` and `_structured_llm` with the budget callback.
  - It also installs a fail-closed send guard: a runtime patch of the `src.core.llm._TimeoutGuard` send methods,
    which every production Gemini / Groq client inherits.
  - Any send without an open reservation for its `run_id` is refused.
- **Backlog (owner decision D):**
  - duplicate `_judge_bounds` computation;
  - duplicate `TOOL_AGENT_WORST_CASE_CALLS` constant;
  - temp files left behind by a hard kill;
  - the unsupported Groq fallback configuration.
