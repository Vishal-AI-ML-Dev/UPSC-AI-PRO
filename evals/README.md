# evals/

Future home of UPSC-AI-PRO evaluations (skeleton created in Phase 00).

| Folder | Will hold |
|---|---|
| `datasets/` | Versioned eval cases per workflow (JSON/JSONL) |
| `runners/` | Scripts that run a workflow's production path over a dataset |
| `evaluators/` | Metrics and LLM-as-judge scorers, quality gates |
| `reports/` | Generated reports (not imported by app code) |

**The live eval harness is still `src/eval/`** (`llm_eval.py`, `retrieval_eval.py`, `gates.py`,
`eval_dataset.json`), run by `.github/workflows/ci.yml`. It moves here in Phase 08 (ADR-003,
`.claude/specs/phase-08-evals.md`). Do not add eval code here before that phase.

Rules: evals needing live API keys never run in the default `pytest` suite; reports never contain secrets
or real user data.
