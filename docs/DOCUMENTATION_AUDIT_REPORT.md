# Documentation audit report — 2026-11-05

> **Publication note (release cleanup).** This report was written in a working tree that also held unpublished Phase 09 work (Sentry privacy hardening commit `6c07f81`, backup/restore runbook and scripts, a log-privacy test file, `docs/DECISIONS.md` edits). The published branch is based on `origin/main` and contains **none** of it. References below to those items describe the author's local tree, not the published repository; the backup/restore and Sentry-setup runbooks are **not** part of this release. `src/eval/eval_report.md` is unmodified and its provenance remains UNVERIFIED.

Record of the documentation-only upgrade: what changed, what was verified, what could not be, and how it was validated.
No application code, tests, migrations, configuration, CI, deployment settings or Git history were changed.

## Git safety record (start of task)
- Branch `main`; `origin/main...HEAD` = 0 behind / **1 ahead** (commit `6c07f81 fix: harden Sentry privacy and exception logging`, **not pushed, not touched**).
- Pre-existing uncommitted work preserved: modified `.claude/specs/phase-09-operations.md`, `docs/DECISIONS.md`, `docs/phase-reports/phase-09-slice-2-report.md`,
  `src/agents/current_affairs/{graph,ingest,monthly_ingest}.py`, `src/api/request_id.py`, `src/core/{error_monitoring,log_formatters,logging_config}.py`, `src/graph/tools.py`,
  `tests/test_error_monitoring.py`; untracked Phase 09 plan/reports, `docs/runbooks/backup-restore.md`, `scripts/backup/`, `tests/test_logging_privacy.py`.
- None of the documentation targets had uncommitted changes, so no merge was needed. `docs/DECISIONS.md` and the Phase 09 files were deliberately left alone.
- No reset, clean, checkout, stash, branch change, amend, commit or push was run.

## Files created (14)
`docs/PRODUCT_OVERVIEW.md`, `docs/HR_PROJECT_BRIEF.md`, `docs/PROJECT_HIGHLIGHTS.md`, `docs/ARCHITECTURE.md`, `docs/WORKFLOWS.md`, `docs/AGENT_ARCHITECTURE.md`,
`docs/RAG_AND_RETRIEVAL.md`, `docs/API_OVERVIEW.md`, `docs/SECURITY.md`, `docs/TESTING.md`, `docs/DEPLOYMENT.md`, `docs/EVALUATION.md`, `docs/DOCUMENTATION_INDEX.md`,
`docs/DOCUMENTATION_AUDIT_REPORT.md`.

## Files updated (4)
- `README.md` — rewritten (recruiter/engineer entry point; stale badges and numbers removed).
- `docs/ENGINEERING_METRICS.md` — added a "Re-audited summary" section at the top; the existing detailed benchmark sections are unchanged.
- `ARCHITECTURE.md` (root) — one pointer line added; content was already accurate.
- `DEPLOYMENT_GUIDE.md` (root) — stale "not deployed yet" banner replaced with a status and pointer.

## Intentionally untouched
All source, tests, migrations, `frontend/`, `.github/`, `render.yaml`, `Dockerfile`, `docker-compose.yml`, `.env*`, `.claude/`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`,
phase reports, runbooks, `scripts/`, `evals/`, `screenshots/`.

## Key factual corrections (old claim → verified)
| Old claim / source | Verified |
|---|---|
| README: "1212 offline tests / 76 modules" | 1,847 collected cases, 91 test files (2026-11-05) |
| README: "15,455 LOC / 109 modules", "6,243 LOC / 40 files" | 113 modules / 20,185 physical lines (excl. `src/eval`); 44 files / 7,663 lines |
| README: "4 Alembic revisions"; `CLAUDE.md` "0001–0004" | 6 revisions (0001–0006). `CLAUDE.md` corrected to 0001–0006 in the final review pass |
| README: "16 registered route modules" | 16 route groups; 54 operations under `/api/v1` (56 incl. `/`, `/health`) |
| README: stricter auth rate limit "configured but not yet applied" | Applied to six auth endpoints (`AUTH_LIMITED_PATHS`, ADR-010); `/auth/refresh` excluded |
| README: "Deployment status: being redeployed; links to be added" | Live links supplied by owner and added |
| README / deploy guide: Langfuse, Sentry, Upstash shown as part of the deployed architecture | Code exists; `render.yaml` sets no Upstash/Sentry; Sentry has no DSN; Langfuse activation unverifiable → labelled optional/UNVERIFIED |
| README: CI badge "lint · tests · build · LLM-eval" and "nightly faithfulness gate ≥ 0.9" | The gate is nightly/manual, needs secrets, tests the CRAG graph not served over HTTP; the committed sample report has 3 cases. Documented as such |
| README: "Pinned `LICENSE` MIT (recommended)" | `LICENSE` is MIT (Vishal Shivhare, 2026) |
| Task brief: "117 backend Python files excluding src/eval" | 113 excluding `src/eval`; 117 including it |
| Task brief: "97-query retrieval benchmark over five corpora" | Four labelled corpora; NCERT supported but empty |
| Task brief: "1,845 passing and 2 skipped" | Not in any repository record. Last recorded: 1765 passed / 2 skipped (Phase 08 close), 1790 / 2 (dirty Phase 09). Marked UNVERIFIED |
| Audit baseline (2026-11-02): global upload keys, model-chosen `persist_key`, unused auth limit, jobs without retries | Superseded by Phases 02–04 (owner-scoped keys, KB-only tool, applied auth limit, leased jobs) |

## Verified metrics (details and quote-safety in `docs/ENGINEERING_METRICS.md`)
- MEASURED-NOW: 113 / 117 Python files; 20,185 physical lines (15,525 non-blank non-comment lines); 44 TS/TSX files, 7,663 lines; 54 `/api/v1` operations; 6 migrations; 1,847 collected test cases
  (`pytest --collect-only`, **no tests executed**); 1,111 `def test_` definitions.
- RECORDED (not re-run): retrieval benchmark, routing 160→0/200 (stubbed), ≈ 13× synthetic throughput, 21 → 0 T1 failures, job-reliability benchmark, last full-suite results.
- All counts were taken on a **dirty working tree**.

## Missing evidence / UNVERIFIED
- Clean-release pass count (the full suite was intentionally not run for a Markdown task).
- Liveness, cold-start time and current health of the owner-supplied production URLs (not probed).
- Whether Upstash, Langfuse or Sentry are active in production; whether Vercel/Render auto-deploy is configured; production CORS values.
- Real-model quality, safety (T3), routing accuracy, latency, token usage and cost; production P50/P95/P99; user numbers.
- Human verification of retrieval labels; NCERT retrieval quality; production Qdrant latency.
- Whether the nightly judge job has ever run in GitHub, and the provenance of the 3-case `src/eval/eval_report.md`.
- The exact scope of the owner's personal contribution — `HR_PROJECT_BRIEF.md` describes it as owner-directed, AI-assisted (per `CLAUDE.md` and the phase workflow) and asks the owner to confirm.

## Broken links discovered
None in the 52 Markdown files outside `node_modules`, `.venv`, `.git`, `.claude/` and `data/` (relative links and anchors checked for the files edited here; relative file targets checked for the rest).
The only transient failure during authoring was the forward link to this report, now resolved.

## Discrepancies noted but not edited (out of scope)
- `render.yaml` header comment says "The app is stateless", which contradicts the documented in-process job pool (PROJECT_STATE "Doc drift").
- `render.yaml` service name `upsc-ai-pro-api` vs the owner-reported host `upsc-ai-pro-puih` (Render appends a suffix; expected).
- `pyproject.toml` version `0.1.0`, app version `2.0.0`, frontend `1.0.0`.
- `docs/phase-reports/phase-09-*` and `docs/runbooks/backup-restore.md` are uncommitted and were not reviewed or modified.

## Remaining documentation gaps
No architecture diagrams for the frontend component tree; no data-model (ERD) diagram; no recorded real-model examples or latency; `.env.example` variable reference is by group only (the file was not read, per project rules);
no changelog; `DEPLOYMENT_GUIDE.md` still contains first-deploy steps that partly duplicate `docs/DEPLOYMENT.md`.

## Validation commands and outcomes
| Check | Command / method | Outcome |
|---|---|---|
| Git state | `git status --short`, `git log --oneline -10`, `git rev-list --left-right --count origin/main...HEAD` | Recorded above; no history change |
| Relative links and anchors (17 edited/created files) | throwaway script in the session scratchpad | 0 problems after this report was created |
| Relative links, all 52 Markdown files | same | 0 broken |
| Secret patterns (API-key shapes, JWTs, connection strings, key blocks, `password=`) over all touched docs | `grep -E` | No matches |
| Inaccurate-term search (`8 agents`, `every answer`, `BM25`, `reranking enabled`, `Sentry active`, `Langfuse active`, `Prometheus`, `faithfulness ≥ 0.9`, `1,845`, `hybrid`) | `grep -Ei` | Remaining hits are all explicit negations or qualifications ("no BM25 index", "no Prometheus export", "not eight agents", "do not describe it as hybrid", the UNVERIFIED 1,845 note) or the historical Phase 06 decision text in `ENGINEERING_METRICS.md` ("Cross-encoder reranking / BM25: deferred") |
| Mermaid syntax | Manual review + bracket/quote balance script. A search for a local renderer (`mmdc`, a `mermaid` package in `frontend/node_modules`, global npm) found none, and nothing was installed | 7 diagrams (README 2, ARCHITECTURE 3, AGENT_ARCHITECTURE 1, RAG_AND_RETRIEVAL 1); no unbalanced brackets or quotes; flagged items are valid `[("…")]` cylinder nodes; logic re-read against the code. **No diagram has been rendered, so GitHub rendering is UNVERIFIED** until someone opens the files on GitHub |
| Routes | `app.openapi()` in a throwaway script (no server started; temp SQLite) | 56 operations / 54 paths; 54 under `/api/v1` |
| Test count | `uv run pytest --collect-only -q` | `1847 tests collected` |
| Full test suite | **Not run** (Markdown-only change) | — |

## Final review pass (2026-11-05, second session)
Documentation-only again; same Git safety rules (no commit, push, reset, branch change). The unpushed commit `6c07f81` and all Phase 09 work were preserved.

**Changed in this pass:** `CLAUDE.md` (one line: migrations `0001–0004` → `0001–0006`, confirmed from the six files in `migrations/versions/`);
`docs/ENGINEERING_METRICS.md` (historical `1212 passed` row marked superseded; "how to read the historical sections" note; routing-benchmark methodology limit;
T1/T2/T3 tier clarification for the "21 failures"; new "CI judge gate status" and "Never measured" sections; migration note); `docs/HR_PROJECT_BRIEF.md` (role section rewritten as a
responsibility table; "collected" test wording); `docs/PRODUCT_OVERVIEW.md` (removed an unsupported "single-developer" statement); `README.md` (prominent UI-preview screenshot warning and per-image captions,
"collected" test wording, one diagram label); `screenshots/README_SCREENSHOTS.md` (warning banner, and note that `mentor-streaming.png` shows a UI state, not current Mentor behaviour);
this report.

**Consistency review:** README, HR brief, highlights, evaluation, metrics, index and CLAUDE.md agree on 1,847 *collected* cases (never "passed"), 54 operations, 113/117 modules, four labelled corpora, six migrations,
eight workflows (not agents), and the unactivated state of Sentry/Langfuse/Upstash. Remaining keyword hits (BM25, reranking, p95, Sentry/Langfuse) are negations, disabled-feature descriptions, synthetic or offline latency
labelled as such, or historical decision text.

**Needs owner confirmation:** the wording of the "My role" table in `HR_PROJECT_BRIEF.md` (especially hosting-account setup, production releases, and the manual browser checks); that the owner-supplied live URLs are current;
whether Upstash, Langfuse or auto-deploy are enabled on the live services; provenance of `src/eval/eval_report.md`.
