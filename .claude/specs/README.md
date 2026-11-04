# Specs

One spec per phase, named `phase-NN-<slug>.md`, created from `_TEMPLATE.md`.

- A spec says **what** and **why** (goal, non-goals, verified current behaviour, acceptance criteria).
- The matching plan in `../plans/` says **how** (files, steps, verification).
- Status flow: `STUB` → `DRAFT` → `APPROVED` (by the user) → `DONE`.
- Only an `APPROVED` spec with an approved plan may be implemented.

## Roadmap (derived from `docs/audit/2026-11-02-architecture-audit.md` risk register)
| Phase | Spec | Status |
|---|---|---|
| 00 | Workspace setup (no spec; see `../plans/phase-00-workspace.md`) | DONE |
| 01 | `phase-01-baseline-safety-net.md` | DONE (2026-11-02) |
| 02 | `phase-02-security-ownership.md` | DONE (2026-11-02) |
| 03 | `phase-03-request-path-performance.md` | DONE (2026-11-02) |
| 04 | `phase-04-durable-jobs.md` | DONE (2026-11-02) |
| 05 | `phase-05-agent-architecture.md` | DONE (2026-11-02) |
| 06 | `phase-06-rag-quality.md` | DONE (2026-11-03) |
| 07 | `phase-07-llm-eval-safety.md` (LLM evaluation, safety & quality) | DONE (2026-11-03) — offline T1/T2; T3 live NOT RUN (bounds unvalidated) |
| 08 | `phase-08-product-gaps.md` (product persistence & gaps; ADR-017) | **COMPLETE (2026-11-04)** — APPROVED rev 4; slices A (+ A-1), B (+ B-1), C1 and C2 all CLOSED (`docs/phase-reports/phase-08-slice-{a,b,c1,c2}-report.md`); release preflight `docs/phase-reports/phase-08-release-preflight.md`; not yet deployed |
| — | `phase-08-evals.md` | SUPERSEDED by phase 07 |
| 09 | `phase-09-operations.md` | STUB |

Order is a recommendation; the user may re-prioritise. Jev / TypeSafe is not in the codebase and only gets
a phase if the user requests it.
